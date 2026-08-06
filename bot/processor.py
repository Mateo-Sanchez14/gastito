"""Core message processing: parse -> route command vs expense -> reply.

Mirrors american-store-chatbot's task handler shape, but for group expenses.
Runs in a background task (sync httpx is fine there).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date

import categories as cats
import guards
import pending
from commands import detect_command, handle_command
from config import settings
from fx.provider import ConversionError, convert
from llm.extractor import extract, extract_edit, roast_joke
from llm.transcriber import transcribe
from splits import SplitError, convert_shares, fill_remainder
from util import (
    active_participants,
    format_money,
    match_participant,
    normalize_name,
)
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
        cats.prompt_names(),
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
    # Deterministic guards over the extraction (each one earned by a real
    # mis-parse in the group; see guards.py). The thousands fix goes before the
    # confidence gate so a corrected amount doesn't read as "missing".
    extraction.amount = guards.fix_thousands_misread(extraction.amount, text)

    if extraction.confidence < settings.confidence_threshold or not extraction.amount:
        question = extraction.clarification_needed or "¿Cuánto fue y entre quiénes lo divido?"
        _reply(msg, f"🤔 {question}")
        return

    question = guards.decimal_currency_question(extraction.amount, extraction.currency, text)
    if question:
        _reply(msg, question)
        return

    sender_canonical = next(
        (p["name"] for p in participants if p["id"] == sender_pid), msg.sender_name or ""
    )
    extraction.paid_for_names = guards.ensure_sender_included(
        extraction.paid_for_names, text, sender_canonical, participants
    )

    # A lone entry in payers is just the payer said redundantly; only 2+ means
    # a genuinely shared payment, which becomes one expense per payer.
    if len(extraction.payers) == 1 and not extraction.paid_by_name:
        extraction.paid_by_name = extraction.payers[0].name
    if len(extraction.payers) > 1:
        _process_multi_payer(
            msg, extraction, participants, categories, link, sender_pid, group_id, today
        )
        return

    # Resolve payer (default: the sender), split, and FX (shared with edits).
    try:
        resolved = _resolve_expense_fields(extraction, participants, link, sender_pid)
    except _ResolveError as e:
        _reply(msg, e.reply)
        return

    # Split: EVENLY, or BY_AMOUNT/BY_PERCENTAGE when the message spelled out
    # per-person parts (resolved["shares"] is aligned with resolved["paid_for"];
    # both come from the same list — don't reorder one without the other).
    # We build the payload now but DON'T save it — it waits for confirmation.
    # `externalId` is the original message id so the eventual create stays
    # idempotent and replying to that message later still edits this expense.
    category_id = cats.resolve(extraction.category, categories)
    expense_payload: dict = {
        "groupId": group_id,
        "title": (extraction.title or "").strip() or "Gasto",
        "amount": resolved["usd_cents"],
        "category": category_id,
        "paidById": resolved["paid_by_id"],
        "paidForIds": [p["id"] for p in resolved["paid_for"]],
        "splitMode": resolved["split_mode"],
        "expenseDate": extraction.date or today,
        "source": "whatsapp",
        "externalId": msg.message_id,
        "createdByParticipantId": sender_pid,
    }
    if resolved["shares"] is not None:
        expense_payload["shares"] = resolved["shares"]
    expense_payload.update(_original_currency_fields(resolved))

    display = _build_display(extraction, resolved, participants, category_id)

    # Always require a description. If the message didn't give one, ask for it
    # first — we'll confirm once we have it.
    if not (extraction.title or "").strip():
        pending.set(msg.chat_id, msg.sender_jid, "description", [expense_payload], display)
        _reply(msg, "📝 ¿De qué fue el gasto? Pasame una descripción cortita.")
        return

    # Ask for confirmation before saving anything.
    _present_confirmation(msg, [expense_payload], display)


# --- multi-payer -----------------------------------------------------------


def _process_multi_payer(
    msg: InboundGroupMessage,
    extraction,
    participants: list[dict],
    categories: list[dict],
    link: dict,
    sender_pid: str | None,
    group_id: str,
    today: str,
) -> None:
    """"A pagó el 30% y B el 70%": propose one expense per payer.

    Each payer's contribution goes through the normal single-expense pipeline
    (split, FX, preview), and the whole set is confirmed or discarded as one
    unit. The first expense keeps the bare message id as ``externalId`` (so
    reply-to-edit and dedupe keep working); the rest get a ``#N`` suffix —
    they're still idempotent on retries but can only be corrected from the web.
    """
    # An exact-amount split can't be copied onto each sub-expense (the parts
    # sum to the FULL total, not to one payer's slice) — don't guess.
    if extraction.split_mode == "BY_AMOUNT" and extraction.split_parts:
        _reply(
            msg,
            "✋ No puedo con varios pagadores Y montos exactos por persona a la "
            "vez. Cargalo como gastos separados, uno por pagador.",
        )
        return

    payer_people: list[dict] = []
    seen_ids: set[str] = set()
    for part in extraction.payers:
        p = match_participant(part.name, participants)
        if not p:
            _reply(msg, f"No reconozco a *{part.name}*. ¿Quiénes pagaron?")
            return
        if p["id"] in seen_ids:
            _reply(
                msg,
                f"*{p['name']}* aparece dos veces como pagador 🤔. ¿Me lo pasás de nuevo?",
            )
            return
        seen_ids.add(p["id"])
        payer_people.append(p)

    # What each payer put in, in original-currency cents. Same philosophy as
    # splits: the LLM transcribed the literal numbers, the math happens here.
    total_cents = round(extraction.amount * 100)
    values = [
        round(part.value * 100) if part.value is not None else None
        for part in extraction.payers
    ]
    try:
        if extraction.payer_mode == "BY_PERCENTAGE":
            bps = fill_remainder(10000, values)
            contributions = [round(total_cents * bp / 10000) for bp in bps]
            # Per-payer rounding can leave the sum a hair off; the largest
            # contribution absorbs it (same rule as convert_shares).
            diff = total_cents - sum(contributions)
            if diff:
                contributions[contributions.index(max(contributions))] += diff
        else:  # BY_AMOUNT
            contributions = fill_remainder(total_cents, values)
    except SplitError as e:
        if e.reason in ("sum_exceeds_total", "sum_mismatch"):
            explicit = sum(v for v in values if v is not None) / 100
            if extraction.payer_mode == "BY_PERCENTAGE":
                _reply(
                    msg,
                    f"Lo pagado suma {explicit:g}% pero tiene que sumar 100% 🤔. "
                    "¿Me pasás de nuevo cuánto puso cada uno?",
                )
            else:
                currency = (extraction.currency or "CLP").upper()
                _reply(
                    msg,
                    f"Lo que pusieron suma {explicit:g} pero el total es "
                    f"{extraction.amount:g} {currency} 🤔. ¿Me pasás de nuevo "
                    "cuánto puso cada uno?",
                )
        else:
            _reply(msg, "Con esos números a un pagador le queda $0 🤔. ¿Cuánto puso cada uno?")
        return
    if any(c <= 0 for c in contributions):
        _reply(msg, "Con esos números a un pagador le queda $0 🤔. ¿Cuánto puso cada uno?")
        return

    category_id = cats.resolve(extraction.category, categories)
    payloads: list[dict] = []
    displays: list[dict] = []
    for i, (payer, cents) in enumerate(zip(payer_people, contributions)):
        sub = extraction.model_copy(
            update={"amount": cents / 100, "paid_by_name": payer["name"], "payers": []}
        )
        try:
            resolved = _resolve_expense_fields(sub, participants, link, sender_pid)
        except _ResolveError as e:
            _reply(msg, e.reply)
            return
        payload: dict = {
            "groupId": group_id,
            "title": (extraction.title or "").strip() or "Gasto",
            "amount": resolved["usd_cents"],
            "category": category_id,
            "paidById": resolved["paid_by_id"],
            "paidForIds": [p["id"] for p in resolved["paid_for"]],
            "splitMode": resolved["split_mode"],
            "expenseDate": extraction.date or today,
            "source": "whatsapp",
            "externalId": msg.message_id if i == 0 else f"{msg.message_id}#{i + 1}",
            "createdByParticipantId": sender_pid,
        }
        if resolved["shares"] is not None:
            payload["shares"] = resolved["shares"]
        payload.update(_original_currency_fields(resolved))
        payloads.append(payload)
        displays.append(_build_display(sub, resolved, participants, category_id))

    display = {"multi": displays, "title": (extraction.title or "").strip()}

    if not (extraction.title or "").strip():
        pending.set(msg.chat_id, msg.sender_jid, "description", payloads, display)
        _reply(msg, "📝 ¿De qué fue el gasto? Pasame una descripción cortita.")
        return

    _present_confirmation(msg, payloads, display)


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
        logger.exception("Transcription crashed (%s bytes, %s)", len(audio), msg.audio_mime)
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

    if result is None:
        # Every provider failed — not the same as "heard it, no speech".
        _reply(msg, "🎙️ No pude escuchar tu audio ahora ⚠️. Probá de nuevo en un rato.")
        return ""
    if not result.has_speech or not result.transcript:
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

    # Non-even splits: the people AND their slices come from split_parts, the
    # single source of truth (paid_for_names is ignored on this branch — two
    # lists the LLM must keep aligned is one list too many).
    split_mode = extraction.split_mode
    split_parts = extraction.split_parts if split_mode in ("BY_AMOUNT", "BY_PERCENTAGE") else []

    # Who it's split among (empty = everyone who's currently on the trip).
    if split_parts:
        paid_for = []
        seen_ids: set[str] = set()
        for part in split_parts:
            p = match_participant(part.name, participants)
            if not p:
                raise _ResolveError(f"No reconozco a *{part.name}* en el grupo.")
            if p["id"] in seen_ids:
                raise _ResolveError(
                    f"*{p['name']}* aparece dos veces en la división 🤔. ¿Me la pasás de nuevo?"
                )
            seen_ids.add(p["id"])
            paid_for.append(p)
    elif extraction.paid_for_names:
        # Names are matched against the FULL roster, not just the active subset:
        # someone who already left (or hasn't arrived) can still have fronted
        # money or owe a share, and naming them explicitly must keep working.
        # Duplicates (e.g. an alias plus the canonical name, seen in production
        # with "…y el resto para mí") merge silently — unlike split_parts, an
        # EVENLY duplicate carries no number that could get lost.
        paid_for = []
        dedupe_ids: set[str] = set()
        for name in extraction.paid_for_names:
            p = match_participant(name, participants)
            if not p:
                raise _ResolveError(f"No reconozco a *{name}* en el grupo.")
            if p["id"] in dedupe_ids:
                continue
            dedupe_ids.add(p["id"])
            paid_for.append(p)
    else:
        paid_for = active_participants(participants)
        if not paid_for:
            # Better to stop than to silently split among the whole group when
            # the sender believes only a few are around. Recoverable: /entra.
            raise _ResolveError(
                "No hay nadie marcado como presente 🤔. Usá `/entra <nombres>` "
                "o decime entre quiénes lo divido."
            )

    # Currency conversion to USD (group currency).
    # CLP is the group's default: if the LLM couldn't tell, assume chilean pesos.
    currency = (extraction.currency or "CLP").upper()
    try:
        conv = convert(extraction.amount, currency, link.get("fxArsSource", "blue"))
    except (ConversionError, Exception):
        logger.exception("FX conversion failed for %s", currency)
        raise _ResolveError(
            f"No pude convertir {currency} a USD ahora ⚠️. Probá de nuevo en un rato."
        )

    resolved = {
        "paid_by_id": paid_by_id,
        "paid_for": paid_for,
        "conv": conv,
        "currency": currency,
        "original_cents": round(extraction.amount * 100),
        "usd_cents": round(conv.usd * 100),
        # EVENLY unless split_parts below says otherwise.
        "split_mode": "EVENLY",
        "shares": None,  # group cents (BY_AMOUNT) or basis points (BY_PERCENTAGE)
        "shares_orig_cents": None,  # original-currency cents, BY_AMOUNT only
        "share_is_remainder": None,  # which shares the bot derived (not transcribed)
    }

    if split_parts:
        # The LLM only transcribes; every derived number (the remainder, the FX
        # per share, the rounding) is computed here so a bad list becomes a
        # question instead of a silently wrong expense.
        values = [
            round(part.value * 100) if part.value is not None else None
            for part in split_parts
        ]
        try:
            if split_mode == "BY_AMOUNT":
                orig = fill_remainder(resolved["original_cents"], values)
                shares = convert_shares(orig, resolved["usd_cents"], conv.conversion_rate)
                resolved["shares_orig_cents"] = orig
            else:  # BY_PERCENTAGE: basis points of 10000, no FX involved
                shares = fill_remainder(10000, values)
        except SplitError as e:
            raise _ResolveError(_split_error_reply(e, extraction, split_mode, values))
        resolved["split_mode"] = split_mode
        resolved["shares"] = shares
        resolved["share_is_remainder"] = [v is None for v in values]

    return resolved


def _split_error_reply(err: SplitError, extraction, split_mode: str, values: list) -> str:
    """The clarification for a split the numbers can't honor."""
    if err.reason in ("sum_exceeds_total", "sum_mismatch"):
        explicit = sum(v for v in values if v is not None) / 100
        if split_mode == "BY_PERCENTAGE":
            return (
                f"Los porcentajes suman {explicit:g}% pero tienen que sumar 100% 🤔. "
                "¿Me pasás la división de nuevo?"
            )
        currency = (extraction.currency or "CLP").upper()
        return (
            f"Las partes suman {explicit:g} pero el total es {extraction.amount:g} "
            f"{currency} 🤔. ¿Me pasás la división de nuevo?"
        )
    # zero_remainder / zero_share / rounding_wiped_share / empty
    return "Con esa división a alguien le queda $0 🤔. ¿Cómo lo divido?"


