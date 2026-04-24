"""Real-contention stress test for shared.file_lock + shared.atomic_write.

Spawns N worker processes. Each performs M read-modify-write iterations on a
shared counter file under a file lock. The final value must equal N*M exactly;
any off-by-one indicates the lock is not serializing writers correctly.
"""
from __future__ import annotations

import json
import multiprocessing as mp
from pathlib import Path

import pytest

import shared

WORKERS = 20
ITERATIONS = 100
EXPECTED_FINAL = WORKERS * ITERATIONS  # 2000


def _worker(counter_path_str: str, lock_path_str: str, iterations: int) -> int:
    """Run a tight read-modify-write loop. Returns the count this worker saw."""
    counter_path = Path(counter_path_str)
    lock_path = Path(lock_path_str)
    own_increments = 0
    for _ in range(iterations):
        with shared.file_lock(lock_path, timeout=60.0, poll_interval=0.01):
            if counter_path.exists():
                current = json.loads(counter_path.read_text())["count"]
            else:
                current = 0
            current += 1
            shared.atomic_write(counter_path, json.dumps({"count": current}))
            own_increments += 1
    return own_increments


@pytest.mark.parametrize("run", range(5))
def test_file_lock_serializes_concurrent_writers(tmp_path, run):
    """Run the stress test five times to shake out flakes."""
    counter = tmp_path / "counter.json"
    lock = tmp_path / "counter.lock"

    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=WORKERS) as pool:
        results = pool.starmap(
            _worker,
            [(str(counter), str(lock), ITERATIONS)] * WORKERS,
        )

    # Each worker should have done exactly ITERATIONS increments
    assert sum(results) == EXPECTED_FINAL, f"Workers reported {sum(results)}"

    # The counter file should reflect all increments without loss
    final = json.loads(counter.read_text())["count"]
    assert final == EXPECTED_FINAL, (
        f"Expected {EXPECTED_FINAL} but counter shows {final} "
        f"(run={run}, lost {EXPECTED_FINAL - final} writes)"
    )

    # No tmp files left behind
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Leftover tmp files: {tmp_files}"
