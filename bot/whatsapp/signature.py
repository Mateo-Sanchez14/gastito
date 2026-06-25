"""Validation of Gowa's X-Hub-Signature-256 webhook signature.

Copied verbatim from american-store-chatbot — Gowa keys an HMAC-SHA256 with
WHATSAPP_WEBHOOK_SECRET over the raw body and sends it as ``sha256=<hex>`` (some
builds omit the prefix, so a bare hex digest is also accepted).
"""

import hashlib
import hmac


def verify_gowa_signature(secret: str, payload: bytes, header: str) -> bool:
    if not secret or not header:
        return False
    received = (
        header.split("=", 1)[1].strip()
        if header.startswith("sha256=")
        else header.strip()
    )
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)
