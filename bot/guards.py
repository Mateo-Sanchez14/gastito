"""Deterministic guards over the LLM extraction, built from real group failures.

The prompt already states these rules, but llama-class models drift on exactly
these patterns (each guard's docstring quotes the production message that
motivated it). Guards are pure functions so they can be unit-tested; the
processor wires them in right after ``extract()``.
"""

from __future__ import annotations

import re

from util import match_participant, normalize_name

# "8.042" / "1.234.567": dot + exactly-3-digit groups, not part of a larger
# number ("1.500,50" still matches its "1.500" head — the comma tail is decimals).
_THOUSANDS_RE = re.compile(r"(?<![\d,.])\d{1,3}(?:\.\d{3})+(?!\d)")

# Any hint that a CLP/ARS amount really was meant in pesos (accent-stripped text).
_PESOS_MARKER_RE = re.compile(
    r"pesos?|clp|ars\b|argentin|chilen|urugua|luca|mango|palo|gamba|\$"
)

_INCLUDE_ME_RE = re.compile(r"\b[ye] yo\b|\bconmigo\b|incluido yo|incluyendome|incluime")
_EXCLUDE_ME_RE = re.compile(r"menos yo|sin mi\b|excepto yo|salvo yo")


def fix_thousands_misread(amount: float | None, text: str) -> float | None:
    """Undo the LLM reading a thousands dot as a decimal point.

    Production: "Pague Uber hacia AEP 8.042" came back as amount=8.042 (≈$0.01);
    the user had to retype "8042" — three times in a row. A dot followed by
    exactly 3 digits is never a decimal in this domain, so when the extracted
    amount equals that misreading, snap it to the thousands value. Only the
    exact misreading is touched: an amount that's already right passes through.
    """
    if amount is None:
        return amount
    for token in _THOUSANDS_RE.findall(text or ""):
        # The misreading keeps only the last dot as a decimal point.
        misread = float(token.replace(".", "", token.count(".") - 1))
        correct = float(int(token.replace(".", "")))
        if amount != correct and abs(amount - misread) < 1e-9:
            return correct
    return amount


def decimal_currency_question(
    amount: float | None, currency: str | None, text: str
) -> str | None:
    """Ask which currency a decimal amount is, instead of trusting CLP/ARS.

    Production: "Uber la pica del esqui - 27.26 - dividido entre todos" was
    booked as CLP 27.26 (≈$0.03) when it was USD. CLP and ARS don't use cents
    in practice, so a fractional amount with no pesos marker in the message is
    almost surely USD — but flipping the currency silently is the same class of
    bug, so we ask.
    """
    if amount is None:
        return None
    if (currency or "CLP").upper() not in ("CLP", "ARS"):
        return None
    if amount == int(amount):
        return None
    if _PESOS_MARKER_RE.search(normalize_name(text or "")):
        return None
    return (
        f"🤔 ¿{amount:g} en qué moneda? Un monto con decimales me suena a dólares. "
        f"Decime «{amount:g} usd» o «{amount:g} pesos» y lo cargo."
    )


def ensure_sender_included(
    paid_for_names: list[str],
    text: str,
    sender_name: str,
    participants: list[dict],
) -> list[str]:
    """Append the writer to an enumerated split when the message says "…y yo".

    Production: "divide entre errazquin, tigre, quevedo y yo" came back without
    the writer. Only fires on an explicit enumeration (an empty list already
    means "everyone") with a first-person inclusion and no exclusion, and only
    when no listed name already resolves to the sender (an alias counts — the
    comparison is by participant id, not by string).
    """
    if not paid_for_names or not sender_name:
        return paid_for_names
    norm = normalize_name(text or "")
    if not _INCLUDE_ME_RE.search(norm) or _EXCLUDE_ME_RE.search(norm):
        return paid_for_names
    sender = match_participant(sender_name, participants)
    if sender is None:
        return paid_for_names
    for name in paid_for_names:
        p = match_participant(name, participants)
        if p and p["id"] == sender["id"]:
            return paid_for_names
    return [*paid_for_names, sender_name]
