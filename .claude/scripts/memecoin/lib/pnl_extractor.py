"""Read portfolio + 24h P&L state directly from the MemeCoinInvestor2026 Postgres.

Why direct SQL and not the HTTP API: the daily agent runs in a clean
environment without the Node app booted. A standalone psycopg query
against the same database is the cheapest reliable read path.

Limitation we know about and accept today: the canonical ``portfolios``
table holds *cumulative-since-account-creation* aggregates — there is no
``portfolio_snapshots`` table yet, so a true 24h-delta of *total
portfolio value* (which includes unrealized PnL on still-open positions)
is not computable here. We instead surface:

    - cumulative total value, realized PnL, unrealized PnL  (canonical)
    - 24h realized PnL summed from the ``trades`` ledger        (proxy)
    - prev 24h realized PnL summed from the ``trades`` ledger   (proxy)

Adding ``portfolio_snapshots`` is the agent's first scheduled improvement
to MemeCoinInvestor2026 — see ``reference_memecoin_architecture.md``.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class PnlExtractionError(RuntimeError):
    """Raised when the extractor cannot produce a useful read of DB state."""


@dataclass
class PnlSnapshot:
    cumulative_total_value_usd: float | None = None
    cumulative_realized_pnl_usd: float | None = None
    cumulative_unrealized_pnl_usd: float | None = None
    cumulative_total_pnl_usd: float | None = None
    cumulative_total_pnl_pct: float | None = None
    realized_24h_usd: float | None = None
    realized_prev_24h_usd: float | None = None
    open_positions: int | None = None
    total_trades_lifetime: int | None = None
    win_rate_pct: float | None = None
    notes: list[str] = field(default_factory=list)


_PORTFOLIO_QUERY = """
SELECT cash,
       positions_value,
       total_value,
       realized_pnl,
       unrealized_pnl,
       total_pnl,
       total_pnl_pct,
       open_positions,
       total_trades,
       win_rate
FROM portfolios
WHERE user_id = %s
LIMIT 1
"""

# Realized PnL from the trades ledger over a [start, end) window.
# We sum the net cash impact of sells minus buys; this is a rough proxy.
# The trades table stores total_value (price * qty) per side; realized
# PnL on a closed position is captured in the ledgers table, but trades
# in a window is the simplest defensible proxy without snapshots.
_TRADE_REALIZED_WINDOW_QUERY = """
SELECT COALESCE(
         SUM(CASE WHEN side = 'sell' THEN total_value ELSE -total_value END),
         0
       )::float AS realized_in_window
FROM trades
WHERE user_id = %s
  AND created_at >= %s
  AND created_at < %s
"""


def _coerce_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coerce_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def extract_pnl(database_url: str, user_id: str, *,
                now_utc=None) -> PnlSnapshot:
    """Connect to ``database_url`` and return today's portfolio + 24h windows.

    Network/auth/schema failures are surfaced as ``PnlExtractionError`` so the
    orchestrator can record them as a blocker without aborting the run.
    """
    from datetime import datetime, timedelta, timezone

    if not database_url or not database_url.strip():
        raise PnlExtractionError("DATABASE_URL_MEMECOIN is empty")
    if not user_id or not user_id.strip():
        raise PnlExtractionError("MEMECOIN_USER_ID is empty")

    now = now_utc or datetime.now(timezone.utc)
    window_24h_start = now - timedelta(hours=24)
    window_prev_24h_start = now - timedelta(hours=48)

    snap = PnlSnapshot()

    try:
        import psycopg
    except ImportError as e:
        raise PnlExtractionError(
            "psycopg not installed; add psycopg[binary] to requirements"
        ) from e

    try:
        with psycopg.connect(database_url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(_PORTFOLIO_QUERY, (user_id,))
                row = cur.fetchone()
                if row is None:
                    snap.notes.append(
                        f"No portfolio row for user_id={user_id!r}; check MEMECOIN_USER_ID"
                    )
                else:
                    (cash, positions_value, total_value, realized_pnl,
                     unrealized_pnl, total_pnl, total_pnl_pct,
                     open_positions, total_trades, win_rate) = row
                    snap.cumulative_total_value_usd = _coerce_float(total_value)
                    snap.cumulative_realized_pnl_usd = _coerce_float(realized_pnl)
                    snap.cumulative_unrealized_pnl_usd = _coerce_float(unrealized_pnl)
                    snap.cumulative_total_pnl_usd = _coerce_float(total_pnl)
                    snap.cumulative_total_pnl_pct = _coerce_float(total_pnl_pct)
                    snap.open_positions = _coerce_int(open_positions)
                    snap.total_trades_lifetime = _coerce_int(total_trades)
                    snap.win_rate_pct = _coerce_float(win_rate)

                cur.execute(_TRADE_REALIZED_WINDOW_QUERY,
                            (user_id, window_24h_start, now))
                snap.realized_24h_usd = _coerce_float(cur.fetchone()[0])

                cur.execute(_TRADE_REALIZED_WINDOW_QUERY,
                            (user_id, window_prev_24h_start, window_24h_start))
                snap.realized_prev_24h_usd = _coerce_float(cur.fetchone()[0])

    except psycopg.Error as e:
        raise PnlExtractionError(f"DB query failed: {e}") from e

    snap.notes.append(
        "24h figures are realized-only (sums from trades table); "
        "true mark-to-market 24h delta requires portfolio_snapshots, "
        "which the agent will add as its first improvement."
    )
    return snap
