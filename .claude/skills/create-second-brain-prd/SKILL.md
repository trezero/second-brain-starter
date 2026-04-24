---
name: create-second-brain-prd
description: Generate a personalized Second Brain PRD from a completed requirements template. Use when the user has filled out my-second-brain-requirements.md and wants to generate their build plan. Triggers on "create my second brain PRD", "generate my PRD", "build my second brain plan", "/create-second-brain-prd", or after completing the requirements template.
argument-hint: <path-to-requirements> [output-path]
---

# Second Brain PRD Generator

Generate a personalized Product Requirements Document for building an AI Second Brain, based on the user's completed requirements template.

**A blank template is bundled with this skill at `${CLAUDE_SKILL_DIR}/my-second-brain-requirements.md`.** Copy it to your workspace and fill it out before running this skill.

## Parameters

- **`$0`** (required) — Path to the filled-out requirements file (e.g., `./my-second-brain-requirements.md`)
- **`$1`** (optional) — Output path for the PRD. Defaults to `.agent/plans/second-brain-prd.md`

## Workflow

1. **Read the requirements** — Read the filled-out requirements file at `$0`. If no argument was provided, ask the user for the path. If they haven't filled one out yet, tell them a blank template is available at `${CLAUDE_SKILL_DIR}/my-second-brain-requirements.md` — they should copy it to their workspace and fill it out first.

2. **Load the architecture reference** — Read `${CLAUDE_SKILL_DIR}/references/architecture-reference.md` for the blueprint.

3. **Research ALL tools and APIs** — Do not assume familiarity with any platform or library. Even common APIs like Gmail or Slack have nuances, rate limits, and SDK-specific patterns that matter for implementation. For every tool in the user's stack, do web research to ensure the PRD contains accurate, specific guidance.

   **Always research these core dependencies:**
   - **Claude Agent SDK** — How to create conversations, system_prompt presets, setting_sources, allowed_tools, streaming responses, credential handling
   - **FastEmbed** — ONNX model loading, batch embedding API, model cache configuration, supported models
   - **Hook system** — Claude Code hook types (PreToolUse, PostToolUse, etc.), callback signatures, settings.json configuration

   **For every platform the user selected (Gmail, Slack, Linear, HubSpot, etc.):**
   - Authentication method (OAuth2 flow, API tokens, bot tokens, etc.)
   - Key SDK/library to use (e.g., google-api-python-client, slack_sdk, etc.)
   - The specific API endpoints needed for the user's top tasks
   - Platform-specific setup requirements (e.g., Slack Socket Mode needs an App Token + Bot Token, Gmail needs OAuth consent screen published to Production for custom domains)
   - Rate limits, pagination patterns, and common gotchas

   **The goal:** Every phase in the PRD should contain enough technical specificity that a coding agent can implement it without guessing. Don't bloat the PRD with raw research - distill it into actionable implementation notes per phase.

4. **Generate the PRD** — Create a phased build plan at the output path ($1, or `.agent/plans/second-brain-prd.md` if not specified) with these sections:

### PRD Structure

The output PRD should have:

**Header:**
- Project name (personalized: "[User's Name]'s Second Brain")
- Date generated
- Summary: 1-2 sentences based on their top tasks

