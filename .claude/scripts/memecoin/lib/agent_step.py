"""The Claude Agent SDK call that does today's research or improvement.

The orchestrator (``run_daily.py``) hands us a freshly cloned copy of
``MemeCoinInvestor2026`` plus a ``DailyRun`` payload. We invoke a Claude
agent with file-system tools scoped to the clone's CWD, capture its
final structured-JSON output, and mutate the run object in place.

Modes:
    research     — agent writes a focused comprehension report into
                   ``docs/agent-recon/<date>-run<n>.md``. Code edits
                   outside that directory are forbidden by the prompt.
    improvement  — agent picks one concrete improvement, implements it,
                   stages the diff. The orchestrator does the commit/push.

Output contract: the agent must end its turn with a single fenced
``json`` block matching ``AGENT_OUTPUT_SCHEMA``.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

import shared

AGENT_OUTPUT_SCHEMA = {
    "primary_task": "str — one-line summary of what you did",
    "outcome": "'success' | 'partial' | 'blocked'",
    "summary": "str — 2-3 sentences explaining what changed and why",
    "files_changed": "list[str] — repo-relative paths",
    "blockers": "list[str] — empty if no blockers",
}

_FENCED_JSON = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)
_BARE_JSON = re.compile(r"\{[^{}]*\"primary_task\".*?\}", re.DOTALL)

DEFAULT_MAX_TURNS = 80
DEFAULT_MAX_BUDGET_USD = 8.0


class AgentOutputParseError(RuntimeError):
    """Raised when the agent's final message did not contain a valid JSON block."""


def _system_prompt_research(date: str, run_number: int) -> str:
    return f"""You are the MemeCoinInvestor2026 daily-research agent. \
Today is {date}. This is research run #{run_number}.

PROJECT GOAL: take the paper-trading account from $1,000 → $1,000,000. \
Every action ladders up to "more profit, fewer losses." All trading is \
simulated; this is a learning experiment to see whether an AI can own a \
trading project end-to-end.

YOUR JOB TODAY: deepen our understanding of the codebase so future \
improvement runs land precise, high-leverage changes.

DELIVERABLES:
1. Read any prior comprehension reports in ``docs/agent-recon/`` so you \
don't repeat work.
2. Pick ONE focused research question (e.g., "how does \
``recalculatePortfolio`` produce P&L?", "what does the autonomous \
trading loop do per cycle?", "where would a portfolio_snapshots table \
plug in?"). Bias toward questions that unblock concrete improvements.
3. Investigate using Read / Glob / Grep / read-only Bash. Cite file:line \
freely.
4. Write your report to ``docs/agent-recon/{date}-run{run_number}.md``. \
Required sections: ``# Scope``, ``# Findings``, ``# Open Questions``, \
``# Recommended Next Actions``.

CONSTRAINTS:
- Do NOT modify code outside ``docs/agent-recon/``.
- Do NOT modify configuration, schema, or migration files.
- Read-only Bash is fine. Don't ``npm install``, don't start servers.

WHEN YOU ARE DONE, end your turn with a single fenced JSON block — \
nothing after it:

```json
{{
  "primary_task": "...",
  "outcome": "success",
  "summary": "...",
  "files_changed": ["docs/agent-recon/{date}-run{run_number}.md"],
  "blockers": []
}}
```

If you got stuck, use ``"outcome": "blocked"`` and list specific blockers."""


def _system_prompt_improvement(date: str, run_number: int) -> str:
    return f"""You are the MemeCoinInvestor2026 daily-improvement agent. \
Today is {date}. This is improvement run #{run_number}.

PROJECT GOAL: $1,000 → $1,000,000 paper-trading. Every change must \
plausibly increase profit and/or reduce losses, OR enable better future \
decisions toward that goal (observability, snapshots, tests).

PROCESS:
1. Read recent ``docs/agent-recon/*.md`` to ground yourself in what's \
already understood.
2. Pick ONE improvement. Sources of ideas: \
``docs/known-limitations-and-failure-modes.md``, \
``docs/ai-automation-performance-next-phase-plan.md``, \
``docs/agent-recon/backlog.md`` if it exists, recent commits' TODO \
comments.
3. Estimate scope. If >2 hours, log the idea to \
``docs/agent-recon/backlog.md`` and pick something smaller.
4. Implement it. Stay tight to the primary task — opportunistic \
bundling allowed only if <2h extra AND clearly related.
5. Update or add tests where reasonable.
6. Run ``npm run check`` (TypeScript typecheck) and capture the result. \
A failing check is a blocker, not a "success."
7. Stage your changes with ``git add -A``. The harness commits and pushes.

HIGH-LEVERAGE TARGET (use this if no clearer winner emerges from the \
comprehension docs): add a ``portfolio_snapshots`` table + a \
scheduled snapshot job. Today P&L numbers are cumulative-since-start; \
without snapshots there is no clean way to compute true 24h delta. \
This unlocks every downstream metric. Files: ``shared/schema.ts``, \
``migrations/``, ``server/services/autonomousTrading.ts``, \
``server/storage.ts``.

WHEN YOU ARE DONE, end your turn with a single fenced JSON block:

```json
{{
  "primary_task": "...",
  "outcome": "success" | "partial" | "blocked",
  "summary": "...",
  "files_changed": ["<repo-relative path>", ...],
  "blockers": []
}}
```"""


