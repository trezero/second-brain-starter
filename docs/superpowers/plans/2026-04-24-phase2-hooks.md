# Phase 2: Hooks (Context Persistence) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire three Claude Code lifecycle hooks (SessionStart, PreCompact, SessionEnd), a background Agent SDK summarizer (`memory_flush.py`), and cross-platform concurrency utilities (`shared.py`) so that every session auto-loads vault context and every compaction/end flushes decisions into the daily log.

**Architecture:** SessionStart reads the vault markdown files and emits them via `hookSpecificOutput.additionalContext`. PreCompact and SessionEnd each `Popen` `memory_flush.py` detached, exit fast, and let the flush summarize the conversation in the background via the Anthropic API (`allowed_tools=[]` for safety). `shared.py` provides `file_lock`, `with_retry`, `atomic_write`, and PT datetime helpers used by every writer to keep the state directory and daily logs corruption-free under concurrent access.

**Tech Stack:** Python 3.10+ (for `zoneinfo`), `claude-agent-sdk`, `python-dotenv`, `pytest`, stdlib `fcntl` / `msvcrt` for file locking, stdlib `multiprocessing` for stress testing. Isolated virtualenv at `.claude/.venv/`.

---

## Source Spec

`docs/superpowers/specs/2026-04-24-phase2-hooks-design.md`

## File Map

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `.claude/scripts/requirements.txt` | Pinned deps |
| Create | `.env.example` | Env var template |
| Create | `.claude/scripts/shared.py` | `file_lock`, `with_retry`, `atomic_write`, PT helpers |
| Create | `.claude/scripts/tests/__init__.py` | Test package marker |
| Create | `.claude/scripts/tests/conftest.py` | Shared pytest fixtures |
| Create | `.claude/scripts/tests/test_shared.py` | Unit tests for shared utilities |
| Create | `.claude/scripts/tests/test_stress_file_lock.py` | Multi-process contention stress test |
| Create | `.claude/scripts/memory_flush.py` | Background Agent SDK summarizer |
| Create | `.claude/scripts/tests/test_integration_flush.py` | End-to-end canned-transcript test |
| Create | `.claude/scripts/tests/fixtures/short_transcript.jsonl` | Small transcript fixture |
| Create | `.claude/scripts/tests/fixtures/large_transcript.jsonl` | Large transcript fixture (triggers chunking) |
| Create | `.claude/hooks/session-start-context.py` | SessionStart — inject vault context |
| Create | `.claude/hooks/pre-compact-flush.py` | PreCompact — spawn flush, exit fast |
| Create | `.claude/hooks/session-end-flush.py` | SessionEnd — spawn flush, exit fast |
| Create | `.claude/settings.json` | Hook registrations |
| Modify | `.gitignore` | Add `.claude/.venv/`, `flush.log` |
| Modify | `CLAUDE.md` | Phase 2 build commands + completion marker |

---

### Task 1: Virtualenv + dependencies + env template

**Files:**
- Create: `.claude/scripts/requirements.txt`
- Create: `.env.example`
- Modify: `.gitignore`

- [ ] **Step 1: Create `.claude/scripts/requirements.txt`**

```
claude-agent-sdk>=0.1.0
python-dotenv>=1.0.1
pytest>=8.0.0
```

- [ ] **Step 2: Create the virtualenv and install deps**

Run:

```bash
python3 -m venv .claude/.venv
.claude/.venv/bin/pip install --upgrade pip
.claude/.venv/bin/pip install -r .claude/scripts/requirements.txt
```

Expected: `pip install` ends with `Successfully installed ...` listing `claude-agent-sdk`, `python-dotenv`, `pytest`.

- [ ] **Step 3: Verify imports**

Run:

```bash
.claude/.venv/bin/python3 -c "import claude_agent_sdk, dotenv, pytest; print('ok')"
```

Expected output: `ok`

- [ ] **Step 4: Create `.env.example` at repo root**

```
# Anthropic API key — required for .claude/scripts/memory_flush.py
ANTHROPIC_API_KEY=

# Optional overrides
# SECOND_BRAIN_FLUSH_MODEL=claude-sonnet-4-6
```

- [ ] **Step 5: Append virtualenv and flush log patterns to `.gitignore`**

Append these lines to the end of the existing `.gitignore`:

```
# Python virtualenv
.claude/.venv/

# Flush outcome log (regenerable)
.claude/data/state/flush.log
```

- [ ] **Step 6: Verify the venv is excluded from git**

Run:

```bash
git status --short .claude/.venv/ || true
git check-ignore -v .claude/.venv/bin/python3
```

Expected: `git status` shows no `.claude/.venv/` entries. `git check-ignore` prints a line confirming the ignore rule matches.

- [ ] **Step 7: Commit**

```bash
git add .claude/scripts/requirements.txt .env.example .gitignore
git commit -m "feat(phase2): scaffold venv, deps, env template"
```

---

### Task 2: `shared.py` — atomic_write + PT helpers (TDD)

**Files:**
- Create: `.claude/scripts/tests/__init__.py`
- Create: `.claude/scripts/tests/conftest.py`
- Create: `.claude/scripts/tests/test_shared.py`
- Create: `.claude/scripts/shared.py`

- [ ] **Step 1: Create empty test package marker**

Create `.claude/scripts/tests/__init__.py` as an empty file.

- [ ] **Step 2: Create `.claude/scripts/tests/conftest.py` with path fixup**

```python
"""Shared pytest fixtures. Makes shared.py importable without installing as a package."""
import sys
from pathlib import Path

# Ensure .claude/scripts is on sys.path so tests can import shared, memory_flush, etc.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
```

- [ ] **Step 3: Write the failing tests in `.claude/scripts/tests/test_shared.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they fail**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_shared.py -v
```

Expected: collection errors or `ModuleNotFoundError: No module named 'shared'`.

- [ ] **Step 5: Create `.claude/scripts/shared.py` with the atomic_write + PT helpers**

```python
"""Cross-platform concurrency utilities for Second Brain scripts.

Provides:
    - file_lock(path): exclusive advisory file lock (context manager)
    - with_retry(func, ...): exponential backoff + jitter for transient API errors
    - atomic_write(path, data): tmp-file-then-rename atomic write
    - pt_now() / pt_today_str(): Pacific Time helpers
"""
from contextlib import contextmanager
from datetime import datetime
import errno
import os
import platform
import random
import time
from pathlib import Path
from zoneinfo import ZoneInfo

if platform.system() == "Windows":  # pragma: no cover - exercised on Windows only
    import msvcrt
else:
    import fcntl

PT = ZoneInfo("America/Los_Angeles")


def atomic_write(path, data: str, encoding: str = "utf-8") -> None:
    """Write `data` to `path` atomically via tmp + os.replace.

    Creates parent directories as needed. Fsyncs before rename so a crash
    cannot leave a partially-written file at the target path.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding=encoding) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def pt_now() -> datetime:
    """Current time in America/Los_Angeles (timezone-aware)."""
    return datetime.now(PT)


def pt_today_str() -> str:
    """'YYYY-MM-DD' in Pacific Time."""
    return pt_now().strftime("%Y-%m-%d")
```

- [ ] **Step 6: Run tests to verify they pass**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_shared.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add .claude/scripts/shared.py .claude/scripts/tests/
git commit -m "feat(phase2): add shared.atomic_write and PT datetime helpers with tests"
```

---

### Task 3: `shared.py` — with_retry (TDD)

**Files:**
- Modify: `.claude/scripts/tests/test_shared.py`
- Modify: `.claude/scripts/shared.py`

- [ ] **Step 1: Append with_retry tests to `.claude/scripts/tests/test_shared.py`**

Append at the end of the file:

```python
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
```

Also, at the top of the file, add `import time` (next to the existing imports).

- [ ] **Step 2: Run tests to verify the new ones fail**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_shared.py -v
```

Expected: previously-passing tests still pass; the four new `with_retry` tests FAIL with `AttributeError: module 'shared' has no attribute 'with_retry'`.

- [ ] **Step 3: Add `with_retry` to `.claude/scripts/shared.py`**

Insert the following function definition after `atomic_write` and before `pt_now`:

