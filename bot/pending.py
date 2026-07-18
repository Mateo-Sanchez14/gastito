"""In-memory store for expenses awaiting confirmation.

gastito now asks before saving: it proposes the parsed expense and only writes it
once someone says "sí" (and only after a description exists). The half-built
expense lives here between the user's message and their reply.

Keyed by (chat, sender) so each member's proposal is an INDEPENDENT thread: when
several people load expenses at the same time in the same group, one person's
pending confirmation no longer clobbers another's, and a "sí" only ever confirms
the sender's own proposal. (Before, a single slot per group meant a second
person's message would silently drop the first's, and a "sí" could confirm the
wrong expense.)

Single-process FastAPI background tasks share this module-level dict; a lock
keeps concurrent webhook deliveries from racing on it. Entries expire so a stale
"sí" long after the fact can't resurrect an abandoned expense. State is lost on
restart by design — a pending confirmation is cheap to redo.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

# How long a proposal stays answerable (seconds).
PENDING_TTL_SECONDS = 600


@dataclass
class Pending:
    stage: str  # "description" (waiting for one) | "confirmation" (waiting for sí/no)
    payload: dict  # the expense payload to POST verbatim once confirmed
    display: dict  # fields needed to (re)render the confirmation preview
    created_at: float


_lock = threading.Lock()
_pending: dict[tuple[str, str], Pending] = {}


def _key(chat_id: str, sender_jid: str) -> tuple[str, str]:
    return (chat_id, sender_jid)


def get(chat_id: str, sender_jid: str) -> Pending | None:
    """Return the live proposal for this sender in this chat, or None (also
    drops expired ones)."""
    key = _key(chat_id, sender_jid)
    with _lock:
        p = _pending.get(key)
        if p and (time.time() - p.created_at) > PENDING_TTL_SECONDS:
            _pending.pop(key, None)
            return None
        return p


def set(chat_id: str, sender_jid: str, stage: str, payload: dict, display: dict) -> None:
    with _lock:
        _pending[_key(chat_id, sender_jid)] = Pending(stage, payload, display, time.time())


def clear(chat_id: str, sender_jid: str) -> None:
    with _lock:
        _pending.pop(_key(chat_id, sender_jid), None)
