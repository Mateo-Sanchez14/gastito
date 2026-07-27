"""Thin client for Gowa (go-whatsapp-web-multidevice), adapted from
american-store-chatbot to use httpx and to send to WhatsApp *groups*.

Auth is HTTP Basic plus an optional ``X-Device-Id`` scoping header. ``to`` is a
full JID — for a group that's the ``...@g.us`` chat id.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote, urlparse

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Media Gowa auto-downloaded lives under its own ``statics/`` dir. The path comes
# from the (HMAC-verified, so semi-trusted) webhook body, and we turn it into an
# HTTP GET — so whitelist it rather than trusting it, and never let ".." through.
_MEDIA_PATH_RE = re.compile(r"^statics/[A-Za-z0-9._\-/]+$")


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

    def _get(
        self, path: str, *, params: dict | None = None, retry_unscoped: bool = True
    ) -> httpx.Response:
        """GET with Basic auth + device scope.

        Two deliberate differences from ``_post``:
        - ``follow_redirects=True`` — httpx doesn't follow by default, and Gowa's
          static file handler can 301 on path normalization, which would otherwise
          look like a hard failure.
        - ``retry_unscoped`` is opt-out. For an API POST a 404 means "device not
          found"; for a static file GET it means "the file isn't there", and
          ``_post``'s retry + log line would be actively misleading.
        """
        url = f"{self.base_url}{path}"

        def do(headers: dict) -> httpx.Response:
            return httpx.get(
                url,
                params=params,
                auth=self._auth,
                headers=headers,
                timeout=self.timeout,
                follow_redirects=True,
            )

        scoped = {"X-Device-Id": self.device_id} if self.device_id else {}
        resp = do(scoped)
        if resp.status_code == 404 and scoped and retry_unscoped:
            logger.warning("Gowa device %s not found; retrying unscoped", self.device_id)
            resp = do({})
        return resp

    def fetch_media(self, path: str, max_bytes: int) -> bytes | None:
        """Download an auto-downloaded media file by its gowa-container path.

        ``path`` is what the webhook gave us (e.g. ``statics/media/….ogg``) — a
        path inside the *gowa* container, so we can't read it off disk; we fetch it
        over HTTP from Gowa itself.
        """
        path = (path or "").lstrip("/")
        if ".." in path or not _MEDIA_PATH_RE.match(path):
            logger.warning("Refusing suspicious Gowa media path %r", path)
            return None

        resp = self._get(f"/{path}", retry_unscoped=False)
        if resp.status_code >= 400:
            logger.warning("Gowa media fetch failed (%s) for %s", resp.status_code, path)
            return None

        # No streaming: a WhatsApp voice note is opus at roughly 1 KB/s (a 5-minute
        # note is ~400 KB) and WhatsApp's own ceiling is ~16 MB, so a header check
        # plus a length check after the read is enough.
        declared = int(resp.headers.get("content-length") or 0)
        size = len(resp.content)
        if declared > max_bytes or size > max_bytes:
            logger.warning("Gowa media %s too large (%s bytes)", path, declared or size)
            return None
        return resp.content or None

    def resolve_media_ref(self, message_id: str, chat_id: str) -> tuple[str, str] | None:
        """Fallback: ask Gowa to (re)download a message's media. -> (path, mime).

        Slower than the webhook-supplied path (it may re-fetch from WhatsApp's
        servers and can fail on old messages), so only used when that path 404s.
        Needs ``WHATSAPP_CHAT_STORAGE: 'true'`` on the gowa service.
        """
        resp = self._get(
            f"/message/{quote(message_id, safe='')}/download", params={"phone": chat_id}
        )
        if resp.status_code >= 400:
            logger.warning(
                "Gowa media download failed (%s) for %s: %s",
                resp.status_code, message_id, resp.text[:200],
            )
            return None
        try:
            results = resp.json()["results"]
        except (KeyError, TypeError, ValueError):
            logger.warning("Gowa media download: unexpected response %s", resp.text[:200])
            return None

        path = results.get("file_path") or ""
        if not path and results.get("file_url"):
            # Keep ONLY the path and re-attach our own base_url: the host Gowa puts
            # in file_url is its *configured* base (typically localhost:3000), which
            # from inside Docker points at this container, not at gowa.
            path = urlparse(results["file_url"]).path.lstrip("/")
        if not path:
            return None
        return path, results.get("media_type") or ""

    def send_text(self, to: str, body: str, reply_to: str | None = None) -> str | None:
        """Send a text message. ``to`` is a full JID (group ``...@g.us``).

        ``reply_to`` (a message id) quotes that message, so in a busy group the
        reply is visibly threaded to the member it's answering — no confusion
        about whose pending expense the bot is talking about.
        """
        json: dict = {"phone": to, "message": body}
        if reply_to:
            json["reply_message_id"] = reply_to
        resp = self._post("/send/message", json)
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

    def react(self, to: str, message_id: str, emoji: str) -> bool:
        """React to a message with an emoji (best-effort). ``to`` is a full JID
        (group ``...@g.us``); ``message_id`` is the message being reacted to."""
        path = f"/message/{quote(message_id, safe='')}/reaction"
        resp = self._post(path, {"phone": to, "emoji": emoji})
        if resp.status_code >= 400:
            logger.error("Gowa react failed (%s): %s", resp.status_code, resp.text)
            return False
        return True