def _original_currency_fields(resolved: dict) -> dict:
    """Original-amount fields to merge into a payload when currency != USD."""
    if resolved["currency"] == "USD":
        return {}
    return {
        "originalAmount": resolved["original_cents"],
        "originalCurrency": resolved["currency"],
        "conversionRate": resolved["conv"].conversion_rate,
    }


def _current_split_desc(target: dict, name_by_id: dict, currency: str) -> str | None:
    """Describe a non-EVENLY split per person for the edit prompt (informative
    only — the prompt forbids copying these numbers back). BY_AMOUNT shares are
    stored in group cents; shown in the original currency via the stored
    conversionRate, rounded (the exact figures the user once said are gone)."""
    mode = target.get("splitMode") or "EVENLY"
    shares = target.get("shares") or []
    ids = target.get("paidForIds", [])
    if mode == "EVENLY" or len(shares) != len(ids):
        return None
    parts = []
    for pid, share in zip(ids, shares):
        name = name_by_id.get(pid, "?")
        if mode == "BY_PERCENTAGE":
            parts.append(f"{name}: {share / 100:g}%")
        elif mode == "BY_AMOUNT":
            rate = target.get("conversionRate")
            if rate and currency != "USD":
                parts.append(f"{name}: ~{(share / 100) / rate:.0f} {currency}")
            else:
                parts.append(f"{name}: {format_money(share)}")
        else:  # BY_SHARES
            parts.append(f"{name}: {share / 100:g} partes")
    return ", ".join(parts)


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
        # Spanish label, so "categoría actual" speaks the same language as the
        # "categorías disponibles" menu (the DB stores spliit's English names).
        "category": cats.label(target.get("categoryId") or 0, with_emoji=False),
        "date": target.get("expenseDate"),
        "split_mode": target.get("splitMode") or "EVENLY",
        "split_desc": _current_split_desc(target, name_by_id, cur_currency),
    }

    extraction = extract_edit(
        current,
        text,
        msg.sender_name,
        participants,
        SUPPORTED_CURRENCIES,
        cats.prompt_names(),
        today,
    )
    if extraction is None:
        _reply(msg, "Uy, no pude procesar la corrección 😞. ¿La reescribís?")
        return

    # Not actually a correction (a comment/emoji/thanks) — stay quiet.
    if extraction.message_type != "expense":
        return
    extraction.amount = guards.fix_thousands_misread(extraction.amount, text)
    if len(extraction.payers) > 1:
        _reply(
            msg,
            "✋ Cada gasto tiene un solo pagador. Para repartir lo pagado entre "
            "varios, cargalo como gastos separados.",
        )
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

    # The correction didn't restate the split but the expense has a non-even
    # one: preserve it (saving the hardcoded EVENLY here used to silently
    # destroy a BY_AMOUNT division). Preserving only works if the people and —
    # for exact amounts — the total still match; otherwise ask.
    target_mode = target.get("splitMode") or "EVENLY"
    if resolved["split_mode"] == "EVENLY" and target_mode != "EVENLY":
        target_ids = target.get("paidForIds", [])
        target_shares = target.get("shares") or []
        if (
            {p["id"] for p in resolved["paid_for"]} != set(target_ids)
            or len(target_shares) != len(target_ids)
        ):
            _reply(
                msg,
                "Ese gasto tiene una división especial y el cambio toca entre "
                "quiénes va ✋. Pasame la división completa de nuevo (montos o "
                "porcentajes por persona).",
            )
            return
        if target_mode == "BY_AMOUNT":
            # Compare in the ORIGINAL currency: the group-currency total is
            # re-converted at today's rate, so it drifts even when the user
            # didn't touch the amount (e.g. a title-only correction).
            old_orig = target.get("originalAmount") or target.get("amount")
            old_currency = target.get("originalCurrency") or "USD"
            if resolved["currency"] != old_currency or resolved["original_cents"] != old_orig:
                _reply(
                    msg,
                    "Ese gasto tiene división por montos exactos y cambiaste el "
                    "total ✋. Pasame la división de nuevo (ej: «10900 por Mauri, "
                    "16700 por Errazquin y el resto para mí»).",
                )
                return
            # FX moved since the expense was saved: rescale the stored shares
            # proportionally so they still sum exactly to the fresh total.
            old_total = target.get("amount")
            if old_total and resolved["usd_cents"] != old_total:
                try:
                    target_shares = convert_shares(
                        target_shares, resolved["usd_cents"], resolved["usd_cents"] / old_total
                    )
                except SplitError:
                    _reply(msg, "No pude reacomodar la división por montos ✋. Pasámela de nuevo.")
                    return
        by_id = {p["id"]: p for p in participants}
        resolved["paid_for"] = [by_id[i] for i in target_ids if i in by_id]
        resolved["split_mode"] = target_mode
        resolved["shares"] = target_shares

    category_id = cats.resolve(extraction.category, categories)
    edit_payload: dict = {
        "groupId": group_id,
        "title": extraction.title or target.get("title") or "Gasto",
        "amount": resolved["usd_cents"],
        "category": category_id,
        "paidById": resolved["paid_by_id"],
        "paidForIds": [p["id"] for p in resolved["paid_for"]],
        "splitMode": resolved["split_mode"],
        "expenseDate": extraction.date or target.get("expenseDate") or today,
        "createdByParticipantId": sender_pid,
    }
    if resolved["shares"] is not None:
        edit_payload["shares"] = resolved["shares"]
    edit_payload.update(_original_currency_fields(resolved))

    try:
        web.update_expense(expense_id, edit_payload)
    except Exception:
        logger.exception("update_expense failed")
        _reply(msg, "No pude actualizar el gasto 😞. Probá de nuevo.")
        return

    confirmation = _confirmation(extraction, resolved, participants, category_id, edited=True)
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
        # Use the message as the description, then ask to confirm. A multi-payer
        # proposal shares one description across all its sub-expenses.
        description = text[:80].strip() or "Gasto"
        for payload in pend.payloads:
            payload["title"] = description
        pend.display["title"] = description
        for sub in pend.display.get("multi") or []:
            sub["title"] = description
        _present_confirmation(msg, pend.payloads, pend.display)
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


