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
