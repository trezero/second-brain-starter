"""Centralized env-var loading for the MemeCoin daily agent.

All secrets and IDs live in the project ``.env`` (gitignored). This module
loads them once and exposes a typed accessor; downstream modules import the
``Config`` instance rather than calling ``os.environ`` themselves.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ENV_PATH = REPO_ROOT / ".env"


class ConfigError(RuntimeError):
    """Raised when a required env var is missing."""


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str
    github_pat: str
    github_repo: str          # e.g. "trezero/MemeCoinInvestor2026"
    trello_api_key: str
    trello_token: str
    trello_board_id: str
    trello_list_inbox: str
    trello_list_next: str
    trello_list_in_progress: str
    trello_list_waiting: str
    trello_list_done: str
    trello_label_atlas_ops: str
    trello_label_product_eng: str
    telegram_bot_token: str
    telegram_chat_id: str
    flush_model: str
    memecoin_database_url: str    # may be empty → P&L extractor reports as blocker
    memecoin_user_id: str         # may be empty → P&L extractor reports as blocker

    @property
    def github_clone_url(self) -> str:
        return f"https://x-access-token:{self.github_pat}@github.com/{self.github_repo}.git"


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise ConfigError(f"Missing required env var: {name}")
    return val


def load_config(env_path: Path | str | None = None) -> Config:
    """Load and validate environment configuration.

    ``env_path`` defaults to ``<repo-root>/.env``. Re-loading is allowed but
    ``override`` is False so already-set process env vars win.
    """
    env_path = Path(env_path) if env_path else DEFAULT_ENV_PATH
    if env_path.exists():
        load_dotenv(env_path, override=False)
    return Config(
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        github_pat=_require("GITHUB_PAT_MEMECOININVESTOR"),
        github_repo=os.environ.get("GITHUB_REPO_MEMECOININVESTOR", "trezero/MemeCoinInvestor2026"),
        trello_api_key=_require("TRELLO_API_KEY"),
        trello_token=_require("TRELLO_TOKEN"),
        trello_board_id=_require("TRELLO_ATLAS_BOARD_ID"),
        trello_list_inbox=_require("TRELLO_ATLAS_LIST_INBOX"),
        trello_list_next=_require("TRELLO_ATLAS_LIST_NEXT"),
        trello_list_in_progress=_require("TRELLO_ATLAS_LIST_IN_PROGRESS"),
        trello_list_waiting=_require("TRELLO_ATLAS_LIST_WAITING"),
        trello_list_done=_require("TRELLO_ATLAS_LIST_DONE"),
        trello_label_atlas_ops=_require("TRELLO_ATLAS_LABEL_ATLAS_OPS"),
        trello_label_product_eng=_require("TRELLO_ATLAS_LABEL_PRODUCT_ENG"),
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_require("TELEGRAM_CHAT_ID"),
        flush_model=os.environ.get("SECOND_BRAIN_FLUSH_MODEL", "claude-sonnet-4-6"),
        memecoin_database_url=os.environ.get("DATABASE_URL_MEMECOIN", "").strip(),
        memecoin_user_id=os.environ.get("MEMECOIN_USER_ID", "").strip(),
    )
