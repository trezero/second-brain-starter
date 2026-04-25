"""MemeCoin daily-improvement agent — orchestrator.

Wires every piece of the daily run together:

    1. fresh shallow clone of trezero/MemeCoinInvestor2026 into a tmp dir
    2. checkout (or create) the auto/main branch off main
    3. pull the last 24h of commits from main + auto/main for the report
    4. extract live P&L from MemeCoinInvestor2026 Postgres (best-effort —
       missing/unreachable DB surfaces as a blocker, run continues)
    5. invoke the Claude Agent SDK in research or improvement mode
    6. commit any staged file changes the agent produced and push to
       auto/main
    7. post / update today's Trello card on the Atlas Task Board
    8. send a Telegram push
    9. append to the vault daily log
   10. bump the persisted run counter

Usage:
    run_daily.py [--mode research|improvement] [--run-number N] [--dry-run]

Defaults:
    --mode      research if run-number <= 2 else improvement
    --run-number persisted to .claude/data/state/memecoin_run_counter.txt;
                 each invocation increments it after a successful run

In --dry-run mode no external side-effects fire — no agent SDK call, no
Trello card, no Telegram push, no daily-log write, no commits/push, run
counter not bumped. Useful for verifying clone + P&L extraction in
isolation.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

# Make `from scripts.shared import ...` work when invoked as a script.
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared import file_lock, pt_now, pt_today_str  # noqa: E402

from memecoin.lib import agent_step, github_helper, pnl_extractor  # noqa: E402
from memecoin.lib.config import Config, load_config  # noqa: E402
from memecoin.lib.report import (  # noqa: E402
    DailyRun,
    format_daily_log_block,
    format_telegram,
    format_trello_desc,
)
from memecoin.lib.telegram_client import TelegramClient  # noqa: E402
from memecoin.lib.trello_client import TrelloClient  # noqa: E402

REPO_ROOT = SCRIPTS_DIR.parent.parent
RUN_COUNTER_PATH = REPO_ROOT / ".claude" / "data" / "state" / "memecoin_run_counter.txt"
DAILY_LOG_DIR = REPO_ROOT / "Brain" / "Memory" / "daily"
GIT_AUTHOR_NAME = "MemeCoin Daily Bot"
GIT_AUTHOR_EMAIL = "memecoin-bot@trezero.local"
AUTO_BRANCH = "auto/main"


def read_run_counter() -> int:
    if not RUN_COUNTER_PATH.exists():
        return 0
    try:
        return int(RUN_COUNTER_PATH.read_text(encoding="utf-8").strip() or "0")
    except ValueError:
        return 0


def bump_run_counter(new_value: int) -> None:
    RUN_COUNTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUN_COUNTER_PATH.write_text(f"{new_value}\n", encoding="utf-8")


def default_mode_for_run(run_number: int) -> str:
    return "research" if run_number <= 2 else "improvement"


def append_daily_log(text_block: str) -> Path:
    DAILY_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = DAILY_LOG_DIR / f"{pt_today_str()}.md"
    lock_path = log_path.with_suffix(log_path.suffix + ".lock")
    with file_lock(lock_path, timeout=30.0):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(text_block)
    return log_path


def upsert_trello_card(client: TrelloClient, cfg: Config, run: DailyRun):
    """Create today's daily card in Atlas Inbox; update if it already exists."""
    name = f"MemeCoin Daily — {run.date} (run #{run.run_number})"
    desc = format_trello_desc(run)
    label_ids = (cfg.trello_label_atlas_ops, cfg.trello_label_product_eng)
    existing = client.find_card_on_board(cfg.trello_board_id, name)
    if existing:
        return client.update_card(existing.id, desc=desc)
    return client.create_card(cfg.trello_list_inbox, name=name, desc=desc, label_ids=label_ids)


