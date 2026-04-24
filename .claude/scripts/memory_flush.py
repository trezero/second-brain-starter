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
