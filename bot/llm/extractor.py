"""Natural-language -> structured expense.

Primary provider: GitHub Models (OpenAI-compatible chat completions, JSON mode).
Fallback provider: Gemini (generateContent, responseMimeType application/json).

Both keys are reused from the reserv-ia deployment. We use plain httpx + JSON
mode (rather than provider SDKs) to keep deps light and the two paths uniform.
"""

from __future__ import annotations

import json
import logging
import random

import httpx

from config import settings
from llm.schema import (
    JSON_INSTRUCTION,
    ROAST_JSON_INSTRUCTION,
    ExpenseExtraction,
    RoastResult,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Sos el asistente de un grupo de amigos que registran gastos compartidos por WhatsApp.
Tu trabajo es leer un mensaje casual (en español rioplatense/chileno o inglés) y extraer
un gasto compartido en formato estructurado.

Reglas:
- REGLA CRÍTICA DE MONTOS: transcribí el número TAL CUAL aparece, sin redondear ni quitar
  dígitos. "8000" es 8000 (NO 8). "12500" es 12500 (NO 12.5).
- Slang de plata a dígitos completos: "luca"/"lucas"/"k"/"mil" = miles (8 lucas = 8000,
  15k = 15000); "palo"/"palos" = millones (2 palos = 2000000); "gamba" = 100;
  "mango"/"pesos" = CLP.
- NÚMEROS EN PALABRAS a dígitos (una nota de voz transcripta puede traerlos así):
  "ocho mil" = 8000; "ocho mil quinientos" = 8500; "ciento veinte mil" = 120000;
  "doce con cincuenta" = 12.5; "quince lucas" = 15000; "dos palos y medio" = 2500000.
- La jerga puede venir con decimal (típico de una nota de voz transcripta): "2,5 palos"
  = 2500000; "0,5 palos" = 500000; "1,5 lucas" = 1500; "0,5 gambas" = 50.
- Notación: en español/argentino el PUNTO es separador de miles (1.500 = 1500) y la COMA es
  decimal (12,5 = 12.5). amount va en unidades mayores de la moneda (15000 para 15.000 ARS,
  12.5 para US$12,50).
- Inferí la moneda (ISO 4217). La moneda POR DEFECTO del grupo es CLP (pesos chilenos): si no
  hay pista de país, si dice "pesos"/"mangos"/"lucas" o si aparece "$" a secas, usá CLP.
  Usá ARS solo si el mensaje aclara que son pesos argentinos, UYU si son uruguayos, etc.
  Usá USD solo si dice "dólares"/"usd"/"US$".
- PRIMERA PERSONA = quien escribe: "yo", "mí", "conmigo", "yo también", "incluido yo" se refieren
  a la persona indicada en "Quien escribe". Si el mensaje divide el gasto e incluye a quien escribe
  (ej. "entre Benja, Fer y yo"), AGREGÁ su nombre a paid_for_names. Nunca lo omitas.
- paid_by_name: si no se aclara quién pagó, dejalo vacío (el sistema asume que pagó quien escribe).
- paid_for_names: si dice "entre todos", "entre los que estamos", "como siempre" o no aclara,
  dejá la lista VACÍA. No la expandas vos: el sistema ya sabe quiénes están. Si nombra
  personas, ponelas TODAS (incluí a quien escribe cuando corresponda, ver regla de primera persona).
  Matcheá nombres sin distinguir mayúsculas/acentos contra la lista provista.
- PRESENCIA: un participante marcado "[no está]" sigue anotado en el grupo pero no está
  presente ahora. Cuando tengas que ENUMERAR (ej. "todos menos Pichi"), NO lo incluyas.
  Sí incluilo si el mensaje lo nombra explícitamente (pudo poner plata igual).
- APODOS: a cada participante le pueden figurar apodos entre paréntesis, "(apodos: ...)". Un apodo
  refiere al MISMO participante; devolvé SIEMPRE el nombre canónico (el de antes del paréntesis),
  nunca el apodo. Ej: con "Fer (apodos: Fernando, Tuco)", tanto "Tuco" como "Fernando" -> "Fer".
- split_mode: EVENLY salvo que indique porcentajes (BY_PERCENTAGE, ej. "yo 70% y él 30%"),
  partes/proporciones (BY_SHARES) o montos exactos por persona (BY_AMOUNT, ej. "10900 por
  Mauri, 16700 por Errazquin").
- PARTES POR PERSONA (BY_AMOUNT / BY_PERCENTAGE): completá split_parts con TODAS las
  personas entre las que se divide, una entrada por persona. TRANSCRIBÍ cada número tal
  cual aparece (montos en la MISMA moneda del total; porcentajes 0-100). NO HAGAS
  ARITMÉTICA: no calcules restos, no sumes, no verifiques totales — el sistema lo hace.
  Si alguien se lleva "el resto" / "lo que queda" / no tiene número explícito, poné value
  null para esa persona ("el resto para mí" = null para quien escribe). Si dice "el resto
  entre los demás", enumerá a los presentes que faltan (sin los "[no está]") con value null.
  En estos modos paid_for_names debe listar los mismos nombres que split_parts (nombres
  canónicos, regla de apodos).
- date: ISO YYYY-MM-DD. "anoche"/"ayer" = el día anterior a hoy; si no se aclara, hoy.
- message_type: "expense" si registra plata gastada; "command" si parece un comando del bot
  ("saldo", "deshacer", "ayuda", "/soy ...", "/cotizacion ..."); "chitchat" si no es ninguno.
- confidence: 0..1. Bajá la confianza si falta el monto, la moneda es dudosa, o no se entiende
  bien quién pagó / entre quiénes se divide. Si algo esencial es ambiguo, completá
  clarification_needed con una pregunta corta en el idioma del mensaje.
"""


def _format_participants(participants: list[dict]) -> str:
    """Render participants for the prompt, appending each one's apodos so the
    model can map a nickname back to the canonical name, plus a "[no está]" mark
    for whoever isn't currently on the trip. e.g.
    "Fer (apodos: Fernando, Tuco), Benja [no está]"."""
    parts: list[str] = []
    for p in participants:
        label = p["name"]
        aliases = [a for a in (p.get("aliases") or []) if a]
        if aliases:
            label += f" (apodos: {', '.join(aliases)})"
        if not p.get("active", True):
            label += " [no está]"
        parts.append(label)
    return ", ".join(parts) or "(ninguno)"


def _build_user_prompt(
    text: str,
    sender_name: str,
    participants: list[dict],
    currencies: list[str],
    categories: list[str],
    today: str,
) -> str:
    return (
        f"Fecha de hoy: {today}\n"
        f"Quien escribe: {sender_name or 'desconocido'}\n"
        f"Participantes del grupo: {_format_participants(participants)}\n"
        f"Monedas soportadas: {', '.join(currencies)}\n"
        f"Categorías disponibles: {', '.join(categories) or '(ninguna)'}\n\n"
        f"Mensaje:\n{text}"
    )


EDIT_SYSTEM_PROMPT = """\
Sos el asistente de un grupo de amigos que registran gastos compartidos por WhatsApp.
El usuario está RESPONDIENDO (citando) un gasto ya registrado para CORREGIRLO.
Te doy el gasto actual y el mensaje de corrección; devolvé el gasto YA CORREGIDO COMPLETO.

Reglas:
- Devolvé SIEMPRE el gasto completo: copiá TAL CUAL los campos que el mensaje NO cambia
  (título, monto, moneda, quién pagó, entre quiénes) y modificá solo lo que el usuario corrige.
- REGLA CRÍTICA DE MONTOS: transcribí el número TAL CUAL aparece, sin redondear ni quitar
  dígitos. "8000" es 8000 (NO 8). amount va en unidades mayores de la moneda.
- Slang de plata a dígitos: "luca"/"lucas"/"k"/"mil" = miles (8 lucas = 8000); "palo"/"palos"
  = millones; "gamba" = 100; "mango"/"pesos" = CLP (moneda por defecto del grupo). PUNTO = miles (1.500=1500), COMA = decimal.
- La jerga puede venir con decimal: "2,5 palos" = 2500000; "0,5 palos" = 500000;
  "1,5 lucas" = 1500; "0,5 gambas" = 50.
- paid_for_names: si el usuario cambia entre quiénes se divide ("dividí entre todos menos X",
  "solo entre A y B"), calculá la NUEVA lista completa de nombres a partir de los participantes.
  Si no toca la división, copiá la lista actual. Lista vacía = todos los que están.
  "yo"/"mí"/"conmigo" se refieren a quien escribe (dato "Quien escribe"): incluí su nombre
  si el cambio lo abarca.
- split_mode y PARTES POR PERSONA (BY_AMOUNT / BY_PERCENTAGE): si la corrección redefine la
  división con montos exactos o porcentajes, poné el split_mode que corresponda y completá
  split_parts con TODAS las personas, una entrada por persona. TRANSCRIBÍ cada número tal
  cual aparece (montos en la MISMA moneda del total; porcentajes 0-100). NO HAGAS
  ARITMÉTICA: no calcules restos ni verifiques totales — el sistema lo hace. "el resto" /
  sin número explícito = value null para esa persona.
- Si la corrección NO toca la división, devolvé split_parts: [] (el sistema conserva la
  actual). NUNCA copies los números de la división actual a split_parts: solo transcribí
  números que el mensaje de corrección diga explícitamente.
- PRESENCIA: un participante marcado "[no está]" sigue anotado en el grupo pero no está
  presente ahora. Cuando tengas que ENUMERAR (ej. "todos menos Pichi"), NO lo incluyas.
  Sí incluilo si el mensaje lo nombra explícitamente.
- APODOS: a los participantes les pueden figurar apodos entre paréntesis "(apodos: ...)". Un apodo
  refiere al MISMO participante; devolvé SIEMPRE el nombre canónico, nunca el apodo.
- paid_by_name: si cambia quién pagó, ponelo; si no, copiá el actual.
- message_type: "expense" SOLO si el mensaje realmente corrige/cambia el gasto. Si es un
  comentario, emoji, agradecimiento o charla que NO cambia nada, devolvé "chitchat".
- confidence: 0..1. Bajala si la corrección es ambigua y completá clarification_needed con una
  pregunta corta en el idioma del mensaje.
"""


def _build_edit_prompt(
    current: dict,
    text: str,
    sender_name: str,
    participants: list[dict],
    currencies: list[str],
    categories: list[str],
    today: str,
) -> str:
    split = ", ".join(current.get("paid_for_names") or []) or "todos"
    # Non-even splits get a per-person description (informative only: the
    # prompt forbids copying these numbers back into split_parts).
    split_desc = current.get("split_desc")
    if split_desc:
        split = f"{split} — división actual ({current.get('split_mode')}): {split_desc}"
    return (
        f"Fecha de hoy: {today}\n"
        f"Quien escribe: {sender_name or 'desconocido'}\n"
        f"Participantes del grupo: {_format_participants(participants)}\n"
        f"Monedas soportadas: {', '.join(currencies)}\n"
        f"Categorías disponibles: {', '.join(categories) or '(ninguna)'}\n\n"
        "Gasto ACTUAL (a corregir):\n"
        f"- título: {current.get('title') or 'Gasto'}\n"
        f"- monto: {current.get('amount')} {current.get('currency') or 'USD'}\n"
        f"- pagó: {current.get('paid_by_name') or 'desconocido'}\n"
        f"- dividido entre: {split}\n"
        f"- categoría: {current.get('category') or '(ninguna)'}\n"
        f"- fecha: {current.get('date') or today}\n\n"
        f"Mensaje de corrección:\n{text}"
    )


def _call_openai_compatible(
    base_url: str, token: str, model: str, system: str, user: str, temperature: float = 0.0
) -> dict:
    """OpenAI-compatible chat completions in JSON mode (Groq, OpenAI, OpenRouter...)."""
    resp = httpx.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
        },
        timeout=30,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(content)


def _call_primary(system: str, user: str, temperature: float = 0.0) -> dict:
    return _call_openai_compatible(
        settings.llm_primary_base_url,
        settings.llm_primary_token,
        settings.llm_primary_model,
        system,
        user,
        temperature,
    )


def _call_secondary(system: str, user: str, temperature: float = 0.0) -> dict:
    return _call_openai_compatible(
        settings.llm_secondary_base_url,
        settings.llm_secondary_token,
        settings.llm_secondary_model,
        system,
        user,
        temperature,
    )


def _providers() -> list[tuple[str, object]]:
    """Configured providers, in fallback order. A slot without a token is skipped
    so an unused one doesn't burn a round-trip (and a log line) per message."""
    chain = []
    if settings.llm_primary_token:
        chain.append((f"primary:{settings.llm_primary_model}", _call_primary))
    if settings.llm_secondary_token:
        chain.append((f"secondary:{settings.llm_secondary_model}", _call_secondary))
    if settings.gemini_api_key:
        chain.append(("gemini", _call_gemini))
    return chain


def _call_gemini(system: str, user: str, temperature: float = 0.0) -> dict:
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
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": temperature,
            },
        },
        timeout=30,
    )
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def _run(system: str, user: str, what: str) -> ExpenseExtraction | None:
    """Run the prompt through the configured providers; parse to ExpenseExtraction."""
    for name, provider in _providers():
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

    logger.error("All LLM providers failed for %s: %s", what, user[:200])
    return None


