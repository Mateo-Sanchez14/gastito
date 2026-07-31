"""Edit-path behavior around non-even splits: preserve, guard, recompute."""

from types import SimpleNamespace

import pytest

import processor
from llm.schema import ExpenseExtraction

PARTICIPANTS = [
    {"id": "p-mauri", "name": "Mauri", "active": True},
    {"id": "p-erra", "name": "Errazquin", "active": True},
    {"id": "p-franco", "name": "Franco", "active": True},
]

OLD_RATE = 1 / 1565
NEW_RATE = 1 / 1600  # FX moved since the expense was saved

# A BY_AMOUNT expense as stored: 46800 ARS -> 2990 usd cents at the old rate.
TARGET = {
    "id": "e1",
    "groupId": "g1",
    "title": "Starbucks",
    "categoryId": 0,
    "amount": 2990,
    "originalAmount": 4680000,
    "originalCurrency": "ARS",
    "conversionRate": OLD_RATE,
    "paidById": "p-franco",
    "paidForIds": ["p-mauri", "p-erra", "p-franco"],
    "shares": [696, 1067, 1227],
    "splitMode": "BY_AMOUNT",
    "expenseDate": "2026-07-31",
}

MSG = SimpleNamespace(
    chat_id="chat@g.us",
    sender_jid="franco@s.whatsapp.net",
    sender_name="Franco",
    message_id="MSG1",
    transcript=None,
)


@pytest.fixture
def harness(monkeypatch):
    """Stub the network edges of _process_edit and capture what it does."""
    calls = SimpleNamespace(updated=None, replies=[])

    def convert(amount, currency, ars_source):
        rate = 1.0 if currency == "USD" else NEW_RATE
        return SimpleNamespace(usd=amount * rate, conversion_rate=rate, label="blue 1600")

    monkeypatch.setattr(processor, "convert", convert)
    monkeypatch.setattr(
        processor.web, "get_participants",
        lambda gid: {"participants": PARTICIPANTS, "categories": []},
    )
    monkeypatch.setattr(processor.web, "record_message_ref", lambda mid, eid: True)
    monkeypatch.setattr(
        processor.web, "update_expense",
        lambda eid, payload: calls.__setattr__("updated", (eid, payload)),
    )
    monkeypatch.setattr(
        processor.gowa, "send_text",
        lambda chat, body, reply_to=None: calls.replies.append(body) or "SENT1",
    )

    def set_extraction(extraction):
        monkeypatch.setattr(processor, "extract_edit", lambda *a, **k: extraction)

    calls.set_extraction = set_extraction
    return calls


def _edit_extraction(**overrides):
    data = {
        "message_type": "expense",
        "title": "Starbucks",
        "amount": 46800,
        "currency": "ARS",
        "paid_by_name": "Franco",
        "paid_for_names": ["Mauri", "Errazquin", "Franco"],
        "confidence": 0.95,
    }
    data.update(overrides)
    return ExpenseExtraction(**data)


def test_title_only_edit_preserves_split_and_rescales_for_fx(harness):
    harness.set_extraction(_edit_extraction(title="Starbucks aeropuerto"))
    processor._process_edit(MSG, "g1", {}, "p-franco", dict(TARGET), "era en el aeropuerto")

    assert harness.updated is not None
    _, payload = harness.updated
    assert payload["splitMode"] == "BY_AMOUNT"
    assert payload["title"] == "Starbucks aeropuerto"
    # 46800 ARS at the new rate -> a different total; shares rescaled to match.
    assert payload["amount"] == round(46800 * NEW_RATE * 100)
    assert sum(payload["shares"]) == payload["amount"]
    assert payload["paidForIds"] == TARGET["paidForIds"]


def test_total_change_on_by_amount_asks_for_the_split(harness):
    harness.set_extraction(_edit_extraction(amount=47000))
    processor._process_edit(MSG, "g1", {}, "p-franco", dict(TARGET), "era 47000")

    assert harness.updated is None
    assert any("montos exactos" in r for r in harness.replies)


def test_people_change_on_by_amount_asks_for_the_split(harness):
    harness.set_extraction(_edit_extraction(paid_for_names=["Mauri", "Franco"]))
    processor._process_edit(MSG, "g1", {}, "p-franco", dict(TARGET), "sin Errazquin")

    assert harness.updated is None
    assert any("división" in r for r in harness.replies)


def test_restated_split_recomputes(harness):
    harness.set_extraction(
        _edit_extraction(
            split_mode="BY_AMOUNT",
            split_parts=[
                {"name": "Mauri", "value": 20000},
                {"name": "Errazquin", "value": 16700},
                {"name": "Franco", "value": None},
            ],
        )
    )
    processor._process_edit(MSG, "g1", {}, "p-franco", dict(TARGET), "eran 20000 de Mauri")

    assert harness.updated is not None
    _, payload = harness.updated
    assert payload["splitMode"] == "BY_AMOUNT"
    assert sum(payload["shares"]) == payload["amount"]
    # Mauri's share reflects the restated 20000 ARS at the new rate.
    assert payload["shares"][0] == round(2000000 * NEW_RATE)


def test_evenly_expense_edits_stay_evenly(harness):
    target = dict(TARGET, splitMode="EVENLY", shares=[100, 100, 100])
    harness.set_extraction(_edit_extraction(title="Café"))
    processor._process_edit(MSG, "g1", {}, "p-franco", target, "era café")

    assert harness.updated is not None
    _, payload = harness.updated
    assert payload["splitMode"] == "EVENLY"
    assert "shares" not in payload
