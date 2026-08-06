"""Confirmation preview rendering: category line, single vs multi-payer."""

from types import SimpleNamespace

import processor
from llm.schema import ExpenseExtraction

PARTICIPANTS = [
    {"id": "p-benja", "name": "Benja", "active": True},
    {"id": "p-fer", "name": "Fer", "active": True},
]


def _resolved(paid_by="p-benja"):
    return {
        "paid_by_id": paid_by,
        "paid_for": PARTICIPANTS,
        "conv": SimpleNamespace(label="CLP 950", conversion_rate=1 / 950),
        "currency": "CLP",
        "original_cents": 1500000,
        "usd_cents": 1579,
        "split_mode": "EVENLY",
        "shares": None,
        "shares_orig_cents": None,
        "share_is_remainder": None,
    }


def _extraction(**overrides):
    data = {
        "message_type": "expense",
        "title": "Birras",
        "amount": 15000,
        "currency": "CLP",
        "confidence": 0.9,
    }
    data.update(overrides)
    return ExpenseExtraction(**data)


def test_preview_shows_category_line():
    display = processor._build_display(_extraction(), _resolved(), PARTICIPANTS, 10)
    text = processor._preview_text(display)
    assert "🏷️ 🍻 Birras / Alcohol" in text
    assert "¿Lo confirmo?" in text


def test_preview_defaults_to_otro():
    display = processor._build_display(_extraction(), _resolved(), PARTICIPANTS, 0)
    assert "🏷️ 🧾 Otro" in processor._preview_text(display)


def test_multi_preview_lists_each_expense():
    d1 = processor._build_display(_extraction(), _resolved("p-benja"), PARTICIPANTS, 10)
    d2 = processor._build_display(_extraction(), _resolved("p-fer"), PARTICIPANTS, 10)
    text = processor._preview_text({"multi": [d1, d2], "title": "Birras"})
    assert "2 gastos" in text
    assert "1. " in text and "2. " in text
    assert text.count("🍻") == 2
    assert "¿Los confirmo?" in text


def test_edit_confirmation_shows_category():
    text = processor._confirmation(
        _extraction(), _resolved(), PARTICIPANTS, category_id=35, edited=True
    )
    assert text.startswith("✏️ Actualizado:")
    assert "🏷️ 🚕 Taxi / Uber" in text
