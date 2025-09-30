from __future__ import annotations

from typing import Optional

from src.workers import worker_session

WORKER = "crawl"


def run(limit: int = 50, *, concurrency: Optional[int] = None) -> None:
    """Collect new Toutiao articles and persist them."""
    with worker_session(WORKER, limit=limit):
        raise NotImplementedError("crawl_toutiao.run needs an implementation")


__all__ = ["run"]
