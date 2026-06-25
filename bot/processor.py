"""Core message processing: parse -> route command vs expense -> reply.

Mirrors american-store-chatbot's task handler shape, but for group expenses.
Runs in a background task (sync httpx is fine there).
"""

from __future__ import annotations

import logging
import time
from datetime import date

from commands import detect_command, handle_command
from config import settings
from fx.provider import ConversionError, convert
from llm.extractor import extract
from util import format_money, match_participant, normalize_name
from web_client import WebClient
from whatsapp.channel import parse_group_message
from whatsapp.gowa_client import GowaClient

logger = logging.getLogger(__name__)

web = WebClient()
gowa = GowaClient()

# Currencies we tell the LLM it may emit (FX covers all of these via dolarapi/erapi).
SUPPORTED_CURRENCIES = [
    "USD", "ARS", "CLP", "UYU", "BRL", "EUR", "MXN", "PEN", "COP", "PYG", "BOB",
]


def process_payload(payload: dict) -> None:
    """Entry point for the background task. Never raises."""
    try:
        _process(payload)
    except Exception:
        logger.exception("Unhandled error processing payload")


def _process(payload: dict) -> None:
    msg = parse_group_message(payload)
    if not msg:
        return

    link = web.get_link(msg.chat_id)
    if not link:
        logger.info("Ignoring message from unlinked group %s", msg.chat_id)
        return

    group_id = link["groupId"]
    text = (msg.text or "").strip()
    if not text:
        return

    # Small human-cadence pause before acting: show "typing…" then wait briefly.
    if settings.reply_delay_seconds > 0:
        gowa.send_chat_presence(msg.chat_id, "start")
        time.sleep(settings.reply_delay_seconds)

    # Resolve the sender -> participant (needed for /undo and default payer).
    members = web.get_members(msg.chat_id)
    sender_pid = next(
        (m["participantId"] for m in members if m["senderJid"] == msg.sender_jid), None
    )

    # Commands first (cheap, deterministic).
    command = detect_command(text)
    if command:
        reply = handle_command(command, text, msg, link, sender_pid, web)
        if reply:
            gowa.send_text(msg.chat_id, reply)
        return

    # Expense path requires a known sender.
    if not sender_pid:
        gowa.send_text(
            msg.chat_id,
            "👋 No sé quién sos todavía. Respondé `/soy <tu nombre>` para empezar.",
        )
        return

    data = web.get_participants(group_id)
    participants = data["participants"]
    categories = data.get("categories", [])
    today = date.today().isoformat()

    extraction = extract(
        text,
        msg.sender_name,
        [p["name"] for p in participants],
        SUPPORTED_CURRENCIES,
        [c["name"] for c in categories],
        today,
    )
    if extraction is None:
        gowa.send_text(msg.chat_id, "Uy, no pude procesar eso 😞. ¿Lo reescribís?")
        return

    if extraction.message_type == "chitchat":
        return  # stay quiet on non-expense chatter
    if extraction.message_type == "command":
        gowa.send_text(msg.chat_id, "¿Querías un comando? Probá `ayuda`.")
        return

    # --- it's an expense ---
    if extraction.confidence < settings.confidence_threshold or not extraction.amount:
        question = extraction.clarification_needed or "¿Cuánto fue y entre quiénes lo divido?"
        gowa.send_text(msg.chat_id, f"🤔 {question}")
        return

    # Resolve payer (default: the sender).
    if extraction.paid_by_name:
        payer = match_participant(extraction.paid_by_name, participants)
        if not payer:
            gowa.send_text(msg.chat_id, f"No reconozco a *{extraction.paid_by_name}*. ¿Quién pagó?")
            return
        paid_by_id = payer["id"]
    else:
        paid_by_id = sender_pid

    # Resolve who it's split among (empty = everyone).
    if extraction.paid_for_names:
        resolved = []
        for name in extraction.paid_for_names:
            p = match_participant(name, participants)
            if not p:
                gowa.send_text(msg.chat_id, f"No reconozco a *{name}* en el grupo.")
                return
            resolved.append(p)
        paid_for = resolved
    else:
        paid_for = participants

    # Currency conversion to USD (group currency).
    currency = (extraction.currency or "USD").upper()
    try:
        conv = convert(extraction.amount, currency, link.get("fxArsSource", "blue"))
    except (ConversionError, Exception):
        logger.exception("FX conversion failed for %s", currency)
        gowa.send_text(
            msg.chat_id, f"No pude convertir {currency} a USD ahora ⚠️. Probá de nuevo en un rato."
        )
        return

    original_cents = round(extraction.amount * 100)
    usd_cents = round(conv.usd * 100)

    # NOTE v1: we always split EVENLY among `paid_for` (we don't yet extract
    # per-person amounts/percentages). Named subsets still work via paid_for.
    expense_payload: dict = {
        "groupId": group_id,
        "title": extraction.title or "Gasto",
        "amount": usd_cents,
        "category": _resolve_category(extraction.category, categories),
        "paidById": paid_by_id,
        "paidForIds": [p["id"] for p in paid_for],
        "splitMode": "EVENLY",
        "expenseDate": extraction.date or today,
        "source": "whatsapp",
        "externalId": msg.message_id,
        "createdByParticipantId": sender_pid,
    }
    if currency != "USD":
        expense_payload.update(
            {
                "originalAmount": original_cents,
                "originalCurrency": currency,
                "conversionRate": conv.conversion_rate,
            }
        )

    try:
        web.create_expense(expense_payload)
    except Exception:
        logger.exception("create_expense failed")
        gowa.send_text(msg.chat_id, "No pude guardar el gasto 😞. Probá de nuevo.")
        return

    gowa.send_text(
        msg.chat_id,
        _confirmation(extraction, conv, usd_cents, currency, paid_by_id, paid_for, participants),
    )


def _resolve_category(name: str | None, categories: list[dict]) -> int:
    if not name:
        return 0
    target = normalize_name(name)
    for c in categories:
        if normalize_name(c["name"]) == target:
            return c["id"]
    return 0


def _confirmation(extraction, conv, usd_cents, currency, paid_by_id, paid_for, participants) -> str:
    name_by_id = {p["id"]: p["name"] for p in participants}
    payer = name_by_id.get(paid_by_id, "alguien")
    everyone = len(paid_for) == len(participants)
    among = "todos" if everyone else ", ".join(p["name"] for p in paid_for)
    title = extraction.title or "Gasto"

    line = f"✅ {format_money(usd_cents)} — {title}"
    if currency != "USD":
        line += f" ({extraction.amount:g} {currency} @ {conv.label})"
    line += f"\nPagó {payer}, dividido entre {among}."
    return line