def extract(
    text: str,
    sender_name: str,
    participants: list[dict],
    currencies: list[str],
    categories: list[str],
    today: str,
) -> ExpenseExtraction | None:
    """Return a parsed extraction, trying GitHub Models then Gemini.

    ``participants`` are the full participant dicts ({id, name, aliases}); their
    apodos are shown to the model so it maps a nickname to the canonical name.
    """
    system = f"{SYSTEM_PROMPT}\n\n{JSON_INSTRUCTION}"
    user = _build_user_prompt(text, sender_name, participants, currencies, categories, today)
    return _run(system, user, "extract")


def extract_edit(
    current: dict,
    text: str,
    sender_name: str,
    participants: list[dict],
    currencies: list[str],
    categories: list[str],
    today: str,
) -> ExpenseExtraction | None:
    """Re-extract a *full* expense given the current state + a correction.

    ``current`` describes the expense being edited (title, amount in its original
    currency, currency, payer, who it's split among). The model returns the FULL
    corrected expense, copying fields the message doesn't change.
    """
    system = f"{EDIT_SYSTEM_PROMPT}\n\n{JSON_INSTRUCTION}"
    user = _build_edit_prompt(
        current, text, sender_name, participants, currencies, categories, today
    )
    return _run(system, user, "extract_edit")


