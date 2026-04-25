"""Unit tests for the P&L extractor's input validation + DB error handling."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from memecoin.lib import pnl_extractor  # noqa: E402
from memecoin.lib.pnl_extractor import PnlExtractionError, extract_pnl  # noqa: E402


def test_raises_on_empty_database_url():
    with pytest.raises(PnlExtractionError, match="DATABASE_URL_MEMECOIN is empty"):
        extract_pnl("", "user-id")


def test_raises_on_whitespace_database_url():
    with pytest.raises(PnlExtractionError, match="DATABASE_URL_MEMECOIN is empty"):
        extract_pnl("   ", "user-id")


def test_raises_on_empty_user_id():
    with pytest.raises(PnlExtractionError, match="MEMECOIN_USER_ID is empty"):
        extract_pnl("postgres://x:y@h/d", "")


def test_db_unreachable_surfaces_as_extraction_error(monkeypatch):
    """A real DB query failure must come back as PnlExtractionError, not a raw exception."""
    import psycopg

    class _FakeError(psycopg.Error):
        pass

    def _explode(*_args, **_kwargs):
        raise _FakeError("simulated network failure")

    monkeypatch.setattr(psycopg, "connect", _explode)
    with pytest.raises(PnlExtractionError, match="DB query failed"):
        extract_pnl("postgres://x:y@h/d", "user-id")


def test_psycopg_missing_raises_clear_error(monkeypatch):
    """If psycopg isn't installed, the error message should point to fix."""
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "psycopg":
            raise ImportError("No module named 'psycopg'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    with pytest.raises(PnlExtractionError, match="psycopg not installed"):
        extract_pnl("postgres://x:y@h/d", "user-id")
