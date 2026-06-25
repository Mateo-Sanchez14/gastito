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

    # Anthropic (natural-language extraction)
    anthropic_api_key: str
    anthropic_model: str
    confidence_threshold: float

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
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8"),
        confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.7")),
        dolarapi_url=os.getenv("DOLARAPI_URL", "https://dolarapi.com").rstrip("/"),
        fx_general_url=os.getenv("FX_GENERAL_URL", "https://open.er-api.com").rstrip("/"),
        port=int(os.getenv("BOT_PORT", "8000")),
    )


settings = load_settings()
