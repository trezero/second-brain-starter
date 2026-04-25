"""MemeCoin daily-improvement agent — orchestrator.

Day 1 status: scaffolding. The agent step (research / pick-and-implement
improvement) is stubbed; runs in this state still produce a real Telegram
push, real Trello card, real daily-log entry, and exercise every other
moving part end-to-end.

Usage:
    run_daily.py [--mode research|improvement] [--run-number N] [--dry-run]

Defaults:
    --mode      research if run-number <= 2 else improvement
    --run-number persisted to .claude/data/state/memecoin_run_counter.txt;
                 each invocation increments it after a successful run

In --dry-run mode no external side-effects fire (no Trello card, no
Telegram push, no daily-log write, no commits, no push).
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

from memecoin.lib import github_helper  # noqa: E402
from memecoin.lib.config import Config, load_config  # noqa: E402
from memecoin.lib.report import DailyRun, format_daily_log_block, format_telegram, format_trello_desc  # noqa: E402
from memecoin.lib.telegram_client import TelegramClient  # noqa: E402
from memecoin.lib.trello_client import TrelloClient  # noqa: E402

REPO_ROOT = SCRIPTS_DIR.parent.parent
RUN_COUNTER_PATH = REPO_ROOT / ".claude" / "data" / "state" / "memecoin_run_counter.txt"
DAILY_LOG_DIR = REPO_ROOT / "Brain" / "Memory" / "daily"
GIT_AUTHOR_NAME = "MemeCoin Daily Bot"
GIT_AUTHOR_EMAIL = "memecoin-bot@trezero.local"


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


def inventory_docs(repo: Path) -> list[str]:
    """Top-level filenames in <repo>/docs/ — used to surface candidate sources."""
    docs = repo / "docs"
    if not docs.is_dir():
        return []
    return sorted(p.name for p in docs.iterdir() if p.is_file())


def stubbed_agent_step(run: DailyRun, repo: Path) -> None:
    """Placeholder for the Agent SDK call that picks today's task and acts on it.

    Fills the run with a research-phase outcome and a blocker noting the stub.
    Day 2-3 replaces this with a real Claude SDK invocation.
    """
    docs = inventory_docs(repo)
    run.primary_task = (
        f"Inventoried {len(docs)} top-level docs/* files; agent step not yet wired"
    )
    run.outcome = "pending"
    run.blockers.append("Agent SDK step not yet implemented — see run_daily.py:stubbed_agent_step")
    run.blockers.append("P&L extraction from paper-trading state not yet wired")


def append_daily_log(text_block: str) -> Path:
    DAILY_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = DAILY_LOG_DIR / f"{pt_today_str()}.md"
    lock_path = log_path.with_suffix(log_path.suffix + ".lock")
    with file_lock(lock_path, timeout=30.0):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(text_block)
    return log_path


def upsert_trello_card(client: TrelloClient, cfg: Config, run: DailyRun):
    """Create today's daily card in Atlas Inbox; if a card with the same name
    exists, update it instead. Returns the ``TrelloCard``.
    """
    name = f"MemeCoin Daily — {run.date} (run #{run.run_number})"
    desc = format_trello_desc(run)
    label_ids = (cfg.trello_label_atlas_ops, cfg.trello_label_product_eng)
    existing = client.find_card_on_board(cfg.trello_board_id, name)
    if existing:
        return client.update_card(existing.id, desc=desc)
    return client.create_card(cfg.trello_list_inbox, name=name, desc=desc, label_ids=label_ids)


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
        daily.commits_24h = github_helper.commits_in_window(repo, hours=24)
        stubbed_agent_step(daily, repo)

        telegram_text = format_telegram(daily)

        if dry_run:
            print("=" * 72)
            print(f"DRY RUN — date={daily.date} run#={daily.run_number} mode={daily.mode}")
            print("=" * 72)
            print(telegram_text)
            print("=" * 72)
            print("(no Trello card created, no Telegram sent, no daily log written, "
                  "no commits/push, run counter not bumped)")
            return 0

        trello = TrelloClient(cfg.trello_api_key, cfg.trello_token)
        tg = TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id)

        card = upsert_trello_card(trello, cfg, daily)
        daily.trello_card_url = card.url
        # Refresh the card desc so it includes its own self-link, matching
        # what we send to Telegram and write to the daily log.
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
