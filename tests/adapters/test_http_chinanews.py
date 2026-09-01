from __future__ import annotations

from datetime import datetime, timezone

from src.adapters import http_chinanews


LIST_HTML = """
<div class="content_list">
  <li>
    <span class="dd_lm">[新疆]</span>
    <span class="dd_bt">
      <a href="/sh/2026/09-01/123456.shtml">测试新闻</a>
    </span>
    <span class="dd_time">09-01 10:30</span>
  </li>
</div>
"""

DETAIL_HTML = """
<html>
  <head><meta name="source" content="和田市融媒体中心"></head>
  <body>
    <h1>测试新闻</h1>
    <div id="source_baidu">来源：和田市融媒体中心</div>
    <div id="p-detail">
      <p>这是用于测试的新闻正文第一段，正文长度需要足够长。</p>
      <p>这是用于测试的新闻正文第二段，网页署名不应成为原始来源。</p>
    </div>
  </body>
</html>
"""


def test_parse_page_items_use_fixed_source() -> None:
    items, _, _ = http_chinanews._parse_page_items(LIST_HTML, None, 5, 0)

    assert len(items) == 1
    assert items[0].section == "中国新闻网"


def test_parse_detail_ignores_source_from_page_content() -> None:
    data = http_chinanews._parse_detail_html(
        DETAIL_HTML,
        "https://www.chinanews.com.cn/sh/2026/09-01/123456.shtml",
    )

    assert data["source"] == "中国新闻网"


def test_row_builders_force_china_news_as_source() -> None:
    item = http_chinanews.FeedItemLike(
        title="测试新闻",
        url="https://www.chinanews.com.cn/sh/2026/09-01/123456.shtml",
        section="具体栏目名称",
        publish_time_iso=None,
        raw={},
    )
    fetched_at = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)

    feed_row = http_chinanews.feed_item_to_row(
        item,
        "chinanews:/sh/2026/09-01/123456",
        fetched_at=fetched_at,
    )
    detail_row = http_chinanews.build_detail_update(
        item,
        "chinanews:/sh/2026/09-01/123456",
        {"source": "另一个具体来源", "content": "<p>正文</p>"},
        detail_fetched_at=fetched_at,
    )

    assert feed_row["source"] == detail_row["source"] == "中国新闻网"
