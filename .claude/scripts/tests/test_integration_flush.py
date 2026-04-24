"""Integration tests for memory_flush.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import memory_flush


FIXTURES = Path(__file__).parent / "fixtures"


def test_load_transcript_extracts_user_and_assistant_text():
    path = FIXTURES / "short_transcript.jsonl"
    text = memory_flush.load_transcript(path)
    assert "Persalto landing page copy by Friday" in text
    assert "architecture doc update" in text
    assert "May 10" in text
    # Tool-use blocks should be reduced to placeholders, not raw JSON
    assert "[tool: Read]" in text
    assert "tool_use_id" not in text


def test_load_transcript_missing_file_returns_empty(tmp_path):
    assert memory_flush.load_transcript(tmp_path / "does_not_exist.jsonl") == ""


def test_load_transcript_empty_file_returns_empty(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert memory_flush.load_transcript(empty) == ""


def test_load_transcript_skips_malformed_lines(tmp_path):
    mixed = tmp_path / "mixed.jsonl"
    mixed.write_text(
        '{"role":"user","content":"valid one"}\n'
        "not valid json\n"
        '{"role":"assistant","content":[{"type":"text","text":"valid two"}]}\n'
    )
    text = memory_flush.load_transcript(mixed)
    assert "valid one" in text
    assert "valid two" in text
    assert "not valid json" not in text


# ---------------------------------------------------------------------------
# Dedup state
# ---------------------------------------------------------------------------


def test_dedup_first_flush_is_allowed(tmp_path):
    state_path = tmp_path / "flush-state.json"
    allowed = memory_flush.check_and_update_dedup(
        state_path, session_id="s1", source="SessionEnd", window_seconds=60,
    )
    assert allowed is True
    data = json.loads(state_path.read_text())
    assert "s1" in data["sessions"]
    assert data["sessions"]["s1"]["last_flush_source"] == "SessionEnd"


def test_dedup_within_window_is_blocked(tmp_path):
    state_path = tmp_path / "flush-state.json"
    assert memory_flush.check_and_update_dedup(
        state_path, "s1", "PreCompact", window_seconds=60,
    ) is True
    # Immediate retry within the window is blocked
    assert memory_flush.check_and_update_dedup(
        state_path, "s1", "SessionEnd", window_seconds=60,
    ) is False


def test_dedup_after_window_is_allowed(tmp_path, monkeypatch):
    state_path = tmp_path / "flush-state.json"

    # First flush at t=0
    import shared
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    base = datetime(2026, 4, 24, 14, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles"))

    monkeypatch.setattr(shared, "pt_now", lambda: base)
    assert memory_flush.check_and_update_dedup(
        state_path, "s1", "PreCompact", window_seconds=60,
    ) is True

    # 120 seconds later — outside the 60s window
    monkeypatch.setattr(shared, "pt_now", lambda: base + timedelta(seconds=120))
    assert memory_flush.check_and_update_dedup(
        state_path, "s1", "SessionEnd", window_seconds=60,
    ) is True


def test_dedup_prunes_entries_older_than_24h(tmp_path, monkeypatch):
    import shared
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    state_path = tmp_path / "flush-state.json"
    base = datetime(2026, 4, 24, 14, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles"))

    # Seed a state file with an old entry
    ancient = base - timedelta(hours=30)
    state_path.write_text(json.dumps({
        "sessions": {
            "ancient": {
                "last_flush_at": ancient.isoformat(),
                "last_flush_source": "SessionEnd",
            }
        }
    }))

    monkeypatch.setattr(shared, "pt_now", lambda: base)
    memory_flush.check_and_update_dedup(
        state_path, "fresh", "PreCompact", window_seconds=60,
    )

    data = json.loads(state_path.read_text())
    assert "ancient" not in data["sessions"]
    assert "fresh" in data["sessions"]


# ---------------------------------------------------------------------------
# Daily log append
# ---------------------------------------------------------------------------


def test_append_daily_log_creates_file_with_header(tmp_path, monkeypatch):
    import shared
    from datetime import datetime
    from zoneinfo import ZoneInfo
    fixed = datetime(2026, 4, 24, 14, 32, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    monkeypatch.setattr(shared, "pt_now", lambda: fixed)

    daily_dir = tmp_path / "daily"
    memory_flush.append_daily_log(
        daily_dir=daily_dir,
        session_id="abc123de45",
        source="SessionEnd",
        bullets="- Decided to prioritize landing page\n- Eric requested arch doc",
    )

    log_path = daily_dir / "2026-04-24.md"
    content = log_path.read_text()
    assert "# Daily Log — 2026-04-24" in content
    assert "## 14:32 PT — Flush from SessionEnd (session abc123de)" in content
    assert "- Decided to prioritize landing page" in content
    assert content.rstrip().endswith("---")


def test_append_daily_log_appends_to_existing(tmp_path, monkeypatch):
    import shared
    from datetime import datetime
    from zoneinfo import ZoneInfo
    fixed = datetime(2026, 4, 24, 18, 5, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    monkeypatch.setattr(shared, "pt_now", lambda: fixed)

    daily_dir = tmp_path / "daily"
    daily_dir.mkdir()
    existing = daily_dir / "2026-04-24.md"
    existing.write_text("# Daily Log — 2026-04-24\n\nManual note from earlier.\n")

    memory_flush.append_daily_log(
        daily_dir=daily_dir,
        session_id="newsess999",
        source="PreCompact",
        bullets="- New decision made",
    )

    content = existing.read_text()
    assert content.startswith("# Daily Log — 2026-04-24")
    assert "Manual note from earlier." in content
    assert "## 18:05 PT — Flush from PreCompact (session newsess9)" in content
    assert "- New decision made" in content