def _present_confirmation(
    msg: InboundGroupMessage, payloads: list[dict], display: dict
) -> None:
    pending.set(msg.chat_id, msg.sender_jid, "confirmation", payloads, display)
    _reply(msg, _preview_text(display))


def _confirm_and_save(msg: InboundGroupMessage, pend: pending.Pending) -> None:
    """Create the confirmed expense(s), then only react to the confirming message.

    A partial failure keeps the proposal so a follow-up "sí" retries the whole
    list — the web endpoint is idempotent on (source, externalId), so the
    already-saved ones come back as no-ops instead of duplicates.
    """
    try:
        for payload in pend.payloads:
            web.create_expense(payload)
    except Exception:
        logger.exception("create_expense failed")
        # Keep the proposal so a follow-up "sí" can retry after a transient error.
        _reply(msg, "No pude guardar el gasto 😞. Respondé *sí* para reintentar.")
        return
    pending.clear(msg.chat_id, msg.sender_jid)
    # Once confirmed the bot stays quiet and just reacts to the "sí".
    gowa.react(msg.chat_id, msg.message_id, "✅")


def _among_label(paid_for: list[dict], participants: list[dict]) -> str:
    """How the split reads in the preview and the confirmation.

    Telling "todos" apart from "the whole group including whoever left" is not
    cosmetic: the preview is the last chance to catch a wrong split before it's
    saved, so it has to be obvious whether an absent member is in or out.
    """
    ids = {p["id"] for p in paid_for}
    if ids == {p["id"] for p in active_participants(participants)}:
        return "todos"
    if ids == {p["id"] for p in participants}:
        return "todos (incluidos los que no están)"
    return ", ".join(p["name"] for p in paid_for)


