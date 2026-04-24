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
