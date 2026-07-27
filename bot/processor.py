"""Core message processing: parse -> route command vs expense -> reply.

Mirrors american-store-chatbot's task handler shape, but for group expenses.
Runs in a background task (sync httpx is fine there).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date

import pending
from commands import detect_command, handle_command
from config import settings
from fx.provider import ConversionError, convert
from llm.extractor import extract, extract_edit, roast_joke
from llm.transcriber import transcribe
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


def _first_name(name: str) -> str:
    parts = (name or "").strip().split()
    return parts[0] if parts else ""


def _addressed(name: str, body: str) -> str:
    """Prefix a reply with the member's first name, so in a busy group everyone
    can tell whose expense the bot is talking about."""
    first = _first_name(name)
    return f"{first}, {body}" if first else body


_HEARD_MAX_CHARS = 300


def _heard_prefix(msg: InboundGroupMessage) -> str:
    """Echo what we transcribed, once, above whatever we're about to say.

    NOT decoration: Gemini can hallucinate words out of a noisy or near-silent
    audio, so together with the existing confirm-before-save gate this echo is the
    mechanism that lets a member catch a mis-hear before anything is written.
    Empty for typed messages, so every caller can add it unconditionally.
    """
    heard = " ".join((msg.transcript or "").split())  # collapse newlines to one line
    if not heard:
        return ""
    if len(heard) > _HEARD_MAX_CHARS:
        heard = heard[: _HEARD_MAX_CHARS - 1].rstrip() + "…"
    return f"🎙️ Escuché: «{heard}»\n\n"


def _reply(msg: InboundGroupMessage, body: str) -> str | None:
    """Answer the member who wrote ``msg``: name them AND quote their message,
    so several people loading expenses at once stay untangled — each prompt is
    visibly threaded to the person it's for. Returns the sent message id."""
    return gowa.send_text(
        msg.chat_id,
        _heard_prefix(msg) + _addressed(msg.sender_name, body),
        reply_to=msg.message_id,
    )


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

    # Voice notes: transcribe and use the transcript AS the message text, so every
    # route below (comandos, edición citando, sí/no, gasto nuevo) works unchanged.
    #
    # Placed here on purpose: AFTER get_link, because the bot's number may sit in
    # unrelated groups and we must neither pay for nor ship their audio to Google;
    # and BEFORE the routing and the pending lookup, so a failed transcription
    # returns without ever touching someone's in-flight proposal.
    paused = False
    if not text and msg.audio_path:
        gowa.send_chat_presence(msg.chat_id, "start")  # transcribing takes a beat
        paused = True
        text = _transcribe_voice_note(msg, group_id)  # replies on failure, returns ""
        if not text:
            return
        # _handle_pending and _maybe_roast both re-read msg.text, so a voice "sí"
        # only classifies if we write it back onto the message itself.
        msg.text = text
        msg.transcript = text
        gowa.react(msg.chat_id, msg.message_id, "👂")  # "te escuché", even if it's chitchat

    if not text:
        return

    # Small human-cadence pause before acting: show "typing…" then wait briefly.
    # Skipped for voice — the transcription already was the pause, and the typing
    # indicator is already up.
    if settings.reply_delay_seconds > 0 and not paused:
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
            # Not _reply(): command answers are group-wide (a balances table isn't
            # addressed to one person), so they stay un-named and un-quoted. But a
            # spoken "saldo" still needs the 🎙️ echo, or you can't tell what the
            # bot thought you said. No-op for typed commands.
            gowa.send_text(msg.chat_id, _heard_prefix(msg) + reply)
        return

    # Edit path: is this a reply that quotes a known expense? If so, treat the
    # message as a correction. (If the quoted message isn't an expense we know,
    # fall through to the normal create path below.)
    if msg.replied_to_id:
        target = web.get_expense_by_message(msg.replied_to_id)
        if target and target.get("groupId") == group_id:
            _process_edit(msg, group_id, link, sender_pid, target, text)
            return

    # Does THIS sender have an expense waiting on a description or a sí/no?
    # Keyed per member, so another person's pending proposal is never touched
    # and a "sí" only ever confirms the sender's own expense.
    pend = pending.get(msg.chat_id, msg.sender_jid)
    if pend:
        if _handle_pending(msg, pend):
            return
        # Not an answer we can use (e.g. a brand-new expense): drop this
        # sender's stale proposal and process the message fresh.
        pending.clear(msg.chat_id, msg.sender_jid)

    # Expense path requires a known sender.
    if not sender_pid:
        _reply(
            msg,
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
        participants,
        SUPPORTED_CURRENCIES,
        [c["name"] for c in categories],
        today,
    )
    if extraction is None:
        _reply(msg, "Uy, no pude procesar eso 😞. ¿Lo reescribís?")
        return

    if extraction.message_type == "chitchat":
        _maybe_roast(msg, participants, sender_pid)  # easter egg: roast the target's jokes
        return  # otherwise stay quiet on non-expense chatter
    if extraction.message_type == "command":
        _reply(msg, "¿Querías un comando? Probá `ayuda`.")
        return

    # --- it's an expense ---
    if extraction.confidence < settings.confidence_threshold or not extraction.amount:
        question = extraction.clarification_needed or "¿Cuánto fue y entre quiénes lo divido?"
        _reply(msg, f"🤔 {question}")
        return

    # Resolve payer (default: the sender), split, and FX (shared with edits).
    try:
        resolved = _resolve_expense_fields(extraction, participants, link, sender_pid)
    except _ResolveError as e:
        _reply(msg, e.reply)
        return

    # NOTE v1: we always split EVENLY among `paid_for` (we don't yet extract
    # per-person amounts/percentages). Named subsets still work via paid_for.
    # We build the payload now but DON'T save it — it waits for confirmation.
    # `externalId` is the original message id so the eventual create stays
    # idempotent and replying to that message later still edits this expense.
    expense_payload: dict = {
        "groupId": group_id,
        "title": (extraction.title or "").strip() or "Gasto",
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

    display = _build_display(extraction, resolved, participants)

    # Always require a description. If the message didn't give one, ask for it
    # first — we'll confirm once we have it.
    if not (extraction.title or "").strip():
        pending.set(msg.chat_id, msg.sender_jid, "description", expense_payload, display)
        _reply(msg, "📝 ¿De qué fue el gasto? Pasame una descripción cortita.")
        return

    # Ask for confirmation before saving anything.
    _present_confirmation(msg, expense_payload, display)


# --- voice notes -----------------------------------------------------------

# Rate-limit the "no puedo escuchar audios" notice: a group that lives on voice
# notes would otherwise get that line 40 times a day. Same idiom as pending.py.
_NOTICE_TTL_SECONDS = 3600
_notice_lock = threading.Lock()
_last_notice: dict[str, float] = {}


def _notice_once(chat_id: str) -> bool:
    """True at most once per hour per chat (so we warn, but don't nag)."""
    now = time.monotonic()
    with _notice_lock:
        last = _last_notice.get(chat_id, 0.0)
        if now - last < _NOTICE_TTL_SECONDS:
            return False
        _last_notice[chat_id] = now
        return True


def _voice_vocabulary(group_id: str) -> list[str]:
    """Participant names + their apodos, as an ASR vocabulary hint. The apodos are
    exactly the words speech recognition mangles, and they're load-bearing for
    ``match_participant``."""
    try:
        participants = web.get_participants(group_id)["participants"]
    except Exception:
        logger.warning("Could not load participants for the voice vocabulary", exc_info=True)
        return []
    vocab: list[str] = []
    for p in participants:
        vocab.append(p["name"])
        vocab.extend(a for a in (p.get("aliases") or []) if a)
    return vocab


def _transcribe_voice_note(msg: InboundGroupMessage, group_id: str) -> str:
    """Transcribe ``msg``'s audio, or reply with the reason and return "".

    Every failure path replies: a voice note is a deliberate act, and silence is
    indistinguishable from a dead bot. None of them carry the 🎙️ prefix, because
    ``msg.transcript`` is only set once we actually have a transcript.
    """
    if not settings.voice_notes_enabled or not settings.gemini_api_key:
        logger.warning(
            "Voice note dropped: %s",
            "VOICE_NOTES_ENABLED=false" if not settings.voice_notes_enabled
            else "GEMINI_API_KEY not set",
        )
        if _notice_once(msg.chat_id):
            _reply(msg, "🎙️ Todavía no puedo escuchar audios. Contámelo por texto y lo cargo igual.")
        return ""

    if not msg.audio_mime:
        _reply(msg, "🎙️ No pude leer ese formato de audio 😞. Contámelo por texto.")
        return ""

    # Duration is the better cost proxy when Gowa reports it (a forwarded 4-minute
    # song is not a voice note); bytes are the backstop when it doesn't.
    if msg.audio_seconds and msg.audio_seconds > settings.voice_max_seconds:
        logger.info("Voice note too long: %ss", msg.audio_seconds)
        _reply(
            msg,
            "🎙️ Ese audio es muy largo para mí 😅. Mandame uno más cortito "
            "o contámelo por texto.",
        )
        return ""

    started = time.monotonic()
    audio = gowa.fetch_media(msg.audio_path, settings.voice_max_bytes)
    if audio is None:
        # The webhook path may be stale (media lives in gowa's container layer);
        # ask Gowa to resolve/re-download it before giving up.
        ref = gowa.resolve_media_ref(msg.message_id, msg.chat_id)
        if ref:
            audio = gowa.fetch_media(ref[0], settings.voice_max_bytes)
    if not audio:
        _reply(
            msg,
            "🎙️ No pude descargar tu audio 😞. ¿Me lo mandás de nuevo o me lo contás por texto?",
        )
        return ""

    try:
        result = transcribe(audio, msg.audio_mime, _voice_vocabulary(group_id))
    except Exception:
        logger.exception("Gemini transcription failed (%s bytes, %s)", len(audio), msg.audio_mime)
        _reply(msg, "🎙️ No pude escuchar tu audio ahora ⚠️. Probá de nuevo en un rato.")
        return ""

    elapsed_ms = int((time.monotonic() - started) * 1000)
    # One line per voice note: in prod this is the entire debugging surface.
    logger.info(
        "Transcribed voice note: %s bytes, %s, ptt=%s, %sms, has_speech=%s, text=%r",
        len(audio), msg.audio_mime, msg.is_voice_note, elapsed_ms,
        result.has_speech if result else None,
        result.transcript if result else None,
    )

    if not result or not result.has_speech or not result.transcript:
        _reply(msg, "🎙️ Escuché el audio pero no entendí nada 😅. ¿Lo repetís un poco más claro?")
        return ""
    return result.transcript


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
        participants,
        SUPPORTED_CURRENCIES,
        [c["name"] for c in categories],
        today,
    )
    if extraction is None:
        _reply(msg, "Uy, no pude procesar la corrección 😞. ¿La reescribís?")
        return

    # Not actually a correction (a comment/emoji/thanks) — stay quiet.
    if extraction.message_type != "expense":
        return
    if extraction.confidence < settings.confidence_threshold or not extraction.amount:
        question = extraction.clarification_needed or "¿Qué querés corregir del gasto?"
        _reply(msg, f"🤔 {question}")
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
        _reply(msg, e.reply)
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
        _reply(msg, "No pude actualizar el gasto 😞. Probá de nuevo.")
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
    conf_id = _reply(msg, confirmation)
    # Link the new confirmation so a further reply keeps editing the same expense.
    if conf_id:
        web.record_message_ref(conf_id, expense_id)


