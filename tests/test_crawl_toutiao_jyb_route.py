from __future__ import annotations

from datetime import datetime, timezone

from src.adapters.http_chinaeducationdaily import make_article_id
from src.adapters.http_toutiao import FeedItem
from src.workers import crawl_sources


def test_jyb_article_id_normalizes_url_path() -> None:
    url = "https://www.jyb.cn/rmtzcg/xwy/wzxw/202606/t20260610_211135.html?from=toutiao"

    assert make_article_id(url) == "jyb:/rmtzcg/xwy/wzxw/202606/t20260610_211135"


def test_toutiao_detail_uses_jyb_parser_for_jyb_url(monkeypatch) -> None:
    detail_url = "https://www.jyb.cn/rmtzcg/xwy/wzxw/202606/t20260610_211135.html"
    item = FeedItem(
        token="author-token",
        profile_url="https://www.toutiao.com/c/user/token/author-token/",
        title="feed title",
        summary="feed summary",
        source="中国教育新闻网",
        publish_time=1781049600,
        publish_time_iso="2026-06-10T00:00:00+08:00",
        article_url=detail_url,
        comment_count=12,
        digg_count=34,
        raw={"group_id": "1234567890123456789"},
    )

    def fake_jyb_fetch_detail(url: str) -> dict[str, object]:
        assert url == detail_url
        return {
            "title": "web title",
            "source": "中国教育新闻网",
            "publish_time_iso": "2026-06-10T00:00:00+08:00",
            "url": detail_url,
            "content_markdown": "web parsed body",
        }

    monkeypatch.setattr(crawl_sources, "jyb_fetch_detail", fake_jyb_fetch_detail)

    row = crawl_sources._build_toutiao_detail_update(
        item,
        "1234567890123456789",
        {"url": detail_url, "content": "<p>toutiao body</p>"},
        detail_fetched_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
    )

    assert row["article_id"] == "1234567890123456789"
    assert row["content_markdown"] == "web parsed body"
    assert row["token"] == "author-token"
    assert row["summary"] == "feed summary"
    assert row["comment_count"] == 12
    assert row["digg_count"] == 34
