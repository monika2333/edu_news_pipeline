from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import pytest

from src.adapters.http_gmw import GMWArticle
from src.workers import crawl_sources


class _DummyAdapter:
    def __init__(self) -> None:
        self.feed_rows: Optional[List[Dict[str, Any]]] = None
        self.detail_rows: Optional[List[Dict[str, Any]]] = None
        self.pending_payloads: List[Dict[str, Any]] = []
        self.pending_keywords: List[Sequence[str]] = []

    def upsert_raw_feed_rows(self, rows: Sequence[Dict[str, Any]]) -> int:
        self.feed_rows = list(rows)
        return len(rows)

    def update_raw_article_details(self, rows: Sequence[Dict[str, Any]]) -> int:
        self.detail_rows = list(rows)
        return len(rows)

    def insert_pending_summary(
        self,
        payload: Dict[str, Any],
        *,
        keywords: Optional[Sequence[str]] = None,
        fetched_at: Optional[str] = None,
    ) -> None:
        payload_copy = dict(payload)
        payload_copy["fetched_at"] = fetched_at
        self.pending_payloads.append(payload_copy)
        self.pending_keywords.append(tuple(keywords or ()))


@pytest.fixture
def sample_article() -> GMWArticle:
    return GMWArticle(
        title="Policy Update",
        url="https://news.gmw.cn/2025-10/15/content_38342943.htm",
        publish_time=1739558400,
        publish_time_iso=datetime(2025, 10, 15, tzinfo=timezone.utc),
        content_markdown="This article covers new education policy in detail.",
        raw_publish_text="2025-10-15",
    )


def test_gmw_flow_processes_article(monkeypatch: pytest.MonkeyPatch, sample_article: GMWArticle) -> None:
    def _fake_fetch_articles(*, limit=None, base_url=None, timeout=None):  # pragma: no cover - signature compatibility
        return [sample_article]

    monkeypatch.setattr(crawl_sources, "gmw_fetch_articles", _fake_fetch_articles)
    adapter = _DummyAdapter()

    stats = crawl_sources._run_gmw_flow(  # type: ignore[attr-defined] - accessing internal helper for tests
        adapter=adapter,
        keywords=["education"],
        remaining_limit=5,
        base_url="https://news.gmw.cn/node_4108.htm",
        timeout_value=5.0,
    )

    assert stats["consumed"] == 1
    assert stats["ok"] == 1
    assert stats["failed"] == 0
    assert adapter.feed_rows is not None and adapter.feed_rows[0]["article_id"].startswith("gmw:")
    assert adapter.detail_rows is not None and adapter.detail_rows[0]["content_markdown"].startswith("This article")
    assert adapter.pending_payloads and adapter.pending_keywords[0] == ("education",)


def test_gmw_flow_counts_duplicates(monkeypatch: pytest.MonkeyPatch, sample_article: GMWArticle) -> None:
    def _fake_fetch_articles(*, limit=None, base_url=None, timeout=None):  # pragma: no cover - signature compatibility
        return [sample_article, sample_article]

    monkeypatch.setattr(crawl_sources, "gmw_fetch_articles", _fake_fetch_articles)
    adapter = _DummyAdapter()

    stats = crawl_sources._run_gmw_flow(  # type: ignore[attr-defined] - accessing internal helper for tests
        adapter=adapter,
        keywords=["education"],
        remaining_limit=5,
        base_url="https://news.gmw.cn/node_4108.htm",
        timeout_value=5.0,
    )

    assert stats["consumed"] == 1
    assert stats["skipped"] == 1
    assert adapter.feed_rows is not None and len(adapter.feed_rows) == 1
    assert adapter.pending_payloads and len(adapter.pending_payloads) == 1
