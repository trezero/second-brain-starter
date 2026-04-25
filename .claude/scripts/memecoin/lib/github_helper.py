"""Git operations for the MemeCoin daily agent.

All ops happen against a fresh shallow clone in a tmp dir so the remote
scheduled agent (which runs in a clean container) does not need any local
state across runs. Authentication uses a fine-grained PAT injected into the
clone URL.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class CommitInfo:
    sha_short: str
    subject: str
    branch: str
    iso_timestamp: str


class GitError(RuntimeError):
    """Raised when a git command exits non-zero."""


def _run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GitError(f"{' '.join(cmd)} -> {proc.returncode}: {proc.stderr.strip()}")
    return proc


def clone(clone_url: str, dest: Path, *, depth: int | None = None) -> Path:
    """Fresh clone into ``dest``. Caller is responsible for cleanup."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone"]
    if depth:
        cmd += ["--depth", str(depth), "--no-single-branch"]
    cmd += [clone_url, str(dest)]
    _run(cmd)
    return dest


def fetch_all(repo: Path) -> None:
    _run(["git", "fetch", "--all", "--prune"], cwd=repo)


def checkout_branch(repo: Path, branch: str, *, create_from: str | None = None) -> None:
    """Checkout ``branch``. If it does not exist locally, create from ``create_from`` (or origin/branch)."""
    existing = _run(["git", "rev-parse", "--verify", f"refs/heads/{branch}"], cwd=repo, check=False)
    if existing.returncode == 0:
        _run(["git", "checkout", branch], cwd=repo)
        return
    remote = _run(["git", "rev-parse", "--verify", f"refs/remotes/origin/{branch}"], cwd=repo, check=False)
    if remote.returncode == 0:
        _run(["git", "checkout", "-b", branch, f"origin/{branch}"], cwd=repo)
        return
    base = create_from or "main"
    _run(["git", "checkout", "-b", branch, base], cwd=repo)


def commit_all(repo: Path, message: str, *, author_name: str, author_email: str) -> str | None:
    """Stage everything and commit; returns short SHA, or None if nothing to commit."""
    _run(["git", "add", "-A"], cwd=repo)
    diff = _run(["git", "diff", "--cached", "--quiet"], cwd=repo, check=False)
    if diff.returncode == 0:
        return None
    env = ["-c", f"user.name={author_name}", "-c", f"user.email={author_email}"]
    _run(["git", *env, "commit", "-m", message], cwd=repo)
    sha = _run(["git", "rev-parse", "--short", "HEAD"], cwd=repo).stdout.strip()
    return sha


def push(repo: Path, branch: str, *, force_with_lease: bool = False) -> None:
    cmd = ["git", "push", "origin", branch]
    if force_with_lease:
        cmd.insert(2, "--force-with-lease")
    _run(cmd, cwd=repo)


def fast_forward(repo: Path, source_branch: str, target_branch: str) -> bool:
    """Fast-forward ``target_branch`` to ``source_branch``. Returns True if FF succeeded.

    Refuses (returns False) if the merge would not be a fast-forward — never
    rewrites history.
    """
    checkout_branch(repo, target_branch)
    proc = _run(["git", "merge", "--ff-only", source_branch], cwd=repo, check=False)
    return proc.returncode == 0


def commits_in_window(repo: Path, *, hours: int = 24,
                      branches: tuple[str, ...] = ("main", "auto/main")) -> list[CommitInfo]:
    """Return commits authored in the trailing ``hours`` window across the given branches.

    De-duplicates by SHA. Branches that don't exist locally are silently skipped.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    seen: set[str] = set()
    out: list[CommitInfo] = []
    for br in branches:
        exists = _run(["git", "rev-parse", "--verify", br], cwd=repo, check=False)
        if exists.returncode != 0:
            continue
        proc = _run(
            ["git", "log", br, f"--since={since}", "--pretty=format:%h%x09%s%x09%cI"],
            cwd=repo,
        )
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            sha, subj, ts = parts
            if sha in seen:
                continue
            seen.add(sha)
            out.append(CommitInfo(sha_short=sha, subject=subj, branch=br, iso_timestamp=ts))
    out.sort(key=lambda c: c.iso_timestamp, reverse=True)
    return out
