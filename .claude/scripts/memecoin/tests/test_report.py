"""Unit tests for the daily-run report formatter."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from memecoin.lib.github_helper import CommitInfo  # noqa: E402
from memecoin.lib.report import (  # noqa: E402
    DailyRun,
    NOT_WIRED,
    format_daily_log_block,
    format_telegram,
    format_trello_desc,
)


def _placeholder_run(**overrides) -> DailyRun:
    base = dict(run_number=1, date="2026-04-24", mode="research")
    base.update(overrides)
    return DailyRun(**base)


def test_research_phase_uses_not_wired_placeholder():
    out = format_telegram(_placeholder_run())
    assert NOT_WIRED in out
    assert "Δ vs prev" in out
    assert "_no commits in last 24h_" in out
    assert "MemeCoin Daily — 2026-04-24" in out


def test_pnl_formatting_signs_and_commas():
    run = _placeholder_run(
        pnl_24h_usd=1234.5,
        pnl_24h_pct=12.34,
        pnl_prev_24h_usd=-50.0,
        portfolio_value_usd=2_234.5,
    )
    out = format_telegram(run)
    assert "+$1,234.50" in out
    assert "+12.34%" in out
    assert "-$50.00" in out
    # Δ = current - prev = 1234.5 - (-50.0) = 1284.5
    assert "+$1,284.50" in out
    assert "$2,234.50" in out


def test_negative_pnl_renders_minus_sign():
    run = _placeholder_run(pnl_24h_usd=-7.5, pnl_24h_pct=-0.75,
                           pnl_prev_24h_usd=-2.0, portfolio_value_usd=992.5)
    out = format_telegram(run)
    assert "-$7.50" in out
    assert "-0.75%" in out


def test_partial_pnl_falls_back_to_placeholder_for_delta():
    """If only current 24h is known, the delta line still falls back."""
    run = _placeholder_run(pnl_24h_usd=10.0, pnl_24h_pct=1.0)
    out = format_telegram(run)
    # delta requires both fields
    assert out.count(NOT_WIRED) >= 2  # prev + delta both fall back


def test_commits_section_lists_each_commit():
    commits = [
        CommitInfo(sha_short="abc1234", subject="fix: thing", branch="main",
                   iso_timestamp="2026-04-24T03:00:00+00:00"),
        CommitInfo(sha_short="def5678", subject="feat: other", branch="auto/main",
                   iso_timestamp="2026-04-24T01:00:00+00:00"),
    ]
    out = format_telegram(_placeholder_run(commits_24h=commits))
    assert "fix: thing" in out
    assert "`abc1234`" in out
    assert "auto/main" in out
    assert "_no commits in last 24h_" not in out


def test_blockers_section_renders_each_blocker():
    run = _placeholder_run(blockers=["agent step stub", "P&L not wired"])
    out = format_telegram(run)
    assert "- agent step stub" in out
    assert "- P&L not wired" in out


def test_blockers_section_empty_state():
    out = format_telegram(_placeholder_run())
    assert "- _none_" in out


def test_trello_card_link_only_included_when_url_present():
    out_without = format_telegram(_placeholder_run())
    assert "[Trello card]" not in out_without
    out_with = format_telegram(_placeholder_run(trello_card_url="https://trello.com/c/abc"))
    assert "[Trello card](https://trello.com/c/abc)" in out_with


def test_format_trello_desc_matches_telegram_payload():
    run = _placeholder_run(pnl_24h_usd=5.0, pnl_24h_pct=0.5)
    assert format_trello_desc(run) == format_telegram(run)


def test_daily_log_block_has_section_header_and_no_duplicate_title():
    block = format_daily_log_block(_placeholder_run(), header_time="07:15 PT")
    assert block.startswith("\n## 07:15 PT — MemeCoin daily run #1 (research)\n")
    # The bolded MemeCoin Daily title from the Telegram view should NOT appear
    # in the daily-log block — we use our own ## header instead.
    assert "*MemeCoin Daily" not in block
