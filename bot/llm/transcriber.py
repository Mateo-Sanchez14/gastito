"""WhatsApp voice note -> text, so the normal expense pipeline can read it.

Gemini only. This is the one place with NO fallback provider: the primary
extraction model (GitHub Models `openai/gpt-4o-mini`) can't accept audio, so the
two-provider resilience `extractor._run` gives every other LLM call does not
exist here. A Gemini outage or a free-tier 429 burst takes voice notes down —
a deliberate trade, not an oversight.

The transcript is a *wire format between two models*: whatever comes out of here
is fed straight into `extractor.SYSTEM_PROMPT`, whose money rules are already
tuned and scarred (see commit 8d45209, "8000 was parsed as 8"). So the prompt
below is co-designed with that one — read both before changing either.
"""

from __future__ import annotations

import base64
import json
import logging

import httpx

from config import settings
from llm.schema import TRANSCRIBE_JSON_INSTRUCTION, Transcription

logger = logging.getLogger(__name__)

TRANSCRIBE_SYSTEM_PROMPT = """\
Sos el transcriptor de las notas de voz de un grupo de amigos que registran gastos
compartidos por WhatsApp. Hablan español rioplatense (argentino) y chileno, rápido,
con jerga y muchas veces con ruido de fondo.

Tu ÚNICA tarea es transcribir lo que se dice. No interpretes, no resumas, no traduzcas,
no agregues comentarios ni aclaraciones, no completes lo que falta, no describas el audio.

Reglas:
- Transcribí en español tal como se habla (voseo, "che", "dale", muletillas incluidas).
  No pases a español neutro ni "corrijas" el estilo.
- NÚMEROS EN DÍGITOS, SIEMPRE: todo número dicho en palabras va en dígitos, SIN separador
  de miles y SIN símbolo de moneda.
  "ocho mil" -> "8000" · "ocho mil quinientos" -> "8500" · "quince" -> "15"
  "doce con cincuenta" -> "12,50" · "ciento veinte mil" -> "120000"
  NUNCA escribas "8.500", ni "8,500", ni "$8500", ni "8 mil".
- JERGA DE PLATA: dejá la palabra TAL CUAL y ponele el número en dígitos adelante.
  NO la conviertas a su valor.
  "quince lucas" -> "15 lucas" · "dos palos" -> "2 palos" · "una gamba" -> "1 gamba"
  "treinta mangos" -> "30 mangos"
- FRACCIONES DE JERGA con coma decimal pegada al número:
  "dos palos y medio" -> "2,5 palos" · "medio palo" -> "0,5 palos" · "luca y media" -> "1,5 lucas"
- NOMBRES PROPIOS con mayúscula inicial, tal como suenan. No los reemplaces por nombres
  más comunes. Si te doy la lista de nombres del grupo, usá EXACTAMENTE uno de esos cuando
  lo que escuchás se le parezca.
- NO pongas punto ni ningún signo de puntuación al final del texto. Adentro podés usar comas.
- Si dice "barra" o "slash" seguido de una palabra, escribilo como comando:
  "barra soy Mateo" -> "/soy Mateo" · "barra apodo Tuco igual Fer" -> "/apodo Tuco = Fer"
  "barra cotización blue" -> "/cotizacion blue"
- Si el audio no tiene habla inteligible (silencio, ruido, música, tos, un "mmm"),
  devolvé has_speech=false y transcript vacío. NUNCA inventes texto.
"""


def _build_prompt(vocabulary: list[str]) -> str:
    """The user-turn text. The vocabulary hint is the cheapest accuracy win we
    have: apodos like "Tuco" or "Pichi" are exactly what ASR mangles, and they're
    load-bearing for ``util.match_participant``."""
    if not vocabulary:
        return "Transcribí este audio siguiendo las reglas."
    names = ", ".join(vocabulary)
    return (
        "Nombres del grupo (usá EXACTAMENTE estos cuando lo que escuchás se les "
        f"parezca): {names}\n\nTranscribí este audio siguiendo las reglas."
    )


def transcribe(audio: bytes, mime_type: str, vocabulary: list[str]) -> Transcription | None:
    """Transcribe a voice note. None = the provider failed (vs. a valid
    ``has_speech=False`` result, which means "we heard it, there was no speech").

    The httpx plumbing deliberately duplicates ``extractor._call_gemini`` rather
    than sharing a helper: factoring it out would touch the working extraction
    path for no user-visible gain. The differences that matter are the audio
    ``inline_data`` part and the much longer timeout.
    """
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")

    system = f"{TRANSCRIBE_SYSTEM_PROMPT}\n\n{TRANSCRIBE_JSON_INSTRUCTION}"
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_transcribe_model}:generateContent?key={settings.gemini_api_key}"
    )
    resp = httpx.post(
        url,
        json={
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": _build_prompt(vocabulary)},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": base64.b64encode(audio).decode("ascii"),
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0,
            },
        },
        timeout=settings.voice_transcribe_timeout,
    )
    if resp.status_code == 429:
        # Worth its own line: "rate limited" and "broken" need very different
        # reactions when you're reading prod logs at 2am.
        logger.warning("Gemini transcription rate-limited (429): %s", resp.text[:200])
        resp.raise_for_status()
    resp.raise_for_status()

    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    result = Transcription(**json.loads(text))
    result.transcript = (result.transcript or "").strip()
    if not result.transcript:
        result.has_speech = False
    return result
