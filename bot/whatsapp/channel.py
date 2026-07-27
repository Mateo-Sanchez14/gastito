"""Parse Gowa webhook payloads into normalized *group* messages.

Adapted from american-store-chatbot's GowaChannel.parse, with the group filter
INVERTED: gastito only acts on group chats (``...@g.us``) and ignores DMs.

CRITICAL: in a group, the chat id (the group) and the sender (a member) are
different. american-store-chatbot collapses both into one field; here we keep
``chat_id`` (the group, used as the reply target + link key) and ``sender_jid``
(the member, mapped to a spliit Participant) separate.

The exact field carrying the sender varies across Gowa versions, so we try a
list of candidates and log the raw payload for the first few messages so the
real shape can be confirmed live (see the note in the plan).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class InboundGroupMessage:
    chat_id: str  # the group JID ("...@g.us"); reply target + link key
    sender_jid: str  # the member who sent it; mapped to a Participant
    sender_name: str  # WhatsApp pushname
    message_id: str
    text: str
    timestamp: str
    raw: dict
    replied_to_id: str = ""  # id of the quoted message (a reply), if any
    quoted_body: str = ""  # text of the quoted message, if any

    # --- voice notes -------------------------------------------------------
    audio_path: str = ""  # media path INSIDE the gowa container; fetched over HTTP
    audio_mime: str = ""  # bare mime for Gemini inline_data; "" = unsupported format
    audio_seconds: int = 0  # duration if Gowa reported one, else 0 (unknown)
    is_voice_note: bool = False  # True when WhatsApp flagged it as PTT
    transcript: str = ""  # filled in by the processor; drives the 🎙️ echo


def normalize_jid(jid: str) -> str:
    """Normalize a sender JID to a stable key.

    WhatsApp encodes the device as ``user:device@server`` (e.g.
    ``54911...:12@s.whatsapp.net``); strip the device so the same person maps to
    one Participant regardless of which linked device sent the message.
    """
    if not jid:
        return ""
    return re.sub(r":\d+(?=@)", "", jid.strip())


def _extract_sender(msg: dict, chat_id: str) -> str:
    """Best-effort extraction of the sending member's JID within a group."""
    for key in ("sender_id", "sender", "participant", "author", "from"):
        value = msg.get(key)
        if value and value != chat_id and isinstance(value, str):
            return normalize_jid(value)
    # Fallback: no distinct sender field — use the group id so the message is at
    # least attributable to *something* (will require an explicit /soy mapping).
    return normalize_jid(chat_id)


def _extract_text(msg: dict) -> str:
    for key in ("body", "text", "message", "caption"):
        value = msg.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):  # some builds nest {"text": {"body": ...}}
            nested = value.get("body") or value.get("text")
            if isinstance(nested, str) and nested:
                return nested
    return ""


# Voice notes. With WHATSAPP_AUTO_DOWNLOAD_MEDIA=true (Gowa's default) an audio
# message carries a PATH inside the gowa container, not bytes:
#   {"audio": "statics/media/1752404905-b9393cd1-….ogg"}
_AUDIO_KEYS = ("audio", "ptt", "voice", "voice_note", "audio_path")
_PATH_KEYS = ("path", "file_path", "media_path", "url")

# Extension -> the mime we hand Gemini. Gemini's inline_data.mime_type must be a
# BARE type from its supported set: WhatsApp's real mime is "audio/ogg;
# codecs=opus" and passing that through risks a 400, so the codecs part gets
# stripped. Values of "" are formats we recognize but cannot send (no transcode:
# python:3.12-slim has no ffmpeg, and this shouldn't happen for real voice notes).
_AUDIO_EXT_MIME = {
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".aiff": "audio/aiff",
    ".amr": "",
    ".3gp": "",
}
_GEMINI_AUDIO_MIMES = {m for m in _AUDIO_EXT_MIME.values() if m}


def _audio_ext(path: str) -> str:
    tail = path.rsplit("/", 1)[-1]
    dot = tail.rfind(".")
    return tail[dot:].lower() if dot > 0 else ""


def _audio_mime(path: str, declared: str) -> str:
    """Bare, Gemini-acceptable mime for an audio file, or "" if unsupported.

    Extension wins over the payload's own mime: a WhatsApp PTT is always ogg/opus,
    and the declared value is the one carrying the ``; codecs=opus`` suffix.
    """
    ext = _audio_ext(path)
    if ext in _AUDIO_EXT_MIME:
        return _AUDIO_EXT_MIME[ext]
    bare = str(declared or "").split(";", 1)[0].strip().lower()
    return bare if bare in _GEMINI_AUDIO_MIMES else ""