def _maybe_roast(
    msg: InboundGroupMessage, participants: list[dict], sender_pid: str | None
) -> None:
    """Easter egg: if the configured target member cracked a joke, roast back."""
    if not settings.joke_roasts_enabled or not settings.joke_target_name:
        return

    # Sender's display name: their participant name, falling back to the pushname.
    sender_name = next(
        (p["name"] for p in participants if p["id"] == sender_pid), ""
    ) or (msg.sender_name or "")

    target = normalize_name(settings.joke_target_name)
    if target not in normalize_name(sender_name) and target not in normalize_name(
        msg.sender_name or ""
    ):
        return  # not the target member — stay quiet

    result = roast_joke(msg.text, sender_name)
    if result and result.is_joke and result.roast and result.roast.strip():
        _reply(msg, result.roast.strip())


def _resolve_category(name: str | None, categories: list[dict]) -> int:
    if not name:
        return 0
    target = normalize_name(name)
    for c in categories:
        if normalize_name(c["name"]) == target:
            return c["id"]
    return 0


# --- confirmation flow -----------------------------------------------------

# Short words we accept as "sí" / "no" when answering a proposal. Matched
# accent/case-insensitively against the (few) words of the reply.
_AFFIRM = {
    "si", "sii", "siii", "sip", "sipi", "sisi", "dale", "ok", "oka", "okok",
    "okey", "okay", "listo", "joya", "va", "vale", "obvio", "claro", "confirmo",
    "confirmado", "correcto", "exacto", "tal", "yes", "👍", "✅", "🆗", "🙆",
    # Spoken confirmations are wordier than typed ones.
    "confirmalo", "confirma", "guardalo", "guarda", "registralo", "mandale",
    "perfecto", "genial", "porfa", "porfavor", "favor", "eso",
}
_NEGATE = {
    "no", "nop", "nope", "nada", "cancelar", "cancela", "cancelado", "olvidalo",
    "olvidate", "borralo", "borra", "negativo", "nel", "❌", "🚫", "👎",
    "descartalo", "descarta",
}

