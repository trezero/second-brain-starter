# Jason Perr's Second Brain

## Project Description
AI second brain for a founder managing a company launch, legal/financial
obligations, and family priorities. Built with Claude Code hooks, Agent SDK,
and Google Workspace APIs.

## Key Paths
- Memory vault: Brain/Memory/
- Daily logs: Brain/Memory/daily/
- Drafts: Brain/Memory/drafts/
- Hooks: .claude/hooks/
- Scripts: .claude/scripts/
- Integrations: .claude/scripts/integrations/
- Skills: .claude/skills/
- State data: .claude/data/state/
- Search DB: .claude/data/search.db
- PRD: .agent/plans/second-brain-prd.md
- Phase specs: docs/superpowers/specs/
- Phase plans: docs/superpowers/plans/

## Conventions
- Timezone: Pacific Time (PT) — all timestamps in PT
- Proactivity: Partner level (act on most things, ask for irreversible)
- Security overrides: NEVER send emails/messages, post to social media,
  access financial data, delete anything, or communicate with spouse /
  touch legal matters without explicit permission
- No secrets in vault — API keys live in .env only
- Daily logs: append-only, timestamped bullets
- YAML frontmatter on all structured files (drafts, skills)
- Checkbox syntax: - [ ] / - [x]
- Infrastructure: Ubuntu 22 WSL2 on Windows 10
- **Never commit `.claude/` to any of Jason's GitHub repos.** Skills and commands are managed via Archon (`.claude/skills/`, `.claude/commands/`, `.claude/plugins/`) — these and other `.claude/` runtime/config files are gitignored. The pre-existing tracked code under `.claude/hooks/`, `.claude/scripts/`, `.claude/settings.json`, `.env.example` is the Second Brain implementation and stays — but no NEW additions to `.claude/` go into git in this repo or any other.

## Build Commands

- `.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/` — run Phase 2 test suite
- `.claude/.venv/bin/python3 .claude/scripts/memory_flush.py --transcript <path> --session-id <id> --source manual` — run a flush manually against an existing transcript
- `python3 -m venv .claude/.venv && .claude/.venv/bin/pip install -r .claude/scripts/requirements.txt` — (re)create the venv if deps drift or it was deleted

## Completed Phases
- **Phase 1: Foundation** — Memory vault initialized at Brain/Memory/. SOUL.md, USER.md, MEMORY.md, BOOTSTRAP.md created. CLAUDE.md established. BOOTSTRAP.md ready for first-run onboarding.
- **Phase 2: Hooks (Context Persistence)** — SessionStart/PreCompact/SessionEnd hooks wired in `.claude/settings.json`. `memory_flush.py` summarizes conversations via Agent SDK into daily logs (PT dates), with cross-platform file locking, 60s dedup window, and chunk-then-summarize for long transcripts. `shared.py` utilities covered by unit + contention-stress tests.


<!-- archon-rules-start -->
## Archon Knowledge Base — Ambient Behavio

Archon is a RAG knowledge management system connected via MCP (`archon` server). It provides semantic search across project documentation and shared cross-project knowledge. Use the `/archon-memory` skill for explicit operations.

### Session Start — Always Show Status
At the start of every session, check Archon state and display a **one-liner status** to the user:

1. Check if `.claude/archon-state.json` exists in the project
2. If yes, read it and note the Archon `project_id` and `source_id` for searches
3. Check doc freshness: compute MD5 hashes of docs vs stored hashes (use `md5sum <file> | cut -d' ' -f1` on Linux, `md5 -q` on macOS)
4. Display one of these status lines:

   - **Configured & fresh:** `Archon KB: <project> — <N> docs, synced <relative time>, up to date`
   - **Configured & stale:** `Archon KB: <project> — <N> docs, synced <relative time>, <N> files changed. Run /archon-memory sync`
   - **Not configured:** `Archon KB: not configured. Run /archon-memory ingest to set up.`
   - **Server unreachable:** `Archon KB: server unreachable — search unavailable this session`

This check should be quick (read state file + hash a few files). Do NOT call Archon APIs for this — just use local state.

### During Normal Work
- When needing project context (architecture, patterns, deployment, historic issues):
  PREFER `rag_search_knowledge_base(query, project_id)` over reading raw doc files
- Archon search is faster and uses less context than reading entire files
- Fall back to direct file reads only when Archon search returns no relevant results
- For code pattern questions, also try `rag_search_code_examples(query, project_id)`

### When Docs Are Modified
- If documentation files are modified during a session, Archon knowledge is stale
- Remind user to run `/archon-memory sync` before ending the session

### Cross-Project Knowledge
- Shared knowledge (framework docs, tool patterns) is available via `~/.claude/archon-global.json`
- Search shared KB: `rag_search_knowledge_base(query, project_id=shared_project_id)`
<!-- archon-rules-end -->