# Easter egg: roast a specific member when they crack a joke. The three running
# gags about them: they're very tall, AI could do their job, and they talk loud.
_ROAST_THEMES = {
    "alto": "que es altísimo / un poste / le pega el sol primero",
    "ia": "que una IA le puede hacer el laburo / lo reemplaza un script",
    "fuerte": "que habla re fuerte / grita / lo escucha todo el barrio",
}

ROAST_SYSTEM_PROMPT = """\
Sos el bot cargador de un grupo de amigos. {target} es un personaje del grupo con tres
chistes recurrentes: es MUY alto, habla muy fuerte, y su trabajo lo podría hacer una IA.

Te paso un mensaje que mandó {target}. Si es un CHISTE, una joda o una cargada, respondé
con una cargada corta, ingeniosa y de buena onda (1 o 2 frases, español rioplatense, podés
usar 1 emoji) enfocada en ESTE tema: {theme}.
Si el mensaje NO es un chiste (es algo serio, una pregunta real, un gasto, etc.), NO lo
cargues: devolvé is_joke=false.
"""


def roast_joke(text: str, target_name: str) -> RoastResult | None:
    """If ``target_name``'s message is a joke, return a roast to fire back."""
    theme_key = random.choice(list(_ROAST_THEMES))
    system = (
        ROAST_SYSTEM_PROMPT.format(target=target_name or "el pibe", theme=_ROAST_THEMES[theme_key])
        + "\n\n"
        + ROAST_JSON_INSTRUCTION
    )
    user = f"Mensaje de {target_name or 'el pibe'}:\n{text}"

    for name, provider in _providers():
        try:
            data = provider(system, user, 0.9)
        except Exception as e:
            logger.warning("Roast provider %s failed: %s", name, e)
            continue
        try:
            return RoastResult(**data)
        except Exception:
            logger.exception("Roast provider %s returned unparseable data: %s", name, data)
            continue
    return None