**Phase 1: Foundation (Memory Layer)**
- Set up the memory vault folder using the name they specified in "Memory vault folder name" (e.g., `MyVault/Memory/`). Do NOT hardcode "Dynamous" — always use their chosen name.
- If they're using Obsidian, mention it as the viewer; if not, note that the vault is just a folder of markdown files that works with any editor.
- Create SOUL.md, USER.md, MEMORY.md, BOOTSTRAP.md, daily/ structure
- BOOTSTRAP.md is a first-run onboarding script: on the user's very first Claude Code session, it drives an interactive conversation to personalize USER.md, SOUL.md, and HEARTBEAT.md (asks about name, timezone, role, communication style, integrations, proactivity preferences — one question at a time). It deletes itself after onboarding completes. If a session ends mid-onboarding, the file persists and picks up next time. The SessionStart hook should detect BOOTSTRAP.md and inject it into context.
- Create **CLAUDE.md** at the repo root — this is Claude Code's project instruction file, loaded into every conversation. Initialize it with: project description, key paths (vault, scripts, hooks, skills, data directories), project conventions (timezone, advisor mode, no secrets in vault, checkbox syntax, YAML frontmatter), and a "Completed Phases" section to be updated after each phase. Also add a "Build Commands" section — start with placeholder entries and populate with real commands as each phase introduces them. This file is the agent's reference for how to interact with the project.
- Customize each file based on their "About You" and "Memory Categories" answers
- Key files to create, estimated complexity: Low

**Phase 2: Hooks (Context Persistence)**
- SessionStart hook (inject memory into every conversation — should also detect and inject BOOTSTRAP.md for first-run onboarding)
- PreCompact hook (extract conversation context → spawn background `memory_flush.py`)
- SessionEnd hook (same pattern — extract context → spawn background flush)
- `memory_flush.py`: Background Agent SDK script spawned by PreCompact/SessionEnd. Uses Claude with `allowed_tools=[]` (pure reasoning, no tools) to intelligently decide what decisions, lessons, and facts from the conversation are worth saving. Writes bullet-point summary to daily log. Has deduplication and file locking. This is critical — without it, daily logs contain mechanical transcript excerpts instead of intelligent summaries that the daily reflection can actually promote to MEMORY.md.
- **Hook recursion prevention**: Every Agent SDK session (heartbeat, reflection, chat, memory flush) must set `os.environ["CLAUDE_INVOKED_BY"] = "<name>"`. SessionEnd and PreCompact hooks check this env var and skip if set — this prevents Agent SDK exits from triggering additional flushes, which would cause duplicate log entries or infinite recursion.
- **Shared utilities** (`shared.py`): Cross-platform file locking (`file_lock()` using `msvcrt` on Windows / `fcntl` on Unix) for concurrent write safety, retry with exponential backoff (`with_retry()`) for external API calls, and atomic state writes. Multiple processes (heartbeat, reflection, chat, flush) write to daily logs and state files concurrently — without file locking, they corrupt each other.
- Key files, estimated complexity: Medium

**Phase 3: Memory Search (Hybrid RAG)**
- Set up chunking + embedding pipeline
- SQLite + sqlite-vec + FTS5 (local) or Postgres + pgvector (VPS)
- Hybrid search: 0.7 vector + 0.3 keyword
- Key files, estimated complexity: Medium

**Phase 4: Integrations (Their Top 3 First)**
- Use their "Integration Priority" rankings
- For each: auth setup, API module, registry entry, query.py subcommand
- Reference the integration_template.py pattern
- Key files per integration, estimated complexity: Medium per integration

**Phase 5: Skills (Starter Pack)**
- Vault structure skill (teach agent their file organization)
- At least one custom skill based on their "Top Tasks"
- Skill anatomy: SKILL.md + scripts/ + references/
- Key files, estimated complexity: Low-Medium

**Phase 6: Proactive Systems (Heartbeat + Reflection)**
- Heartbeat: gather data from integrations → Claude reasons → notify
- Set schedule based on their proactivity level
- Daily reflection: promote important daily log items to MEMORY.md. Must include **SOUL.md write-protection** — a PreToolUse hook that blocks the reflection agent from editing SOUL.md. If the reflection wants to suggest changes to the agent's identity or rules, it writes those suggestions to the daily log instead. This prevents "soul drift" where the agent gradually rewrites its own personality without user approval.
- Map their proactivity level choice to specific heartbeat behaviors
- **Draft management** (required for Advisor/Assistant/Partner proactivity levels): The heartbeat must implement a full draft lifecycle system. Specifically: (1) scan integration data for emails, DMs, and community posts needing a reply, (2) generate draft replies in the user's voice using RAG on `drafts/sent/` for voice-matching (`memory_search.py --path-prefix drafts/sent`), (3) write drafts as markdown files in `drafts/active/` with YAML frontmatter (type, source_id, recipient, subject, context, created, status) + Original Message + Draft Reply sections, (4) expire drafts >24h old with no action by moving to `drafts/expired/`, (5) detect when the user has actually replied on the platform and move the file to `drafts/sent/` capturing their real reply text. The heartbeat Agent SDK session needs Write/Edit tools to create draft files — read-only tools are insufficient. Drafting criteria should be defined in USER.md (what to draft, what to skip).
- **Habits tracking**: HABITS.md with customizable pillars, auto-detection rules for objective achievements, daily reset by heartbeat, late-day nudges for unchecked pillars
- Key files, estimated complexity: High

