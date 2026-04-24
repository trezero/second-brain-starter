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
