"""Unit tests for memecoin config loading."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from memecoin.lib.config import ConfigError, load_config  # noqa: E402

_REQUIRED = (
    "ANTHROPIC_API_KEY",
    "GITHUB_PAT_MEMECOININVESTOR",
    "TRELLO_API_KEY",
    "TRELLO_TOKEN",
    "TRELLO_ATLAS_BOARD_ID",
    "TRELLO_ATLAS_LIST_INBOX",
    "TRELLO_ATLAS_LIST_NEXT",
    "TRELLO_ATLAS_LIST_IN_PROGRESS",
    "TRELLO_ATLAS_LIST_WAITING",
    "TRELLO_ATLAS_LIST_DONE",
    "TRELLO_ATLAS_LABEL_ATLAS_OPS",
    "TRELLO_ATLAS_LABEL_PRODUCT_ENG",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)


def _set_all(monkeypatch):
    for name in _REQUIRED:
        monkeypatch.setenv(name, f"value_for_{name}")


def test_load_config_returns_typed_object_when_all_present(tmp_path, monkeypatch):
    _set_all(monkeypatch)
    cfg = load_config(env_path=tmp_path / ".env_does_not_exist")
    assert cfg.github_repo == "trezero/MemeCoinInvestor2026"
    assert cfg.flush_model.startswith("claude-")
    assert "trezero/MemeCoinInvestor2026" in cfg.github_clone_url
    assert "x-access-token" in cfg.github_clone_url


def test_load_config_respects_repo_override(tmp_path, monkeypatch):
    _set_all(monkeypatch)
    monkeypatch.setenv("GITHUB_REPO_MEMECOININVESTOR", "trezero/SomeOtherRepo")
    cfg = load_config(env_path=tmp_path / ".env_does_not_exist")
    assert cfg.github_repo == "trezero/SomeOtherRepo"
    assert cfg.github_clone_url.endswith("trezero/SomeOtherRepo.git")


@pytest.mark.parametrize("missing", _REQUIRED)
def test_load_config_raises_for_each_missing_required_var(tmp_path, monkeypatch, missing):
    _set_all(monkeypatch)
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(ConfigError, match=missing):
        load_config(env_path=tmp_path / ".env_does_not_exist")


def test_load_config_treats_blank_values_as_missing(tmp_path, monkeypatch):
    _set_all(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        load_config(env_path=tmp_path / ".env_does_not_exist")
