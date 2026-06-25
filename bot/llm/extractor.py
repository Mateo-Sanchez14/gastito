"""Natural-language -> structured expense.

Primary provider: GitHub Models (OpenAI-compatible chat completions, JSON mode).
Fallback provider: Gemini (generateContent, responseMimeType application/json).

Both keys are reused from the reserv-ia deployment. We use plain httpx + JSON
mode (rather than provider SDKs) to keep deps light and the two paths uniform.
"""

from __future__ import annotations

import json
import logging

import httpx

from config import settings
from llm.schema import JSON_INSTRUCTION, ExpenseExtraction

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Sos el asistente de un grupo de amigos que registran gastos compartidos por WhatsApp.
Tu trabajo es leer un mensaje casual (en español rioplatense/chileno o inglés) y extraer
un gasto compartido en formato estructurado.

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


def _call_github_models(system: str, user: str) -> dict:
    """Primary: GitHub Models, OpenAI-compatible chat completions in JSON mode."""
    if not settings.github_models_token:
        raise RuntimeError("GITHUB_MODELS_TOKEN not configured")
    resp = httpx.post(
        f"{settings.github_models_base_url}/chat/completions",
        headers={"Authorization": f"Bearer {settings.github_models_token}"},
        json={
            "model": settings.github_models_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        },
        timeout=30,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(content)


def _call_gemini(system: str, user: str) -> dict:
    """Fallback: Gemini generateContent with JSON response."""
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent?key={settings.gemini_api_key}"
    )
    resp = httpx.post(
        url,
        json={
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
        },
        timeout=30,
    )
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def extract(
    text: str,
    sender_name: str,
    participants: list[str],
    currencies: list[str],
    categories: list[str],
    today: str,
) -> ExpenseExtraction | None:
    """Return a parsed extraction, trying GitHub Models then Gemini."""
    system = f"{SYSTEM_PROMPT}\n\n{JSON_INSTRUCTION}"
    user = _build_user_prompt(text, sender_name, participants, currencies, categories, today)

    for name, provider in (("github_models", _call_github_models), ("gemini", _call_gemini)):
        try:
            data = provider(system, user)
        except Exception as e:
            logger.warning("LLM provider %s failed: %s", name, e)
            continue
        try:
            return ExpenseExtraction(**data)
        except Exception:
            logger.exception("LLM provider %s returned unparseable data: %s", name, data)
            continue

    logger.error("All LLM providers failed for message: %s", text[:200])
    return None