def _extract_audio(msg: dict) -> tuple[str, str, int, bool]:
    """Best-effort (audio_path, bare_mime, duration_seconds, is_ptt).

    Returns ``("", "", 0, False)`` when the message carries no audio.
    """
    path = ""
    declared = ""
    for key in _AUDIO_KEYS:
        value = msg.get(key)
        if isinstance(value, str) and value:
            path = value
            break
        if isinstance(value, dict):  # some builds nest {"audio": {"path": ...}}
            for inner in _PATH_KEYS:
                nested = value.get(inner)
                if isinstance(nested, str) and nested:
                    path = nested
                    break
            declared = value.get("mimetype") or value.get("mime_type") or ""
            if path:
                break

    if not path:
        # Generic media field — only claim it if it actually looks like audio, or
        # we'd start feeding photos and PDFs to the transcriber.
        for key in ("media_path", "file_path"):
            value = msg.get(key)
            if isinstance(value, str) and value and _audio_ext(value) in _AUDIO_EXT_MIME:
                path = value
                break

    if not path:
        return "", "", 0, False

    declared = declared or msg.get("mimetype") or msg.get("mime_type") or ""
    media_type = str(msg.get("media_type") or "").lower()
    is_ptt = bool(msg.get("is_ptt") or msg.get("ptt")) or media_type == "ptt"

    duration = 0
    for key in ("duration", "seconds", "audio_duration"):
        value = msg.get(key)
        if isinstance(value, (int, float)) and value > 0:
            duration = int(value)
            break

    return path, _audio_mime(path, declared), duration, is_ptt


def _extract_reply(msg: dict) -> tuple[str, str]:
    """Best-effort (replied_to_id, quoted_body) for a quoted/reply message.

    Confirmed Gowa shape (2026-06-26): ``replied_to_id`` + ``quoted_body``. We
    also accept a couple of alternate spellings other builds use.
    """
    replied_to_id = ""
    for key in ("replied_to_id", "quoted_message_id", "reply_to_id", "stanza_id"):
        value = msg.get(key)
        if isinstance(value, str) and value:
            replied_to_id = value
            break

    quoted_body = ""
    for key in ("quoted_body", "quoted_message", "quoted_text"):
        value = msg.get(key)
        if isinstance(value, str) and value:
            quoted_body = value
            break
        if isinstance(value, dict):
            nested = value.get("body") or value.get("text")
            if isinstance(nested, str) and nested:
                quoted_body = nested
                break

    return replied_to_id, quoted_body


_seen_debug = 0


def parse_group_message(payload: dict) -> InboundGroupMessage | None:
    """Return a normalized group message, or None if it should be ignored."""
    global _seen_debug
    if payload.get("event") != "message":
        return None

    msg = payload.get("payload") or {}
    if msg.get("is_from_me"):
        return None  # our own outgoing message; never react to it

    chat_id = msg.get("chat_id") or msg.get("from") or ""
    if not isinstance(chat_id, str) or not chat_id.endswith("@g.us"):
        return None  # gastito only acts in groups

    # Raised from 10 for the voice-note rollout: the audio field names and the
    # statics/ layout come from Gowa's docs, not from observed traffic, so these
    # logs are the ground truth for _extract_audio (and tell us whether the
    # payload actually carries duration / is_ptt).
    if _seen_debug < 30:
        _seen_debug += 1
        logger.info("Gowa group payload (debug %s): %s", _seen_debug, msg)

    replied_to_id, quoted_body = _extract_reply(msg)
    audio_path, audio_mime, audio_seconds, is_ptt = _extract_audio(msg)
    return InboundGroupMessage(
        chat_id=chat_id,
        sender_jid=_extract_sender(msg, chat_id),
        sender_name=msg.get("from_name") or msg.get("pushname") or "",
        message_id=msg.get("id", ""),
        text=_extract_text(msg),
        timestamp=str(msg.get("timestamp", "")),
        raw=msg,
        replied_to_id=replied_to_id,
        quoted_body=quoted_body,
        audio_path=audio_path,
        audio_mime=audio_mime,
        audio_seconds=audio_seconds,
        is_voice_note=is_ptt,
    )
