"""Minimal Trello REST client for the Atlas Task Board.

The local Trello MCP server (127.0.0.1:8009) is unreachable from the remote
scheduled agent. This client talks to the Trello REST API directly using the
key/token stashed in the project ``.env``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import requests

API_BASE = "https://api.trello.com/1"
DEFAULT_TIMEOUT = 15.0


@dataclass(frozen=True)
class TrelloCard:
    id: str
    name: str
    desc: str
    list_id: str
    url: str


class TrelloError(RuntimeError):
    """Raised when the Trello API returns a non-2xx response."""


class TrelloClient:
    def __init__(self, api_key: str, token: str, *, timeout: float = DEFAULT_TIMEOUT):
        self._auth = {"key": api_key, "token": token}
        self._timeout = timeout

    def _request(self, method: str, path: str, *, params=None, json=None) -> dict | list:
        merged_params = dict(self._auth)
        if params:
            merged_params.update(params)
        url = f"{API_BASE}{path}"
        resp = requests.request(method, url, params=merged_params, json=json, timeout=self._timeout)
        if not resp.ok:
            raise TrelloError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    def list_cards(self, list_id: str) -> list[TrelloCard]:
        rows = self._request("GET", f"/lists/{list_id}/cards", params={"fields": "name,desc,idList,url"})
        return [TrelloCard(id=r["id"], name=r["name"], desc=r.get("desc", ""),
                           list_id=r["idList"], url=r["url"]) for r in rows]

    def find_card_on_board(self, board_id: str, name: str) -> TrelloCard | None:
        """Linear scan of open cards on the board for an exact name match."""
        rows = self._request("GET", f"/boards/{board_id}/cards",
                             params={"fields": "name,desc,idList,url", "filter": "open"})
        for r in rows:
            if r["name"] == name:
                return TrelloCard(id=r["id"], name=r["name"], desc=r.get("desc", ""),
                                  list_id=r["idList"], url=r["url"])
        return None

    def create_card(self, list_id: str, name: str, desc: str = "",
                    label_ids: Iterable[str] = ()) -> TrelloCard:
        params = {"idList": list_id, "name": name, "desc": desc}
        ids = ",".join(label_ids)
        if ids:
            params["idLabels"] = ids
        r = self._request("POST", "/cards", params=params)
        return TrelloCard(id=r["id"], name=r["name"], desc=r.get("desc", ""),
                          list_id=r["idList"], url=r["url"])

    def update_card(self, card_id: str, *, name: str | None = None,
                    desc: str | None = None, list_id: str | None = None) -> TrelloCard:
        params: dict[str, str] = {}
        if name is not None:
            params["name"] = name
        if desc is not None:
            params["desc"] = desc
        if list_id is not None:
            params["idList"] = list_id
        if not params:
            raise ValueError("update_card called with no fields to update")
        r = self._request("PUT", f"/cards/{card_id}", params=params)
        return TrelloCard(id=r["id"], name=r["name"], desc=r.get("desc", ""),
                          list_id=r["idList"], url=r["url"])

    def add_comment(self, card_id: str, text: str) -> dict:
        return self._request("POST", f"/cards/{card_id}/actions/comments",
                             params={"text": text})  # type: ignore[return-value]
