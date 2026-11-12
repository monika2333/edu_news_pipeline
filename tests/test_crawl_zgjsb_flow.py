from __future__ import annotations

from typing import Any, Optional, Sequence

import pytest

from src.adapters.http_chinateacher import ZgjsbArticle, ZgjsbIssueItem
from src.business_config import SOURCE_BY_KEY, validate_crawl_sources
from src.workers import crawl_sources


class _DummyIngestNamespace:
    def __init__(self, adapter: _DummyAdapter) -> None:
        self._adapter = adapter

    def upsert_raw_feed_rows(self, rows: Sequence[dict[str, Any]]) -> int:
        self._adapter.feed_rows = list(rows)
        return len(rows)

    def get_raw_ids_missing_content(self, article_ids: Sequence[str]) -> set[str]:
        return {
            article_id
            for article_id in article_ids
            if article_id in self._adapter.missing_ids
        }

    def update_raw_details(self, rows: Sequence[dict[str, Any]]) -> int:
        self._adapter.detail_rows = list(rows)
        return len(rows)

    def upsert_filtered(self, rows: Sequence[dict[str, Any]]) -> int:
        self._adapter.filtered_rows = list(rows)
        return len(rows)


class _DummyAdapter:
    def __init__(self, missing_ids: Optional[set[str]] = None) -> None:
        self.missing_ids = missing_ids or set()
        self.feed_rows: Optional[list[dict[str, Any]]] = None
        self.detail_rows: Optional[list[dict[str, Any]]] = None
        self.filtered_rows: Optional[list[dict[str, Any]]] = None
        self.ingest = _DummyIngestNamespace(self)


def _item(article_id: str, title: str) -> ZgjsbIssueItem:
    content_id = article_id.removeprefix("zgjsb:")
    return ZgjsbIssueItem(
        article_id=article_id,
        title=title,
        url=(
            "http://chinateacher.jyb.cn/zgjsb/html/2026-11/11/"
            f"content_{content_id}.htm"
        ),
        publish_date="2026-11-11",
        page_name="第02版：新闻",
        content_id=content_id,
    )


def _article(item: ZgjsbIssueItem, content: str) -> ZgjsbArticle:
    return ZgjsbArticle(
        article_id=item.article_id,
        title=item.title,
        url=item.url,
        publish_date=item.publish_date,
        content_markdown=content,
        page_name=item.page_name,
        introtitle="地方传真",
        subtitle=None,
        author=None,
    )


def test_b1_zgjsb_flow_queues_only_keyword_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _item("zgjsb:650342", "教育新闻")
    second = _item("zgjsb:650345", "普通新闻")
    calls: list[str] = []

    def fake_list_issue_items(*, limit=None, timeout=None):
        return [first, second, first]

    def fake_fetch_article(item: ZgjsbIssueItem, *, timeout=None):
        calls.append(item.article_id)
        content = (
            "这是一条教育相关内容。"
            if item.article_id == first.article_id
            else "普通内容。"
        )
        return _article(item, content)

    monkeypatch.setattr(crawl_sources, "zgjsb_list_issue_items", fake_list_issue_items)
    monkeypatch.setattr(crawl_sources, "zgjsb_fetch_article", fake_fetch_article)
    adapter = _DummyAdapter(missing_ids={first.article_id, second.article_id})

    stats = crawl_sources._run_zgjsb_flow(
        adapter=adapter,
        keywords=["教育"],
        remaining_limit=10,
        timeout_value=5.0,
        delay_value=0.0,
    )

    assert stats["consumed"] == 2
    assert stats["ok"] == 2
    assert stats["skipped"] == 1
    assert calls == [first.article_id, second.article_id]
    assert adapter.feed_rows is not None and len(adapter.feed_rows) == 2
    assert adapter.detail_rows is not None and len(adapter.detail_rows) == 2
    assert adapter.filtered_rows is not None and len(adapter.filtered_rows) == 1
    assert adapter.filtered_rows[0]["article_id"] == first.article_id
    assert adapter.filtered_rows[0]["keywords"] == ["教育"]


def test_b2_zgjsb_flow_skips_detail_for_existing_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _item("zgjsb:650342", "已有正文")

    def fake_list_issue_items(*, limit=None, timeout=None):
        return [first]

    def fake_fetch_article(item: ZgjsbIssueItem, *, timeout=None):
        raise AssertionError("detail fetch should not run for populated articles")

    monkeypatch.setattr(crawl_sources, "zgjsb_list_issue_items", fake_list_issue_items)
    monkeypatch.setattr(crawl_sources, "zgjsb_fetch_article", fake_fetch_article)
    adapter = _DummyAdapter(missing_ids=set())

    stats = crawl_sources._run_zgjsb_flow(
        adapter=adapter,
        keywords=["教育"],
        remaining_limit=10,
        timeout_value=5.0,
        delay_value=0.0,
    )

    assert stats["consumed"] == 1
    assert stats["ok"] == 0
    assert stats["skipped"] == 1
    assert adapter.feed_rows is not None and len(adapter.feed_rows) == 1
    assert adapter.detail_rows is None
    assert adapter.filtered_rows is None


def test_b3_zgjsb_is_a_daily_only_source_with_alias() -> None:
    definition = SOURCE_BY_KEY["zgjsb"]

    assert definition.display_name == "中国教师报"
    assert definition.daily_only is True

    # validate_crawl_sources 的现有文案是「仅允许每日单独任务运行」，不是「仅每日任务」。
    with pytest.raises(ValueError, match="仅允许每日单独任务运行"):
        validate_crawl_sources(["zgjsb"])
    assert validate_crawl_sources(["zgjsb"], allow_daily=True) == ["zgjsb"]
    assert validate_crawl_sources(["chinateacher"], allow_daily=True) == ["zgjsb"]
