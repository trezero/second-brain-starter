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


def with_retry(func, *, max_retries: int = 3, base_delay: float = 1.0,
               max_delay: float = 30.0,
               retry_on_status=(429, 500, 502, 503, 504)):
    """Call ``func()``, retrying on transient failures with exponential backoff + jitter.

    ``func`` must take no arguments. Retries on:
        - any exception with a ``.status_code`` in ``retry_on_status``
        - any exception with a ``.status`` in ``retry_on_status`` (SDK variant)
        - any ``OSError`` whose ``errno`` is ECONNRESET or ETIMEDOUT

    Returns the function's return value on success. Re-raises the final
    exception after ``max_retries`` retries.
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            if attempt >= max_retries:
                raise
            status = getattr(e, "status_code", None)
            if status is None:
                status = getattr(e, "status", None)
            transient = status in retry_on_status
            if not transient and isinstance(e, OSError) and e.errno in (
                errno.ECONNRESET, errno.ETIMEDOUT,
            ):
                transient = True
            if not transient:
                raise
            delay = min(max_delay, base_delay * (2 ** attempt))
            delay += random.uniform(0, delay * 0.1)  # up to 10% jitter
            time.sleep(delay)


def pt_now() -> datetime:
    """Current time in America/Los_Angeles (timezone-aware)."""
    return datetime.now(PT)


def pt_today_str() -> str:
    """'YYYY-MM-DD' in Pacific Time."""
    return pt_now().strftime("%Y-%m-%d")
