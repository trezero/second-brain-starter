"""Daily-run report formatters — Telegram (HTML), Trello card (Markdown),
and vault daily-log (Markdown).

All surfaces share the same canonical ``DailyRun`` payload so the spec
in ``project_memecoin_comms.md`` is enforced in one place. Telegram uses
HTML mode (safer than legacy Markdown for free-form content like file
paths and identifiers); Trello and the vault daily log use Markdown.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape as _html_escape

from .github_helper import CommitInfo

NOT_WIRED_MD = "_not yet wired — research phase_"
NOT_WIRED_HTML = "<i>not yet wired — research phase</i>"
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
    cumulative_total_pnl_usd: float | None = None
    cumulative_total_pnl_pct: float | None = None
    open_positions: int | None = None
    primary_task: str | None = None
    outcome: str = "pending"             # success | partial | blocked | pending
    trello_card_url: str | None = None
    blockers: list[str] = field(default_factory=list)


def _fmt_money_md(v: float | None) -> str:
    if v is None:
        return NOT_WIRED_MD
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.2f}"


def _fmt_money_html(v: float | None) -> str:
    if v is None:
        return NOT_WIRED_HTML
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.2f}"


def _fmt_pct_md(v: float | None) -> str:
    if v is None:
        return NOT_WIRED_MD
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def _fmt_pct_html(v: float | None) -> str:
    if v is None:
        return NOT_WIRED_HTML
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def _portfolio_md(run: DailyRun) -> str:
    if run.portfolio_value_usd is None:
        return f"{NOT_WIRED_MD} (started: ${STARTING_BANKROLL_USD:,.2f})"
    return f"${run.portfolio_value_usd:,.2f} (started: ${STARTING_BANKROLL_USD:,.2f})"


def _portfolio_html(run: DailyRun) -> str:
    if run.portfolio_value_usd is None:
        return f"{NOT_WIRED_HTML} (started: ${STARTING_BANKROLL_USD:,.2f})"
    return f"${run.portfolio_value_usd:,.2f} (started: ${STARTING_BANKROLL_USD:,.2f})"


def _delta_md(run: DailyRun) -> str:
    if run.pnl_24h_usd is None or run.pnl_prev_24h_usd is None:
        return NOT_WIRED_MD
    return _fmt_money_md(run.pnl_24h_usd - run.pnl_prev_24h_usd)


def _delta_html(run: DailyRun) -> str:
    if run.pnl_24h_usd is None or run.pnl_prev_24h_usd is None:
        return NOT_WIRED_HTML
    return _fmt_money_html(run.pnl_24h_usd - run.pnl_prev_24h_usd)


def _commits_md(run: DailyRun) -> list[str]:
    if not run.commits_24h:
        return ["- _no commits in last 24h_"]
    return [f"- {c.subject} (`{c.sha_short}` on `{c.branch}`)" for c in run.commits_24h]


def _commits_html(run: DailyRun) -> list[str]:
    if not run.commits_24h:
        return ["- <i>no commits in last 24h</i>"]
    return [
        f"- {_html_escape(c.subject)} (<code>{_html_escape(c.sha_short)}</code> "
        f"on <code>{_html_escape(c.branch)}</code>)"
        for c in run.commits_24h
    ]


def _blockers_md(run: DailyRun) -> list[str]:
    if not run.blockers:
        return ["- _none_"]
    return [f"- {b}" for b in run.blockers]


def _blockers_html(run: DailyRun) -> list[str]:
    if not run.blockers:
        return ["- <i>none</i>"]
    return [f"- {_html_escape(b)}" for b in run.blockers]


def _cumulative_html(run: DailyRun) -> str:
    if run.cumulative_total_pnl_usd is None:
        return ""
    pct_str = (f" ({_fmt_pct_html(run.cumulative_total_pnl_pct)})"
               if run.cumulative_total_pnl_pct is not None else "")
    return f"- Cumulative P&amp;L: {_fmt_money_html(run.cumulative_total_pnl_usd)}{pct_str}"


def _cumulative_md(run: DailyRun) -> str:
    if run.cumulative_total_pnl_usd is None:
        return ""
    pct_str = (f" ({_fmt_pct_md(run.cumulative_total_pnl_pct)})"
               if run.cumulative_total_pnl_pct is not None else "")
    return f"- Cumulative P&L: {_fmt_money_md(run.cumulative_total_pnl_usd)}{pct_str}"


def format_telegram(run: DailyRun) -> str:
    """HTML payload for Telegram (parse_mode=HTML).

    HTML is safer than legacy Markdown for free-form content like file
    paths (``docs/*``) and identifiers (``run_daily.py:stubbed_agent_step``),
    which contain characters Telegram's Markdown parser refuses to balance.
    """
    primary = _html_escape(run.primary_task) if run.primary_task else "<i>pending</i>"
    perf_lines = [
        f"- 24h P&amp;L: {_fmt_money_html(run.pnl_24h_usd)} "
        f"({_fmt_pct_html(run.pnl_24h_pct)})",
        f"- Prev 24h: {_fmt_money_html(run.pnl_prev_24h_usd)}",
        f"- Δ vs prev: {_delta_html(run)}",
        f"- Portfolio: {_portfolio_html(run)}",
    ]
    cumulative = _cumulative_html(run)
    if cumulative:
        perf_lines.append(cumulative)
    if run.open_positions is not None:
        perf_lines.append(f"- Open positions: {run.open_positions}")

    lines = [
        f"<b>MemeCoin Daily — {_html_escape(run.date)}</b> "
        f"(run #{run.run_number}, <i>{_html_escape(run.mode)}</i>)",
        "",
        "📦 <b>Changes (last 24h)</b>",
        *_commits_html(run),
        "",
        "💰 <b>Performance</b>",
        *perf_lines,
        "",
        f"🎯 <b>Today's task:</b> {primary}",
        f"✅ <b>Outcome:</b> {_html_escape(run.outcome)}",
    ]
    if run.trello_card_url:
        lines.append(f'🔗 <a href="{_html_escape(run.trello_card_url)}">Trello card</a>')
    lines += ["", "🚧 <b>Blockers</b>", *_blockers_html(run)]
    return "\n".join(lines)


def format_markdown(run: DailyRun) -> str:
    """Markdown payload for Trello card descriptions and the vault daily log."""
    perf_lines = [
        f"- 24h P&L: {_fmt_money_md(run.pnl_24h_usd)} ({_fmt_pct_md(run.pnl_24h_pct)})",
        f"- Prev 24h: {_fmt_money_md(run.pnl_prev_24h_usd)}",
        f"- Δ vs prev: {_delta_md(run)}",
        f"- Portfolio: {_portfolio_md(run)}",
    ]
    cumulative = _cumulative_md(run)
    if cumulative:
        perf_lines.append(cumulative)
    if run.open_positions is not None:
        perf_lines.append(f"- Open positions: {run.open_positions}")

    lines = [
        f"**MemeCoin Daily — {run.date}** (run #{run.run_number}, _{run.mode}_)",
        "",
        "📦 **Changes (last 24h)**",
        *_commits_md(run),
        "",
        "💰 **Performance**",
        *perf_lines,
        "",
        f"🎯 **Today's task:** {run.primary_task or '_pending_'}",
        f"✅ **Outcome:** {run.outcome}",
    ]
    if run.trello_card_url:
        lines.append(f"🔗 [Trello card]({run.trello_card_url})")
    lines += ["", "🚧 **Blockers**", *_blockers_md(run)]
    return "\n".join(lines)


def format_trello_desc(run: DailyRun) -> str:
    """Long-form Markdown for the Trello card description."""
    return format_markdown(run)


def format_daily_log_block(run: DailyRun, *, header_time: str) -> str:
    """Block to append to ``Brain/Memory/daily/<date>.md``.

    ``header_time`` is the PT timestamp string (e.g. ``"07:15 PT"``).
    """
    body = format_markdown(run).split("\n", 1)[1]  # drop the bolded title — we use our own header
    return f"\n## {header_time} — MemeCoin daily run #{run.run_number} ({run.mode})\n\n{body}\n"
