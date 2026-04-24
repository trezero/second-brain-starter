# Phase 2: Hooks (Context Persistence) — Design Spec

**Date:** 2026-04-24
**Source PRD:** `.agent/plans/second-brain-prd.md` § Phase 2
**Planning brief:** `docs/superpowers/prompts/phase2-planning.md`
**Scope:** Three Claude Code lifecycle hooks, a background memory-flush Agent SDK script, and shared cross-platform utilities. Together they give the agent persistent memory across sessions.
**Dependencies:** Phase 1 (vault at `Brain/Memory/`, `CLAUDE.md`)
**Complexity:** Medium

---

## Infrastructure Context

- **OS:** Ubuntu 22 WSL2 on Windows 10 (Linux semantics for file locking; `msvcrt` path kept for any future Windows-side invocation)
- **Python:** system `python3` with an isolated virtualenv at `.claude/.venv/`
- **Timezone:** Pacific Time (PT) for all daily-log filenames and timestamps — `zoneinfo.ZoneInfo("America/Los_Angeles")`
- **Claude API:** `ANTHROPIC_API_KEY` lives in `.env` (never in the vault); `claude-agent-sdk` calls the API directly over HTTPS
- **Default flush model:** `claude-sonnet-4-6` (overridable via `SECOND_BRAIN_FLUSH_MODEL` env var)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Claude Code session                                         │
│                                                             │
│   SessionStart ──► session-start-context.py                 │
│                      └─ reads SOUL/USER/MEMORY/HEARTBEAT    │
│                         + last 3 daily logs                 │
│                         + BOOTSTRAP.md (if present)         │
│                      └─ emits hookSpecificOutput            │
│                         with additionalContext              │
│                                                             │
│   PreCompact  ──► pre-compact-flush.py                      │
│   SessionEnd  ──► session-end-flush.py                      │
│                      └─ guard: CLAUDE_INVOKED_BY? exit 0    │
│                      └─ Popen(memory_flush.py, detached)    │
│                      └─ returns within ~100ms               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼ (detached background process)
┌─────────────────────────────────────────────────────────────┐
│ memory_flush.py                                             │
│                                                             │
│   1. os.environ["CLAUDE_INVOKED_BY"] = "memory_flush"       │
│   2. Parse argv (--transcript, --session-id, --source)      │
│   3. Dedup check: flush-state.json, 60s window per session  │
│   4. Load JSONL transcript; strip tool-use noise            │
│   5. Chunk-then-summarize:                                  │
│        if est_tokens < 80 000: single-pass                  │
│        else: chunk ~50 000 each → per-chunk bullets         │
│              → final merge/dedup pass                       │
│   6. Agent SDK query(allowed_tools=[], model=sonnet-4-6)    │
│   7. Append sectioned block to daily/YYYY-MM-DD.md (PT)     │
│   8. Update flush-state.json (file_lock, atomic_write)      │
│   9. One-line outcome to flush.log                          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## File Map

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `.claude/hooks/session-start-context.py` | SessionStart — inject vault context |
| Create | `.claude/hooks/pre-compact-flush.py` | PreCompact — spawn flush, exit fast |
| Create | `.claude/hooks/session-end-flush.py` | SessionEnd — spawn flush, exit fast |
| Create | `.claude/scripts/memory_flush.py` | Background Agent SDK summarizer |
| Create | `.claude/scripts/shared.py` | `file_lock`, `with_retry`, `atomic_write`, PT helpers |
| Create | `.claude/scripts/requirements.txt` | Pinned deps (`claude-agent-sdk`, `pytest`) |
| Create | `.claude/scripts/tests/__init__.py` | Test package marker |
| Create | `.claude/scripts/tests/test_shared.py` | Unit tests for shared utilities |
| Create | `.claude/scripts/tests/test_stress_file_lock.py` | Multi-process contention stress test |
| Create | `.claude/scripts/tests/test_integration_flush.py` | Hook→flush→daily-log canned-transcript test |
| Create | `.claude/scripts/tests/fixtures/short_transcript.jsonl` | Small fixture (<8k tokens) |
| Create | `.claude/scripts/tests/fixtures/large_transcript.jsonl` | Large fixture (>80k tokens, exercises chunking) |
| Create | `.claude/settings.json` | Hook registrations |
| Create | `.env.example` | Template — `ANTHROPIC_API_KEY=`, `SECOND_BRAIN_FLUSH_MODEL=` |
| Modify | `.gitignore` | Add `.claude/.venv/`, `.claude/data/state/flush.log` |
| Modify | `CLAUDE.md` | Phase 2 build commands + completion marker |
| Runtime | `.claude/.venv/` | Isolated virtualenv (gitignored) |
| Runtime | `.claude/data/state/flush-state.json` | Dedup state (gitignored — existing rule covers it) |
| Runtime | `.claude/data/state/flush.log` | Outcome log (gitignored) |

