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

## Build Commands

- `.claude/.venv/bin/python3 -m pytest .claude/scripts/tests/` — run Phase 2 test suite
- `.claude/.venv/bin/python3 .claude/scripts/memory_flush.py --transcript <path> --session-id <id> --source manual` — run a flush manually against an existing transcript
- `python3 -m venv .claude/.venv && .claude/.venv/bin/pip install -r .claude/scripts/requirements.txt` — (re)create the venv if deps drift or it was deleted

## Completed Phases
- **Phase 1: Foundation** — Memory vault initialized at Brain/Memory/. SOUL.md, USER.md, MEMORY.md, BOOTSTRAP.md created. CLAUDE.md established. BOOTSTRAP.md ready for first-run onboarding.
- **Phase 2: Hooks (Context Persistence)** — SessionStart/PreCompact/SessionEnd hooks wired in `.claude/settings.json`. `memory_flush.py` summarizes conversations via Agent SDK into daily logs (PT dates), with cross-platform file locking, 60s dedup window, and chunk-then-summarize for long transcripts. `shared.py` utilities covered by unit + contention-stress tests.
