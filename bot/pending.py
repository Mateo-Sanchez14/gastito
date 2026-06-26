"""In-memory store for expenses awaiting confirmation.

gastito now asks before saving: it proposes the parsed expense and only writes it
once someone says "sí" (and only after a description exists). The half-built
expense lives here, keyed by chat (group) JID, between the user's message and
their reply.

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
_pending: dict[str, Pending] = {}


def get(chat_id: str) -> Pending | None:
    """Return the live proposal for a chat, or None (also drops expired ones)."""
    with _lock:
        p = _pending.get(chat_id)
        if p and (time.time() - p.created_at) > PENDING_TTL_SECONDS:
            _pending.pop(chat_id, None)
            return None
        return p


def set(chat_id: str, stage: str, payload: dict, display: dict) -> None:
    with _lock:
        _pending[chat_id] = Pending(stage, payload, display, time.time())


def clear(chat_id: str) -> None:
    with _lock:
        _pending.pop(chat_id, None)