---

## File Specifications

### `.claude/settings.json`

Hook registrations. Each hook invokes the venv's `python3` directly to bypass shell PATH concerns.

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

Relative paths are resolved against the project root (Claude Code's `cwd`).

---

### `.claude/hooks/session-start-context.py`

**Input (stdin JSON):**

```json
{
  "session_id": "abc123...",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/home/winadmin/projects/second-brain-starter",
  "hook_event_name": "SessionStart"
}
```

**Behavior:**

1. Parse stdin JSON; extract `cwd`. Fall back to `os.getcwd()` if missing.
2. Build context in this order, skipping any section whose source file is missing (log the miss to stderr):
   1. `Brain/Memory/BOOTSTRAP.md` — only if file exists
   2. `Brain/Memory/SOUL.md`
   3. `Brain/Memory/USER.md`
   4. `Brain/Memory/MEMORY.md`
   5. `Brain/Memory/HEARTBEAT.md`
   6. Last 3 daily logs from `Brain/Memory/daily/*.md` sorted by filename descending (PT date order)
3. Concatenate with clear `## <source>` headers so the model can tell sections apart.
4. Emit to stdout:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "<concatenated markdown>"
  }
}
```

**Failure posture:** Every failure path exits 0 with an error line to stderr. The session must never be blocked by a broken hook.

**Empty-vault edge case:** If no memory files exist at all (theoretical — Phase 1 has already created them), emit `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ""}}` and exit 0.

---

### `.claude/hooks/pre-compact-flush.py` and `.claude/hooks/session-end-flush.py`

These two hooks are structurally identical except for the `--source` value passed to the flush.

**Behavior:**

1. **Recursion guard:** if `os.environ.get("CLAUDE_INVOKED_BY")` is non-empty, exit 0 immediately.
2. Parse stdin JSON; extract `session_id` and `transcript_path`. If either is missing, log to stderr and exit 0.
3. Resolve venv python: `<cwd>/.claude/.venv/bin/python3`. If the venv is missing, log to stderr and exit 0 (flush is best-effort).
4. Spawn the flush in a detached background process:

```python
subprocess.Popen(
    [venv_python, ".claude/scripts/memory_flush.py",
     "--transcript", transcript_path,
     "--session-id", session_id,
     "--source", "PreCompact"],        # or "SessionEnd"
    start_new_session=True,            # detach from hook's process group
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    cwd=cwd,
)
```

5. Exit 0 without waiting.

**Why argv, not a handoff temp file:** the three needed pieces (transcript path, session id, source) fit cleanly in argv. A handoff file adds a write + read + cleanup with no corresponding benefit.

**Why `start_new_session=True`:** decouples the flush's process group from the hook. When the hook's parent (Claude Code) reaps the hook, the flush keeps running.

---

### `.claude/scripts/memory_flush.py`

Background Agent SDK script. Runs after the hook spawns it.

**Argv:**

- `--transcript <path>` — path to the JSONL transcript Claude Code wrote
- `--session-id <id>` — for dedup keying and the daily-log section header
- `--source <PreCompact|SessionEnd>` — labels the section header

**Behavior:**

1. **Recursion guard set FIRST, before any SDK import:**
   ```python
   os.environ["CLAUDE_INVOKED_BY"] = "memory_flush"
   ```
2. Load `.env` from project root (use `python-dotenv` or a tiny hand-rolled parser — see Deps Note below). If `ANTHROPIC_API_KEY` is absent, log to `flush.log` and exit 0.
3. **Dedup check** (under file lock on `flush-state.json`):
   - Read `flush-state.json`. If `sessions[session_id].last_flush_at` is within 60 seconds of now, log `skipped: dedup`, exit 0.
   - Prune entries older than 24h from the state while we have the lock.
4. **Load transcript:** read JSONL line-by-line. For each entry, extract only user and assistant text content. Drop tool-use/tool-result payloads (keep a single-line placeholder like `[tool: Read]` so continuity is clear). If the file is missing or empty, log and exit 0.
5. **Estimate tokens:** `len(text) // 4` as a cheap proxy.
6. **Summarize:**
   - **Single-pass** (`est_tokens < 80_000`): one call, full transcript as user message, curator system prompt.
   - **Chunked** (`est_tokens >= 80_000`): split transcript into chunks of ~50 000 tokens each on message boundaries. For each chunk call the curator prompt to produce bullets. Then concatenate all per-chunk bullets and call the curator once more with a "merge and dedup these bullets" prompt. Output is the final bullet set.
