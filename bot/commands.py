"""Bot command handlers: /soy, saldo, deshacer, /cotizacion, ayuda.

These short-circuit the LLM path (cheap, deterministic). Each returns the reply
text to send back to the group (or None to stay silent).
"""

from __future__ import annotations

import logging
import re

from fx.provider import _ARS_ENDPOINTS
from util import (
    active_participants,
    format_money,
    match_participant,
    normalize_name,
)
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
    "• `/apodo <apodo> = <participante>` — anotar un apodo, ej. `/apodo Tuco = Fer`\n"
    "• `/entra <nombres>` — marcar que alguien está (entra al *dividido entre todos*)\n"
    "• `/sale <nombres>` — marcar que alguien ya no está. No lo borra: sus gastos "
    "y su saldo quedan igual\n"
    "• `/quienes` — ver quién está y quién no\n"
    "• `saldo` — ver quién le debe a quién\n"
    "• `resumen` — ver cuánto puso y cuánto gastó cada uno\n"
    "• `deshacer` — borrar tu último gasto\n"
    "• `/cotizacion oficial|blue|mep` — fijar qué dólar usar para ARS\n"
    "• 🎙️ *Audios*: podés mandarme una nota de voz para cualquiera de estas cosas "
    "(contar un gasto, decirme *sí*/*no*, corregir citando). Te muestro lo que escuché "
    "antes de guardar nada. Los comandos con barra (`/soy`, `/apodo`) mejor escribilos.\n"
    "• `ayuda` — este mensaje"
)


_ENTRA_PREFIXES = ("/entra", "/entran", "/vuelve", "/vuelven")
_SALE_PREFIXES = ("/sale", "/salen", "/se fue", "/se fueron")


def detect_command(text: str) -> str | None:
    """Return a command keyword if the text is a command, else None."""
    # Strip trailing punctuation before the exact matches below: a transcribed
    # voice note arrives as "saldo." and would otherwise miss every tuple, fall
    # through to the LLM, and come back as the useless "¿Querías un comando?".
    # (_classify_reply already does this per word; detect_command didn't.)
    t = text.strip().lower().rstrip(".,!¡¿?;:")
    if t.startswith(("/soy", "/iam")):
        return "soy"
    if t.startswith(("/apodo", "/apodos", "/alias")):
        return "apodo"
    if t in ("saldo", "saldos", "balance", "balances"):
        return "saldo"
    if t in ("resumen", "resúmen", "gastos", "total", "totales", "/resumen", "/gastos"):
        return "resumen"
    if t in ("deshacer", "undo", "/deshacer", "/undo"):
        return "undo"
    if t.startswith(("/cotizacion", "/cotización", "cotizacion", "cotización")):
        return "cotizacion"
    # Presencia. The two mutating ones require the slash: a bare "entra"/"sale"
    # shows up in ordinary chatter ("sale, dale") and a false positive here
    # silently changes how every later expense is split.
    if t.startswith(_ENTRA_PREFIXES):
        return "entra"
    if t.startswith(_SALE_PREFIXES):
        return "sale"
    if t in ("/quienes", "/quiénes", "quienes", "quiénes", "/presentes", "presentes"):
        return "quienes"
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

    if command == "apodo":
        return _handle_apodo(text, group_id, web)

    if command in ("entra", "sale"):
        return _handle_presencia(text, group_id, web, active=(command == "entra"))

    if command == "quienes":
        return _roster(web.get_participants(group_id)["participants"])

    if command == "cotizacion":
        parts = text.strip().split()
        source = parts[1].lower() if len(parts) > 1 else ""
        if source not in _ARS_ENDPOINTS:
            return "Usá: `/cotizacion oficial`, `/cotizacion blue` o `/cotizacion mep`"
        web.set_fx_source(msg.chat_id, source)
        return f"✅ Para pesos argentinos voy a usar el dólar *{source}*."

    if command == "saldo":
        return _format_balances(web.get_balances(group_id))

    if command == "resumen":
        return _format_summary(web.get_balances(group_id))

    if command == "undo":
        if not sender_participant_id:
            return "Primero decime quién sos con `/soy <tu nombre>`."
        last = web.get_last_expense(group_id, sender_participant_id)
        if not last:
            return "No tenés ningún gasto para deshacer."
        web.delete_expense(last["id"], group_id, sender_participant_id)
        return f"🗑️ Borré tu último gasto: *{last['title']}*."

    return None


_APODO_USAGE = (
    "Para anotar un apodo: `/apodo <apodo> = <participante>`\n"
    "Ej: `/apodo Tuco = Fer` · varios juntos: `/apodo Fer = Fernando, Tuco, Tuquina`"
)


