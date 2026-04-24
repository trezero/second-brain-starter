# Phase 2 Planning Prompt

> Paste this entire file's contents (below the `---`) into a fresh Claude Code
> session opened in this repo. The assistant will invoke the brainstorming and
> writing-plans superpowers, then produce the Phase 2 spec + plan under
> `docs/superpowers/specs/` and `docs/superpowers/plans/`.
>
> Canonical location: `docs/superpowers/prompts/phase2-planning.md`

---

Plan Phase 2 of the Second Brain build: **Hooks (Context Persistence)**.

This prompt is the canonical Phase 2 planning brief. It is stored at
`docs/superpowers/prompts/phase2-planning.md` — if you need to revise scope or
constraints, update that file rather than re-typing a new version.

## Context to load

- `CLAUDE.md` (project root) — auto-loads
- `Brain/Memory/SOUL.md`, `USER.md`, `MEMORY.md` — agent identity, user profile, and promoted facts populated via BOOTSTRAP
- `Brain/Memory/HEARTBEAT.md` — monitoring checklist (Phase 6 wires it up; Phase 2 must not break it)
- `.agent/plans/second-brain-prd.md` — the full 9-phase PRD
- `docs/superpowers/specs/2026-04-03-phase1-memory-layer-design.md` and `docs/superpowers/plans/2026-04-03-phase1-memory-layer.md` — precedent for how phases are specced and planned in this repo
- `archonIntegration.md` (repo root) — Archon MCP reference; not needed until Phase 4, but loadable if relevant

## Phase 2 scope (from the PRD § Phase 2)

- **SessionStart hook** — inject SOUL/USER/MEMORY into every conversation. Detect `Brain/Memory/BOOTSTRAP.md` and inject it when present. (BOOTSTRAP has been deleted after first-run onboarding, but the detection logic must exist for any future reset.)
- **PreCompact hook** — extract conversation context → spawn background `memory_flush.py`.
- **SessionEnd hook** — same pattern — extract context → spawn background flush.
- **`memory_flush.py`** — background Agent SDK script spawned by PreCompact/SessionEnd. Uses Claude with `allowed_tools=[]` (pure reasoning, no tools). Decides what decisions, lessons, and facts from the conversation are worth saving. Writes bullet-point summary to `Brain/Memory/daily/YYYY-MM-DD.md`. Deduplication + file locking required.
- **Hook recursion prevention** — every Agent SDK session (heartbeat, reflection, chat, flush) sets `os.environ["CLAUDE_INVOKED_BY"] = "<name>"`. SessionEnd and PreCompact check this env var and skip if set. Prevents Agent SDK exits from triggering additional flushes (duplicate entries / infinite recursion).
- **`shared.py`** — cross-platform `file_lock()` (`msvcrt` on Windows / `fcntl` on Unix), `with_retry()` exponential backoff, atomic state writes. Multiple processes (heartbeat, reflection, chat, flush) write to daily logs and state files concurrently — without this, they corrupt each other.

## Constraints and standards

1. **"Fix now, don't defer"** (SOUL.md core principle). If the plan surfaces something broken or missing, the plan says fix it — never defer to a later phase or "future work."
2. **Pass the Eric bar.** No empty functions, no stubs pointing to things that don't exist, no sloppy output. The plan must not prescribe half-implementations.
3. **Platform:** Ubuntu 22 on WSL2 (Windows 10 host). `file_lock()` must actually work in that environment — test against real contention, not just "use `fcntl` and hope." The cross-platform `msvcrt` path matters if scripts ever run from the Windows side.
4. **Timezone:** Pacific Time everywhere. Daily-log filenames use PT dates.
5. **No secrets in vault** — API keys live in `.env` only. `memory_flush.py` must authenticate via env, never read keys into any file inside `Brain/`.
6. **Hard boundaries (SOUL.md)** — hooks must never bypass rules 1–5 (never send, never post, never access financial data, never delete, never send to Anne / engage legal). `memory_flush.py` should have no tools that could violate these.

## Deliverables

Use `superpowers:brainstorming` first to surface assumptions and risks, then `superpowers:writing-plans` to produce:

1. `docs/superpowers/specs/<today>-phase2-hooks-design.md` — design doc
2. `docs/superpowers/plans/<today>-phase2-hooks.md` — implementation plan

Use today's Pacific date (e.g. `2026-04-24`) as the `<today>` prefix.

## Stop conditions

- **Do not start implementation.** Stop at plan-written.
- Hold for review. The user will kick off execution in a subsequent session.
