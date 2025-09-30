from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter
from typing import Optional


def log_info(worker: str, message: str) -> None:
    print(f"[{worker}] {message}")


def log_error(worker: str, item: str, error: Exception) -> None:
    log_info(worker, f"ERROR {item}: {error}")


def log_summary(worker: str, *, ok: int, failed: int, skipped: Optional[int] = None) -> None:
    parts = [f"ok={ok}", f"failed={failed}"]
    if skipped is not None:
        parts.append(f"skipped={skipped}")
    log_info(worker, "result: " + " ".join(parts))


@contextmanager
def worker_session(worker: str, *, limit: Optional[int] = None) -> None:
    start = perf_counter()
    limit_note = f" (limit={limit})" if limit is not None else ""
    log_info(worker, f"start{limit_note}")
    try:
        yield
    finally:
        elapsed = perf_counter() - start
        log_info(worker, f"finished in {elapsed:.2f}s")


__all__ = ["log_info", "log_error", "log_summary", "worker_session"]
