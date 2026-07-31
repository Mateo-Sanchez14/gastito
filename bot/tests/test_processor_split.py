from types import SimpleNamespace

import pytest

import processor
from llm.schema import ExpenseExtraction
from processor import _ResolveError, _resolve_expense_fields

PARTICIPANTS = [
    {"id": "p-mauri", "name": "Mauri", "active": True},
    {"id": "p-erra", "name": "Errazquin", "active": True},
    {"id": "p-franco", "name": "Franco", "active": True},
]
LINK = {"fxArsSource": "blue"}
RATE = 1 / 1565  # USD per ARS, ~blue


@pytest.fixture(autouse=True)
def fake_convert(monkeypatch):
    def convert(amount, currency, ars_source):
        rate = 1.0 if currency == "USD" else RATE
        return SimpleNamespace(
            usd=amount * rate, conversion_rate=rate, label=f"{ars_source} 1565"
        )

    monkeypatch.setattr(processor, "convert", convert)


def _extract(**overrides):
    data = {
        "message_type": "expense",
        "title": "Starbucks",
        "amount": 46800,
        "currency": "ARS",
        "confidence": 0.95,
    }
    data.update(overrides)
    return ExpenseExtraction(**data)


class TestByAmount:
    def test_starbucks_case(self):
        extraction = _extract(
            split_mode="BY_AMOUNT",
            split_parts=[
                {"name": "Mauri", "value": 10900},
                {"name": "Errazquin", "value": 16700},
                {"name": "Franco", "value": None},
            ],
        )
        resolved = _resolve_expense_fields(extraction, PARTICIPANTS, LINK, "p-franco")
        assert resolved["split_mode"] == "BY_AMOUNT"
        assert [p["id"] for p in resolved["paid_for"]] == ["p-mauri", "p-erra", "p-franco"]
        assert resolved["shares_orig_cents"] == [1090000, 1670000, 1920000]
        assert sum(resolved["shares"]) == resolved["usd_cents"]
        assert resolved["share_is_remainder"] == [False, False, True]

    def test_unknown_name(self):
        extraction = _extract(
            split_mode="BY_AMOUNT",
            split_parts=[{"name": "Rodolfo", "value": 10000}, {"name": "Franco", "value": None}],
        )
        with pytest.raises(_ResolveError, match="Rodolfo"):
            _resolve_expense_fields(extraction, PARTICIPANTS, LINK, "p-franco")

    def test_sum_exceeding_total_asks(self):
        extraction = _extract(
            split_mode="BY_AMOUNT",
            split_parts=[
                {"name": "Mauri", "value": 40000},
                {"name": "Errazquin", "value": 16700},
                {"name": "Franco", "value": None},
            ],
        )
        with pytest.raises(_ResolveError, match="suman"):
            _resolve_expense_fields(extraction, PARTICIPANTS, LINK, "p-franco")

    def test_duplicate_person_asks(self):
        extraction = _extract(
            split_mode="BY_AMOUNT",
            split_parts=[
                {"name": "Mauri", "value": 10000},
                {"name": "Mauri", "value": 20000},
                {"name": "Franco", "value": None},
            ],
        )
        with pytest.raises(_ResolveError, match="dos veces"):
            _resolve_expense_fields(extraction, PARTICIPANTS, LINK, "p-franco")

    def test_empty_parts_falls_back_to_evenly(self):
        extraction = _extract(split_mode="BY_AMOUNT", split_parts=[])
        resolved = _resolve_expense_fields(extraction, PARTICIPANTS, LINK, "p-franco")
        assert resolved["split_mode"] == "EVENLY"
        assert resolved["shares"] is None


class TestByPercentage:
    def test_percent_with_remainder(self):
        extraction = _extract(
            split_mode="BY_PERCENTAGE",
            split_parts=[
                {"name": "Franco", "value": 70},
                {"name": "Mauri", "value": None},
                {"name": "Errazquin", "value": None},
            ],
        )
        resolved = _resolve_expense_fields(extraction, PARTICIPANTS, LINK, "p-franco")
        assert resolved["split_mode"] == "BY_PERCENTAGE"
        assert resolved["shares"] == [7000, 1500, 1500]
        assert resolved["shares_orig_cents"] is None

    def test_percent_not_100_asks(self):
        extraction = _extract(
            split_mode="BY_PERCENTAGE",
            split_parts=[
                {"name": "Franco", "value": 70},
                {"name": "Mauri", "value": 20},
            ],
        )
        with pytest.raises(_ResolveError, match="100%"):
            _resolve_expense_fields(extraction, PARTICIPANTS, LINK, "p-franco")


class TestEvenlyRegression:
    def test_plain_evenly_unchanged(self):
        extraction = _extract()
        resolved = _resolve_expense_fields(extraction, PARTICIPANTS, LINK, "p-franco")
        assert resolved["split_mode"] == "EVENLY"
        assert resolved["shares"] is None
        assert len(resolved["paid_for"]) == 3

    def test_by_shares_falls_back_to_evenly(self):
        extraction = _extract(
            split_mode="BY_SHARES",
            split_parts=[{"name": "Mauri", "value": 2}, {"name": "Franco", "value": 1}],
        )
        resolved = _resolve_expense_fields(extraction, PARTICIPANTS, LINK, "p-franco")
        assert resolved["split_mode"] == "EVENLY"
        assert resolved["shares"] is None