# A typed "sí" is 1-2 words; a spoken one is a sentence ("sí, dale, confirmalo por
# favor" is five). Overshooting the cap used to mean _handle_pending returned
# False, pending.clear() threw the proposal away, and the reprocessed message came
# back as chitchat — i.e. the expense vanished in silence.
_MAX_REPLY_WORDS_TEXT = 4
_MAX_REPLY_WORDS_VOICE = 8


def _classify_reply(text: str, max_words: int = _MAX_REPLY_WORDS_TEXT) -> str | None:
    """Classify a short reply as 'yes' / 'no' / None (anything else).

    Only a *leading* negate word counts as a rejection, so a correction like
    "eran 8000 no 800" (where "no" means "not") falls through to be re-parsed
    instead of being read as a cancel.
    """
    words = [w.strip(".,!¡¿?;:") for w in normalize_name(text).split()]
    words = [w for w in words if w]
    if not words or len(words) > max_words:
        return None
    has_no = any(w in _NEGATE for w in words)
    has_yes = any(w in _AFFIRM for w in words)
    if words[0] in _NEGATE and not has_yes:
        return "no"
    if has_yes and not has_no:
        return "yes"
    return None


def _handle_pending(msg: InboundGroupMessage, pend: pending.Pending) -> bool:
    """React to a reply to a pending proposal. Returns True if it was consumed."""
    text = (msg.text or "").strip()
    max_words = _MAX_REPLY_WORDS_VOICE if msg.transcript else _MAX_REPLY_WORDS_TEXT
    kind = _classify_reply(text, max_words)

    if pend.stage == "description":
        if kind == "no":
            pending.clear(msg.chat_id, msg.sender_jid)
            _reply(msg, "❌ Listo, lo descarté.")
            return True
        # Use the message as the description, then ask to confirm.
        description = text[:80].strip() or "Gasto"
        pend.payload["title"] = description
        pend.display["title"] = description
        _present_confirmation(msg, pend.payload, pend.display)
        return True

    # stage == "confirmation"
    if kind == "yes":
        _confirm_and_save(msg, pend)  # clears the proposal only once it's saved
        return True
    if kind == "no":
        pending.clear(msg.chat_id, msg.sender_jid)
        _reply(msg, "❌ Listo, lo descarté.")
        return True
    return False  # not a sí/no — let the caller treat it as a new message