```python
def with_retry(func, *, max_retries: int = 3, base_delay: float = 1.0,
               max_delay: float = 30.0,
               retry_on_status=(429, 500, 502, 503, 504)):
    """Call ``func()``, retrying on transient failures with exponential backoff + jitter.

    ``func`` must take no arguments. Retries on:
        - any exception with a ``.status_code`` in ``retry_on_status``
        - any exception with a ``.status`` in ``retry_on_status`` (SDK variant)
        - any ``OSError`` whose ``errno`` is ECONNRESET or ETIMEDOUT

    Returns the function's return value on success. Re-raises the final
    exception after ``max_retries`` retries.
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            if attempt >= max_retries:
                raise
            status = getattr(e, "status_code", None)
            if status is None:
                status = getattr(e, "status", None)
            transient = status in retry_on_status
            if not transient and isinstance(e, OSError) and e.errno in (
                errno.ECONNRESET, errno.ETIMEDOUT,
            ):
                transient = True
            if not transient:
                raise
            delay = min(max_delay, base_delay * (2 ** attempt))
            delay += random.uniform(0, delay * 0.1)  # up to 10% jitter
            time.sleep(delay)
```

- [ ] **Step 4: Run tests to verify all pass**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_shared.py -v
```

Expected: all 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add .claude/scripts/shared.py .claude/scripts/tests/test_shared.py
git commit -m "feat(phase2): add shared.with_retry with backoff + jitter"
```

---

### Task 4: `shared.py` — file_lock basic behavior (TDD)

**Files:**
- Modify: `.claude/scripts/tests/test_shared.py`
- Modify: `.claude/scripts/shared.py`

- [ ] **Step 1: Append file_lock tests to `.claude/scripts/tests/test_shared.py`**

Append at the end of the file:

```python
# ---------------------------------------------------------------------------
# file_lock (basic behavior; concurrency stress is in test_stress_file_lock.py)
# ---------------------------------------------------------------------------


def test_file_lock_creates_file_if_missing(tmp_path):
    target = tmp_path / "new_lock"
    assert not target.exists()
    with shared.file_lock(target):
        pass
    assert target.exists()


def test_file_lock_acquires_and_releases(tmp_path):
    target = tmp_path / "lockme"
    # First acquire/release
    with shared.file_lock(target):
        pass
    # Second acquire should succeed immediately (lock was released)
    t0 = time.monotonic()
    with shared.file_lock(target, timeout=1.0):
        pass
    assert (time.monotonic() - t0) < 0.5


def test_file_lock_times_out_when_held_by_another_process(tmp_path):
    """Spawn a child that holds the lock for 2s; parent's attempt with
    timeout=0.3 should raise TimeoutError in ~0.3s."""
    import subprocess
    import sys as _sys

    target = tmp_path / "contended"
    # Touch the file first so both processes lock the same existing inode
    target.touch()

    script = (
        "import sys, time; sys.path.insert(0, {scripts!r}); import shared; "
        "f = open({target!r}, 'r+'); "
        "import fcntl; fcntl.flock(f, fcntl.LOCK_EX); "
        "print('LOCKED', flush=True); time.sleep(2.0)"
    ).format(
        scripts=str(Path(shared.__file__).parent),
        target=str(target),
    )

    child = subprocess.Popen(
        [_sys.executable, "-c", script],
        stdout=subprocess.PIPE,
    )
    try:
        # Wait until the child signals it has acquired the lock
        line = child.stdout.readline()
        assert line.strip() == b"LOCKED"

        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            with shared.file_lock(target, timeout=0.3, poll_interval=0.05):
                pass
        elapsed = time.monotonic() - t0
        # Allow generous upper bound for CI jitter
        assert 0.25 <= elapsed <= 1.5
    finally:
        child.kill()
        child.wait()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_shared.py::test_file_lock_creates_file_if_missing -v
```

Expected: `AttributeError: module 'shared' has no attribute 'file_lock'`.

- [ ] **Step 3: Add `file_lock` to `.claude/scripts/shared.py`**

Insert the following after `with_retry` and before `pt_now`. Also add `from contextlib import contextmanager` to the imports at the top of the file if not already present.

```python
@contextmanager
def file_lock(path, timeout: float = 30.0, poll_interval: float = 0.1):
    """Acquire an exclusive advisory lock on ``path``.

    Creates the file (zero-byte) if it does not exist. Polls up to
    ``timeout`` seconds for the lock, then raises ``TimeoutError``.

    On Unix uses ``fcntl.flock`` with ``LOCK_EX | LOCK_NB``. On Windows
    uses ``msvcrt.locking`` with ``LK_NBLCK``. Both are advisory — all
    writers must cooperate by using this helper.
    """
    Path(path).touch(exist_ok=True)
    fd = os.open(str(path), os.O_RDWR)
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while True:
            try:
                if platform.system() == "Windows":  # pragma: no cover
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (BlockingIOError, OSError) as e:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"file_lock timeout after {timeout}s on {path}"
                    ) from e
                time.sleep(poll_interval)
        yield fd
    finally:
        try:
            if acquired:
                if platform.system() == "Windows":  # pragma: no cover
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
```

- [ ] **Step 4: Run tests to verify all pass**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_shared.py -v
```

Expected: all 15 tests PASS (including the three new `file_lock` tests).

- [ ] **Step 5: Commit**

```bash
git add .claude/scripts/shared.py .claude/scripts/tests/test_shared.py
git commit -m "feat(phase2): add shared.file_lock cross-platform advisory lock"
```

---

### Task 5: Concurrent contention stress test for `file_lock`

**Files:**
- Create: `.claude/scripts/tests/test_stress_file_lock.py`

- [ ] **Step 1: Create `.claude/scripts/tests/test_stress_file_lock.py`**

```python
"""Real-contention stress test for shared.file_lock + shared.atomic_write.

Spawns N worker processes. Each performs M read-modify-write iterations on a
shared counter file under a file lock. The final value must equal N*M exactly;
any off-by-one indicates the lock is not serializing writers correctly.
"""
from __future__ import annotations

import json
import multiprocessing as mp
from pathlib import Path

import pytest

import shared

WORKERS = 20
ITERATIONS = 100
EXPECTED_FINAL = WORKERS * ITERATIONS  # 2000


def _worker(counter_path_str: str, lock_path_str: str, iterations: int) -> int:
    """Run a tight read-modify-write loop. Returns the count this worker saw."""
    counter_path = Path(counter_path_str)
    lock_path = Path(lock_path_str)
    own_increments = 0
    for _ in range(iterations):
        with shared.file_lock(lock_path, timeout=60.0, poll_interval=0.01):
            if counter_path.exists():
                current = json.loads(counter_path.read_text())["count"]
            else:
                current = 0
            current += 1
            shared.atomic_write(counter_path, json.dumps({"count": current}))
            own_increments += 1
    return own_increments


@pytest.mark.parametrize("run", range(5))
def test_file_lock_serializes_concurrent_writers(tmp_path, run):
    """Run the stress test five times to shake out flakes."""
    counter = tmp_path / "counter.json"
    lock = tmp_path / "counter.lock"

    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=WORKERS) as pool:
        results = pool.starmap(
            _worker,
            [(str(counter), str(lock), ITERATIONS)] * WORKERS,
        )

    # Each worker should have done exactly ITERATIONS increments
    assert sum(results) == EXPECTED_FINAL, f"Workers reported {sum(results)}"

    # The counter file should reflect all increments without loss
    final = json.loads(counter.read_text())["count"]
    assert final == EXPECTED_FINAL, (
        f"Expected {EXPECTED_FINAL} but counter shows {final} "
        f"(run={run}, lost {EXPECTED_FINAL - final} writes)"
    )

    # No tmp files left behind
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Leftover tmp files: {tmp_files}"
```

- [ ] **Step 2: Run the stress test**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_stress_file_lock.py -v
```

Expected: all 5 parametrized runs PASS. Each run may take ~3-10 seconds.

If any run reports a count less than 2000, the lock is not serializing writers and this must be diagnosed before proceeding (likely cause: `fcntl` semantics differ on this platform; rerun with `ps aux | grep python` to confirm workers are launching and check WSL2 is fully booted).

- [ ] **Step 3: Commit**

```bash
git add .claude/scripts/tests/test_stress_file_lock.py
git commit -m "test(phase2): add 20-worker contention stress test for file_lock"
```

---

### Task 6: `memory_flush.py` — transcript loading (TDD)

**Files:**
- Create: `.claude/scripts/tests/fixtures/short_transcript.jsonl`
- Create: `.claude/scripts/tests/test_integration_flush.py`
- Create: `.claude/scripts/memory_flush.py`

