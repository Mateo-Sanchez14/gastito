"""Environment-backed settings for the gastito WhatsApp bot."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # Gowa (go-whatsapp-web-multidevice) REST server
    gowa_base_url: str
    gowa_user: str
    gowa_pass: str
    gowa_device_id: str
    gowa_webhook_secret: str

    # gastito web app ingestion API
    web_ingest_url: str
    bot_ingest_secret: str

    # LLM (natural-language extraction). GitHub Models (OpenAI-compatible) is the
    # primary; Gemini is the fallback. Both keys are reused from reserv-ia.
    github_models_token: str
    github_models_base_url: str
    github_models_model: str
    gemini_api_key: str
    gemini_model: str
    confidence_threshold: float

    # Voice notes (WhatsApp audio -> transcript -> the normal text pipeline).
    # Gemini-only: GitHub Models' gpt-4o-mini can't hear, so unlike extraction
    # there is NO fallback provider here. `voice_notes_enabled` is a kill switch
    # separate from `gemini_api_key` on purpose — that key is also the extractor's
    # fallback, so you can't turn voice off by clearing it.
    voice_notes_enabled: bool
    gemini_transcribe_model: str
    voice_transcribe_timeout: float
    voice_max_bytes: int
    voice_max_seconds: int

    # Small human-cadence pause before the bot acts/replies (seconds).
    reply_delay_seconds: float

    # Easter egg: when this group member cracks a joke, the bot roasts them back
    # (about being tall, AI replacing their job, or talking loud). Empty/disabled
    # turns the feature off. Match is case/accent-insensitive against the name.
    joke_target_name: str
    joke_roasts_enabled: bool

    # Running gag: when this member is the payer, the confirmation flags the
    # expense as "suspicious". Empty turns it off. Case/accent-insensitive match.
    suspicious_payer_name: str

    # FX
    dolarapi_url: str
    fx_general_url: str

    port: int


def load_settings() -> Settings:
    return Settings(
        gowa_base_url=os.getenv("GOWA_BASE_URL", "http://gowa:4000"),
        gowa_user=os.getenv("GOWA_BASIC_AUTH_USER", "admin"),
        gowa_pass=os.getenv("GOWA_BASIC_AUTH_PASS", ""),
        gowa_device_id=os.getenv("GOWA_DEVICE_ID", ""),
        gowa_webhook_secret=os.getenv("GOWA_WEBHOOK_SECRET", ""),
        web_ingest_url=os.getenv("WEB_INGEST_URL", "http://web:3000/api/bot").rstrip("/"),
        bot_ingest_secret=os.getenv("BOT_INGEST_SECRET", ""),
        github_models_token=os.getenv("GITHUB_MODELS_TOKEN", ""),
        github_models_base_url=os.getenv(
            "GITHUB_MODELS_BASE_URL", "https://models.github.ai/inference"
        ).rstrip("/"),
        github_models_model=os.getenv("GITHUB_MODELS_MODEL", "openai/gpt-4o-mini"),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.7")),
        voice_notes_enabled=os.getenv("VOICE_NOTES_ENABLED", "true").lower()
        in ("1", "true", "yes", "on"),
        # Deliberately NOT settings.gemini_model: that one is the extractor's
        # fallback model. Pointing it at a text-only model shouldn't silently
        # break transcription.
        gemini_transcribe_model=os.getenv("GEMINI_TRANSCRIBE_MODEL", "gemini-2.0-flash"),
        # Audio is much slower than text; the extractor's 30s would time out on a
        # long voice note.
        voice_transcribe_timeout=float(os.getenv("VOICE_TRANSCRIBE_TIMEOUT_SECONDS", "90")),
        voice_max_bytes=int(os.getenv("VOICE_MAX_BYTES", str(8 * 1024 * 1024))),
        voice_max_seconds=int(os.getenv("VOICE_MAX_SECONDS", "120")),
        reply_delay_seconds=float(os.getenv("BOT_REPLY_DELAY_SECONDS", "2")),
        joke_target_name=os.getenv("JOKE_TARGET_NAME", "Benja"),
        joke_roasts_enabled=os.getenv("JOKE_ROASTS_ENABLED", "true").lower()
        in ("1", "true", "yes", "on"),
        suspicious_payer_name=os.getenv("SUSPICIOUS_PAYER_NAME", "Pichi"),
        dolarapi_url=os.getenv("DOLARAPI_URL", "https://dolarapi.com").rstrip("/"),
        fx_general_url=os.getenv("FX_GENERAL_URL", "https://open.er-api.com").rstrip("/"),
        port=int(os.getenv("BOT_PORT", "8000")),
    )


settings = load_settings()