def _build_breakdown(resolved: dict) -> list[dict] | None:
    """Per-person lines for a non-even split, or None when EVENLY.

    ``group_cents`` for percentages mirrors how spliit computes them
    (amount * bp / 10000), with the last person absorbing the rounding — it's
    display only, the saved shares are the basis points.
    """
    if resolved.get("split_mode") in (None, "EVENLY") or not resolved.get("shares"):
        return None
    shares = resolved["shares"]
    is_remainder = resolved["share_is_remainder"] or [False] * len(shares)
    breakdown = []
    if resolved["split_mode"] == "BY_AMOUNT":
        # A preserved split (edit path) has no original-currency figures — the
        # stored shares are group cents only; render those alone.
        origs = resolved.get("shares_orig_cents") or [None] * len(shares)
        for p, orig, group, rem in zip(resolved["paid_for"], origs, shares, is_remainder):
            breakdown.append(
                {"name": p["name"], "orig_cents": orig, "group_cents": group,
                 "percent_bp": None, "is_remainder": rem}
            )
    else:  # BY_PERCENTAGE
        remaining = resolved["usd_cents"]
        for i, (p, bp, rem) in enumerate(
            zip(resolved["paid_for"], shares, is_remainder)
        ):
            group = remaining if i == len(shares) - 1 else round(
                resolved["usd_cents"] * bp / 10000
            )
            remaining -= group
            breakdown.append(
                {"name": p["name"], "orig_cents": None, "group_cents": group,
                 "percent_bp": bp, "is_remainder": rem}
            )
    return breakdown


