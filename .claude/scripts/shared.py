"""Cross-platform concurrency utilities for Second Brain scripts.

Provides:
    - file_lock(path): exclusive advisory file lock (context manager)
    - with_retry(func, ...): exponential backoff + jitter for transient API errors
    - atomic_write(path, data): tmp-file-then-rename atomic write
    - pt_now() / pt_today_str(): Pacific Time helpers
"""
from contextlib import contextmanager
from datetime import datetime
import errno
import os
import platform
import random
import time
from pathlib import Path
from zoneinfo import ZoneInfo

if platform.system() == "Windows":  # pragma: no cover - exercised on Windows only
    import msvcrt
else:
    import fcntl

PT = ZoneInfo("America/Los_Angeles")


def atomic_write(path, data: str, encoding: str = "utf-8") -> None:
    """Write `data` to `path` atomically via tmp + os.replace.

    Creates parent directories as needed. Fsyncs before rename so a crash
    cannot leave a partially-written file at the target path.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding=encoding) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def pt_now() -> datetime:
    """Current time in America/Los_Angeles (timezone-aware)."""
    return datetime.now(PT)


def pt_today_str() -> str:
    """'YYYY-MM-DD' in Pacific Time."""
    return pt_now().strftime("%Y-%m-%d")
