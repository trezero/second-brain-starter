"""Unit tests for shared.py utilities."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import time

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


# ---------------------------------------------------------------------------
# with_retry
# ---------------------------------------------------------------------------


class _FakeApiError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def test_with_retry_returns_immediately_on_success():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    t0 = time.monotonic()
    result = shared.with_retry(fn)
    elapsed = time.monotonic() - t0

    assert result == "ok"
    assert calls["n"] == 1
    assert elapsed < 0.5


def test_with_retry_retries_on_429(monkeypatch):
    sleeps = []
    monkeypatch.setattr(shared.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _FakeApiError(429)
        return "done"

    result = shared.with_retry(fn, base_delay=0.01, max_retries=5)

    assert result == "done"
    assert calls["n"] == 3
    assert len(sleeps) == 2
    # Base delay 0.01, exponential: ~0.01 then ~0.02 (plus up to 10% jitter).
    assert 0.009 <= sleeps[0] <= 0.012
    assert 0.019 <= sleeps[1] <= 0.023


def test_with_retry_does_not_retry_on_non_matching_exception(monkeypatch):
    monkeypatch.setattr(shared.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("not a transient api error")

    with pytest.raises(ValueError):
        shared.with_retry(fn)

    assert calls["n"] == 1


def test_with_retry_reraises_after_max_retries(monkeypatch):
    monkeypatch.setattr(shared.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _FakeApiError(503)

    with pytest.raises(_FakeApiError):
        shared.with_retry(fn, max_retries=2, base_delay=0.001)

    assert calls["n"] == 3  # initial attempt + 2 retries