def _handle_apodo(text: str, group_id: str, web: WebClient) -> str:
    """`/apodo <apodo> = <participante>` — attach nickname(s) to a participant.

    Order-agnostic: whichever side names an existing participant is the target;
    the other side is the new apodo(s) (comma-separated for several at once).
    """
    _, _, rest = text.strip().partition(" ")  # drop the "/apodo" word
    rest = rest.strip()
    sep = "=" if "=" in rest else (":" if ":" in rest else None)
    if not sep:
        return _APODO_USAGE
    left, _, right = rest.partition(sep)
    left, right = left.strip(), right.strip()
    if not left or not right:
        return _APODO_USAGE

    participants = web.get_participants(group_id)["participants"]

    # Which side is the existing participant? (a comma-list side is apodos, not a name)
    left_p = match_participant(left, participants) if "," not in left else None
    right_p = match_participant(right, participants) if "," not in right else None
    if left_p and right_p:
        if left_p["id"] != right_p["id"]:
            return (
                f"Ambos son participantes distintos (*{left_p['name']}* y *{right_p['name']}*). "
                "Poné el apodo nuevo de un lado y el participante del otro."
            )
        # Both sides point at the same person: one is an apodo that already
        # exists. Keep the side that isn't the canonical name as the apodo, so
        # a re-add lands as an idempotent no-op rather than a false error.
        canonical = left_p
        alias_side = (
            right if normalize_name(left) == normalize_name(canonical["name"]) else left
        )
    elif left_p:
        canonical, alias_side = left_p, right
    elif right_p:
        canonical, alias_side = right_p, left
    else:
        names = ", ".join(p["name"] for p in participants)
        return f"No reconozco a ninguno como participante. Participantes: {names}"

    raw_aliases = [a.strip() for a in alias_side.split(",") if a.strip()]
    if not raw_aliases:
        return _APODO_USAGE

    # An apodo can't collide with another participant's real name (ambiguous).
    participant_names = {normalize_name(p["name"]) for p in participants}
    aliases = [a for a in raw_aliases if normalize_name(a) not in participant_names]
    name_clashes = [a for a in raw_aliases if normalize_name(a) in participant_names]

    added: list[str] = []
    conflicts: list[dict] = []
    if aliases:
        try:
            result = web.add_aliases(group_id, canonical["id"], aliases)
        except Exception:
            logger.exception("add_aliases failed")
            return "No pude guardar el apodo 😞. Probá de nuevo."
        added = result.get("added", [])
        conflicts = result.get("conflicts", [])

    name_by_id = {p["id"]: p["name"] for p in participants}
    lines: list[str] = []
    if added:
        verb = "es" if len(added) == 1 else "son"
        lines.append(
            f"✅ Anotado: *{', '.join(added)}* ahora también {verb} *{canonical['name']}*."
        )
    for c in conflicts:
        owner = name_by_id.get(c.get("participantId"), "otra persona")
        lines.append(f"⚠️ *{c.get('alias')}* ya está en uso por *{owner}*.")
    for a in name_clashes:
        lines.append(f"⚠️ *{a}* ya es un participante, no lo puedo usar de apodo.")
    if not lines:
        lines.append(f"Ya tenía esos apodos anotados para *{canonical['name']}*.")
    return "\n".join(lines)


_PRESENCIA_USAGE = (
    "Para marcar quién está:\n"
    "• `/entra Fer, Juan` — se suman al *dividido entre todos*\n"
    "• `/sale Pichi` — deja de contar (no se borra, sus gastos quedan)\n"
    "• `/quienes` — ver la lista"
)

# "Fer, Juan y Pichi" -> three names. \by\b so a name like "Yamila" survives.
_NAME_SEPARATOR = re.compile(r"\s*(?:,|;|\by\b)\s*", re.IGNORECASE)


def _strip_command(text: str, prefixes: tuple[str, ...]) -> str:
    """Drop the command itself and return the rest. Longest prefix first, so
    "/se fue Pichi" doesn't leave "fue" behind and try to match it as a name."""
    t = text.strip()
    low = t.lower()
    for prefix in sorted(prefixes, key=len, reverse=True):
        if low.startswith(prefix):
            return t[len(prefix) :].strip()
    _, _, rest = t.partition(" ")
    return rest.strip()


