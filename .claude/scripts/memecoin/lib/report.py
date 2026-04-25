"""Daily-run report formatters — Telegram, Trello card, vault daily-log.

All three surfaces share the same canonical ``DailyRun`` payload so the spec
in ``project_memecoin_comms.md`` is enforced in one place.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .github_helper import CommitInfo

NOT_WIRED = "_not yet wired — research phase_"
STARTING_BANKROLL_USD = 1_000.0


@dataclass
class DailyRun:
    run_number: int
    date: str                            # PT YYYY-MM-DD
    mode: str                            # 'research' | 'improvement'
    commits_24h: list[CommitInfo] = field(default_factory=list)
    pnl_24h_usd: float | None = None
    pnl_24h_pct: float | None = None
    pnl_prev_24h_usd: float | None = None
    portfolio_value_usd: float | None = None
    primary_task: str | None = None
    outcome: str = "pending"             # success | partial | blocked | pending
    trello_card_url: str | None = None
    blockers: list[str] = field(default_factory=list)


def _fmt_money(v: float | None) -> str:
    if v is None:
        return NOT_WIRED
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.2f}"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return NOT_WIRED
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def _delta_line(run: DailyRun) -> str:
    if run.pnl_24h_usd is None or run.pnl_prev_24h_usd is None:
        return NOT_WIRED
    delta = run.pnl_24h_usd - run.pnl_prev_24h_usd
    return _fmt_money(delta)


def _portfolio_line(run: DailyRun) -> str:
    if run.portfolio_value_usd is None:
        return f"{NOT_WIRED} (started: ${STARTING_BANKROLL_USD:,.2f})"
    return f"${run.portfolio_value_usd:,.2f} (started: ${STARTING_BANKROLL_USD:,.2f})"


def _commits_section(run: DailyRun) -> list[str]:
    if not run.commits_24h:
        return ["- _no commits in last 24h_"]
    return [f"- {c.subject} (`{c.sha_short}` on `{c.branch}`)" for c in run.commits_24h]


def _blockers_section(run: DailyRun) -> list[str]:
    if not run.blockers:
        return ["- _none_"]
    return [f"- {b}" for b in run.blockers]


def format_telegram(run: DailyRun) -> str:
    """Compact Markdown payload for Telegram. Capped well under the 4096-char limit."""
    lines = [
        f"*MemeCoin Daily — {run.date}* (run #{run.run_number}, _{run.mode}_)",
        "",
        "📦 *Changes (last 24h)*",
        *_commits_section(run),
        "",
        "💰 *Performance*",
        f"- 24h P&L: {_fmt_money(run.pnl_24h_usd)} ({_fmt_pct(run.pnl_24h_pct)})",
        f"- Prev 24h: {_fmt_money(run.pnl_prev_24h_usd)}",
        f"- Δ vs prev: {_delta_line(run)}",
        f"- Portfolio: {_portfolio_line(run)}",
        "",
        f"🎯 *Today's task:* {run.primary_task or '_pending_'}",
        f"✅ *Outcome:* {run.outcome}",
    ]
    if run.trello_card_url:
        lines.append(f"🔗 [Trello card]({run.trello_card_url})")
    lines += ["", "🚧 *Blockers*", *_blockers_section(run)]
    return "\n".join(lines)


def format_trello_desc(run: DailyRun) -> str:
    """Long-form Markdown for Trello card description."""
    return format_telegram(run)  # identical surface for now; diverge if needed


def format_daily_log_block(run: DailyRun, *, header_time: str) -> str:
    """Block to append to ``Brain/Memory/daily/<date>.md``.

    ``header_time`` is the PT timestamp string (e.g. ``"07:15 PT"``).
    """
    body = format_telegram(run).split("\n", 1)[1]  # drop the bolded title — we use our own header
    return f"\n## {header_time} — MemeCoin daily run #{run.run_number} ({run.mode})\n\n{body}\n"
