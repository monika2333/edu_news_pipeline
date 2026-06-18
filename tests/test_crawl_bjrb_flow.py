from __future__ import annotations

from typing import Any, Optional, Sequence

from src.adapters.http_bjrb import BjrbArticle, BjrbIssueItem
from src.workers import crawl_sources


class _DummyAdapter:
    def __init__(self, missing_ids: Optional[set[str]] = None) -> None:
        self.missing_ids = missing_ids or set()
        self.feed_rows: Optional[list[dict[str, Any]]] = None
        self.detail_rows: Optional[list[dict[str, Any]]] = None
        self.filtered_rows: Optional[list[dict[str, Any]]] = None

    def upsert_raw_feed_rows(self, rows: Sequence[dict[str, Any]]) -> int:
        self.feed_rows = list(rows)
        return len(rows)

    def get_raw_articles_missing_content(self, article_ids: Sequence[str]) -> set[str]:
        return {article_id for article_id in article_ids if article_id in self.missing_ids}

    def update_raw_article_details(self, rows: Sequence[dict[str, Any]]) -> int:
        self.detail_rows = list(rows)
        return len(rows)

    def upsert_filtered_articles(self, rows: Sequence[dict[str, Any]]) -> int:
        self.filtered_rows = list(rows)
        return len(rows)


def _item(article_id: str, title: str) -> BjrbIssueItem:
    newid = article_id.removeprefix("bjrb:")
    return BjrbIssueItem(
        article_id=article_id,
        title=title,
        url=f"https://bjrbdzb.bjd.com.cn/bjrb/mobile/2026/20260618/20260618_001/{newid}.htm",
        publish_date="20260618",
        page_name="第1版 头版",
        newid=newid,
    )


def _article(item: BjrbIssueItem, content: str) -> BjrbArticle:
    return BjrbArticle(
        article_id=item.article_id,
        title=item.title,
        url=item.url,
        publish_date=item.publish_date,
        content_markdown=content,
        page_name=item.page_name,
        guide="导语",
        subtitle=None,
        newid=item.newid,
    )


def test_bjrb_flow_queues_only_keyword_hits(monkeypatch) -> None:
    first = _item("bjrb:bjrb2026061800101", "教育新闻")
    second = _item("bjrb:bjrb2026061800102", "普通新闻")
    calls: list[str] = []

    def fake_list_issue_items(*, limit=None, timeout=None):
        return [first, second, first]

    def fake_fetch_article(item: BjrbIssueItem, *, timeout=None):
        calls.append(item.article_id)
        content = "这是一条教育相关内容。" if item.article_id == first.article_id else "普通内容。"
        return _article(item, content)

    monkeypatch.setattr(crawl_sources, "bjrb_list_issue_items", fake_list_issue_items)
    monkeypatch.setattr(crawl_sources, "bjrb_fetch_article", fake_fetch_article)
    adapter = _DummyAdapter(missing_ids={first.article_id, second.article_id})

    stats = crawl_sources._run_bjrb_flow(
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


def test_bjrb_flow_skips_detail_for_existing_content(monkeypatch) -> None:
    first = _item("bjrb:bjrb2026061800101", "已有正文")

    def fake_list_issue_items(*, limit=None, timeout=None):
        return [first]

    def fake_fetch_article(item: BjrbIssueItem, *, timeout=None):
        raise AssertionError("detail fetch should not run for populated articles")

    monkeypatch.setattr(crawl_sources, "bjrb_list_issue_items", fake_list_issue_items)
    monkeypatch.setattr(crawl_sources, "bjrb_fetch_article", fake_fetch_article)
    adapter = _DummyAdapter(missing_ids=set())

    stats = crawl_sources._run_bjrb_flow(
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
