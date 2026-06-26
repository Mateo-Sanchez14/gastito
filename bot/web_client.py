"""HTTP client for the gastito web app's /api/bot/* ingestion endpoints."""

from __future__ import annotations

import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)


class WebClient:
    def __init__(self, timeout: int = 15):
        self.base_url = settings.web_ingest_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {settings.bot_ingest_secret}"}
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        return httpx.get(
            f"{self.base_url}{path}", params=params, headers=self._headers, timeout=self.timeout
        )

    def _post(self, path: str, json: dict) -> httpx.Response:
        return httpx.post(
            f"{self.base_url}{path}", json=json, headers=self._headers, timeout=self.timeout
        )

    def _patch(self, path: str, json: dict) -> httpx.Response:
        return httpx.patch(
            f"{self.base_url}{path}", json=json, headers=self._headers, timeout=self.timeout
        )

    def _delete(self, path: str, params: dict) -> httpx.Response:
        return httpx.request(
            "DELETE",
            f"{self.base_url}{path}",
            params=params,
            headers=self._headers,
            timeout=self.timeout,
        )

    # --- links -------------------------------------------------------------
    def get_link(self, chat_id: str) -> dict | None:
        resp = self._get("/link", {"chatId": chat_id})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()["link"]

    def set_fx_source(self, chat_id: str, source: str) -> dict:
        resp = self._patch("/link", {"chatId": chat_id, "fxArsSource": source})
        resp.raise_for_status()
        return resp.json()["link"]

    # --- members -----------------------------------------------------------
    def get_members(self, chat_id: str) -> list[dict]:
        resp = self._get("/members", {"chatId": chat_id})
        resp.raise_for_status()
        return resp.json()["members"]

    def upsert_member(
        self, chat_id: str, sender_jid: str, participant_id: str, display_name: str | None
    ) -> dict:
        resp = self._post(
            "/members",
            {
                "chatId": chat_id,
                "senderJid": sender_jid,
                "participantId": participant_id,
                "displayName": display_name,
            },
        )
        resp.raise_for_status()
        return resp.json()["member"]

    # --- groups ------------------------------------------------------------
    def get_participants(self, group_id: str) -> dict:
        resp = self._get(f"/groups/{group_id}/participants")
        resp.raise_for_status()
        return resp.json()

    def get_balances(self, group_id: str) -> dict:
        resp = self._get(f"/groups/{group_id}/balances")
        resp.raise_for_status()
        return resp.json()

    # --- expenses ----------------------------------------------------------
    def create_expense(self, payload: dict) -> dict:
        resp = self._post("/expenses", payload)
        if resp.status_code >= 400:
            logger.error("create_expense failed (%s): %s", resp.status_code, resp.text)
            resp.raise_for_status()
        return resp.json()

    def update_expense(self, expense_id: str, payload: dict) -> dict:
        resp = self._patch(f"/expenses/{expense_id}", payload)
        if resp.status_code >= 400:
            logger.error("update_expense failed (%s): %s", resp.status_code, resp.text)
            resp.raise_for_status()
        return resp.json()

    # --- reply-to-edit: message <-> expense links --------------------------
    def get_expense_by_message(self, message_id: str) -> dict | None:
        """Resolve a quoted message id to its expense, or None if not linked."""
        resp = self._get("/message-refs", {"messageId": message_id})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()["expense"]

    def record_message_ref(self, message_id: str, expense_id: str) -> bool:
        """Link a WhatsApp message id to an expense. Returns True if newly
        created, False if it was already recorded (idempotency lock)."""
        resp = self._post(
            "/message-refs", {"messageId": message_id, "expenseId": expense_id}
        )
        if resp.status_code >= 400:
            logger.error("record_message_ref failed (%s): %s", resp.status_code, resp.text)
            return False
        return bool(resp.json().get("created"))

    def delete_expense(self, expense_id: str, group_id: str, participant_id: str | None) -> dict:
        params = {"groupId": group_id}
        if participant_id:
            params["participantId"] = participant_id
        resp = self._delete(f"/expenses/{expense_id}", params)
        resp.raise_for_status()
        return resp.json()

    def get_last_expense(self, group_id: str, participant_id: str) -> dict | None:
        resp = self._get(
            "/last-expense", {"groupId": group_id, "participantId": participant_id}
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()["expense"]
