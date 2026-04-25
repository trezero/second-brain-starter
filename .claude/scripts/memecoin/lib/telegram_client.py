"""Minimal Telegram Bot API client — unsolicited push to a single chat_id."""
from __future__ import annotations

import requests

DEFAULT_TIMEOUT = 15.0


class TelegramError(RuntimeError):
    """Raised when the Telegram API returns a non-ok response."""


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, *, timeout: float = DEFAULT_TIMEOUT):
        self._base = f"https://api.telegram.org/bot{bot_token}"
        self._chat_id = chat_id
        self._timeout = timeout

    def send_message(self, text: str, *, parse_mode: str = "Markdown",
                     disable_web_page_preview: bool = True) -> dict:
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        resp = requests.post(f"{self._base}/sendMessage", json=payload, timeout=self._timeout)
        if not resp.ok:
            raise TelegramError(f"sendMessage -> {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        if not body.get("ok"):
            raise TelegramError(f"sendMessage not-ok: {body}")
        return body["result"]