def _handle_presencia(text: str, group_id: str, web: WebClient, active: bool) -> str:
    """`/entra <nombres>` / `/sale <nombres>` — move people in and out of the
    "presentes" list, which is what a default "entre todos" split covers.

    Nobody is ever removed from the group: on a trip the roster is fixed up
    front and people just arrive and leave, and deleting a participant would
    cascade and take their expenses with them.
    """
    prefixes = _ENTRA_PREFIXES if active else _SALE_PREFIXES
    rest = _strip_command(text, prefixes)
    if not rest:
        return _PRESENCIA_USAGE

    participants = web.get_participants(group_id)["participants"]

    targets: list[dict] = []
    unknown: list[str] = []
    for raw in _NAME_SEPARATOR.split(rest):
        name = raw.strip()
        if not name:
            continue
        p = match_participant(name, participants)
        if not p:
            unknown.append(name)
        elif all(t["id"] != p["id"] for t in targets):
            targets.append(p)

    if not targets:
        names = ", ".join(p["name"] for p in participants)
        return (
            f"❓ No reconozco a *{', '.join(unknown)}* en el grupo.\n"
            f"Participantes: {names}"
        )

    changed = [p for p in targets if bool(p.get("active", True)) != active]
    already = [p for p in targets if bool(p.get("active", True)) == active]

    if changed:
        try:
            web.set_participants_active(group_id, [p["id"] for p in changed], active)
        except Exception:
            logger.exception("set_participants_active failed")
            return "No pude actualizar la lista 😞. Probá de nuevo."
        for p in changed:
            p["active"] = active  # keep the local snapshot in sync for the roster

    lines: list[str] = []
    if changed:
        who = ", ".join(p["name"] for p in changed)
        if active:
            verb = "está" if len(changed) == 1 else "están"
            lines.append(f"✅ Ahora también {verb}: *{who}*.")
        else:
            verb = "ya no está" if len(changed) == 1 else "ya no están"
            lines.append(
                f"👋 Anotado, *{who}* {verb}. Sus gastos y su saldo quedan igual."
            )
    if already:
        who = ", ".join(p["name"] for p in already)
        plural = "" if len(already) == 1 else "n"
        state = "ya estaba" if active else "ya no estaba"
        lines.append(f"ℹ️ *{who}* {state}{plural}.")
    for u in unknown:
        lines.append(f"⚠️ A *{u}* no lo reconozco en el grupo, lo salteé.")

    lines.append(_roster(participants))
    if not active_participants(participants):
        lines.append(
            "⚠️ Así no queda nadie presente: los gastos \"entre todos\" van a "
            "fallar hasta que uses `/entra`."
        )
    return "\n".join(lines)


def _roster(participants: list[dict]) -> str:
    """Who's on the trip right now. Sent on its own (`/quienes`) and appended to
    every /entra and /sale, so the group always sees the resulting default."""
    active = active_participants(participants)
    active_ids = {p["id"] for p in active}
    away = [p for p in participants if p["id"] not in active_ids]

    if not participants:
        return "El grupo no tiene participantes todavía."
    if not active:
        return f"😴 No está nadie: {', '.join(p['name'] for p in away)}"
    if not away:
        return (
            f"👥 Están todos ({len(active)}): "
            + ", ".join(p["name"] for p in active)
        )
    return (
        f"👥 *Presentes ({len(active)}):* "
        + ", ".join(p["name"] for p in active)
        + f"\n😴 *No están ({len(away)}):* "
        + ", ".join(p["name"] for p in away)
    )


def _format_balances(data: dict) -> str:
    reimbursements = data.get("reimbursements", [])
    if not reimbursements:
        return "🎉 Están a mano, nadie debe nada."
    lines = ["💰 *Saldos:*"]
    for r in reimbursements:
        lines.append(f"• {r['fromName']} le debe {format_money(r['amount'])} a {r['toName']}")
    return "\n".join(lines)


def _format_summary(data: dict) -> str:
    """Per-person breakdown: what each one put in (paid), their share of the
    spending (paidFor), and the resulting net balance.

    `balances` come from the web's /balances endpoint, in group-currency cents:
    `paid` = money they fronted, `paidFor` = their share of all expenses,
    `total` = paid - paidFor (positive = they're owed, negative = they owe).
    """
    balances = data.get("balances", [])
    if not balances:
        return "Todavía no hay gastos cargados. Contá uno y lo registro 📝"

    total_spent = sum(b.get("paid", 0) for b in balances)
    rows = sorted(balances, key=lambda b: b.get("paid", 0), reverse=True)

    lines = [
        "📊 *Resumen del grupo*",
        f"Gastado en total: {format_money(total_spent)}",
        "",
    ]
    for b in rows:
        net = b.get("total", 0)
        if net > 0:
            net_txt = f"le deben {format_money(net)}"
        elif net < 0:
            net_txt = f"debe {format_money(-net)}"
        else:
            net_txt = "a mano"
        lines.append(
            f"• *{b['name']}*: puso {format_money(b.get('paid', 0))} · "
            f"le toca {format_money(b.get('paidFor', 0))} → {net_txt}"
        )
    return "\n".join(lines)
