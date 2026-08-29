from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Optional, Sequence

from src.workers import crawl_sources


class _Ingest:
    def __init__(self, *, missing_ids: set[str]) -> None:
        self.missing_ids = missing_ids
        self.feed_rows: list[dict[str, Any]] = []
        self.detail_rows: list[dict[str, Any]] = []
        self.filtered_rows: list[dict[str, Any]] = []
        self.missing_queries = 0

    @staticmethod
    def get_existing_raw_ids() -> set[str]:
        return {"already-listed"}

    def upsert_raw_feed_rows(self, rows: Sequence[dict[str, Any]]) -> int:
        self.feed_rows = list(rows)
        return len(rows)

    def get_raw_ids_missing_content(self, article_ids: Sequence[str]) -> set[str]:
        self.missing_queries += 1
        return set(article_ids) & self.missing_ids

    def update_raw_details(self, rows: Sequence[dict[str, Any]]) -> int:
        self.detail_rows = list(rows)
        return len(rows)

    def upsert_filtered(self, rows: Sequence[dict[str, Any]]) -> int:
        self.filtered_rows = list(rows)
        return len(rows)


class _Adapter:
    def __init__(self, *, missing_ids: set[str]) -> None:
        self.ingest = _Ingest(missing_ids=missing_ids)


def _prepare_feed(item: str, _fetched_at: datetime) -> tuple[str, dict[str, Any]]:
    return item, {"article_id": item, "title": item, "source": "test"}


def _detail_row(item: str, article_id: str, fetched_at: datetime) -> dict[str, Any]:
    return {
        "article_id": article_id,
        "title": item,
        "source": "test",
        "content_markdown": f"education detail for {item}",
        "detail_fetched_at": fetched_at,
    }


def test_shared_flow_fetches_only_missing_content_and_counts_duplicates() -> None:
    adapter = _Adapter(missing_ids={"first"})
    received_existing_ids: set[str] = set()

    def list_items(_limit: Optional[int], existing_ids: set[str]) -> list[str]:
        received_existing_ids.update(existing_ids)
        return ["first", "second", "first"]

    stats = crawl_sources._run_source_flow(
        adapter=adapter,
        flow=crawl_sources.SourceFlow(
            source="test",
            display_name="Test",
            list_items=list_items,
            prepare_feed=_prepare_feed,
            fetch_detail=_detail_row,
        ),
        keywords=["education"],
        remaining_limit=10,
    )

    assert received_existing_ids == {"already-listed"}
    assert stats == {"consumed": 2, "ok": 1, "failed": 0, "skipped": 2}
    assert [row["article_id"] for row in adapter.ingest.feed_rows] == ["first", "second"]
    assert [row["article_id"] for row in adapter.ingest.detail_rows] == ["first"]
    assert [row["article_id"] for row in adapter.ingest.filtered_rows] == ["first"]


def test_shared_flow_accepts_details_from_list_without_missing_query() -> None:
    adapter = _Adapter(missing_ids=set())
    flow = crawl_sources.SourceFlow(
        source="embedded",
        display_name="Embedded",
        list_items=lambda _limit, _existing: ["article"],
        prepare_feed=_prepare_feed,
        fetch_detail=_detail_row,
        details_in_list=True,
    )

    stats = crawl_sources._run_source_flow(
        adapter=adapter,
        flow=flow,
        keywords=[],
        remaining_limit=None,
    )

    assert stats == {"consumed": 1, "ok": 1, "failed": 0, "skipped": 0}
    assert adapter.ingest.missing_queries == 0
    assert [row["article_id"] for row in adapter.ingest.detail_rows] == ["article"]


def test_run_dispatches_source_alias_through_registry(monkeypatch) -> None:
    calls: list[Optional[int]] = []

    @contextmanager
    def worker_session(*_args: Any, **_kwargs: Any):
        yield

    def run_tencent_flow(**kwargs: Any) -> crawl_sources.CrawlStats:
        calls.append(kwargs["remaining_limit"])
        return {"consumed": 1, "ok": 1, "failed": 0, "skipped": 0}

    monkeypatch.setattr(
        crawl_sources,
        "get_settings",
        lambda: SimpleNamespace(process_limit=None, keywords_path=None),
    )
    monkeypatch.setattr(crawl_sources, "get_adapter", object)
    monkeypatch.setattr(crawl_sources, "worker_session", worker_session)
    monkeypatch.setattr(crawl_sources, "_run_tencent_flow", run_tencent_flow)

    crawl_sources.run(limit=2, sources=["qq"])

    assert calls == [2]
