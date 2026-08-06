"""Spanish-facing layer over spliit's built-in categories.

The DB keeps spliit's English category names (the web app translates them
client-side by using the English name as an i18n key, so renaming rows would
break the web). The bot instead exposes a curated Spanish menu to the LLM and
maps whatever comes back — Spanish menu name, synonym, or a legacy English DB
name — onto the seeded category ids.
"""

from __future__ import annotations

import logging

from util import normalize_name

logger = logging.getLogger(__name__)

# The menu the LLM sees: short, Spanish, trip-relevant. Order matters a little
# (models lean on early items), so the everyday ones go first.
PROMPT_CATEGORIES: list[tuple[str, int]] = [
    ("Comida", 8),  # Dining Out
    ("Supermercado", 9),  # Groceries
    ("Birras / Alcohol", 10),  # Liquor
    ("Taxi / Uber", 35),  # Taxi
    ("Transporte", 27),  # Transportation
    ("Nafta", 31),  # Gas/Fuel
    ("Vuelos", 34),  # Plane
    ("Alojamiento", 32),  # Hotel
    ("Entretenimiento", 2),  # Entertainment
    ("Deporte / Ski", 6),  # Sports
    ("Ropa", 21),  # Clothing
    ("Salud / Farmacia", 25),  # Medical Expenses
    ("Regalos", 23),  # Gifts
    ("Servicios", 19),  # Services
    ("Pago / Deuda", 1),  # Payment
    ("Otro", 0),  # General
]

# Display labels for every seeded id (0-42), Spanish + emoji. Used in the
# confirmation preview and to describe the current category to the edit prompt.
LABELS: dict[int, str] = {
    0: "🧾 Otro",
    1: "💸 Pago / Deuda",
    2: "🎉 Entretenimiento",
    3: "🎮 Juegos",
    4: "🎬 Cine",
    5: "🎵 Música",
    6: "🎿 Deporte / Ski",
    7: "🍽️ Comida y bebida",
    8: "🍽️ Comida",
    9: "🛒 Supermercado",
    10: "🍻 Birras / Alcohol",
    11: "🏠 Hogar",
    12: "📺 Electrónica",
    13: "🛋️ Muebles",
    14: "🧻 Artículos del hogar",
    15: "🔧 Mantenimiento",
    16: "🏦 Hipoteca",
    17: "🐾 Mascotas",
    18: "🏠 Alquiler",
    19: "🔧 Servicios",
    20: "🧒 Niñera",
    21: "👕 Ropa",
    22: "📚 Educación",
    23: "🎁 Regalos",
    24: "🛡️ Seguro",
    25: "💊 Salud / Farmacia",
    26: "🧾 Impuestos",
    27: "🚌 Transporte",
    28: "🚲 Bici",
    29: "🚆 Tren / Micro",
    30: "🚗 Auto",
    31: "⛽ Nafta",
    32: "🏨 Alojamiento",
    33: "🅿️ Estacionamiento",
    34: "✈️ Vuelos",
    35: "🚕 Taxi / Uber",
    36: "💡 Servicios básicos",
    37: "🧹 Limpieza",
    38: "💡 Luz",
    39: "🔥 Gas",
    40: "🗑️ Basura",
    41: "📶 Internet / Cel",
    42: "💧 Agua",
}

