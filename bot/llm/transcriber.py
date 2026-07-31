"""WhatsApp voice note -> text, so the normal expense pipeline can read it.

Gemini (free tier) first, OpenAI as the paid safety net. Voice notes used to be
Gemini-only and a free-tier 429 burst took them down (2026-07-31), so OpenAI is
here strictly for that: it only runs when Gemini fails, which keeps the bill at
roughly zero while removing the single point of failure.

The two providers return different shapes: Gemini answers the JSON contract
below (``has_speech`` + ``transcript``), while OpenAI's transcription endpoint is
plain ASR and returns bare text. `_from_plain_text` reconciles them, and the
prompt rules Gemini follows (numbers as digits, slang kept verbatim) are enforced
downstream by `extractor.SYSTEM_PROMPT` when the OpenAI path is used.

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


def _finish(result: Transcription) -> Transcription:
    result.transcript = (result.transcript or "").strip()
    if not result.transcript:
        result.has_speech = False
    return result


def _transcribe_openai(audio: bytes, mime_type: str, vocabulary: list[str]) -> Transcription:
    """Paid fallback: OpenAI's transcription endpoint (plain ASR, no JSON mode).

    ``prompt`` is the only steering this endpoint accepts — it takes a style/
    vocabulary hint, not instructions, so the group's names go in to stop the ASR
    from mangling apodos. Everything else (digits, slang) is left to the extractor.
    """
    ext = {"audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/wav": "wav"}.get(
        (mime_type or "").split(";")[0].strip(), "ogg"
    )
    data = {"model": settings.openai_audio_model, "language": "es", "response_format": "text"}
    if vocabulary:
        data["prompt"] = "Nombres del grupo: " + ", ".join(vocabulary)

    resp = httpx.post(
        f"{settings.openai_audio_base_url}/audio/transcriptions",
        headers={"Authorization": f"Bearer {settings.openai_audio_key}"},
        files={"file": (f"audio.{ext}", audio, mime_type or "audio/ogg")},
        data=data,
        timeout=settings.voice_transcribe_timeout,
    )
    resp.raise_for_status()
    text = resp.text.strip()
    return _finish(Transcription(has_speech=bool(text), transcript=text))


def _transcribe_gemini(audio: bytes, mime_type: str, vocabulary: list[str]) -> Transcription:
    """Free tier, and the only provider that honours the prompt rules directly.

    The httpx plumbing deliberately duplicates ``extractor._call_gemini`` rather
    than sharing a helper: factoring it out would touch the working extraction
    path for no user-visible gain. The differences that matter are the audio
    ``inline_data`` part and the much longer timeout.
    """
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
    resp.raise_for_status()

    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return _finish(Transcription(**json.loads(text)))


def transcribe(audio: bytes, mime_type: str, vocabulary: list[str]) -> Transcription | None:
    """Transcribe a voice note. None = every configured provider failed (vs. a
    valid ``has_speech=False`` result, which means "we heard it, no speech").

    Gemini first because it's free and prompt-steerable; OpenAI only picks up
    what Gemini drops. A provider without credentials is skipped, so this stays
    Gemini-only until ``OPENAI_AUDIO_KEY`` is set.
    """
    chain = []
    if settings.gemini_api_key:
        chain.append(("gemini", _transcribe_gemini))
    if settings.openai_audio_key:
        chain.append((f"openai:{settings.openai_audio_model}", _transcribe_openai))
    if not chain:
        raise RuntimeError("No transcription provider configured (GEMINI_API_KEY / OPENAI_AUDIO_KEY)")

    for name, provider in chain:
        try:
            return provider(audio, mime_type, vocabulary)
        except httpx.HTTPStatusError as e:
            # Worth its own line: "rate limited" and "broken" need very different
            # reactions when you're reading prod logs at 2am.
            if e.response.status_code == 429:
                logger.warning("%s transcription rate-limited (429): %s", name, e.response.text[:200])
            else:
                logger.warning("%s transcription failed (%s)", name, e.response.status_code)
        except Exception:
            logger.exception("%s transcription failed", name)

    logger.error("All transcription providers failed (%s bytes, %s)", len(audio), mime_type)
    return None
