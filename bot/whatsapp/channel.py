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

    if _seen_debug < 10:
        _seen_debug += 1
        logger.info("Gowa group payload (debug %s): %s", _seen_debug, msg)

    replied_to_id, quoted_body = _extract_reply(msg)
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
    )
