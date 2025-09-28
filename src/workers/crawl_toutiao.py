from __future__ import annotations


def run(limit: int = 50) -> None:
    """Collect new Toutiao articles and persist them.

    Port the ingestion logic from `tools/toutiao_scraper.py` into this worker
    so it can be scheduled independently.
    """
    raise NotImplementedError("crawl_toutiao.run needs an implementation")


__all__ = ["run"]
