"""Small helpers: accent-insensitive name matching and money formatting."""

from __future__ import annotations

import unicodedata


def normalize_name(name: str) -> str:
    """Lowercase + strip accents for case/accent-insensitive matching."""
    if not name:
        return ""
    nfkd = unicodedata.normalize("NFKD", name)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return no_accents.strip().lower()


def match_participant(name: str, participants: list[dict]) -> dict | None:
    """Resolve a free-text name to a participant dict ({id, name}) or None.

    Tries exact (normalized) match first, then a unique prefix/substring match.
    """
    if not name:
        return None
    target = normalize_name(name)
    norm = [(normalize_name(p["name"]), p) for p in participants]

    for n, p in norm:
        if n == target:
            return p

    starts = [p for n, p in norm if n.startswith(target) or target.startswith(n)]
    if len(starts) == 1:
        return starts[0]

    contains = [p for n, p in norm if target in n or n in target]
    if len(contains) == 1:
        return contains[0]
    return None


def format_money(cents: int, symbol: str = "$") -> str:
    """Format integer cents as a money string, e.g. 1250 -> '$12.50'."""
    return f"{symbol}{cents / 100:,.2f}"