**Phase 7: Chat Interface (Optional)**
- Only include if they checked Chat/Messaging in platforms
- Slack/Discord bot with persistent conversations
- Platform adapter pattern for extensibility
- Key files, estimated complexity: High

**Phase 8: Security Hardening**
- **Credential protection hook** (`block-secrets.py`): PreToolUse hook that intercepts ALL file-access tools (Read, Bash, Grep, Edit, Write, Glob) and blocks access to .env files, API tokens, OAuth credentials, SSH keys, and other secrets. Also blocks Bash commands that would expose environment variables, and blocks writing scripts that would exfiltrate secrets to stdout. This is the most critical security component — without it, the LLM can accidentally read and expose every API key. Must be implemented as a separate, dedicated hook (not combined with the general command guard).
- Sanitize all external data (3-layer defense: pattern detection → markdown escaping → XML trust boundaries)
- Command guardrails based on their Security Boundaries answers
- API key isolation (Python CLI wrapper pattern)
- Key files, estimated complexity: Medium-High

**Phase 9: Deployment**
- Based on their Infrastructure answers
- Local: OS scheduler setup (Windows Task Scheduler / cron / launchd)
- If VPS: server setup, vault sync, SSH tunnel
- Cost estimate based on their choices

**Each phase includes:**
- What to build (1-2 sentences)
- Key files to create (with paths)
- Dependencies (which phases must come first)
- Estimated complexity (Low / Medium / High)
- Personalization notes (how their specific answers shape this phase)
- **CLAUDE.md update reminder**: Every phase must end by updating CLAUDE.md with any new paths, build commands, and conventions introduced. This keeps the agent's project reference current — if a command exists but isn't in CLAUDE.md, the agent won't know about it.

**Footer:**
- Recommended build order (phases are mostly sequential but some can parallel)
- "This PRD was generated from your requirements. Revisit and update as your system evolves."

5. **Confirm output** — Tell the user where the PRD was saved (the output path) and suggest they start with Phase 1.

## Personalization Rules

- Use THEIR vault folder name everywhere in file paths (not "Dynamous" — use whatever they wrote in "Memory vault folder name"). For example, if they wrote "SecondBrain", all paths should be `SecondBrain/Memory/`, `SecondBrain/Memory/daily/`, etc.
- If they are NOT using Obsidian, don't mention Obsidian in the PRD — just refer to the vault as a "memory vault" or "markdown folder". If they ARE using Obsidian, mention it as the viewer/editor.
- Use THEIR platform names everywhere (not generic "email" — use "Gmail" if that's what they chose)
- Map their proactivity level to concrete behaviors:
  - Observer → heartbeat notifications only, no drafting, no habit tracking
  - Advisor → heartbeat + draft emails/messages for review, habit tracking with suggestions but no auto-check
  - Assistant → heartbeat + drafts + auto-organize files + auto-log, habit auto-detection for objective pillars
  - Partner → all of the above + send low-risk messages + auto-complete routine tasks + full habit auto-detection
- Map their security boundaries directly into Phase 8 guardrail rules
- Use their memory categories to structure the vault folders in Phase 1
- Their integration priority determines Phase 4 order