def _user_prompt(date: str, run_number: int, mode: str) -> str:
    return (f"Begin {mode} run #{run_number} for {date}. "
            f"Read docs/agent-recon/ first if it exists, then proceed.")


def parse_agent_output(text: str) -> dict[str, Any]:
    """Extract the structured JSON payload from the agent's final message."""
    matches = _FENCED_JSON.findall(text)
    if matches:
        return _coerce_payload(matches[-1])
    bare = _BARE_JSON.findall(text)
    if bare:
        return _coerce_payload(bare[-1])
    raise AgentOutputParseError(
        "No JSON block found in agent output. The agent must end its turn "
        "with a fenced ```json``` block matching the documented schema."
    )


def _coerce_payload(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AgentOutputParseError(f"Agent output is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise AgentOutputParseError("Agent output JSON must be an object.")
    primary = data.get("primary_task")
    outcome = data.get("outcome")
    if not isinstance(primary, str) or not primary.strip():
        raise AgentOutputParseError("Agent output missing non-empty 'primary_task'.")
    if outcome not in {"success", "partial", "blocked"}:
        raise AgentOutputParseError(
            f"Agent output 'outcome' must be one of success|partial|blocked, got {outcome!r}."
        )
    return {
        "primary_task": primary.strip(),
        "outcome": outcome,
        "summary": (data.get("summary") or "").strip(),
        "files_changed": [str(p) for p in (data.get("files_changed") or [])],
        "blockers": [str(b) for b in (data.get("blockers") or [])],
    }


def _collect_text(messages_iter) -> str:
    parts: list[str] = []
    for msg in messages_iter:
        content = getattr(msg, "content", None)
        if content is None:
            continue
        if isinstance(content, str):
            parts.append(content)
            continue
        if isinstance(content, list):
            for block in content:
                text = getattr(block, "text", None)
                if text is None and isinstance(block, dict):
                    text = block.get("text")
                if text:
                    parts.append(text)
    return "\n".join(parts)


async def _run_agent(*, system_prompt: str, user_prompt: str, repo_path: Path,
                     model: str, max_turns: int, max_budget_usd: float) -> str:
    from claude_agent_sdk import ClaudeAgentOptions, query

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model,
        cwd=str(repo_path),
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        setting_sources=[],  # don't inherit user/project/local settings or hooks
    )

    parts: list[str] = []
    async for msg in query(prompt=user_prompt, options=options):
        content = getattr(msg, "content", None)
        if content is None:
            continue
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                text = getattr(block, "text", None)
                if text is None and isinstance(block, dict):
                    text = block.get("text")
                if text:
                    parts.append(text)
    return "\n".join(parts)


def execute_agent_step(daily_run, repo_path: Path, *, model: str,
                       max_turns: int = DEFAULT_MAX_TURNS,
                       max_budget_usd: float = DEFAULT_MAX_BUDGET_USD) -> None:
    """Run today's agent step and mutate ``daily_run`` with its outcome.

    Recursion guard: sets ``CLAUDE_INVOKED_BY=memecoin_daily`` so any nested
    Claude Code processes can detect they're already inside an automation run.
    """
    os.environ["CLAUDE_INVOKED_BY"] = "memecoin_daily"

    if daily_run.mode == "research":
        system = _system_prompt_research(daily_run.date, daily_run.run_number)
    elif daily_run.mode == "improvement":
        system = _system_prompt_improvement(daily_run.date, daily_run.run_number)
    else:
        raise ValueError(f"Unknown mode: {daily_run.mode!r}")

    prompt = _user_prompt(daily_run.date, daily_run.run_number, daily_run.mode)

    final_text = shared.with_retry(
        lambda: asyncio.run(_run_agent(
            system_prompt=system,
            user_prompt=prompt,
            repo_path=Path(repo_path),
            model=model,
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
        )),
        max_retries=1,
    )

    try:
        payload = parse_agent_output(final_text)
    except AgentOutputParseError as e:
        daily_run.primary_task = "Agent step ran but produced no parseable output"
        daily_run.outcome = "blocked"
        daily_run.blockers.append(f"Agent output parse error: {e}")
        return

    daily_run.primary_task = payload["primary_task"]
    daily_run.outcome = payload["outcome"]
    if payload["summary"]:
        daily_run.primary_task = f"{payload['primary_task']} — {payload['summary']}"
    daily_run.blockers.extend(payload["blockers"])