7. **Write to daily log** (under `file_lock` on the daily-log file):
   - Path: `Brain/Memory/daily/<PT today>.md`, create if missing.
   - Append the sectioned block (see format below).
8. **Update `flush-state.json`** (under `file_lock`):
   ```json
   {
     "sessions": {
       "<session_id>": {
         "last_flush_at": "2026-04-24T14:32:00-07:00",
         "last_flush_source": "SessionEnd"
       }
     }
   }
   ```
   Written via `atomic_write`.
9. **Log outcome** to `flush.log` — one line: `ISO_TS\tsession_id\tsource\toutcome\treason`.

**Curator system prompt (verbatim):**

> You are Jason's memory curator. Given a conversation transcript, extract items worth saving to long-term memory: decisions made, commitments, deadlines mentioned, project status changes (Persalto, MemeCoin, Solace scholarships, legal/financial), lessons learned, and emotional state observations. Output bullet points only — no preamble, no headers, one bullet per line starting with `- `. Skip small talk, routine file operations, and tool-use chatter. If nothing is worth saving, output exactly the text `(nothing of note)`.

**Merge prompt (verbatim) — only used in chunked path:**

> You are Jason's memory curator. The following bullets were extracted from sequential segments of a single long conversation. Merge them into a single deduplicated bullet list, preserving chronological order where it matters (e.g., a decision that was later reversed should show both). Output bullets only, same format. If every input is `(nothing of note)`, output `(nothing of note)`.

**Model:** `claude-sonnet-4-6` by default. Override via `SECOND_BRAIN_FLUSH_MODEL` env var.

**Allowed tools:** `[]` — pure reasoning, no tool access. Enforces SOUL hard boundaries by construction (the flush cannot send, post, delete, or touch external systems).

**If output is `(nothing of note)`:** do NOT write an empty section to the daily log. Log `skipped: nothing of note` to `flush.log` and exit 0. The flush-state entry is still updated so dedup works on retries.

---

### Daily log append format

Appended to `Brain/Memory/daily/<PT YYYY-MM-DD>.md`:

```markdown
## 14:32 PT — Flush from SessionEnd (session abc123de)

- Decided to prioritize Persalto landing page copy this week
- Eric requested updated architecture doc by Friday
- MemeCoin reconciliation script now stable, ready to demo
- Emotional note: frustrated by legal filing delay

---
```