def populate_pnl(daily: DailyRun, cfg: Config) -> None:
    """Best-effort P&L read; missing DB or schema mismatches surface as blockers."""
    if not cfg.memecoin_database_url:
        daily.blockers.append(
            "DATABASE_URL_MEMECOIN not set in .env — P&L extraction skipped"
        )
        return
    if not cfg.memecoin_user_id:
        daily.blockers.append(
            "MEMECOIN_USER_ID not set in .env — P&L extraction skipped "
            "(query the `users` table to pick a tenant)"
        )
        return
    try:
        snap = pnl_extractor.extract_pnl(cfg.memecoin_database_url, cfg.memecoin_user_id)
    except pnl_extractor.PnlExtractionError as e:
        daily.blockers.append(f"P&L extraction failed: {e}")
        return
    daily.pnl_24h_usd = snap.realized_24h_usd
    daily.pnl_prev_24h_usd = snap.realized_prev_24h_usd
    daily.portfolio_value_usd = snap.cumulative_total_value_usd
    daily.cumulative_total_pnl_usd = snap.cumulative_total_pnl_usd
    daily.cumulative_total_pnl_pct = snap.cumulative_total_pnl_pct
    daily.open_positions = snap.open_positions


def commit_and_push_agent_changes(repo: Path, daily: DailyRun) -> str | None:
    """Commit any staged changes from the agent step; push to auto/main.

    Returns the new commit's short SHA, or None if nothing to commit.
    """
    message = (f"chore(daily): {daily.mode} run #{daily.run_number} on {daily.date}\n\n"
               f"{daily.primary_task or '(no summary provided)'}")
    sha = github_helper.commit_all(
        repo, message,
        author_name=GIT_AUTHOR_NAME,
        author_email=GIT_AUTHOR_EMAIL,
    )
    if sha:
        github_helper.push(repo, AUTO_BRANCH)
    return sha


def run(*, mode: str | None, run_number_override: int | None, dry_run: bool) -> int:
    cfg = load_config()
    next_run_number = (run_number_override
                       if run_number_override is not None
                       else read_run_counter() + 1)
    effective_mode = mode or default_mode_for_run(next_run_number)

    daily = DailyRun(
        run_number=next_run_number,
        date=pt_today_str(),
        mode=effective_mode,
    )

    with tempfile.TemporaryDirectory(prefix="memecoin-daily-") as tmp:
        repo = Path(tmp) / "MemeCoinInvestor2026"
        github_helper.clone(cfg.github_clone_url, repo)
        github_helper.fetch_all(repo)
        github_helper.checkout_branch(repo, AUTO_BRANCH, create_from="main")
        daily.commits_24h = github_helper.commits_in_window(repo, hours=24)

        populate_pnl(daily, cfg)

        if dry_run:
            daily.primary_task = "DRY RUN — Agent SDK call skipped"
            daily.outcome = "pending"
            print("=" * 72)
            print(f"DRY RUN — date={daily.date} run#={daily.run_number} mode={daily.mode}")
            print("=" * 72)
            print(format_telegram(daily))
            print("=" * 72)
            print("(no Agent SDK call, no Trello card, no Telegram, no commits, "
                  "no daily-log write, run counter not bumped)")
            return 0

        agent_step.execute_agent_step(daily, repo, model=cfg.flush_model)

        sha = commit_and_push_agent_changes(repo, daily)
        if sha:
            # Pull the freshly-pushed commit into the report's commit list.
            daily.commits_24h = github_helper.commits_in_window(repo, hours=24)

        trello = TrelloClient(cfg.trello_api_key, cfg.trello_token)
        tg = TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id)

        card = upsert_trello_card(trello, cfg, daily)
        daily.trello_card_url = card.url
        # Refresh the Trello card so its own desc includes the self-link.
        trello.update_card(card.id, desc=format_trello_desc(daily))

        tg.send_message(format_telegram(daily))

        log_block = format_daily_log_block(daily, header_time=pt_now().strftime("%H:%M PT"))
        append_daily_log(log_block)

        bump_run_counter(next_run_number)

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MemeCoin daily-improvement agent")
    parser.add_argument("--mode", choices=("research", "improvement"), default=None)
    parser.add_argument("--run-number", type=int, default=None,
                        help="Override the persisted run counter for this invocation.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip all external side-effects; print payload only.")
    args = parser.parse_args(argv)
    return run(mode=args.mode, run_number_override=args.run_number, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
