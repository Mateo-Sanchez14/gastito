"""gastito WhatsApp bot — FastAPI webhook receiver.

Gowa POSTs each inbound message here. We verify the HMAC signature, ACK fast,
and process the message in a background task (mirrors american-store-chatbot's
ACK-fast + enqueue shape).
"""

from __future__ import annotations

import json
import logging

from fastapi import BackgroundTasks, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi import FastAPI

from config import settings
from processor import process_payload
from whatsapp.signature import verify_gowa_signature

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("gastito.bot")

app = FastAPI(title="gastito-bot")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/webhooks/gowa/")
async def gowa_webhook(request: Request, background_tasks: BackgroundTasks):
    raw = await request.body()

    signature = request.headers.get("X-Hub-Signature-256", "")
    if settings.gowa_webhook_secret and not verify_gowa_signature(
        settings.gowa_webhook_secret, raw, signature
    ):
        logger.warning("Rejected Gowa webhook with invalid signature")
        return PlainTextResponse("invalid signature", status_code=403)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return PlainTextResponse("invalid body", status_code=400)

    # ACK fast; do the work off the request path.
    background_tasks.add_task(process_payload, payload)
    return JSONResponse({"status": "received"})