def _present_confirmation(msg: InboundGroupMessage, payload: dict, display: dict) -> None:
    pending.set(msg.chat_id, msg.sender_jid, "confirmation", payload, display)
    _reply(msg, _preview_text(display))


def _confirm_and_save(msg: InboundGroupMessage, pend: pending.Pending) -> None:
    """Create the confirmed expense, then only react to the confirming message."""
    try:
        web.create_expense(pend.payload)
    except Exception:
        logger.exception("create_expense failed")
        # Keep the proposal so a follow-up "sí" can retry after a transient error.
        _reply(msg, "No pude guardar el gasto 😞. Respondé *sí* para reintentar.")
        return
    pending.clear(msg.chat_id, msg.sender_jid)
    # Once confirmed the bot stays quiet and just reacts to the "sí".
    gowa.react(msg.chat_id, msg.message_id, "✅")


def _build_display(extraction, resolved: dict, participants: list[dict]) -> dict:
    """Snapshot the fields needed to render the confirmation preview later."""
    name_by_id = {p["id"]: p["name"] for p in participants}
    everyone = len(resolved["paid_for"]) == len(participants)
    among = "todos" if everyone else ", ".join(p["name"] for p in resolved["paid_for"])
    return {
        "title": (extraction.title or "").strip(),
        "usd_cents": resolved["usd_cents"],
        "currency": resolved["currency"],
        "amount": extraction.amount,
        "fx_label": resolved["conv"].label,
        "payer_name": name_by_id.get(resolved["paid_by_id"], "alguien"),
        "among": among,
    }


def _suspicious_line(payer_name: str) -> str | None:
    """Running gag: if the configured member is the payer, flag it as fishy."""
    target = normalize_name(settings.suspicious_payer_name)
    if target and target in normalize_name(payer_name):
        return f"👀 Ojo: lo pagó {payer_name}… me parece medio sospechoso 🤨"
    return None


def _preview_text(display: dict) -> str:
    """The 'voy a registrar… ¿confirmo?' message shown before saving."""
    title = display["title"] or "Gasto"
    line = f"📝 Voy a registrar: {format_money(display['usd_cents'])} — {title}"
    if display["currency"] != "USD":
        line += f" ({display['amount']:g} {display['currency']} @ {display['fx_label']})"
    line += f"\nPagó {display['payer_name']}, dividido entre {display['among']}."
    susp = _suspicious_line(display["payer_name"])
    if susp:
        line += f"\n{susp}"
    line += "\n\n¿Lo confirmo? Respondé *sí* o *no*."
    return line


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
    susp = _suspicious_line(payer)
    if susp:
        line += f"\n{susp}"
    return line
