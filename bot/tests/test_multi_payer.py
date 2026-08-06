"""Multi-payer messages become one expense per payer, confirmed as a unit."""

from types import SimpleNamespace

import pytest

import pending
import processor
from llm.schema import ExpenseExtraction

PARTICIPANTS = [
    {"id": "p-qv2", "name": "Qv2", "active": True, "aliases": ["Quevedo"]},
    {"id": "p-fer", "name": "Fer", "active": True, "aliases": ["Tuco"]},
    {"id": "p-benja", "name": "Benja", "active": True},
]
LINK = {"fxArsSource": "blue"}

MSG = SimpleNamespace(
    chat_id="chat@g.us",
    sender_jid="benja@s.whatsapp.net",
    sender_name="Benja",
    message_id="MSG1",
    transcript=None,
)


@pytest.fixture(autouse=True)
def clean_pending():
    pending.clear(MSG.chat_id, MSG.sender_jid)
    yield
    pending.clear(MSG.chat_id, MSG.sender_jid)


@pytest.fixture
def harness(monkeypatch):
    calls = SimpleNamespace(replies=[], created=[])

    def convert(amount, currency, ars_source):
        rate = 1.0 if currency == "USD" else 1 / 1000
        return SimpleNamespace(usd=amount * rate, conversion_rate=rate, label="x")

    monkeypatch.setattr(processor, "convert", convert)
    monkeypatch.setattr(
        processor.gowa, "send_text",
        lambda chat, body, reply_to=None: calls.replies.append(body) or "SENT1",
    )
    monkeypatch.setattr(
        processor.web, "create_expense", lambda payload: calls.created.append(payload)
    )
    monkeypatch.setattr(processor.gowa, "react", lambda *a, **k: None)
    return calls


def _extraction(**overrides):
    data = {
        "message_type": "expense",
        "title": "Chocolates",
        "amount": 500,
        "currency": "USD",
        "payers": [{"name": "Quevedo", "value": 30}, {"name": "Tuco", "value": 70}],
        "payer_mode": "BY_PERCENTAGE",
        "confidence": 0.9,
    }
    data.update(overrides)
    return ExpenseExtraction(**data)


def _run(extraction):
    processor._process_multi_payer(
        MSG, extraction, PARTICIPANTS, [], LINK, "p-benja", "g1", "2026-08-06"
    )


class TestProposal:
    def test_percentages_become_one_expense_per_payer(self, harness):
        _run(_extraction())
        pend = pending.get(MSG.chat_id, MSG.sender_jid)
        assert pend is not None and pend.stage == "confirmation"
        assert len(pend.payloads) == 2
        assert [p["paidById"] for p in pend.payloads] == ["p-qv2", "p-fer"]
        assert [p["amount"] for p in pend.payloads] == [15000, 35000]  # usd cents
        # First keeps the bare message id (reply-to-edit); the rest get suffixes.
        assert pend.payloads[0]["externalId"] == "MSG1"
        assert pend.payloads[1]["externalId"] == "MSG1#2"
        assert "2 gastos" in harness.replies[0]

    def test_amounts_with_remainder(self, harness):
        _run(
            _extraction(
                payer_mode="BY_AMOUNT",
                payers=[{"name": "Qv2", "value": 400}, {"name": "Fer", "value": None}],
                amount=2000,
            )
        )
        pend = pending.get(MSG.chat_id, MSG.sender_jid)
        assert [p["amount"] for p in pend.payloads] == [40000, 160000]

    def test_percentages_not_100_ask(self, harness):
        _run(_extraction(payers=[{"name": "Qv2", "value": 30}, {"name": "Fer", "value": 30}]))
        assert pending.get(MSG.chat_id, MSG.sender_jid) is None
        assert any("100%" in r for r in harness.replies)

    def test_unknown_payer_asks(self, harness):
        _run(_extraction(payers=[{"name": "Rodolfo", "value": 30}, {"name": "Fer", "value": 70}]))
        assert pending.get(MSG.chat_id, MSG.sender_jid) is None
        assert any("Rodolfo" in r for r in harness.replies)

    def test_duplicate_payer_asks(self, harness):
        # "Quevedo" and "Qv2" are the same participant.
        _run(_extraction(payers=[{"name": "Quevedo", "value": 30}, {"name": "Qv2", "value": 70}]))
        assert pending.get(MSG.chat_id, MSG.sender_jid) is None
        assert any("dos veces" in r for r in harness.replies)

    def test_by_amount_split_combo_rejected(self, harness):
        _run(
            _extraction(
                split_mode="BY_AMOUNT",
                split_parts=[{"name": "Benja", "value": 100}, {"name": "Fer", "value": None}],
            )
        )
        assert pending.get(MSG.chat_id, MSG.sender_jid) is None
        assert any("montos exactos" in r for r in harness.replies)

    def test_missing_title_asks_description_for_all(self, harness):
        _run(_extraction(title=None))
        pend = pending.get(MSG.chat_id, MSG.sender_jid)
        assert pend.stage == "description"
        # The description reply lands on every sub-expense.
        desc_msg = SimpleNamespace(**{**MSG.__dict__, "text": "chocolates del duty"})
        assert processor._handle_pending(desc_msg, pend)
        pend = pending.get(MSG.chat_id, MSG.sender_jid)
        assert pend.stage == "confirmation"
        assert all(p["title"] == "chocolates del duty" for p in pend.payloads)


class TestConfirm:
    def test_yes_saves_all(self, harness):
        _run(_extraction())
        pend = pending.get(MSG.chat_id, MSG.sender_jid)
        yes = SimpleNamespace(**{**MSG.__dict__, "text": "sí"})
        assert processor._handle_pending(yes, pend)
        assert len(harness.created) == 2
        assert pending.get(MSG.chat_id, MSG.sender_jid) is None

    def test_no_discards_all(self, harness):
        _run(_extraction())
        pend = pending.get(MSG.chat_id, MSG.sender_jid)
        no = SimpleNamespace(**{**MSG.__dict__, "text": "no"})
        assert processor._handle_pending(no, pend)
        assert harness.created == []
        assert pending.get(MSG.chat_id, MSG.sender_jid) is None

    def test_partial_failure_keeps_proposal_for_retry(self, harness, monkeypatch):
        _run(_extraction())
        pend = pending.get(MSG.chat_id, MSG.sender_jid)

        state = {"calls": 0}

        def flaky_create(payload):
            state["calls"] += 1
            if state["calls"] == 2:
                raise RuntimeError("boom")
            harness.created.append(payload)

        monkeypatch.setattr(processor.web, "create_expense", flaky_create)
        yes = SimpleNamespace(**{**MSG.__dict__, "text": "sí"})
        processor._handle_pending(yes, pend)
        # First saved, second failed -> proposal survives for a retry "sí".
        assert len(harness.created) == 1
        assert pending.get(MSG.chat_id, MSG.sender_jid) is not None
        assert any("reintentar" in r for r in harness.replies)
