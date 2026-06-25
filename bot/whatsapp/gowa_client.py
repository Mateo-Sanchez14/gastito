"""Thin client for Gowa (go-whatsapp-web-multidevice), adapted from
american-store-chatbot to use httpx and to send to WhatsApp *groups*.

Auth is HTTP Basic plus an optional ``X-Device-Id`` scoping header. ``to`` is a
full JID — for a group that's the ``...@g.us`` chat id.
"""

from __future__ import annotations

import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)


class GowaClient:
    def __init__(self, timeout: int = 15):
        self.base_url = settings.gowa_base_url.rstrip("/")
        self._auth = (settings.gowa_user, settings.gowa_pass)
        self.device_id = settings.gowa_device_id
        self.timeout = timeout

    def _post(self, path: str, json: dict) -> httpx.Response:
        """POST JSON with Basic auth + device scope; on 404 (device not found)
        retry once without the header so single-device setups fall back."""
        url = f"{self.base_url}{path}"

        def do(headers: dict) -> httpx.Response:
            return httpx.post(
                url, json=json, auth=self._auth, headers=headers, timeout=self.timeout
            )

        scoped = {"X-Device-Id": self.device_id} if self.device_id else {}
        resp = do(scoped)
        if resp.status_code == 404 and scoped:
            logger.warning("Gowa device %s not found; retrying unscoped", self.device_id)
            resp = do({})
        return resp

    def send_text(self, to: str, body: str) -> str | None:
        """Send a text message. ``to`` is a full JID (group ``...@g.us``)."""
        resp = self._post("/send/message", {"phone": to, "message": body})
        if resp.status_code >= 400:
            logger.error("Gowa send_text failed (%s): %s", resp.status_code, resp.text)
            return None
        try:
            return resp.json()["results"]["message_id"]
        except (KeyError, TypeError, ValueError):
            logger.warning("Gowa send_text: unexpected response %s", resp.text)
            return None

    def send_chat_presence(self, to: str, action: str = "start") -> bool:
        """Show/clear the typing indicator (best-effort)."""
        resp = self._post("/send/chat-presence", {"phone": to, "action": action})
        return resp.status_code < 400
