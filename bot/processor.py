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
from llm.extractor import extract, extract_edit
from util import format_money, match_participant, normalize_name
from web_client import WebClient
from whatsapp.channel import InboundGroupMessage, parse_group_message
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

    # Edit path: is this a reply that quotes a known expense? If so, treat the
    # message as a correction. (If the quoted message isn't an expense we know,
    # fall through to the normal create path below.)
    if msg.replied_to_id:
        target = web.get_expense_by_message(msg.replied_to_id)
        if target and target.get("groupId") == group_id:
            _process_edit(msg, group_id, link, sender_pid, target, text)
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

    # Resolve payer (default: the sender), split, and FX (shared with edits).
    try:
        resolved = _resolve_expense_fields(extraction, participants, link, sender_pid)
    except _ResolveError as e:
        gowa.send_text(msg.chat_id, e.reply)
        return

    # NOTE v1: we always split EVENLY among `paid_for` (we don't yet extract
    # per-person amounts/percentages). Named subsets still work via paid_for.
    expense_payload: dict = {
        "groupId": group_id,
        "title": extraction.title or "Gasto",
        "amount": resolved["usd_cents"],
        "category": _resolve_category(extraction.category, categories),
        "paidById": resolved["paid_by_id"],
        "paidForIds": [p["id"] for p in resolved["paid_for"]],
        "splitMode": "EVENLY",
        "expenseDate": extraction.date or today,
        "source": "whatsapp",
        "externalId": msg.message_id,
        "createdByParticipantId": sender_pid,
    }
    expense_payload.update(_original_currency_fields(resolved))

    try:
        result = web.create_expense(expense_payload)
    except Exception:
        logger.exception("create_expense failed")
        gowa.send_text(msg.chat_id, "No pude guardar el gasto 😞. Probá de nuevo.")
        return

    confirmation = _confirmation(
        extraction,
        resolved["conv"],
        resolved["usd_cents"],
        resolved["currency"],
        resolved["paid_by_id"],
        resolved["paid_for"],
        participants,
    )
    conf_id = gowa.send_text(msg.chat_id, confirmation)
    # Link the confirmation message so a reply to it can edit this expense.
    expense_id = (result or {}).get("expense", {}).get("id")
    if conf_id and expense_id:
        web.record_message_ref(conf_id, expense_id)


class _ResolveError(Exception):
    """Carries a user-facing reply for a resolution failure (payer/split/FX)."""

    def __init__(self, reply: str):
        super().__init__(reply)
        self.reply = reply


def _resolve_expense_fields(
    extraction, participants: list[dict], link: dict, default_payer_id: str | None
) -> dict:
    """Resolve payer, split, and FX from an extraction. Shared by create/edit.

    Raises _ResolveError(reply) on any unresolved name or FX failure.
    """
    # Payer (defaults to the sender on create / the current payer on edit).
    if extraction.paid_by_name:
        payer = match_participant(extraction.paid_by_name, participants)
        if not payer:
            raise _ResolveError(f"No reconozco a *{extraction.paid_by_name}*. ¿Quién pagó?")
        paid_by_id = payer["id"]
    else:
        paid_by_id = default_payer_id
    if not paid_by_id:
        raise _ResolveError("¿Quién pagó este gasto?")

    # Who it's split among (empty = everyone).
    if extraction.paid_for_names:
        paid_for = []
        for name in extraction.paid_for_names:
            p = match_participant(name, participants)
            if not p:
                raise _ResolveError(f"No reconozco a *{name}* en el grupo.")
            paid_for.append(p)
    else:
        paid_for = participants

    # Currency conversion to USD (group currency).
    currency = (extraction.currency or "USD").upper()
    try:
        conv = convert(extraction.amount, currency, link.get("fxArsSource", "blue"))
    except (ConversionError, Exception):
        logger.exception("FX conversion failed for %s", currency)
        raise _ResolveError(
            f"No pude convertir {currency} a USD ahora ⚠️. Probá de nuevo en un rato."
        )

    return {
        "paid_by_id": paid_by_id,
        "paid_for": paid_for,
        "conv": conv,
        "currency": currency,
        "original_cents": round(extraction.amount * 100),
        "usd_cents": round(conv.usd * 100),
    }


def _original_currency_fields(resolved: dict) -> dict:
    """Original-amount fields to merge into a payload when currency != USD."""
    if resolved["currency"] == "USD":
        return {}
    return {
        "originalAmount": resolved["original_cents"],
        "originalCurrency": resolved["currency"],
        "conversionRate": resolved["conv"].conversion_rate,
    }