- Time is PT, `HH:MM` format.
- Section header includes `source` (`PreCompact` | `SessionEnd`) and first 8 chars of `session_id`.
- Trailing `---` separator keeps sections visually distinct and plays well with any manual notes the user adds.
- If the daily log file does not exist, create it with a top-level header `# Daily Log — <PT YYYY-MM-DD>` followed by a blank line, then the section.

---

### `.claude/scripts/shared.py`

Cross-platform file locking, retry, atomic writes, PT helpers.

```python
from contextlib import contextmanager
from datetime import datetime
import errno, os, platform, random, sys, time
from pathlib import Path
from zoneinfo import ZoneInfo

if platform.system() == "Windows":
    import msvcrt
else:
    import fcntl

PT = ZoneInfo("America/Los_Angeles")


@contextmanager
def file_lock(path, timeout=30.0, poll_interval=0.1):
    """Acquire exclusive advisory lock on `path`. Blocks up to `timeout`.

    Raises TimeoutError if the lock cannot be acquired in `timeout` seconds.
    Creates the file if it does not exist (zero-byte).
    """
    # Ensure the file exists so we have an fd to lock.
    Path(path).touch(exist_ok=True)
    fd = os.open(str(path), os.O_RDWR)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                if platform.system() == "Windows":
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError) as e:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"file_lock timeout on {path}") from e
                time.sleep(poll_interval)
        yield fd
    finally:
        try:
            if platform.system() == "Windows":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def with_retry(func, *, max_retries=3, base_delay=1.0, max_delay=30.0,
               retry_on_status=(429, 500, 502, 503, 504)):
    """Call `func()`, retrying on transient failures with exponential backoff + jitter.

    `func` takes no args. Retries on:
      - any exception with a `.status_code` in `retry_on_status`
      - any exception with `.status` in `retry_on_status` (SDK variants)
      - any `OSError` with errno in (ECONNRESET, ETIMEDOUT)
    Returns the function's return value on success; re-raises on final failure.
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            if attempt >= max_retries:
                raise
            status = getattr(e, "status_code", None) or getattr(e, "status", None)
            if status not in retry_on_status and not (
                isinstance(e, OSError) and e.errno in (errno.ECONNRESET, errno.ETIMEDOUT)
            ):
                raise
            delay = min(max_delay, base_delay * (2 ** attempt))
            delay += random.uniform(0, delay * 0.1)  # 10% jitter
            time.sleep(delay)


def atomic_write(path, data: str, encoding="utf-8"):
    """Write `data` to `path` atomically via tmp + os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding=encoding) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def pt_now() -> datetime:
    """Current time in America/Los_Angeles."""
    return datetime.now(PT)


def pt_today_str() -> str:
    """'YYYY-MM-DD' in PT."""
    return pt_now().strftime("%Y-%m-%d")
```

**Notes on the lock:**

