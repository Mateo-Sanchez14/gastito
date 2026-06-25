"""Structured contract for the natural-language expense extractor."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

SplitMode = Literal["EVENLY", "BY_SHARES", "BY_PERCENTAGE", "BY_AMOUNT"]
MessageType = Literal["expense", "command", "chitchat"]


class ExpenseExtraction(BaseModel):
    message_type: MessageType
    title: Optional[str] = None
    amount: Optional[float] = Field(
        default=None, description="Amount in the currency's major units (e.g. 15000 ARS, 12.50 USD)"
    )
    currency: Optional[str] = Field(default=None, description="ISO 4217 code, e.g. ARS, USD, CLP")
    paid_by_name: Optional[str] = None
    split_mode: SplitMode = "EVENLY"
    paid_for_names: list[str] = Field(default_factory=list, description="Empty means everyone")
    category: Optional[str] = None
    date: Optional[str] = Field(default=None, description="ISO date YYYY-MM-DD; default today")
    confidence: float = 0.0
    clarification_needed: Optional[str] = None


# Instruction appended to the system prompt so both providers (GitHub Models in
# JSON mode, Gemini with responseMimeType application/json) emit exactly these
# keys. Parsed and validated into ExpenseExtraction.
JSON_INSTRUCTION = """\
Respondé ÚNICAMENTE con un objeto JSON (sin texto alrededor, sin markdown) con estas claves:
- message_type: "expense" | "command" | "chitchat"
- title: string (título corto, ej. "birra", "Uber")
- amount: number (unidades mayores: 15000 para 15.000 ARS, 12.5 para US$12,50)
- currency: string (ISO 4217, ej. ARS, USD, CLP)
- paid_by_name: string (quién pagó; "" si no se aclara)
- split_mode: "EVENLY" | "BY_SHARES" | "BY_PERCENTAGE" | "BY_AMOUNT"
- paid_for_names: array de strings (vacío = todos)
- category: string
- date: string (YYYY-MM-DD)
- confidence: number (0..1)
- clarification_needed: string (la pregunta a hacer si algo esencial es ambiguo; "" si no)
"""
