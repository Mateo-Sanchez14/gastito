"""Bot command handlers: /soy, saldo, deshacer, /cotizacion, ayuda.

These short-circuit the LLM path (cheap, deterministic). Each returns the reply
text to send back to the group (or None to stay silent).
"""

from __future__ import annotations

import logging

from fx.provider import _ARS_ENDPOINTS
from util import format_money, match_participant
from web_client import WebClient
from whatsapp.channel import InboundGroupMessage

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "🤖 *gastito* — cómo usarme:\n"
    "• Contá un gasto en lenguaje natural: _\"pagué 15 lucas de birra, entre todos\"_\n"
    "• Te muestro cómo quedaría y lo guardo recién cuando respondés *sí* "
    "(o *no* para descartarlo). Si no aclarás de qué fue, te pregunto la descripción.\n"
    "• *Editar un gasto*: respondé (citá) el mensaje del gasto con la corrección, "
    "ej. _\"eran 8000 no 800\"_ o _\"dividí entre todos menos Pichi\"_\n"
    "• `/soy <tu nombre>` — vinculá tu WhatsApp a tu nombre del grupo\n"
    "• `saldo` — ver quién le debe a quién\n"
    "• `deshacer` — borrar tu último gasto\n"
    "• `/cotizacion oficial|blue|mep` — fijar qué dólar usar para ARS\n"
    "• `ayuda` — este mensaje"
)


def detect_command(text: str) -> str | None:
    """Return a command keyword if the text is a command, else None."""
    t = text.strip().lower()
    if t.startswith(("/soy", "/iam")):
        return "soy"
    if t in ("saldo", "saldos", "balance", "balances"):
        return "saldo"
    if t in ("deshacer", "undo", "/deshacer", "/undo"):
        return "undo"
    if t.startswith(("/cotizacion", "/cotización", "cotizacion", "cotización")):
        return "cotizacion"
    if t in ("ayuda", "help", "/ayuda", "/help"):
        return "ayuda"
    return None


def handle_command(
    command: str,
    text: str,
    msg: InboundGroupMessage,
    link: dict,
    sender_participant_id: str | None,
    web: WebClient,
) -> str | None:
    group_id = link["groupId"]

    if command == "ayuda":
        return HELP_TEXT

    if command == "soy":
        # "/soy Juan" -> map this sender JID to participant "Juan"
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            return "Decime tu nombre así: `/soy Juan`"
        wanted = parts[1].strip()
        data = web.get_participants(group_id)
        participant = match_participant(wanted, data["participants"])
        if not participant:
            names = ", ".join(p["name"] for p in data["participants"])
            return f"No encontré a *{wanted}* en el grupo. Participantes: {names}"
        web.upsert_member(msg.chat_id, msg.sender_jid, participant["id"], msg.sender_name)
        return f"✅ Listo, te tengo como *{participant['name']}*."

    if command == "cotizacion":
        parts = text.strip().split()
        source = parts[1].lower() if len(parts) > 1 else ""
        if source not in _ARS_ENDPOINTS:
            return "Usá: `/cotizacion oficial`, `/cotizacion blue` o `/cotizacion mep`"
        web.set_fx_source(msg.chat_id, source)
        return f"✅ Para pesos argentinos voy a usar el dólar *{source}*."

    if command == "saldo":
        return _format_balances(web.get_balances(group_id))

    if command == "undo":
        if not sender_participant_id:
            return "Primero decime quién sos con `/soy <tu nombre>`."
        last = web.get_last_expense(group_id, sender_participant_id)
        if not last:
            return "No tenés ningún gasto para deshacer."
        web.delete_expense(last["id"], group_id, sender_participant_id)
        return f"🗑️ Borré tu último gasto: *{last['title']}*."

    return None


def _format_balances(data: dict) -> str:
    reimbursements = data.get("reimbursements", [])
    if not reimbursements:
        return "🎉 Están a mano, nadie debe nada."
    lines = ["💰 *Saldos:*"]
    for r in reimbursements:
        lines.append(f"• {r['fromName']} le debe {format_money(r['amount'])} a {r['toName']}")
    return "\n".join(lines)
