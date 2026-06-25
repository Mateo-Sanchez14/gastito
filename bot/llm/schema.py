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


# JSON schema for the Anthropic tool (hand-written to keep the tool input tight
# and version-independent of pydantic's schema generation).
EXTRACTION_TOOL = {
    "name": "record_expense",
    "description": (
        "Record the structured interpretation of a casual WhatsApp message about "
        "a shared expense, or classify it as a command/chitchat."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "message_type": {
                "type": "string",
                "enum": ["expense", "command", "chitchat"],
                "description": "expense=logs money spent; command=bot command; chitchat=neither",
            },
            "title": {"type": "string", "description": "Short human title, e.g. 'birra', 'Uber'"},
            "amount": {
                "type": "number",
                "description": "Amount in the currency's major units (15000 for 15.000 ARS, 12.5 for $12.50)",
            },
            "currency": {
                "type": "string",
                "description": "ISO 4217 code. Infer from context/slang; default USD if a bare '$' with no country cue.",
            },
            "paid_by_name": {
                "type": "string",
                "description": "Name of who paid. If unstated, leave empty (defaults to the sender).",
            },
            "split_mode": {
                "type": "string",
                "enum": ["EVENLY", "BY_SHARES", "BY_PERCENTAGE", "BY_AMOUNT"],
            },
            "paid_for_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names the expense is split among. Empty list means everyone in the group.",
            },
            "category": {"type": "string"},
            "date": {"type": "string", "description": "ISO date YYYY-MM-DD; default to today"},
            "confidence": {
                "type": "number",
                "description": "0..1 confidence that this is a complete, unambiguous expense",
            },
            "clarification_needed": {
                "type": "string",
                "description": "If something essential is ambiguous/missing, the question to ask the group.",
            },
        },
        "required": ["message_type", "confidence"],
    },
}