- Unix uses `fcntl.flock` with `LOCK_NB` in a poll loop rather than blocking, so we can implement a real timeout. (`flock` blocks indefinitely by default; there's no POSIX timed variant.)
- Windows uses `msvcrt.locking(..., LK_NBLCK, 1)` — mandatory byte-range lock on the first byte of the file. This works on empty files because we `touch` first.
- The lock is *advisory* on Unix (processes must both use `flock` to coordinate). All our writers do.
- `fcntl.flock` is tied to the open file description, not the fd, so duplicating the fd after locking is fine.

---

### `.claude/scripts/requirements.txt`

```
claude-agent-sdk>=0.1.0
python-dotenv>=1.0.1
pytest>=8.0.0
```

Pinned floors; upgrade as needed. `python-dotenv` for `.env` loading in `memory_flush.py` — tiny dep, well-worn.

---

### `.env.example`

```
# Anthropic API key — required for memory_flush.py
ANTHROPIC_API_KEY=

# Optional overrides
# SECOND_BRAIN_FLUSH_MODEL=claude-sonnet-4-6
```

The real `.env` lives next to this file at repo root, never committed.

---

### `.gitignore` additions

```
# Python virtualenv
.claude/.venv/

# Flush outcome log (regenerable)
.claude/data/state/flush.log
```

`flush-state.json` is already covered by the existing `.claude/data/state/*.json` rule from Phase 1.

---

## Recursion Prevention

Every Agent SDK script sets `CLAUDE_INVOKED_BY` before any SDK activity. In Phase 2, only `memory_flush.py` exists; Phase 6 adds `heartbeat`/`reflection`, Phase 7 adds `chat`.

The two flush hooks check this env var first and exit 0 if it is set. Today `claude-agent-sdk` calls the Anthropic API over HTTPS directly (no Claude Code subprocess), so the hooks do not fire from inside the SDK. The guard is defense-in-depth for future code that may spawn `claude` CLI from an Agent SDK session.

`SessionStart` does **not** check the guard — if we ever do invoke Claude Code from a background script (e.g., a future orchestrator), we want the vault context to still be injected.

---

## Error Paths

| Failure | Where | Response |
|---|---|---|
| Memory file missing | SessionStart | Skip that section; stderr log; continue |
| No memory files at all | SessionStart | Emit empty `additionalContext`; exit 0 |
| Malformed stdin JSON | any hook | stderr log; exit 0 |
| `ANTHROPIC_API_KEY` unset | `memory_flush.py` | `flush.log` entry: `skipped: no_api_key`; exit 0 |
| `claude-agent-sdk` import fails | `memory_flush.py` | `flush.log` entry: `skipped: sdk_missing`; exit 0 |
| Transcript file missing/empty | `memory_flush.py` | `flush.log` entry: `skipped: empty_transcript`; exit 0 |
| Anthropic API 429 / 5xx | `memory_flush.py` | `with_retry` up to 3 attempts, exponential backoff + jitter |
| Anthropic API hard fail | `memory_flush.py` | `flush.log` entry: `errored: api <status>`; exit 0 |
| `file_lock` timeout (30s) | `memory_flush.py` | `flush.log` entry: `errored: lock_timeout`; exit 0 — do NOT write partial data |
| Dedup window hit | `memory_flush.py` | `flush.log` entry: `skipped: dedup_window`; exit 0 |
| Curator output = `(nothing of note)` | `memory_flush.py` | Do not append to daily log; update state; log `skipped: nothing_of_note` |
| Venv missing | PreCompact / SessionEnd hook | stderr log; exit 0 (flush is best-effort, never blocks) |

**Invariant:** no hook ever returns non-zero or takes longer than its timeout, regardless of vault state.

---

## Test Plan

### Unit — `test_shared.py`

- `file_lock` acquires and releases cleanly; second acquisition from same process works
- `file_lock` times out in ~1s when another process holds the lock
- `with_retry` returns immediately on success (no delay)
- `with_retry` retries on simulated 429 with expected total delay floor; re-raises after `max_retries`
- `with_retry` does NOT retry on non-matching exceptions
- `atomic_write` leaves no `.tmp` file on success; no partial file if the process is killed between open and rename (simulate with a sentinel)
- `pt_now` returns a timezone-aware datetime in `America/Los_Angeles`
- `pt_today_str` returns `YYYY-MM-DD` and handles PDT/PST correctly (parameterize with a frozen clock around the DST boundary)

### Stress — `test_stress_file_lock.py`

The contention guarantee the prompt explicitly demands.

- Spawn **20 worker processes** via `multiprocessing.Pool`.
- Each worker performs **100 iterations** of: acquire `file_lock` on a shared counter file, read current integer, increment, `atomic_write` back, release.
- Assert the final value equals **2000**.
- Assert no `.tmp` files remain in the directory.
- Re-run five times to shake out flakes.

If this test passes on WSL2 Ubuntu, we have actual evidence that `file_lock` + `atomic_write` survive real contention. No `fcntl` hope-and-pray.

### Integration — `test_integration_flush.py`

- Fixture: `fixtures/short_transcript.jsonl` (~20 messages, small enough for single-pass).
- Monkeypatch `claude_agent_sdk.query` to return a canned bullet list without hitting the network.
- Invoke `memory_flush.main(["--transcript", fixture, "--session-id", "testsess", "--source", "SessionEnd"])` in a `tmp_path` project root.
- Assert:
  - `Brain/Memory/daily/<PT today>.md` exists, contains the canned bullets under a correctly-formatted section header.
  - `.claude/data/state/flush-state.json` contains a `sessions.testsess` entry with `last_flush_source == "SessionEnd"`.
  - Immediate second call with the same args is skipped (dedup window).
  - `flush.log` records both runs — `success` then `skipped: dedup_window`.
- Second scenario: `fixtures/large_transcript.jsonl` (>80k token estimate).
  - Monkeypatch `query` to return per-chunk bullets then a merged list.
  - Assert `query` was called ≥ 2 times (chunk + merge).
  - Assert final daily-log block contains merged bullets.

Integration test never calls the real Anthropic API.

### Smoke — manual

After plan execution:

1. Open a fresh Claude Code session in the repo.
2. Confirm SessionStart injection visible in the first system reminder (SOUL / USER / MEMORY / HEARTBEAT visible).
3. Send a couple of messages that include a decision and a deadline.
4. Trigger compaction manually (or end the session).
5. Inspect `Brain/Memory/daily/<today>.md` — expect a flush section with those bullets.
6. Inspect `.claude/data/state/flush.log` — expect a success line.

---

## Post-Phase 2 CLAUDE.md Update

Append to **Build Commands**:

```
- `.claude/.venv/bin/python3 .claude/scripts/memory_flush.py --transcript <path> --session-id <id> --source manual`
  — run a flush manually against an existing transcript
- `.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/` — run Phase 2 test suite
- `python3 -m venv .claude/.venv && .claude/.venv/bin/pip install -r .claude/scripts/requirements.txt`
  — rebuild venv (runbook step if deps drift)
```

Append to **Completed Phases**:

```
- **Phase 2: Hooks (Context Persistence)** — SessionStart/PreCompact/SessionEnd hooks wired in `.claude/settings.json`. `memory_flush.py` summarizes conversations via Agent SDK into daily logs (PT dates), with cross-platform file locking, 60s dedup window, and chunk-then-summarize for long transcripts. `shared.py` utilities covered by unit + contention-stress tests.
```

---

## What This Enables

- **Phase 3 (Memory Search):** daily logs are being populated by real sessions → indexable content accrues automatically.
- **Phase 6 (Heartbeat / Reflection):** `shared.py` (file_lock, retry, atomic writes, PT helpers) is the concurrency base for heartbeat and reflection writing to the same state dir and daily logs.
- **Phase 6 (Reflection):** the `CLAUDE_INVOKED_BY` convention established here carries through — `reflection`, `heartbeat`, `chat` all set it before invoking the SDK.
- **Phase 7 (Chat):** chat bot sets `CLAUDE_INVOKED_BY=chat` — no duplicate flush entries from the bot's own sessions.
- **Phase 8 (Security):** `.env` / credentials already excluded from the vault; `memory_flush.py` has no tools, enforcing SOUL hard boundaries by construction.

---

## Open Items Deferred (not "fix now" because not broken)

- **Venv bootstrap automation.** Plan includes a `setup.sh` or equivalent runbook step, but we do not wire it into a post-install hook. If the venv is deleted, the user must re-create it manually. Acceptable for now — single-user system, documented in CLAUDE.md.
- **Flush model selection heuristics.** Sonnet 4.6 everywhere by default. A future phase might switch to Haiku 4.5 for short transcripts to save cost. Not worth the branch logic today.
- **Transcript parsing robustness.** We assume Claude Code's JSONL format is stable. If Anthropic changes the schema, `memory_flush.py`'s extractor will need updating. Kept the extractor small and isolated for that reason.