def _breakdown_lines(breakdown: list[dict], currency: str) -> str:
    """The bullet list of the split. Original currency (or %) is what the user
    has in their head — it goes first, so a hallucinated number jumps out; the
    group-currency figure follows in parentheses. "— el resto" marks the values
    the bot derived, as opposed to what the LLM transcribed."""
    lines = []
    for b in breakdown:
        if b["percent_bp"] is not None:
            label = f"{b['percent_bp'] / 100:g}% ({format_money(b['group_cents'])})"
        elif b["orig_cents"] is not None and currency != "USD":
            label = (
                f"{b['orig_cents'] / 100:g} {currency} "
                f"({format_money(b['group_cents'])})"
            )
        else:
            label = format_money(b["group_cents"])
        rest = " — el resto" if b["is_remainder"] else ""
        lines.append(f"  • {b['name']}: {label}{rest}")
    return "\n".join(lines)


def _build_display(
    extraction, resolved: dict, participants: list[dict], category_id: int = 0
) -> dict:
    """Snapshot the fields needed to render the confirmation preview later."""
    name_by_id = {p["id"]: p["name"] for p in participants}
    among = _among_label(resolved["paid_for"], participants)
    return {
        "title": (extraction.title or "").strip(),
        "usd_cents": resolved["usd_cents"],
        "currency": resolved["currency"],
        "amount": extraction.amount,
        "fx_label": resolved["conv"].label,
        "payer_name": name_by_id.get(resolved["paid_by_id"], "alguien"),
        "among": among,
        "breakdown": _build_breakdown(resolved),
        "category_label": cats.label(category_id),
        "split_warning": (
            "⚠️ Todavía no sé dividir por partes; lo propongo en partes iguales."
            if extraction.split_mode == "BY_SHARES"
            else None
        ),
    }


