from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from src.adapters.http_laodongwubao import (
    ArticleRecord,
    article_to_detail_row,
    article_to_feed_row,
    make_article_id,
    parse_article,
    SOURCE_NAME,
)


HTML_SAMPLE = """\
<html>
  <head>
    <title>示例标题</title>
  </head>
  <body>
    <!--enpproperty
      <founder-title>示例标题</founder-title>
      <founder-subtitle>副标题</founder-subtitle>
      <founder-date>2025-11-13</founder-date>
    -->
    <founder-content>
      <p>第一段</p>
      <p>第二段</p>
    </founder-content>
  </body>
</html>
"""


def test_make_article_id_normalizes_urls():
    url = "https://ldwb.workerbj.cn/content/2025-11/13/content_161580.htm"
    assert make_article_id(url) == "laodongwubao:/content/2025-11/13/content_161580"


def test_parse_article_extracts_metadata():
    record = parse_article(HTML_SAMPLE, "https://ldwb.workerbj.cn/content/2025-11/13/content_161580.htm", "A01")
    assert record.title == "示例标题｜副标题"
    assert record.publish_date == "2025-11-13"
    assert "第一段" in record.content_markdown
    publish_dt = record.publish_datetime()
    assert publish_dt == datetime(2025, 11, 13, 0, 0, tzinfo=timezone(timedelta(hours=8)))


def test_article_rows_use_fixed_source():
    record = ArticleRecord(
        article_id=make_article_id("https://ldwb.workerbj.cn/content/2025-11/13/content_161580.htm"),
        title="示例标题",
        url="https://ldwb.workerbj.cn/content/2025-11/13/content_161580.htm",
        publish_date="2025-11-13",
        content_markdown="段落一\n\n段落二",
        page_name="A01",
    )
    fetched_at = datetime(2025, 11, 13, 8, 0, tzinfo=timezone.utc)
    feed_row = article_to_feed_row(record, fetched_at=fetched_at)
    assert feed_row["source"] == SOURCE_NAME
    assert feed_row["publish_time_iso"] == datetime(2025, 11, 13, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    detail_row = article_to_detail_row(record, detail_fetched_at=fetched_at)
    assert detail_row["content_markdown"] == "段落一\n\n段落二"
    assert detail_row["source"] == SOURCE_NAME
