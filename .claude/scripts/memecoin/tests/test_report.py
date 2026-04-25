"""Unit tests for the daily-run report formatters (Markdown + Telegram HTML)."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from memecoin.lib.github_helper import CommitInfo  # noqa: E402
from memecoin.lib.report import (  # noqa: E402
    DailyRun,
    NOT_WIRED_HTML,
    NOT_WIRED_MD,
    format_daily_log_block,
    format_markdown,
    format_telegram,
    format_trello_desc,
)


def _placeholder_run(**overrides) -> DailyRun:
    base = dict(run_number=1, date="2026-04-24", mode="research")
    base.update(overrides)
    return DailyRun(**base)


# ---------- markdown surface (Trello + daily log) ----------


def test_markdown_research_phase_uses_placeholder():
    out = format_markdown(_placeholder_run())
    assert NOT_WIRED_MD in out
    assert "Δ vs prev" in out
    assert "_no commits in last 24h_" in out
    assert "**MemeCoin Daily — 2026-04-24**" in out


def test_markdown_pnl_formatting_signs_and_commas():
    run = _placeholder_run(
        pnl_24h_usd=1234.5,
        pnl_24h_pct=12.34,
        pnl_prev_24h_usd=-50.0,
        portfolio_value_usd=2_234.5,
    )
    out = format_markdown(run)
    assert "+$1,234.50" in out
    assert "+12.34%" in out
    assert "-$50.00" in out
    assert "+$1,284.50" in out  # delta = 1234.5 - (-50.0)
    assert "$2,234.50" in out


def test_markdown_negative_pnl_renders_minus_sign():
    run = _placeholder_run(pnl_24h_usd=-7.5, pnl_24h_pct=-0.75,
                           pnl_prev_24h_usd=-2.0, portfolio_value_usd=992.5)
    out = format_markdown(run)
    assert "-$7.50" in out
    assert "-0.75%" in out


def test_markdown_partial_pnl_falls_back_for_delta():
    run = _placeholder_run(pnl_24h_usd=10.0, pnl_24h_pct=1.0)
    out = format_markdown(run)
    assert out.count(NOT_WIRED_MD) >= 2  # prev + delta both fall back


def test_markdown_commits_section_lists_each_commit():
    commits = [
        CommitInfo(sha_short="abc1234", subject="fix: thing", branch="main",
                   iso_timestamp="2026-04-24T03:00:00+00:00"),
        CommitInfo(sha_short="def5678", subject="feat: other", branch="auto/main",
                   iso_timestamp="2026-04-24T01:00:00+00:00"),
    ]
    out = format_markdown(_placeholder_run(commits_24h=commits))
    assert "fix: thing" in out
    assert "`abc1234`" in out
    assert "auto/main" in out


def test_markdown_blockers_section_renders_each():
    out = format_markdown(_placeholder_run(blockers=["A", "B"]))
    assert "- A" in out
    assert "- B" in out


def test_markdown_blockers_empty_state():
    assert "- _none_" in format_markdown(_placeholder_run())


def test_markdown_trello_link_only_when_url_present():
    assert "[Trello card]" not in format_markdown(_placeholder_run())
    out = format_markdown(_placeholder_run(trello_card_url="https://trello.com/c/abc"))
    assert "[Trello card](https://trello.com/c/abc)" in out


def test_format_trello_desc_is_markdown_payload():
    run = _placeholder_run(pnl_24h_usd=5.0, pnl_24h_pct=0.5)
    assert format_trello_desc(run) == format_markdown(run)


def test_daily_log_block_has_section_header_and_no_duplicate_title():
    block = format_daily_log_block(_placeholder_run(), header_time="07:15 PT")
    assert block.startswith("\n## 07:15 PT — MemeCoin daily run #1 (research)\n")
    assert "**MemeCoin Daily" not in block  # our ## header replaces the bolded title


# ---------- telegram HTML surface ----------


def test_telegram_uses_html_tags_not_markdown():
    out = format_telegram(_placeholder_run())
    assert "<b>MemeCoin Daily — 2026-04-24</b>" in out
    assert "<i>research</i>" in out
    assert "*MemeCoin" not in out  # no Markdown bold
    assert "_research_" not in out  # no Markdown italic


def test_telegram_escapes_special_chars_in_user_content():
    """Path-like and identifier-style strings used to break Markdown — verify
    they survive HTML mode by being properly escaped."""
    run = _placeholder_run(
        primary_task="Inventoried docs/* files & checked refactor_v2.py",
        blockers=["bug at run_daily.py:stubbed_agent_step"],
    )
    out = format_telegram(run)
    assert "docs/*" in out                      # not stripped
    assert "&amp;" in out                       # & is escaped
    assert "run_daily.py:stubbed_agent_step" in out
    assert "<script>" not in out                # belt-and-suspenders: no raw injection


def test_telegram_link_uses_html_anchor():
    out = format_telegram(_placeholder_run(trello_card_url="https://trello.com/c/abc"))
    assert '<a href="https://trello.com/c/abc">Trello card</a>' in out


def test_telegram_research_phase_uses_html_placeholder():
    out = format_telegram(_placeholder_run())
    assert NOT_WIRED_HTML in out
    assert NOT_WIRED_MD not in out


def test_telegram_commits_section_escapes_subjects():
    commits = [CommitInfo(sha_short="abc1234", subject="feat: <strong> tag handling",
                          branch="main", iso_timestamp="2026-04-24T03:00:00+00:00")]
    out = format_telegram(_placeholder_run(commits_24h=commits))
    assert "&lt;strong&gt;" in out
    assert "<strong>" not in out  # no raw tag passes through