# Everything we accept back from the LLM (or from an edit of an old expense),
# already normalized: the Spanish menu, the English DB names, and synonyms the
# group actually uses. Many-to-one on purpose.
_RAW_ALIASES: dict[str, int] = {
    # Comida (8)
    "comida": 8, "restaurante": 8, "resto": 8, "restoran": 8, "delivery": 8,
    "almuerzo": 8, "cena": 8, "desayuno": 8, "merienda": 8, "picada": 8,
    "cafe": 8, "dining out": 8, "food and drink": 7, "comida y bebida": 7,
    # Supermercado (9)
    "supermercado": 9, "super": 9, "almacen": 9, "verduleria": 9, "tienda": 9,
    "groceries": 9,
    # Birras / Alcohol (10)
    "birras / alcohol": 10, "birras": 10, "birra": 10, "alcohol": 10,
    "cerveza": 10, "cervezas": 10, "vino": 10, "trago": 10, "tragos": 10,
    "liquor": 10, "previa": 10,
    # Taxi / Uber (35)
    "taxi / uber": 35, "taxi": 35, "uber": 35, "cabify": 35, "didi": 35,
    "remis": 35,
    # Transporte (27) y granulares
    "transporte": 27, "transportation": 27, "bondi": 27, "micro": 29,
    "tren": 29, "bus": 29, "bus/train": 29, "colectivo": 29,
    "auto": 30, "car": 30, "bici": 28, "bicycle": 28,
    # Nafta (31) / estacionamiento (33)
    "nafta": 31, "combustible": 31, "bencina": 31, "gas/fuel": 31,
    "estacionamiento": 33, "parking": 33, "peaje": 33, "peajes": 33,
    # Vuelos (34)
    "vuelos": 34, "vuelo": 34, "avion": 34, "plane": 34, "aereo": 34,
    # Alojamiento (32)
    "alojamiento": 32, "hotel": 32, "airbnb": 32, "hostel": 32, "refugio": 32,
    "cabana": 32, "hospedaje": 32,
    # Entretenimiento (2) y granulares
    "entretenimiento": 2, "entertainment": 2, "entradas": 2, "joda": 2,
    "fiesta": 2, "boliche": 2, "salida": 2,
    "cine": 4, "movies": 4, "peliculas": 4,
    "juegos": 3, "games": 3,
    "musica": 5, "music": 5,
    # Deporte / Ski (6)
    "deporte / ski": 6, "deporte": 6, "deportes": 6, "ski": 6, "esqui": 6,
    "snowboard": 6, "pases": 6, "sports": 6,
    # Ropa (21)
    "ropa": 21, "clothing": 21, "zapatillas": 21,
    # Salud / Farmacia (25)
    "salud / farmacia": 25, "salud": 25, "farmacia": 25, "remedios": 25,
    "medico": 25, "medical expenses": 25,
    # Regalos (23)
    "regalos": 23, "regalo": 23, "gifts": 23,
    # Servicios (19) y utilities
    "servicios": 19, "services": 19, "lavanderia": 19,
    "limpieza": 37, "cleaning": 37,
    "utilities": 36, "servicios basicos": 36,
    "electricity": 38, "luz": 38, "heat/gas": 39, "trash": 40, "basura": 40,
    "tv/phone/internet": 41, "internet": 41, "water": 42, "agua": 42,
    # Hogar residuales (nombres EN de la DB)
    "home": 11, "hogar": 11, "electronics": 12, "electronica": 12,
    "furniture": 13, "muebles": 13, "household supplies": 14,
    "maintenance": 15, "mantenimiento": 15, "mortgage": 16,
    "pets": 17, "mascotas": 17, "rent": 18, "alquiler": 18,
    # Vida residuales
    "childcare": 20, "education": 22, "educacion": 22, "insurance": 24,
    "seguro": 24, "taxes": 26, "impuestos": 26,
    # Pago / Deuda (1)
    "pago / deuda": 1, "pago": 1, "deuda": 1, "devolucion": 1,
    "transferencia": 1, "payment": 1, "reintegro": 1,
    # Otro (0)
    "otro": 0, "general": 0, "varios": 0, "uncategorized": 0,
}

ALIASES: dict[str, int] = {normalize_name(k): v for k, v in _RAW_ALIASES.items()}


def prompt_names() -> list[str]:
    """The Spanish category menu shown to the LLM."""
    return [name for name, _ in PROMPT_CATEGORIES]


def resolve(name: str | None, api_categories: list[dict]) -> int:
    """Map an LLM category string to a spliit category id (0 = General).

    Falls back through: alias table -> exact match against the API's own list
    (future-proofing for categories we haven't aliased). The resolved id must
    exist in ``api_categories`` — a deploy whose DB lacks it gets 0 instead of
    a foreign-key error at save time.
    """
    if not name:
        return 0
    known_ids = {c["id"] for c in api_categories}
    target = normalize_name(name)

    cat_id = ALIASES.get(target)
    if cat_id is None:
        for c in api_categories:
            if normalize_name(c["name"]) == target:
                cat_id = c["id"]
                break
    if cat_id is None:
        logger.info("Unmapped category from LLM: %r", name)
        return 0
    if known_ids and cat_id not in known_ids:
        return 0
    return cat_id


def label(category_id: int, with_emoji: bool = True) -> str:
    """Display label for a category id, e.g. "🚕 Taxi / Uber"."""
    text = LABELS.get(category_id, LABELS[0])
    if with_emoji:
        return text
    return text.split(" ", 1)[1] if " " in text else text
