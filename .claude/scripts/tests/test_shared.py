"""Unit tests for shared.py utilities."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import shared


def test_atomic_write_creates_file(tmp_path):
    target = tmp_path / "state.json"
    shared.atomic_write(target, '{"hello":"world"}')
    assert target.read_text() == '{"hello":"world"}'


def test_atomic_write_overwrites_existing(tmp_path):
    target = tmp_path / "state.json"
    target.write_text("old content")
    shared.atomic_write(target, "new content")
    assert target.read_text() == "new content"


def test_atomic_write_creates_parent_dirs(tmp_path):
    target = tmp_path / "a" / "b" / "c.json"
    shared.atomic_write(target, "hi")
    assert target.read_text() == "hi"


def test_atomic_write_leaves_no_tmp_file(tmp_path):
    target = tmp_path / "state.json"
    shared.atomic_write(target, "payload")
    assert not (tmp_path / "state.json.tmp").exists()


def test_pt_now_is_timezone_aware():
    now = shared.pt_now()
    assert now.tzinfo is not None
    assert now.utcoffset() is not None


def test_pt_now_is_in_los_angeles():
    now = shared.pt_now()
    # The tzinfo should be ZoneInfo("America/Los_Angeles")
    assert str(now.tzinfo) == "America/Los_Angeles"


def test_pt_today_str_format():
    s = shared.pt_today_str()
    # Should parse as a date in YYYY-MM-DD format
    datetime.strptime(s, "%Y-%m-%d")


def test_pt_today_str_uses_pt_date(monkeypatch):
    """At 01:00 UTC the PT date is still the previous day."""
    fixed_utc = datetime(2026, 4, 24, 1, 0, 0, tzinfo=ZoneInfo("UTC"))

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_utc.replace(tzinfo=None)
            return fixed_utc.astimezone(tz)

    monkeypatch.setattr(shared, "datetime", FrozenDatetime)
    assert shared.pt_today_str() == "2026-04-23"