- [ ] **Step 1: Create the short transcript fixture**

Create `.claude/scripts/tests/fixtures/short_transcript.jsonl` with the following content (one JSON object per line):

```
{"role":"user","content":"I need to finalize the Persalto landing page copy by Friday."}
{"role":"assistant","content":[{"type":"text","text":"Noted — Persalto landing page due Friday. I'll start a draft from the investor narrative."}]}
{"role":"user","content":"Also, Eric is asking for the architecture doc update."}
{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"Read","input":{"path":"arch.md"}},{"type":"text","text":"Reading current arch doc for context."}]}
{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"# Arch\n..."}]}
{"role":"assistant","content":[{"type":"text","text":"I'll prep a diff that reflects the new memory layer."}]}
{"role":"user","content":"Thanks. Also Solace's Coca-Cola scholarship deadline moved to May 10."}
{"role":"assistant","content":[{"type":"text","text":"Updated mental model: Coca-Cola scholarship deadline now May 10."}]}
```

- [ ] **Step 2: Create `.claude/scripts/tests/test_integration_flush.py` with the transcript-loading test**

```python
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
```

- [ ] **Step 3: Run tests to see them fail**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_integration_flush.py -v
```

Expected: `ModuleNotFoundError: No module named 'memory_flush'`.

- [ ] **Step 4: Create `.claude/scripts/memory_flush.py` with the transcript loader**

```python
"""Background Agent SDK summarizer.

Spawned by .claude/hooks/pre-compact-flush.py and .claude/hooks/session-end-flush.py.
Reads a JSONL conversation transcript, asks Claude to extract items worth
remembering, and appends a sectioned block to today's daily log.

Usage:
    memory_flush.py --transcript <path> --session-id <id> --source <PreCompact|SessionEnd|manual>

NOTE: The CLAUDE_INVOKED_BY recursion guard is set inside main() (and
re-asserted in _call_curator), not at module import time — tests import this
module, and we don't want the guard to leak into the test process's
environment and skip unrelated spawner tests.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Iterable


def _extract_text_from_content(content) -> str:
    """Reduce a message's ``content`` field to plain text.

    Handles both string content and list-of-block content. Tool-use blocks
    become ``[tool: <name>]`` placeholders. Tool-result blocks are dropped
    entirely (the assistant's surrounding text carries the signal).
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            t = block.get("text", "")
            if t:
                parts.append(t)
        elif btype == "tool_use":
            name = block.get("name", "unknown")
            parts.append(f"[tool: {name}]")
        # tool_result blocks intentionally omitted — too noisy
    return "\n".join(parts)


def load_transcript(path) -> str:
    """Load a Claude Code JSONL transcript and return a flat text rendering.

    Each message becomes a ``<ROLE>: <text>`` line. Missing/empty/malformed
    files return an empty string. Malformed lines are silently skipped.
    """
    path = Path(path)
    if not path.exists():
        return ""
    rendered: list[str] = []
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = msg.get("role", "unknown").upper()
        text = _extract_text_from_content(msg.get("content", ""))
        if text.strip():
            rendered.append(f"{role}: {text}")
    return "\n\n".join(rendered)
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_integration_flush.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add .claude/scripts/memory_flush.py .claude/scripts/tests/test_integration_flush.py .claude/scripts/tests/fixtures/short_transcript.jsonl
git commit -m "feat(phase2): memory_flush.load_transcript — JSONL parsing with tool-block filtering"
```

---

### Task 7: `memory_flush.py` — dedup state (TDD)

**Files:**
- Modify: `.claude/scripts/tests/test_integration_flush.py`
- Modify: `.claude/scripts/memory_flush.py`

- [ ] **Step 1: Append dedup tests to `.claude/scripts/tests/test_integration_flush.py`**

Append at the end of the file:

```python
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
```

- [ ] **Step 2: Run tests to see the new ones fail**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_integration_flush.py -v
```

Expected: `AttributeError: module 'memory_flush' has no attribute 'check_and_update_dedup'`.

- [ ] **Step 3: Add `check_and_update_dedup` to `.claude/scripts/memory_flush.py`**

Append the following to `.claude/scripts/memory_flush.py`:

```python
from datetime import timedelta

import shared


def _load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {"sessions": {}}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"sessions": {}}
    data.setdefault("sessions", {})
    return data


def _prune_old_sessions(data: dict, now, ttl_hours: int = 24) -> None:
    from datetime import datetime as _dt
    cutoff = now - timedelta(hours=ttl_hours)
    fresh = {}
    for sid, entry in data.get("sessions", {}).items():
        ts_raw = entry.get("last_flush_at")
        if not ts_raw:
            continue
        try:
            ts = _dt.fromisoformat(ts_raw)
        except ValueError:
            continue
        if ts >= cutoff:
            fresh[sid] = entry
    data["sessions"] = fresh


def check_and_update_dedup(state_path, session_id: str, source: str,
                           window_seconds: int = 60) -> bool:
    """Return True if this flush should proceed; False if suppressed by dedup.

    On True, also records this flush's timestamp in the state file. Held under
    a file lock so concurrent hook invocations for the same session race safely.
    """
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")

    now = shared.pt_now()
    with shared.file_lock(lock_path, timeout=30.0):
        data = _load_state(state_path)
        _prune_old_sessions(data, now)

        entry = data["sessions"].get(session_id)
        if entry:
            try:
                from datetime import datetime as _dt
                prev = _dt.fromisoformat(entry["last_flush_at"])
            except (KeyError, ValueError):
                prev = None
            if prev is not None and (now - prev).total_seconds() < window_seconds:
                return False

        data["sessions"][session_id] = {
            "last_flush_at": now.isoformat(),
            "last_flush_source": source,
        }
        shared.atomic_write(state_path, json.dumps(data, indent=2))
    return True
```

- [ ] **Step 4: Run tests to verify all pass**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_integration_flush.py -v
```

Expected: 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add .claude/scripts/memory_flush.py .claude/scripts/tests/test_integration_flush.py
git commit -m "feat(phase2): memory_flush dedup state with 60s window and 24h TTL pruning"
```

---

### Task 8: `memory_flush.py` — daily log append (TDD)

**Files:**
- Modify: `.claude/scripts/tests/test_integration_flush.py`
- Modify: `.claude/scripts/memory_flush.py`

- [ ] **Step 1: Append daily-log tests**

Append at the end of `.claude/scripts/tests/test_integration_flush.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_integration_flush.py -v
```

Expected: `AttributeError: module 'memory_flush' has no attribute 'append_daily_log'`.

- [ ] **Step 3: Add `append_daily_log` to `.claude/scripts/memory_flush.py`**

Append to `.claude/scripts/memory_flush.py`:

```python
def append_daily_log(daily_dir, session_id: str, source: str, bullets: str) -> None:
    """Append a sectioned flush block to today's daily log in ``daily_dir``.

    Creates the file (with a top-level header) if it does not exist. The
    append is held under a file lock so concurrent flushes do not interleave.
    """
    daily_dir = Path(daily_dir)
    daily_dir.mkdir(parents=True, exist_ok=True)
    now = shared.pt_now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    log_path = daily_dir / f"{date_str}.md"
    lock_path = log_path.with_suffix(log_path.suffix + ".lock")

    short_sid = (session_id or "unknown")[:8]
    section = (
        f"\n## {time_str} PT — Flush from {source} (session {short_sid})\n\n"
        f"{bullets.rstrip()}\n\n---\n"
    )

    with shared.file_lock(lock_path, timeout=30.0):
        if log_path.exists():
            existing = log_path.read_text(encoding="utf-8")
        else:
            existing = f"# Daily Log — {date_str}\n"
        # Ensure exactly one blank line separates existing content from our section
        new_content = existing.rstrip() + "\n" + section
        shared.atomic_write(log_path, new_content)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_integration_flush.py -v
```

Expected: 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add .claude/scripts/memory_flush.py .claude/scripts/tests/test_integration_flush.py
git commit -m "feat(phase2): memory_flush.append_daily_log sectioned append under file lock"
```

---

### Task 9: `memory_flush.py` — curator SDK call with chunking (TDD)

**Files:**
- Create: `.claude/scripts/tests/fixtures/large_transcript.jsonl`
- Modify: `.claude/scripts/tests/test_integration_flush.py`
- Modify: `.claude/scripts/memory_flush.py`

- [ ] **Step 1: Create `.claude/scripts/tests/fixtures/large_transcript.jsonl`**

This fixture must exceed the 80k-token chunking threshold (~320 000 characters). Generate it with a helper script, not by hand.

Create and run this helper once to generate the fixture. (This script is one-shot; delete or leave as a utility — up to taste. The plan keeps it out of the repo.)

```bash
.claude/.venv/bin/python3 - <<'PY'
import json, pathlib
# Each message ~2000 chars; 200 messages -> ~400 000 chars -> ~100k tokens.
msgs = []
for i in range(200):
    msgs.append({"role": "user", "content": f"Question {i}: " + ("lorem ipsum dolor sit amet " * 80)})
    msgs.append({"role": "assistant", "content": [{"type": "text", "text": f"Answer {i}: " + ("lorem ipsum dolor sit amet " * 80)}]})
out = pathlib.Path(".claude/scripts/tests/fixtures/large_transcript.jsonl")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("\n".join(json.dumps(m) for m in msgs) + "\n")
print("wrote", out, "size", out.stat().st_size)
PY
```

Expected: prints `wrote .claude/scripts/tests/fixtures/large_transcript.jsonl size <somewhere between 400000 and 500000>`.

- [ ] **Step 2: Append chunk/summarize tests to `.claude/scripts/tests/test_integration_flush.py`**

Append:

```python
# ---------------------------------------------------------------------------
# chunk + summarize
# ---------------------------------------------------------------------------


def test_chunk_transcript_small_returns_single_chunk():
    text = "x" * 1000  # ~250 tokens
    chunks = memory_flush.chunk_transcript(text, max_tokens_per_chunk=50_000)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_transcript_large_respects_message_boundaries():
    # Two "messages" separated by the \n\n delimiter used by load_transcript
    big_msg = "USER: " + ("word " * 10_000)  # ~50k chars ~12.5k tokens
    text = (big_msg + "\n\n") * 20  # ~250k tokens total
    chunks = memory_flush.chunk_transcript(text, max_tokens_per_chunk=50_000)
    assert len(chunks) > 1
    # Every chunk should be an integer number of \n\n-separated messages
    for c in chunks:
        assert "\n\n" not in c.strip()[:10] or c.count("\n\n") >= 0  # sanity


def test_summarize_single_pass_calls_query_once(monkeypatch):
    calls = []

    def fake_curator(prompt_text, model):
        calls.append(("single", len(prompt_text)))
        return "- Decided X\n- Committed to Y"

    monkeypatch.setattr(memory_flush, "_call_curator", fake_curator)

    result = memory_flush.summarize("some short transcript", model="test-model")
    assert result == "- Decided X\n- Committed to Y"
    assert len(calls) == 1


def test_summarize_chunked_calls_query_once_per_chunk_plus_merge(monkeypatch):
    # Force chunking by dropping the threshold
    monkeypatch.setattr(memory_flush, "CHUNK_THRESHOLD_TOKENS", 100)
    monkeypatch.setattr(memory_flush, "CHUNK_SIZE_TOKENS", 40)

    calls = []

    def fake_curator(prompt_text, model, system_prompt=memory_flush.CURATOR_SYSTEM_PROMPT):
        calls.append({"len": len(prompt_text), "system_prompt": system_prompt})
        n = len(calls)
        if system_prompt == memory_flush.CURATOR_SYSTEM_PROMPT:
            return f"- Bullet from chunk {n}"
        # merge call uses MERGE_SYSTEM_PROMPT
        return "- Merged bullet A\n- Merged bullet B"

    monkeypatch.setattr(memory_flush, "_call_curator", fake_curator)

    # Create text long enough to force >= 2 chunks at our tiny threshold
    text = "USER: hello world this is a long message\n\n" * 30
    result = memory_flush.summarize(text, model="test-model")

    assert len(calls) >= 2  # at least one per-chunk + one merge
    # Exactly one merge call (last one, with MERGE_SYSTEM_PROMPT)
    merge_calls = [c for c in calls
                   if c["system_prompt"] == memory_flush.MERGE_SYSTEM_PROMPT]
    assert len(merge_calls) == 1
    assert "Merged bullet" in result


def test_summarize_returns_nothing_of_note_passthrough(monkeypatch):
    monkeypatch.setattr(memory_flush, "_call_curator",
                        lambda *args, **kwargs: "(nothing of note)")
    assert memory_flush.summarize("trivial", model="test-model") == "(nothing of note)"
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_integration_flush.py -v
```

Expected: failures on `AttributeError` for `chunk_transcript`, `summarize`, `_call_curator`, `CHUNK_THRESHOLD_TOKENS`, `CHUNK_SIZE_TOKENS`.

- [ ] **Step 4: Add chunking + summarize to `.claude/scripts/memory_flush.py`**

Append:

```python
import asyncio

CHUNK_THRESHOLD_TOKENS = 80_000   # single-pass below this
CHUNK_SIZE_TOKENS = 50_000        # target max tokens per chunk when chunking
MERGE_MARKER_NOTHING = "(nothing of note)"

CURATOR_SYSTEM_PROMPT = (
    "You are Jason's memory curator. Given a conversation transcript, extract "
    "items worth saving to long-term memory: decisions made, commitments, "
    "deadlines mentioned, project status changes (Persalto, MemeCoin, Solace "
    "scholarships, legal/financial), lessons learned, and emotional state "
    "observations. Output bullet points only — no preamble, no headers, one "
    "bullet per line starting with `- `. Skip small talk, routine file "
    "operations, and tool-use chatter. If nothing is worth saving, output "
    "exactly the text `(nothing of note)`."
)

MERGE_SYSTEM_PROMPT = (
    "You are Jason's memory curator. The following bullets were extracted "
    "from sequential segments of a single long conversation. Merge them into "
    "a single deduplicated bullet list, preserving chronological order where "
    "it matters (e.g., a decision that was later reversed should show both). "
    "Output bullets only, same format. If every input is `(nothing of note)`, "
    "output `(nothing of note)`."
)


def _estimate_tokens(text: str) -> int:
    """Cheap token estimate: 4 chars ≈ 1 token."""
    return max(1, len(text) // 4)


def chunk_transcript(text: str, max_tokens_per_chunk: int = CHUNK_SIZE_TOKENS) -> list[str]:
    """Split a load_transcript() rendering into chunks at message boundaries.

    ``load_transcript`` joins messages with ``\\n\\n`` so we split on that.
    Chunks respect ``max_tokens_per_chunk`` on a best-effort basis; a single
    message larger than the limit is placed in its own chunk uncut.
    """
    if _estimate_tokens(text) <= max_tokens_per_chunk:
        return [text]
    messages = [m for m in text.split("\n\n") if m.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for msg in messages:
        t = _estimate_tokens(msg)
        if current and (current_tokens + t) > max_tokens_per_chunk:
            chunks.append("\n\n".join(current))
            current, current_tokens = [], 0
        current.append(msg)
        current_tokens += t
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _call_curator(prompt_text: str, model: str,
                  system_prompt: str = CURATOR_SYSTEM_PROMPT) -> str:
    """Run one Agent SDK query with no tools. Returns the concatenated text.

    This is the only place memory_flush talks to the Anthropic API. Kept tiny
    so tests can monkeypatch it cleanly.
    """
    # Re-assert recursion guard just before talking to the API.
    os.environ["CLAUDE_INVOKED_BY"] = "memory_flush"
    from claude_agent_sdk import ClaudeAgentOptions, query

    async def _run():
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=model,
            allowed_tools=[],
        )
        parts: list[str] = []
        async for msg in query(prompt=prompt_text, options=options):
            content = getattr(msg, "content", None)
            if content is None:
                continue
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None) or (
                        block.get("text") if isinstance(block, dict) else None
                    )
                    if text:
                        parts.append(text)
        return "\n".join(p for p in parts if p).strip()

    return shared.with_retry(lambda: asyncio.run(_run()))


def summarize(transcript_text: str, model: str) -> str:
    """Return a bullet list of memory-worthy items from ``transcript_text``.

    Uses single-pass if small, chunk-then-merge if large. Returns
    ``"(nothing of note)"`` if the curator decides nothing is memory-worthy.
    """
    tokens = _estimate_tokens(transcript_text)
    if tokens <= CHUNK_THRESHOLD_TOKENS:
        return _call_curator(transcript_text, model=model).strip() or MERGE_MARKER_NOTHING

    chunks = chunk_transcript(transcript_text, max_tokens_per_chunk=CHUNK_SIZE_TOKENS)
    per_chunk = [_call_curator(c, model=model).strip() for c in chunks]
    if all(p == MERGE_MARKER_NOTHING or not p for p in per_chunk):
        return MERGE_MARKER_NOTHING
    joined = "\n\n".join(
        f"--- Segment {i+1} ---\n{p}" for i, p in enumerate(per_chunk) if p
    )
    merged = _call_curator(joined, model=model, system_prompt=MERGE_SYSTEM_PROMPT).strip()
    return merged or MERGE_MARKER_NOTHING
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_integration_flush.py -v
```

Expected: all 15 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add .claude/scripts/memory_flush.py .claude/scripts/tests/test_integration_flush.py .claude/scripts/tests/fixtures/large_transcript.jsonl
git commit -m "feat(phase2): memory_flush curator + chunk-then-merge for long transcripts"
```

---

### Task 10: `memory_flush.py` — CLI entrypoint + outcome log (TDD)

**Files:**
- Modify: `.claude/scripts/tests/test_integration_flush.py`
- Modify: `.claude/scripts/memory_flush.py`

- [ ] **Step 1: Append end-to-end tests**

Append at the end of `.claude/scripts/tests/test_integration_flush.py`:

```python
# ---------------------------------------------------------------------------
# End-to-end via the CLI main() entrypoint
# ---------------------------------------------------------------------------


def _project_paths(tmp_path):
    """Create the Brain/Memory and .claude/data/state layout inside tmp_path."""
    (tmp_path / "Brain" / "Memory" / "daily").mkdir(parents=True)
    (tmp_path / ".claude" / "data" / "state").mkdir(parents=True)


def test_main_writes_daily_log_and_state_and_flushlog(tmp_path, monkeypatch):
    _project_paths(tmp_path)
    monkeypatch.chdir(tmp_path)

    # Move fixture into place
    fixture = FIXTURES / "short_transcript.jsonl"
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(fixture.read_text())

    # Monkeypatch the curator so we don't hit the network
    monkeypatch.setattr(
        memory_flush, "_call_curator",
        lambda prompt, model, system_prompt=memory_flush.CURATOR_SYSTEM_PROMPT: (
            "- Persalto landing page copy due Friday\n"
            "- Coca-Cola scholarship deadline moved to May 10"
        ),
    )
    # Provide a bogus API key so the early-exit check passes
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    exit_code = memory_flush.main([
        "--transcript", str(transcript),
        "--session-id", "abc123de45",
        "--source", "SessionEnd",
    ])
    assert exit_code == 0

    # Daily log got the section
    daily_files = list((tmp_path / "Brain" / "Memory" / "daily").glob("*.md"))
    assert len(daily_files) == 1
    log_content = daily_files[0].read_text()
    assert "Flush from SessionEnd (session abc123de)" in log_content
    assert "Coca-Cola scholarship" in log_content

    # State file updated
    state = json.loads(
        (tmp_path / ".claude" / "data" / "state" / "flush-state.json").read_text()
    )
    assert "abc123de45" in state["sessions"]

    # flush.log recorded success
    flush_log = (tmp_path / ".claude" / "data" / "state" / "flush.log").read_text()
    assert "success" in flush_log
    assert "abc123de45" in flush_log


def test_main_second_call_within_window_is_dedup_skipped(tmp_path, monkeypatch):
    _project_paths(tmp_path)
    monkeypatch.chdir(tmp_path)
    fixture = FIXTURES / "short_transcript.jsonl"
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(fixture.read_text())

    monkeypatch.setattr(memory_flush, "_call_curator",
                        lambda *a, **kw: "- Something")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    args = ["--transcript", str(transcript),
            "--session-id", "dup-session",
            "--source", "SessionEnd"]
    assert memory_flush.main(args) == 0
    assert memory_flush.main(args) == 0  # second call — dedup skip

    flush_log = (tmp_path / ".claude" / "data" / "state" / "flush.log").read_text()
    assert "success" in flush_log
    assert "skipped: dedup_window" in flush_log

    # Only one section in the daily log
    daily_files = list((tmp_path / "Brain" / "Memory" / "daily").glob("*.md"))
    content = daily_files[0].read_text()
    assert content.count("Flush from SessionEnd (session dup-sess)") == 1


def test_main_without_api_key_exits_0_and_logs_skip(tmp_path, monkeypatch):
    _project_paths(tmp_path)
    monkeypatch.chdir(tmp_path)
    fixture = FIXTURES / "short_transcript.jsonl"
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(fixture.read_text())

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    exit_code = memory_flush.main([
        "--transcript", str(transcript),
        "--session-id", "nokey",
        "--source", "PreCompact",
    ])
    assert exit_code == 0

    flush_log = (tmp_path / ".claude" / "data" / "state" / "flush.log").read_text()
    assert "skipped: no_api_key" in flush_log

    # No daily log written
    daily_files = list((tmp_path / "Brain" / "Memory" / "daily").glob("*.md"))
    assert daily_files == []


def test_main_nothing_of_note_updates_state_but_not_daily_log(tmp_path, monkeypatch):
    _project_paths(tmp_path)
    monkeypatch.chdir(tmp_path)
    fixture = FIXTURES / "short_transcript.jsonl"
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(fixture.read_text())

    monkeypatch.setattr(memory_flush, "_call_curator",
                        lambda *a, **kw: "(nothing of note)")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    exit_code = memory_flush.main([
        "--transcript", str(transcript),
        "--session-id", "boring",
        "--source", "PreCompact",
    ])
    assert exit_code == 0

    flush_log = (tmp_path / ".claude" / "data" / "state" / "flush.log").read_text()
    assert "skipped: nothing_of_note" in flush_log

    # State was updated (so the dedup window applies to this quiet session too)
    state = json.loads(
        (tmp_path / ".claude" / "data" / "state" / "flush-state.json").read_text()
    )
    assert "boring" in state["sessions"]

    # No daily log content
    daily_files = list((tmp_path / "Brain" / "Memory" / "daily").glob("*.md"))
    assert daily_files == []
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_integration_flush.py -v
```

Expected: failures on `AttributeError: module 'memory_flush' has no attribute 'main'`.

- [ ] **Step 3: Add `main()` + outcome logging to `.claude/scripts/memory_flush.py`**

Append:

```python
import argparse
import sys

STATE_REL_PATH = Path(".claude") / "data" / "state" / "flush-state.json"
FLUSH_LOG_REL_PATH = Path(".claude") / "data" / "state" / "flush.log"
DAILY_DIR_REL_PATH = Path("Brain") / "Memory" / "daily"


def _log_outcome(log_path: Path, session_id: str, source: str,
                 outcome: str, reason: str = "") -> None:
    """Append one tab-separated line to flush.log. Best-effort; swallows errors."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        line = "\t".join([
            shared.pt_now().isoformat(),
            session_id or "",
            source or "",
            outcome,
            reason,
        ]) + "\n"
        # Append mode; locking not strictly needed (line-oriented), but take it anyway
        lock_path = log_path.with_suffix(log_path.suffix + ".lock")
        with shared.file_lock(lock_path, timeout=5.0):
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:  # noqa: BLE001 — logging must never raise
        pass


def _load_dotenv_if_present() -> None:
    """Load a .env file from the current working directory if one exists."""
    env_path = Path(".env")
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
    except ImportError:
        # Tiny fallback parser — only supports KEY=VALUE lines
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Second Brain memory flush")
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--source", required=True,
                        choices=["PreCompact", "SessionEnd", "manual"])
    args = parser.parse_args(argv)

    # Recursion guard — set before any path that may import the SDK.
    os.environ["CLAUDE_INVOKED_BY"] = "memory_flush"

    _load_dotenv_if_present()

    state_path = Path(STATE_REL_PATH)
    log_path = Path(FLUSH_LOG_REL_PATH)
    daily_dir = Path(DAILY_DIR_REL_PATH)

    def log(outcome: str, reason: str = "") -> None:
        _log_outcome(log_path, args.session_id, args.source, outcome, reason)

    # Preflight: API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("skipped", "no_api_key")
        return 0

    # Preflight: SDK importable
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        log("skipped", "sdk_missing")
        return 0

    # Dedup window
    try:
        allowed = check_and_update_dedup(
            state_path, args.session_id, args.source, window_seconds=60,
        )
    except TimeoutError:
        log("errored", "state_lock_timeout")
        return 0
    if not allowed:
        log("skipped", "dedup_window")
        return 0

    # Load transcript
    transcript_text = load_transcript(args.transcript)
    if not transcript_text:
        log("skipped", "empty_transcript")
        return 0

    # Summarize
    model = os.environ.get("SECOND_BRAIN_FLUSH_MODEL", "claude-sonnet-4-6")
    try:
        bullets = summarize(transcript_text, model=model)
    except Exception as e:  # noqa: BLE001 — any API error becomes a log line
        log("errored", f"curator_failed: {type(e).__name__}")
        return 0

    if bullets.strip() == MERGE_MARKER_NOTHING:
        log("skipped", "nothing_of_note")
        return 0

    try:
        append_daily_log(
            daily_dir=daily_dir,
            session_id=args.session_id,
            source=args.source,
            bullets=bullets,
        )
    except TimeoutError:
        log("errored", "daily_log_lock_timeout")
        return 0

    log("success")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run all tests**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/ -v
```

Expected: all 19 integration tests + all 15 shared tests + all 5 stress runs PASS.

- [ ] **Step 5: Commit**

```bash
git add .claude/scripts/memory_flush.py .claude/scripts/tests/test_integration_flush.py
git commit -m "feat(phase2): memory_flush main() + outcome log + dotenv loading"
```

---

### Task 11: `session-start-context.py` — vault injection hook

**Files:**
- Create: `.claude/scripts/tests/test_session_start.py`
- Create: `.claude/hooks/session-start-context.py`

- [ ] **Step 1: Create `.claude/scripts/tests/test_session_start.py`**

```python
"""Tests for the SessionStart hook's context builder."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


HOOK = Path(__file__).resolve().parents[2] / "hooks" / "session-start-context.py"


def _run_hook(project_root: Path, stdin_payload: dict) -> dict:
    venv_python = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python3"
    if not venv_python.exists():
        venv_python = Path(sys.executable)
    proc = subprocess.run(
        [str(venv_python), str(HOOK)],
        input=json.dumps(stdin_payload),
        capture_output=True,
        text=True,
        cwd=str(project_root),
        timeout=10,
    )
    assert proc.returncode == 0, f"hook exited non-zero: stderr={proc.stderr}"
    if not proc.stdout.strip():
        return {}
    return json.loads(proc.stdout)


def _build_vault(root: Path, with_bootstrap: bool = False,
                 daily_count: int = 0) -> None:
    memory = root / "Brain" / "Memory"
    memory.mkdir(parents=True)
    (memory / "SOUL.md").write_text("# SOUL\nsoul content")
    (memory / "USER.md").write_text("# USER\nuser content")
    (memory / "MEMORY.md").write_text("# MEMORY\nmemory content")
    (memory / "HEARTBEAT.md").write_text("# HEARTBEAT\nheartbeat content")
    if with_bootstrap:
        (memory / "BOOTSTRAP.md").write_text("# BOOTSTRAP\nbootstrap content")
    daily = memory / "daily"
    daily.mkdir()
    for i in range(daily_count):
        (daily / f"2026-04-2{i}.md").write_text(f"# Daily 2026-04-2{i}\nentry {i}")


def test_session_start_injects_vault_content(tmp_path):
    _build_vault(tmp_path, with_bootstrap=False, daily_count=0)
    out = _run_hook(tmp_path, {
        "session_id": "s1",
        "transcript_path": "",
        "cwd": str(tmp_path),
        "hook_event_name": "SessionStart",
    })
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "soul content" in ctx
    assert "user content" in ctx
    assert "memory content" in ctx
    assert "heartbeat content" in ctx


def test_session_start_injects_bootstrap_when_present(tmp_path):
    _build_vault(tmp_path, with_bootstrap=True, daily_count=0)
    out = _run_hook(tmp_path, {"cwd": str(tmp_path)})
    ctx = out["hookSpecificOutput"]["additionalContext"]
    # Bootstrap should appear BEFORE SOUL
    assert ctx.index("bootstrap content") < ctx.index("soul content")


def test_session_start_includes_last_3_daily_logs_newest_first(tmp_path):
    _build_vault(tmp_path, daily_count=5)  # creates 2026-04-20 ... 2026-04-24
    out = _run_hook(tmp_path, {"cwd": str(tmp_path)})
    ctx = out["hookSpecificOutput"]["additionalContext"]

    # Only the three most recent should be present
    assert "entry 4" in ctx
    assert "entry 3" in ctx
    assert "entry 2" in ctx
    assert "entry 1" not in ctx
    assert "entry 0" not in ctx
    # Newest first
    assert ctx.index("entry 4") < ctx.index("entry 3") < ctx.index("entry 2")


def test_session_start_skips_missing_files_gracefully(tmp_path):
    # Only SOUL exists; no crash
    (tmp_path / "Brain" / "Memory").mkdir(parents=True)
    (tmp_path / "Brain" / "Memory" / "SOUL.md").write_text("just soul")
    out = _run_hook(tmp_path, {"cwd": str(tmp_path)})
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "just soul" in ctx
    # Missing sections are omitted without error


def test_session_start_empty_vault_emits_empty_context(tmp_path):
    out = _run_hook(tmp_path, {"cwd": str(tmp_path)})
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert out["hookSpecificOutput"]["additionalContext"] == ""
```

- [ ] **Step 2: Run the tests to see them fail (hook does not exist)**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_session_start.py -v
```

Expected: tests fail because `.claude/hooks/session-start-context.py` does not exist; subprocess returncode will be non-zero.

- [ ] **Step 3: Create `.claude/hooks/session-start-context.py`**

```python
#!/usr/bin/env python3
"""SessionStart hook — inject Second Brain vault context into every session.

Reads stdin JSON for the project cwd, concatenates the identity/memory files
and the last 3 daily logs, and emits a hookSpecificOutput block that Claude
Code will prepend to the session.

Failure posture: never blocks the session. Missing files are skipped with a
line to stderr; unrecoverable errors exit 0 with an empty additionalContext.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


MEMORY_FILES_IN_ORDER = [
    ("BOOTSTRAP.md", False),   # only if present
    ("SOUL.md",      True),
    ("USER.md",      True),
    ("MEMORY.md",    True),
    ("HEARTBEAT.md", True),
]


def _read_cwd() -> Path:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print("session-start-context: malformed stdin JSON", file=sys.stderr)
        payload = {}
    cwd = payload.get("cwd") or os.getcwd()
    return Path(cwd)


def _read_section(path: Path, label: str) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"session-start-context: missing {path.name}", file=sys.stderr)
        return None
    except OSError as e:
        print(f"session-start-context: cannot read {path.name}: {e}",
              file=sys.stderr)
        return None
    return f"## {label}\n\n{text.rstrip()}\n"


def _recent_daily_logs(daily_dir: Path, n: int = 3) -> list[str]:
    if not daily_dir.exists():
        return []
    files = sorted(daily_dir.glob("*.md"), reverse=True)  # lexical == date DESC
    sections: list[str] = []
    for p in files[:n]:
        section = _read_section(p, f"daily/{p.name}")
        if section:
            sections.append(section)
    return sections


def build_context(project_root: Path) -> str:
    memory_dir = project_root / "Brain" / "Memory"
    parts: list[str] = []

    # Ordered memory files
    for name, required in MEMORY_FILES_IN_ORDER:
        path = memory_dir / name
        if not path.exists():
            if required:
                print(f"session-start-context: missing required {name}",
                      file=sys.stderr)
            continue
        section = _read_section(path, name)
        if section:
            parts.append(section)

    # Last 3 daily logs, newest first
    parts.extend(_recent_daily_logs(memory_dir / "daily", n=3))

    return "\n".join(parts)


def main() -> int:
    try:
        project_root = _read_cwd()
        ctx = build_context(project_root)
    except Exception as e:  # noqa: BLE001
        print(f"session-start-context: fatal {type(e).__name__}: {e}",
              file=sys.stderr)
        ctx = ""
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": ctx,
        }
    }
    sys.stdout.write(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Make executable (belt and suspenders — we invoke via python3 but the shebang is there for `./` runs)**

Run:

```bash
chmod +x .claude/hooks/session-start-context.py
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_session_start.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add .claude/hooks/session-start-context.py .claude/scripts/tests/test_session_start.py
git commit -m "feat(phase2): SessionStart hook — inject vault context into every session"
```

---

### Task 12: `pre-compact-flush.py` + `session-end-flush.py` — flush spawner hooks

**Files:**
- Create: `.claude/scripts/tests/test_flush_spawners.py`
- Create: `.claude/hooks/pre-compact-flush.py`
- Create: `.claude/hooks/session-end-flush.py`

- [ ] **Step 1: Create `.claude/scripts/tests/test_flush_spawners.py`**

```python
"""Tests for the PreCompact and SessionEnd spawner hooks."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
SCRIPTS_DIR = Path(__file__).resolve().parents[1]


def _write_fake_flush(project_root: Path) -> Path:
    """Replace memory_flush.py with a sentinel-writer for the duration of this test."""
    scripts = project_root / ".claude" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    fake = scripts / "memory_flush.py"
    fake.write_text(
        "import sys, json, time\n"
        "from pathlib import Path\n"
        "sentinel = Path('.claude/data/state/flush-sentinel.json')\n"
        "sentinel.parent.mkdir(parents=True, exist_ok=True)\n"
        "sentinel.write_text(json.dumps({'argv': sys.argv[1:], "
        "'env_guard': __import__('os').environ.get('CLAUDE_INVOKED_BY','')}))\n"
    )
    return fake


def _fake_venv(project_root: Path) -> None:
    """Point `.claude/.venv/bin/python3` at the test interpreter."""
    venv_bin = project_root / ".claude" / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    (venv_bin / "python3").symlink_to(sys.executable)


def _invoke(hook_name: str, project_root: Path, payload: dict,
            extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    # Default: scrub the recursion guard so the hook actually spawns.
    # Individual tests that exercise the guard path pass it back in via extra_env.
    env.pop("CLAUDE_INVOKED_BY", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / hook_name)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(project_root),
        timeout=10,
        env=env,
    )


@pytest.mark.parametrize("hook,source", [
    ("pre-compact-flush.py", "PreCompact"),
    ("session-end-flush.py", "SessionEnd"),
])
def test_flush_hook_spawns_flush_with_correct_args(tmp_path, hook, source):
    _fake_venv(tmp_path)
    _write_fake_flush(tmp_path)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("{}\n")

    proc = _invoke(hook, tmp_path, {
        "session_id": "sess-xyz",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
        "hook_event_name": source,
    })
    assert proc.returncode == 0, proc.stderr

    # Wait for the detached child to write the sentinel
    sentinel = tmp_path / ".claude" / "data" / "state" / "flush-sentinel.json"
    deadline = time.monotonic() + 5.0
    while not sentinel.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert sentinel.exists(), "spawned flush did not run"

    payload = json.loads(sentinel.read_text())
    argv = payload["argv"]
    assert "--transcript" in argv
    assert str(transcript) in argv
    assert "--session-id" in argv
    assert "sess-xyz" in argv
    assert "--source" in argv
    assert source in argv


@pytest.mark.parametrize("hook", ["pre-compact-flush.py", "session-end-flush.py"])
def test_flush_hook_skips_when_claude_invoked_by_is_set(tmp_path, hook):
    _fake_venv(tmp_path)
    _write_fake_flush(tmp_path)

    proc = _invoke(hook, tmp_path, {
        "session_id": "s1",
        "transcript_path": str(tmp_path / "t.jsonl"),
        "cwd": str(tmp_path),
    }, extra_env={"CLAUDE_INVOKED_BY": "memory_flush"})
    assert proc.returncode == 0

    # No sentinel should have been written
    time.sleep(0.5)
    sentinel = tmp_path / ".claude" / "data" / "state" / "flush-sentinel.json"
    assert not sentinel.exists()


@pytest.mark.parametrize("hook", ["pre-compact-flush.py", "session-end-flush.py"])
def test_flush_hook_returns_fast(tmp_path, hook):
    _fake_venv(tmp_path)
    _write_fake_flush(tmp_path)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("{}\n")

    t0 = time.monotonic()
    proc = _invoke(hook, tmp_path, {
        "session_id": "fast",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
    })
    elapsed = time.monotonic() - t0
    assert proc.returncode == 0
    # Hook must return within the 15s timeout — in practice << 2s
    assert elapsed < 2.0, f"hook took {elapsed:.2f}s"


@pytest.mark.parametrize("hook", ["pre-compact-flush.py", "session-end-flush.py"])
def test_flush_hook_without_venv_exits_clean(tmp_path, hook):
    """No .claude/.venv — hook logs to stderr and exits 0."""
    _write_fake_flush(tmp_path)  # venv NOT created
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("{}\n")

    proc = _invoke(hook, tmp_path, {
        "session_id": "novenv",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
    })
    assert proc.returncode == 0
    assert "venv" in proc.stderr.lower() or "python3" in proc.stderr.lower()
```

- [ ] **Step 2: Run the tests to see them fail**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_flush_spawners.py -v
```

Expected: both hook files missing; all tests fail to locate the scripts.

- [ ] **Step 3: Create `.claude/hooks/pre-compact-flush.py`**

```python
#!/usr/bin/env python3
"""PreCompact hook — spawn memory_flush.py in the background and exit fast.

Exits 0 immediately if CLAUDE_INVOKED_BY is set (recursion guard) or if any
precondition (venv missing, malformed stdin, missing fields) fails. Must never
block the session; must return within the configured timeout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


SOURCE_LABEL = "PreCompact"


def _spawn_flush(cwd: Path, session_id: str, transcript_path: str) -> int:
    venv_python = cwd / ".claude" / ".venv" / "bin" / "python3"
    if not venv_python.exists():
        print(f"{SOURCE_LABEL}-flush: venv python3 not found at {venv_python}",
              file=sys.stderr)
        return 0

    flush_script = cwd / ".claude" / "scripts" / "memory_flush.py"
    if not flush_script.exists():
        print(f"{SOURCE_LABEL}-flush: memory_flush.py not found at {flush_script}",
              file=sys.stderr)
        return 0

    try:
        subprocess.Popen(
            [
                str(venv_python), str(flush_script),
                "--transcript", transcript_path,
                "--session-id", session_id,
                "--source", SOURCE_LABEL,
            ],
            start_new_session=True,   # detach from hook's process group
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(cwd),
        )
    except OSError as e:
        print(f"{SOURCE_LABEL}-flush: spawn failed: {e}", file=sys.stderr)
    return 0


def main() -> int:
    # Recursion guard: never flush from inside an Agent SDK session
    if os.environ.get("CLAUDE_INVOKED_BY"):
        return 0

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print(f"{SOURCE_LABEL}-flush: malformed stdin JSON", file=sys.stderr)
        return 0

    session_id = payload.get("session_id")
    transcript_path = payload.get("transcript_path")
    cwd = Path(payload.get("cwd") or os.getcwd())

    if not session_id or not transcript_path:
        print(f"{SOURCE_LABEL}-flush: missing session_id or transcript_path",
              file=sys.stderr)
        return 0

    return _spawn_flush(cwd, session_id, transcript_path)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Create `.claude/hooks/session-end-flush.py` as a near-copy**

```python
#!/usr/bin/env python3
"""SessionEnd hook — spawn memory_flush.py in the background and exit fast.

Exits 0 immediately if CLAUDE_INVOKED_BY is set (recursion guard) or if any
precondition fails. Must never block the session.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


SOURCE_LABEL = "SessionEnd"


def _spawn_flush(cwd: Path, session_id: str, transcript_path: str) -> int:
    venv_python = cwd / ".claude" / ".venv" / "bin" / "python3"
    if not venv_python.exists():
        print(f"{SOURCE_LABEL}-flush: venv python3 not found at {venv_python}",
              file=sys.stderr)
        return 0

    flush_script = cwd / ".claude" / "scripts" / "memory_flush.py"
    if not flush_script.exists():
        print(f"{SOURCE_LABEL}-flush: memory_flush.py not found at {flush_script}",
              file=sys.stderr)
        return 0

    try:
        subprocess.Popen(
            [
                str(venv_python), str(flush_script),
                "--transcript", transcript_path,
                "--session-id", session_id,
                "--source", SOURCE_LABEL,
            ],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(cwd),
        )
    except OSError as e:
        print(f"{SOURCE_LABEL}-flush: spawn failed: {e}", file=sys.stderr)
    return 0


def main() -> int:
    if os.environ.get("CLAUDE_INVOKED_BY"):
        return 0

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print(f"{SOURCE_LABEL}-flush: malformed stdin JSON", file=sys.stderr)
        return 0

    session_id = payload.get("session_id")
    transcript_path = payload.get("transcript_path")
    cwd = Path(payload.get("cwd") or os.getcwd())

    if not session_id or not transcript_path:
        print(f"{SOURCE_LABEL}-flush: missing session_id or transcript_path",
              file=sys.stderr)
        return 0

    return _spawn_flush(cwd, session_id, transcript_path)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Make both executable**

Run:

```bash
chmod +x .claude/hooks/pre-compact-flush.py .claude/hooks/session-end-flush.py
```

- [ ] **Step 6: Run the spawner tests**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/test_flush_spawners.py -v
```

Expected: all 8 parametrized tests PASS.

- [ ] **Step 7: Commit**

```bash
git add .claude/hooks/pre-compact-flush.py .claude/hooks/session-end-flush.py .claude/scripts/tests/test_flush_spawners.py
git commit -m "feat(phase2): PreCompact and SessionEnd hooks — detached flush spawners with recursion guard"
```

---

### Task 13: Hook registrations in `.claude/settings.json`

**Files:**
- Create: `.claude/settings.json`

- [ ] **Step 1: Create `.claude/settings.json`**

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/.venv/bin/python3 .claude/hooks/session-start-context.py",
            "timeout": 10
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/.venv/bin/python3 .claude/hooks/pre-compact-flush.py",
            "timeout": 15
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/.venv/bin/python3 .claude/hooks/session-end-flush.py",
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 2: Validate JSON syntax**

Run:

```bash
.claude/.venv/bin/python3 -c "import json; json.load(open('.claude/settings.json')); print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add .claude/settings.json
git commit -m "feat(phase2): register SessionStart, PreCompact, SessionEnd hooks"
```

---

### Task 14: Full regression — run every test

**Files:** none (verification task)

- [ ] **Step 1: Run the full Phase 2 test suite**

Run:

```bash
.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/ -v
```

Expected: everything green. Approximate test count:

- `test_shared.py` — 15 tests
- `test_stress_file_lock.py` — 5 parametrized runs
- `test_integration_flush.py` — 19 tests
- `test_session_start.py` — 5 tests
- `test_flush_spawners.py` — 8 parametrized tests

Total: 52 passing tests.

- [ ] **Step 2: If any test fails, fix before proceeding**

If a test fails, diagnose and fix before moving to the smoke test. Do not `--skip` or `xfail` anything.

- [ ] **Step 3: (No commit — verification only)**

---

### Task 15: End-to-end smoke test (manual)

**Files:** none (manual verification)

- [ ] **Step 1: Confirm `ANTHROPIC_API_KEY` is set**

Run:

```bash
test -f .env && grep -q '^ANTHROPIC_API_KEY=.\+' .env && echo "key present" || echo "MISSING — create .env from .env.example and set ANTHROPIC_API_KEY"
```

If missing, create `.env` at repo root with a real key. This file is gitignored.

- [ ] **Step 2: Open a fresh Claude Code session in the repo**

In a new terminal:

```bash
cd /home/winadmin/projects/second-brain-starter
claude
```

Ask: `What do you know about my projects?`

Expected: the response reflects content from `MEMORY.md` (Persalto, MemeCoin, Solace scholarships) that could only be known via the SessionStart context injection.

- [ ] **Step 3: Trigger a flush**

Inside the Claude Code session, make one or two statements that contain a decision or deadline, e.g., "Let's commit to shipping the landing page by next Friday."

Then exit the session (Ctrl+D or the `/exit` command).

- [ ] **Step 4: Verify the daily log was updated**

Back at the shell:

```bash
ls Brain/Memory/daily/
cat "Brain/Memory/daily/$(.claude/.venv/bin/python3 -c 'from pathlib import Path; import sys; sys.path.insert(0, ".claude/scripts"); import shared; print(shared.pt_today_str())').md"
```

Expected: a file for today's PT date exists and contains a `## HH:MM PT — Flush from SessionEnd (session <8chars>)` section with bullets referencing your decisions/deadlines.

- [ ] **Step 5: Verify the outcome log**

```bash
cat .claude/data/state/flush.log
```

Expected: at least one line ending in `\tsuccess\t`.

- [ ] **Step 6: Verify the dedup state**

```bash
cat .claude/data/state/flush-state.json
```

Expected: JSON with a `sessions` map containing the session id from your test session and a recent `last_flush_at` in PT.

- [ ] **Step 7: If anything above failed, diagnose before proceeding**

Common issues:

- **No daily log written:** check `flush.log` for the skip reason. Most likely `skipped: no_api_key` (fix `.env`), `skipped: sdk_missing` (rebuild venv), or `skipped: empty_transcript` (the session was too short or Claude Code wrote no transcript — try a longer session).
- **Hook not firing at all:** check `claude --debug` logs for hook-related errors. Verify `.claude/settings.json` is valid JSON and `.claude/.venv/bin/python3` exists.
- **Context injection not visible:** inspect the session reminder. If SOUL/USER/MEMORY content does not appear, verify `Brain/Memory/` files exist and run the hook manually:

  ```bash
  echo '{"cwd":"'$(pwd)'"}' | .claude/.venv/bin/python3 .claude/hooks/session-start-context.py | head -50
  ```

- [ ] **Step 8: (No commit — manual verification only)**

---

### Task 16: Update `CLAUDE.md` — Phase 2 complete

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Replace the `## Build Commands` section**

Locate the existing `## Build Commands` section (currently `(Updated after each phase)`) and replace its body with:

```
## Build Commands

- `.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/` — run Phase 2 test suite
- `.claude/.venv/bin/python3 .claude/scripts/memory_flush.py --transcript <path> --session-id <id> --source manual` — run a flush manually against an existing transcript
- `python3 -m venv .claude/.venv && .claude/.venv/bin/pip install -r .claude/scripts/requirements.txt` — (re)create the venv if deps drift or it was deleted
```

- [ ] **Step 2: Append a Phase 2 entry under `## Completed Phases`**

After the Phase 1 entry, append:

```
- **Phase 2: Hooks (Context Persistence)** — SessionStart/PreCompact/SessionEnd hooks wired in `.claude/settings.json`. `memory_flush.py` summarizes conversations via Agent SDK into daily logs (PT dates), with cross-platform file locking, 60s dedup window, and chunk-then-summarize for long transcripts. `shared.py` utilities covered by unit + contention-stress tests.
```

- [ ] **Step 3: Verify**

Run:

```bash
grep -A3 "## Completed Phases" CLAUDE.md
grep -A4 "## Build Commands" CLAUDE.md
```

Expected: the two sections show the new content.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: mark Phase 2 complete in CLAUDE.md"
```

---

## Self-Review Summary (Post-Implementation)

After all tasks run green, confirm these spec requirements are covered:

| Spec requirement | Where covered |
|---|---|
| SessionStart injects SOUL/USER/MEMORY/HEARTBEAT + last 3 daily | Task 11 |
| SessionStart detects BOOTSTRAP.md | Task 11 (`MEMORY_FILES_IN_ORDER` with `required=False`) |
| PreCompact + SessionEnd spawn detached flush | Task 12 |
| `memory_flush.py` with `allowed_tools=[]` | Task 9 (`_call_curator`) |
| 60s dedup window per session | Task 7 + Task 10 |
| Hook recursion prevention via `CLAUDE_INVOKED_BY` | Tasks 6, 12 |
| Cross-platform `file_lock` + `with_retry` + `atomic_write` | Tasks 2, 3, 4 |
| Real-contention stress test | Task 5 |
| Chunk-then-summarize for long transcripts | Task 9 |
| Integration test with canned transcript | Tasks 6-10 |
| `.env.example` + venv + requirements.txt | Task 1 |
| Hook configuration in `.claude/settings.json` | Task 13 |
| `CLAUDE.md` updated with build commands + Phase 2 marker | Task 16 |
| End-to-end smoke test | Task 15 |

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-24-phase2-hooks.md`.

Per the planning brief's stop conditions: **do not start implementation.** Hold for review. The user will kick off execution in a subsequent session.

When execution begins, two options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration. Uses `superpowers:subagent-driven-development`.
2. **Inline Execution** — tasks run in the active session via `superpowers:executing-plans`, batch execution with checkpoints.
