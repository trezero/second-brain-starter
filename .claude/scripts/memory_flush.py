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
        outcome_field = f"{outcome}: {reason}" if reason else outcome
        line = "\t".join([
            shared.pt_now().isoformat(),
            session_id or "",
            source or "",
            outcome_field,
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
