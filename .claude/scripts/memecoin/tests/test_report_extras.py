"""Additional report-formatter tests for the cumulative + open-positions fields."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from memecoin.lib.report import DailyRun, format_markdown, format_telegram  # noqa: E402


def _run(**kw) -> DailyRun:
    base = dict(run_number=2, date="2026-04-24", mode="research")
    base.update(kw)
    return DailyRun(**base)


def test_cumulative_pnl_appears_in_both_surfaces_when_present():
    run = _run(cumulative_total_pnl_usd=125.5, cumulative_total_pnl_pct=12.55)
    md = format_markdown(run)
    html = format_telegram(run)
    assert "Cumulative P&L: +$125.50 (+12.55%)" in md
    assert "Cumulative P&amp;L: +$125.50 (+12.55%)" in html


def test_cumulative_pnl_omitted_when_none():
    md = format_markdown(_run())
    html = format_telegram(_run())
    assert "Cumulative" not in md
    assert "Cumulative" not in html


def test_cumulative_without_pct_omits_pct_clause():
    run = _run(cumulative_total_pnl_usd=10.0)  # no pct
    md = format_markdown(run)
    assert "Cumulative P&L: +$10.00" in md
    assert "(+0" not in md  # no pct clause


def test_open_positions_appears_when_set():
    run = _run(open_positions=4)
    assert "- Open positions: 4" in format_markdown(run)
    assert "- Open positions: 4" in format_telegram(run)


def test_open_positions_omitted_when_none():
    md = format_markdown(_run())
    assert "Open positions" not in md
