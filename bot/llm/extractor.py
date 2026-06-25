"""Natural-language -> structured expense via the Anthropic API.

Uses forced tool-use (``record_expense``) so the model must return JSON matching
our schema, with no brittle text parsing.
"""

from __future__ import annotations

import logging

import anthropic

from config import settings
from llm.schema import EXTRACTION_TOOL, ExpenseExtraction

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


SYSTEM_PROMPT = """\
Sos el asistente de un grupo de amigos que registran gastos compartidos por WhatsApp.
Tu trabajo es leer un mensaje casual (en español rioplatense/chileno o inglés) y extraer
un gasto compartido en formato estructurado, llamando SIEMPRE a la herramienta record_expense.

Reglas:
- Plata y slang: "luca"/"lucas" = miles (15 lucas = 15000); "palo"/"palos" = millones;
  "gamba" = 100; "mango"/"pesos" = ARS. "$" puede ser USD o pesos según el contexto del país.
- Inferí la moneda (ISO 4217). Si el mensaje claramente es en pesos argentinos usá ARS,
  chilenos CLP, uruguayos UYU, etc. Si dice "dólares"/"usd" o "$" sin pista de país, usá USD.
- amount va en unidades mayores de la moneda (15000 para 15.000 ARS, 12.5 para US$12,50).
- paid_by_name: si no se aclara quién pagó, dejalo vacío (el sistema asume que pagó quien escribe).
- paid_for_names: si dice "entre todos" o no aclara, dejá la lista vacía (= todos). Si nombra
  personas, ponelas. Matcheá nombres sin distinguir mayúsculas/acentos contra la lista provista.
- split_mode: EVENLY salvo que indique porcentajes (BY_PERCENTAGE), partes (BY_SHARES) o montos
  exactos por persona (BY_AMOUNT).
- date: ISO YYYY-MM-DD. "anoche"/"ayer" = el día anterior a hoy; si no se aclara, hoy.
- message_type: "expense" si registra plata gastada; "command" si parece un comando del bot
  ("saldo", "deshacer", "ayuda", "/soy ...", "/cotizacion ..."); "chitchat" si no es ninguno.
- confidence: 0..1. Bajá la confianza si falta el monto, la moneda es dudosa, o no se entiende
  bien quién pagó / entre quiénes se divide. Si algo esencial es ambiguo, completá
  clarification_needed con una pregunta corta en el idioma del mensaje.
"""


def _build_user_prompt(
    text: str,
    sender_name: str,
    participants: list[str],
    currencies: list[str],
    categories: list[str],
    today: str,
) -> str:
    return (
        f"Fecha de hoy: {today}\n"
        f"Quien escribe: {sender_name or 'desconocido'}\n"
        f"Participantes del grupo: {', '.join(participants) or '(ninguno)'}\n"
        f"Monedas soportadas: {', '.join(currencies)}\n"
        f"Categorías disponibles: {', '.join(categories) or '(ninguna)'}\n\n"
        f"Mensaje:\n{text}"
    )


def extract(
    text: str,
    sender_name: str,
    participants: list[str],
    currencies: list[str],
    categories: list[str],
    today: str,
) -> ExpenseExtraction | None:
    """Return a parsed extraction, or None on API/parse failure."""
    try:
        resp = _get_client().messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[EXTRACTION_TOOL],
            tool_choice={"type": "tool", "name": "record_expense"},
            messages=[
                {
                    "role": "user",
                    "content": _build_user_prompt(
                        text, sender_name, participants, currencies, categories, today
                    ),
                }
            ],
        )
    except Exception:
        logger.exception("Anthropic extraction call failed")
        return None

    for block in resp.content:
        if getattr(block, "type", None) == "tool_use":
            try:
                return ExpenseExtraction(**block.input)
            except Exception:
                logger.exception("Failed to validate extraction: %s", block.input)
                return None

    logger.warning("Anthropic response had no tool_use block")
    return None
