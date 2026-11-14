from __future__ import annotations

from typing import Any, Optional, Sequence

import pytest
import requests

from src.adapters.http_bjrb import BjrbArticle, BjrbIssueItem
from src.adapters.http_bjwb import BjwbIssueNotPublished
from src.business_config import (
    SOURCE_BY_KEY,
    validate_crawl_sources,
    validate_epaper_sources,
)
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


def _item(article_id: str, title: str) -> BjrbIssueItem:
    newid = article_id.removeprefix("bjwb:")
    return BjrbIssueItem(
        article_id=article_id,
        title=title,
        url=f"https://bjrbdzb.bjd.com.cn/bjwb/mobile/2026/20261114/20261114_001/{newid}.htm",
        publish_date="20261114",
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
        guide=None,
        subtitle=None,
        newid=item.newid,
    )


def test_bjwb_is_an_epaper_source_with_alias() -> None:
    definition = SOURCE_BY_KEY["bjwb"]

    assert definition.display_name == "北京晚报"
    assert definition.epaper is True
    assert definition.requires_accounts is False

    # 电子报来源不能加入每小时来源列表，但可以进电子报列表与单次 --sources 覆盖
    with pytest.raises(ValueError, match="电子报来源"):
        validate_crawl_sources(["bjwb"])
    assert validate_crawl_sources(["bjwb"], allow_epaper=True) == ["bjwb"]
    assert validate_epaper_sources(["beijingwanbao"]) == ["bjwb"]


def test_bjwb_flow_uses_beijing_evening_news_and_queues_keyword_hits(monkeypatch) -> None:
    first = _item("bjwb:bjwb2026111400101", "教育新闻")
    second = _item("bjwb:bjwb2026111400102", "普通新闻")

    def fake_list_issue_items(*, limit=None, timeout=None):
        return [first, second, first]

    def fake_fetch_article(item: BjrbIssueItem, *, timeout=None):
        content = "这是一条教育相关内容。" if item.article_id == first.article_id else "普通内容。"
        return _article(item, content)

    monkeypatch.setattr(crawl_sources, "bjwb_list_issue_items", fake_list_issue_items)
    monkeypatch.setattr(crawl_sources, "bjwb_fetch_article", fake_fetch_article)
    adapter = _DummyAdapter(missing_ids={first.article_id, second.article_id})

    stats = crawl_sources._run_bjwb_flow(
        adapter=adapter,
        keywords=["教育"],
        remaining_limit=10,
        timeout_value=5.0,
        delay_value=0.0,
    )

    assert stats["consumed"] == 2
    assert stats["ok"] == 2
    assert stats["skipped"] == 1
    assert adapter.feed_rows is not None and len(adapter.feed_rows) == 2
    assert {row["source"] for row in adapter.feed_rows} == {"北京晚报"}
    assert adapter.detail_rows is not None and len(adapter.detail_rows) == 2
    assert adapter.filtered_rows is not None and len(adapter.filtered_rows) == 1
    assert adapter.filtered_rows[0]["article_id"] == first.article_id
    assert adapter.filtered_rows[0]["source"] == "北京晚报"


def test_bjwb_flow_reports_unpublished_issue_without_counting_failure(
    monkeypatch,
    capsys,
) -> None:
    def fake_list_issue_items(*, limit=None, timeout=None):
        raise BjwbIssueNotPublished("北京晚报 20261115 期尚未上线")

    monkeypatch.setattr(crawl_sources, "bjwb_list_issue_items", fake_list_issue_items)
    adapter = _DummyAdapter(missing_ids=set())

    stats = crawl_sources._run_bjwb_flow(
        adapter=adapter,
        keywords=["教育"],
        remaining_limit=10,
        timeout_value=5.0,
        delay_value=0.0,
    )

    assert stats == {"consumed": 0, "ok": 0, "failed": 0, "skipped": 0}
    assert adapter.feed_rows is None
    assert "北京晚报 20261115 期尚未上线" in capsys.readouterr().out


@pytest.mark.parametrize(
    "error",
    [
        requests.HTTPError("500 Server Error"),
        requests.ConnectionError("connection reset"),
        RuntimeError("北京晚报 20261114 期页面未解析出稿件"),
    ],
    ids=["http-500", "network", "parse-failure"],
)
def test_bjwb_flow_keeps_real_failures_visible(monkeypatch, error: Exception) -> None:
    def fake_list_issue_items(*, limit=None, timeout=None):
        raise error

    monkeypatch.setattr(crawl_sources, "bjwb_list_issue_items", fake_list_issue_items)
    adapter = _DummyAdapter(missing_ids=set())

    with pytest.raises(type(error)):
        crawl_sources._run_bjwb_flow(
            adapter=adapter,
            keywords=["教育"],
            remaining_limit=10,
            timeout_value=5.0,
            delay_value=0.0,
        )


def test_bjwb_flow_keeps_image_only_article_with_empty_content(monkeypatch) -> None:
    """纯图片版正文为空，但不算失败：列表照常写入，详情正文留空。"""

    image_page = _item("bjwb:bjwb2026111400703", "■广告")

    def fake_list_issue_items(*, limit=None, timeout=None):
        return [image_page]

    def fake_fetch_article(item: BjrbIssueItem, *, timeout=None):
        return _article(item, "")

    monkeypatch.setattr(crawl_sources, "bjwb_list_issue_items", fake_list_issue_items)
    monkeypatch.setattr(crawl_sources, "bjwb_fetch_article", fake_fetch_article)
    adapter = _DummyAdapter(missing_ids={image_page.article_id})

    stats = crawl_sources._run_bjwb_flow(
        adapter=adapter,
        keywords=["教育"],
        remaining_limit=10,
        timeout_value=5.0,
        delay_value=0.0,
    )

    assert stats["failed"] == 0
    assert stats["ok"] == 1
    assert adapter.feed_rows is not None and len(adapter.feed_rows) == 1
    assert adapter.detail_rows is not None and len(adapter.detail_rows) == 1
    assert adapter.detail_rows[0]["content_markdown"] == ""
    assert adapter.filtered_rows is None
