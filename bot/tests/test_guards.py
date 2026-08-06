"""Deterministic post-extraction guards (each mirrors a real group mis-parse)."""

from guards import (
    decimal_currency_question,
    ensure_sender_included,
    fix_thousands_misread,
)

PARTICIPANTS = [
    {"id": "p-benja", "name": "Benja", "active": True, "aliases": ["Benjamin"]},
    {"id": "p-erra", "name": "Errazquin", "active": True},
    {"id": "p-pichi", "name": "Pichi", "active": True, "aliases": ["Tigre"]},
    {"id": "p-qv2", "name": "Qv2", "active": True, "aliases": ["Quevedo"]},
]


class TestFixThousandsMisread:
    def test_basic_misread_corrected(self):
        # Production: "8.042" read as eight-point-oh-four-two.
        assert fix_thousands_misread(8.042, "Pague Uber hacia AEP 8.042, entre todos") == 8042

    def test_multiple_groups(self):
        assert fix_thousands_misread(1234.567, "salió 1.234.567 la estadía") == 1234567

    def test_already_correct_untouched(self):
        assert fix_thousands_misread(8042, "Pague Uber hacia AEP 8.042") == 8042

    def test_two_decimals_not_touched(self):
        # "27.26" is a genuine decimal — no 3-digit group, no match.
        assert fix_thousands_misread(27.26, "Uber la pica - 27.26 - entre todos") == 27.26

    def test_thousands_with_comma_decimals(self):
        # "1.500,50": the dot is thousands even with a decimal comma after.
        assert fix_thousands_misread(1.5, "fueron 1.500,50 del súper") == 1500

    def test_no_matching_token_no_change(self):
        assert fix_thousands_misread(46800, "Starbucks 46800 ARS") == 46800

    def test_none_amount_passes_through(self):
        assert fix_thousands_misread(None, "gasté 8.042") is None

    def test_unrelated_amount_not_snapped(self):
        # The extracted amount isn't the misreading of the token — leave it be.
        assert fix_thousands_misread(9000, "gasté 8.042 y algo más") == 9000


class TestDecimalCurrencyQuestion:
    def test_fires_on_clp_with_decimals(self):
        q = decimal_currency_question(27.26, "CLP", "Uber la pica del esqui - 27.26")
        assert q and "27.26" in q

    def test_fires_when_currency_missing(self):
        # No currency from the LLM means the CLP default would kick in.
        assert decimal_currency_question(225.91, None, "225.91 Airbnb Santiago")

    def test_silent_with_pesos_marker(self):
        assert decimal_currency_question(18476.75, "ARS", "18476.75 pesos argentinos de uber") is None

    def test_silent_with_dollar_sign(self):
        assert decimal_currency_question(27.26, "CLP", "salió $27.26 el uber") is None

    def test_silent_on_usd(self):
        assert decimal_currency_question(27.26, "USD", "27.26 - entre todos") is None

    def test_silent_on_integer(self):
        assert decimal_currency_question(8042, "CLP", "Uber 8042") is None

    def test_silent_with_lucas_slang(self):
        assert decimal_currency_question(1500.5, "CLP", "una luca y media del kiosco") is None


class TestEnsureSenderIncluded:
    def test_y_yo_appends_sender(self):
        # Production: "divide entre errazquin, tigre, quevedo y yo" dropped the writer.
        names = ensure_sender_included(
            ["Errazquin", "Pichi", "Qv2"],
            "divide entre errazquin, tigre, quevedo y yo.",
            "Benja",
            PARTICIPANTS,
        )
        assert names == ["Errazquin", "Pichi", "Qv2", "Benja"]

    def test_conmigo_appends(self):
        names = ensure_sender_included(
            ["Pichi"], "dividilo entre Pichi y conmigo", "Benja", PARTICIPANTS
        )
        assert names == ["Pichi", "Benja"]

    def test_menos_yo_does_not_append(self):
        names = ensure_sender_included(
            ["Pichi", "Qv2"], "entre Pichi, Qv2 y yo no, menos yo", "Benja", PARTICIPANTS
        )
        assert names == ["Pichi", "Qv2"]

    def test_sender_already_listed_no_dup(self):
        names = ensure_sender_included(
            ["Pichi", "Benja"], "entre Pichi, Benja y yo", "Benja", PARTICIPANTS
        )
        assert names == ["Pichi", "Benja"]

    def test_sender_listed_by_alias_no_dup(self):
        # "Benjamin" is an alias of Benja: the sender is already there.
        names = ensure_sender_included(
            ["Pichi", "Benjamin"], "entre Pichi, Benjamin y yo", "Benja", PARTICIPANTS
        )
        assert names == ["Pichi", "Benjamin"]

    def test_empty_list_untouched(self):
        # "entre todos ... y yo" — empty already means everyone.
        assert ensure_sender_included([], "entre todos y yo", "Benja", PARTICIPANTS) == []

    def test_no_first_person_untouched(self):
        names = ensure_sender_included(
            ["Pichi", "Qv2"], "entre Pichi y Qv2", "Benja", PARTICIPANTS
        )
        assert names == ["Pichi", "Qv2"]
