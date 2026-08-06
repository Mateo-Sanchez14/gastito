"""Structured contract for the natural-language expense extractor."""

from __future__ import annotations

from typing import Literal, Optional, get_args

from pydantic import BaseModel, Field, field_validator

SplitMode = Literal["EVENLY", "BY_SHARES", "BY_PERCENTAGE", "BY_AMOUNT"]
MessageType = Literal["expense", "command", "chitchat"]
PayerMode = Literal["BY_AMOUNT", "BY_PERCENTAGE"]


def _clean_parts_list(value):
    """Shared tolerance for lists of {name, value}: discard the WHOLE list on
    any malformed entry instead of failing the extraction. Never salvage a
    partial list — a missing person would be saved silently wrong, while []
    falls back to a path where the user can catch it."""
    if value is None or value == "":
        return []
    if not isinstance(value, list):
        return []
    cleaned = []
    for entry in value:
        if isinstance(entry, SplitPart):
            cleaned.append(entry)
            continue
        if not isinstance(entry, dict):
            return []
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            return []
        amount = entry.get("value")
        if isinstance(amount, str):
            try:
                amount = float(amount.replace(",", "."))
            except ValueError:
                return []
        if amount is not None and not isinstance(amount, (int, float)):
            return []
        cleaned.append({"name": name, "value": amount})
    return cleaned


class SplitPart(BaseModel):
    """One person's slice of a non-even split, as stated in the message.

    ``value`` is a major-unit amount in the message's currency (BY_AMOUNT) or a
    percentage 0-100 (BY_PERCENTAGE). ``None`` means "el resto": that person
    shares whatever remains, which the processor computes — the LLM must never
    do arithmetic (it has hallucinated derived amounts in production).
    """

    name: str
    value: Optional[float] = None


class ExpenseExtraction(BaseModel):
    message_type: MessageType
    title: Optional[str] = None
    amount: Optional[float] = Field(
        default=None, description="Amount in the currency's major units (e.g. 15000 ARS, 12.50 USD)"
    )
    currency: Optional[str] = Field(
        default=None, description="ISO 4217 code, e.g. CLP (default), USD, ARS"
    )
    paid_by_name: Optional[str] = None
    split_mode: SplitMode = "EVENLY"
    paid_for_names: list[str] = Field(default_factory=list, description="Empty means everyone")
    split_parts: list[SplitPart] = Field(
        default_factory=list, description="Per-person values for BY_AMOUNT/BY_PERCENTAGE"
    )
    payers: list[SplitPart] = Field(
        default_factory=list,
        description="Every payer when MORE than one person paid; empty otherwise",
    )
    payer_mode: PayerMode = "BY_AMOUNT"
    category: Optional[str] = None
    date: Optional[str] = Field(default=None, description="ISO date YYYY-MM-DD; default today")
    confidence: float = 0.0
    clarification_needed: Optional[str] = None

    @field_validator("split_mode", mode="before")
    @classmethod
    def _tolerate_blank_split_mode(cls, value):
        """Accept a missing/blank/unknown split_mode instead of failing the model.

        On a chitchat message there is nothing to split, so gpt-4o-mini answers
        ``"split_mode": ""`` — which isn't a valid Literal, so the whole extraction
        raised, the provider was marked failed, and (once the Gemini fallback was
        also down) the bot answered "Uy, no pude procesar eso 😞" to plain group
        chatter instead of staying quiet. The processor only implements EVENLY
        anyway, so anything we don't recognize means "no opinion".
        """
        if value in get_args(SplitMode):
            return value
        return "EVENLY"

    @field_validator("split_parts", "payers", mode="before")
    @classmethod
    def _tolerate_malformed_parts(cls, value):
        """Discard the whole list on any malformed entry instead of failing.

        Same spirit as ``_tolerate_blank_split_mode``: a noisy model must not
        sink the entire extraction. But never salvage a partial list — a split
        (or payer set) missing one person would be saved silently wrong, while
        ``[]`` falls back to a preview where the user can catch it and say "no".
        """
        return _clean_parts_list(value)

    @field_validator("payer_mode", mode="before")
    @classmethod
    def _tolerate_blank_payer_mode(cls, value):
        """A missing/blank/unknown payer_mode means "no opinion", not a failure."""
        if value in get_args(PayerMode):
            return value
        return "BY_AMOUNT"


class RoastResult(BaseModel):
    """Easter egg: is a group member's message a joke, and the roast to fire back."""

    is_joke: bool = False
    roast: Optional[str] = None


class Transcription(BaseModel):
    """A WhatsApp voice note turned into text.

    ``has_speech`` exists so the bot can say "escuché pero no entendí nada"
    instead of inferring it from an empty string — and so a near-silent audio is
    reported as silence rather than hallucinated into a plausible expense.
    """

    has_speech: bool = False
    transcript: str = ""


TRANSCRIBE_JSON_INSTRUCTION = """\
Respondé ÚNICAMENTE con un objeto JSON (sin texto alrededor, sin markdown) con estas claves:
- has_speech: boolean (false si no hay habla inteligible: silencio, ruido, música, una tos)
- transcript: string (la transcripción; "" si has_speech es false)
"""


ROAST_JSON_INSTRUCTION = """\
Respondé ÚNICAMENTE con un objeto JSON (sin texto alrededor, sin markdown) con estas claves:
- is_joke: boolean (true solo si el mensaje es un chiste, joda o cargada; false si es serio)
- roast: string (la cargada para responder, en español rioplatense; "" si is_joke es false)
"""


# Instruction appended to the system prompt so both providers (GitHub Models in
# JSON mode, Gemini with responseMimeType application/json) emit exactly these
# keys. Parsed and validated into ExpenseExtraction.
JSON_INSTRUCTION = """\
Respondé ÚNICAMENTE con un objeto JSON (sin texto alrededor, sin markdown) con estas claves:
- message_type: "expense" | "command" | "chitchat"
- title: string (título corto, ej. "birra", "Uber")
- amount: number (unidades mayores: 15000 para 15.000 ARS, 12.5 para US$12,50)
- currency: string (ISO 4217, ej. CLP, USD, ARS; por defecto CLP)
- paid_by_name: string (quién pagó; "" si no se aclara)
- split_mode: "EVENLY" | "BY_SHARES" | "BY_PERCENTAGE" | "BY_AMOUNT"
- paid_for_names: array de strings (vacío = todos)
- split_parts: array de objetos {"name": string, "value": number|null} SOLO si split_mode
  es BY_AMOUNT o BY_PERCENTAGE; si no, []. En BY_AMOUNT value es el monto en la MISMA
  moneda del total, unidades mayores; en BY_PERCENTAGE es el porcentaje (0-100).
  null = esa persona se lleva el resto.
- payers: array de objetos {"name": string, "value": number|null}. Vacío CASI SIEMPRE.
  SOLO si el mensaje dice que pagó MÁS de una persona ("A y B pagamos...", "A pagó el 30%
  y B el 70%"), poné acá a TODOS los pagadores con lo que puso cada uno, transcribiendo los
  números tal cual (sin aritmética; null = el resto / no se aclara).
- payer_mode: "BY_AMOUNT" | "BY_PERCENTAGE" (cómo leer los value de payers; BY_PERCENTAGE
  si son porcentajes de lo pagado)
- category: string (una de las categorías provistas)
- date: string (YYYY-MM-DD)
- confidence: number (0..1)
- clarification_needed: string (la pregunta a hacer si algo esencial es ambiguo; "" si no)
"""