def _process_edit(
    msg: InboundGroupMessage,
    group_id: str,
    link: dict,
    sender_pid: str | None,
    target: dict,
    text: str,
) -> None:
    """Apply a correction to an existing expense (a reply that quotes it)."""
    data = web.get_participants(group_id)
    participants = data["participants"]
    categories = data.get("categories", [])
    today = date.today().isoformat()
    name_by_id = {p["id"]: p["name"] for p in participants}
    cat_by_id = {c["id"]: c["name"] for c in categories}

    # Describe the current expense in the user's original currency for the LLM.
    if target.get("originalCurrency"):
        cur_amount = (target.get("originalAmount") or 0) / 100
        cur_currency = target["originalCurrency"]
    else:
        cur_amount = (target.get("amount") or 0) / 100
        cur_currency = "USD"
    current = {
        "title": target.get("title"),
        "amount": f"{cur_amount:g}",
        "currency": cur_currency,
        "paid_by_name": name_by_id.get(target.get("paidById")),
        "paid_for_names": [
            name_by_id[i] for i in target.get("paidForIds", []) if i in name_by_id
        ],
        "category": cat_by_id.get(target.get("categoryId")),
        "date": target.get("expenseDate"),
    }

    extraction = extract_edit(
        current,
        text,
        msg.sender_name,
        [p["name"] for p in participants],
        SUPPORTED_CURRENCIES,
        [c["name"] for c in categories],
        today,
    )
    if extraction is None:
        gowa.send_text(msg.chat_id, "Uy, no pude procesar la corrección 😞. ¿La reescribís?")
        return

    # Not actually a correction (a comment/emoji/thanks) — stay quiet.
    if extraction.message_type != "expense":
        return
    if extraction.confidence < settings.confidence_threshold or not extraction.amount:
        question = extraction.clarification_needed or "¿Qué querés corregir del gasto?"
        gowa.send_text(msg.chat_id, f"🤔 {question}")
        return

    # Idempotency lock: claim this inbound edit message before mutating. A Gowa
    # webhook retry of the same message won't re-apply (and re-confirm) the edit.
    expense_id = target["id"]
    if not web.record_message_ref(msg.message_id, expense_id):
        logger.info("Edit message %s already processed; skipping", msg.message_id)
        return

    try:
        resolved = _resolve_expense_fields(
            extraction, participants, link, target.get("paidById")
        )
    except _ResolveError as e:
        gowa.send_text(msg.chat_id, e.reply)
        return

    edit_payload: dict = {
        "groupId": group_id,
        "title": extraction.title or target.get("title") or "Gasto",
        "amount": resolved["usd_cents"],
        "category": _resolve_category(extraction.category, categories),
        "paidById": resolved["paid_by_id"],
        "paidForIds": [p["id"] for p in resolved["paid_for"]],
        "splitMode": "EVENLY",
        "expenseDate": extraction.date or target.get("expenseDate") or today,
        "createdByParticipantId": sender_pid,
    }
    edit_payload.update(_original_currency_fields(resolved))

    try:
        web.update_expense(expense_id, edit_payload)
    except Exception:
        logger.exception("update_expense failed")
        gowa.send_text(msg.chat_id, "No pude actualizar el gasto 😞. Probá de nuevo.")
        return

    confirmation = _confirmation(
        extraction,
        resolved["conv"],
        resolved["usd_cents"],
        resolved["currency"],
        resolved["paid_by_id"],
        resolved["paid_for"],
        participants,
        edited=True,
    )
    conf_id = gowa.send_text(msg.chat_id, confirmation)
    # Link the new confirmation so a further reply keeps editing the same expense.
    if conf_id:
        web.record_message_ref(conf_id, expense_id)


def _resolve_category(name: str | None, categories: list[dict]) -> int:
    if not name:
        return 0
    target = normalize_name(name)
    for c in categories:
        if normalize_name(c["name"]) == target:
            return c["id"]
    return 0


def _confirmation(
    extraction, conv, usd_cents, currency, paid_by_id, paid_for, participants, edited=False
) -> str:
    name_by_id = {p["id"]: p["name"] for p in participants}
    payer = name_by_id.get(paid_by_id, "alguien")
    everyone = len(paid_for) == len(participants)
    among = "todos" if everyone else ", ".join(p["name"] for p in paid_for)
    title = extraction.title or "Gasto"

    line = f"{'✏️ Actualizado:' if edited else '✅'} {format_money(usd_cents)} — {title}"
    if currency != "USD":
        line += f" ({extraction.amount:g} {currency} @ {conv.label})"
    line += f"\nPagó {payer}, dividido entre {among}."
    return line
