from llm.schema import ExpenseExtraction, SplitPart


def _base(**overrides):
    data = {"message_type": "expense", "amount": 46800, "currency": "ARS"}
    data.update(overrides)
    return data


class TestSplitParts:
    def test_well_formed(self):
        e = ExpenseExtraction(
            **_base(
                split_mode="BY_AMOUNT",
                split_parts=[
                    {"name": "Mauri", "value": 10900},
                    {"name": "Errazquin", "value": 16700},
                    {"name": "Franco", "value": None},
                ],
            )
        )
        assert e.split_parts == [
            SplitPart(name="Mauri", value=10900),
            SplitPart(name="Errazquin", value=16700),
            SplitPart(name="Franco", value=None),
        ]

    def test_numeric_string_value_converted(self):
        e = ExpenseExtraction(
            **_base(split_parts=[{"name": "Mauri", "value": "10900,5"}])
        )
        assert e.split_parts[0].value == 10900.5

    def test_missing_field_defaults_empty(self):
        assert ExpenseExtraction(**_base()).split_parts == []

    def test_blank_and_none_default_empty(self):
        assert ExpenseExtraction(**_base(split_parts="")).split_parts == []
        assert ExpenseExtraction(**_base(split_parts=None)).split_parts == []

    def test_non_list_discarded(self):
        assert ExpenseExtraction(**_base(split_parts="Mauri 10900")).split_parts == []

    def test_entry_without_name_discards_all(self):
        e = ExpenseExtraction(
            **_base(split_parts=[{"name": "Mauri", "value": 1}, {"value": 2}])
        )
        assert e.split_parts == []

    def test_non_numeric_value_discards_all(self):
        e = ExpenseExtraction(
            **_base(
                split_parts=[
                    {"name": "Mauri", "value": 10900},
                    {"name": "Franco", "value": "el resto"},
                ]
            )
        )
        assert e.split_parts == []


class TestSplitModeRegression:
    def test_unknown_split_mode_falls_back_to_evenly(self):
        assert ExpenseExtraction(**_base(split_mode="")).split_mode == "EVENLY"
        assert ExpenseExtraction(**_base(split_mode="HALFSIES")).split_mode == "EVENLY"

    def test_valid_modes_preserved(self):
        assert (
            ExpenseExtraction(**_base(split_mode="BY_AMOUNT")).split_mode == "BY_AMOUNT"
        )
        assert (
            ExpenseExtraction(**_base(split_mode="BY_PERCENTAGE")).split_mode
            == "BY_PERCENTAGE"
        )
