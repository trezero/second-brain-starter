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