def _suspicious_line(payer_name: str) -> str | None:
    """Running gag: if the configured member is the payer, flag it as fishy."""
    target = normalize_name(settings.suspicious_payer_name)
    if target and target in normalize_name(payer_name):
        return f"👀 Ojo: lo pagó {payer_name}… me parece medio sospechoso 🤨"
    return None


def _expense_lines(display: dict) -> str:
    """One expense's body: amount, payer/split, category. Shared between the
    single preview and each entry of a multi-payer preview."""
    title = display["title"] or "Gasto"
    line = f"{format_money(display['usd_cents'])} — {title}"
    if display["currency"] != "USD":
        line += f" ({display['amount']:g} {display['currency']} @ {display['fx_label']})"
    breakdown = display.get("breakdown")
    if breakdown:
        line += f"\nPagó {display['payer_name']}, dividido así:"
        line += f"\n{_breakdown_lines(breakdown, display['currency'])}"
    else:
        line += f"\nPagó {display['payer_name']}, dividido entre {display['among']}."
    if display.get("category_label"):
        line += f"\n🏷️ {display['category_label']}"
    if display.get("split_warning"):
        line += f"\n{display['split_warning']}"
    return line


def _preview_text(display: dict) -> str:
    """The 'voy a registrar… ¿confirmo?' message shown before saving."""
    subs = display.get("multi")
    if subs:
        parts = [f"📝 Son varios pagadores, voy a registrar {len(subs)} gastos:"]
        for i, sub in enumerate(subs, 1):
            parts.append(f"\n{i}. {_expense_lines(sub)}")
        for payer in dict.fromkeys(s["payer_name"] for s in subs):  # ordered unique
            susp = _suspicious_line(payer)
            if susp:
                parts.append(f"\n{susp}")
        parts.append("\n\n¿Los confirmo? Respondé *sí* o *no*.")
        return "\n".join(parts)

    line = f"📝 Voy a registrar: {_expense_lines(display)}"
    susp = _suspicious_line(display["payer_name"])
    if susp:
        line += f"\n{susp}"
    line += "\n\n¿Lo confirmo? Respondé *sí* o *no*."
    return line


def _confirmation(extraction, resolved: dict, participants, category_id: int = 0, edited=False) -> str:
    name_by_id = {p["id"]: p["name"] for p in participants}
    payer = name_by_id.get(resolved["paid_by_id"], "alguien")
    title = extraction.title or "Gasto"

    line = f"{'✏️ Actualizado:' if edited else '✅'} {format_money(resolved['usd_cents'])} — {title}"
    if resolved["currency"] != "USD":
        line += f" ({extraction.amount:g} {resolved['currency']} @ {resolved['conv'].label})"
    breakdown = _build_breakdown(resolved)
    if breakdown:
        line += f"\nPagó {payer}, dividido así:"
        line += f"\n{_breakdown_lines(breakdown, resolved['currency'])}"
    else:
        among = _among_label(resolved["paid_for"], participants)
        line += f"\nPagó {payer}, dividido entre {among}."
    line += f"\n🏷️ {cats.label(category_id)}"
    susp = _suspicious_line(payer)
    if susp:
        line += f"\n{susp}"
    return line
