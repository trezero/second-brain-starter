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
