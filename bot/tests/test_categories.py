"""Spanish category menu <-> spliit category ids."""

import categories as cats

# The categories the web API reports (spliit's English seed, ids 0-42).
API_CATEGORIES = [
    {"id": i, "name": name}
    for i, name in enumerate(
        [
            "General", "Payment", "Entertainment", "Games", "Movies", "Music",
            "Sports", "Food and Drink", "Dining Out", "Groceries", "Liquor",
            "Home", "Electronics", "Furniture", "Household Supplies",
            "Maintenance", "Mortgage", "Pets", "Rent", "Services", "Childcare",
            "Clothing", "Education", "Gifts", "Insurance", "Medical Expenses",
            "Taxes", "Transportation", "Bicycle", "Bus/Train", "Car",
            "Gas/Fuel", "Hotel", "Parking", "Plane", "Taxi", "Utilities",
            "Cleaning", "Electricity", "Heat/Gas", "Trash", "TV/Phone/Internet",
            "Water",
        ]
    )
]


def test_every_prompt_name_resolves_to_its_id():
    for name, expected_id in cats.PROMPT_CATEGORIES:
        assert cats.resolve(name, API_CATEGORIES) == expected_id, name


def test_every_english_db_name_resolves():
    # Legacy path: an edit of an old expense may hand back the English name.
    for c in API_CATEGORIES:
        assert cats.resolve(c["name"], API_CATEGORIES) == c["id"], c["name"]


def test_accents_and_case_insensitive():
    assert cats.resolve("SÚPER", API_CATEGORIES) == 9
    assert cats.resolve("esquí", API_CATEGORIES) == 6
    assert cats.resolve("TAXI / UBER", API_CATEGORIES) == 35


def test_synonyms():
    assert cats.resolve("airbnb", API_CATEGORIES) == 32
    assert cats.resolve("cabify", API_CATEGORIES) == 35
    assert cats.resolve("cerveza", API_CATEGORIES) == 10
    assert cats.resolve("transferencia", API_CATEGORIES) == 1


def test_unknown_falls_back_to_zero():
    assert cats.resolve("astrología", API_CATEGORIES) == 0
    assert cats.resolve("", API_CATEGORIES) == 0
    assert cats.resolve(None, API_CATEGORIES) == 0


def test_resolved_id_must_exist_in_api_list():
    # A deploy whose DB lacks the id gets General instead of an FK error.
    tiny_api = [{"id": 0, "name": "General"}, {"id": 8, "name": "Dining Out"}]
    assert cats.resolve("Taxi / Uber", tiny_api) == 0
    assert cats.resolve("Comida", tiny_api) == 8


def test_empty_api_list_skips_validation():
    # Tests/evals pass [] — alias resolution still works.
    assert cats.resolve("Comida", []) == 8


def test_labels_cover_all_seeded_ids():
    for c in API_CATEGORIES:
        label = cats.label(c["id"])
        assert label and label != ""


def test_label_plain_strips_emoji():
    assert cats.label(35) == "🚕 Taxi / Uber"
    assert cats.label(35, with_emoji=False) == "Taxi / Uber"


def test_label_fallback():
    assert cats.label(999) == cats.label(0)


def test_prompt_names_order_and_size():
    names = cats.prompt_names()
    assert names[0] == "Comida"
    assert "Otro" in names
    assert len(names) == len(cats.PROMPT_CATEGORIES)
