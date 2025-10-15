from datetime import datetime, timezone

from src.adapters import http_gmw


def test_make_article_id_normalizes_gmw_url() -> None:
    url = "https://news.gmw.cn/2025-10/15/content_38342943.htm"
    assert http_gmw.make_article_id(url) == "gmw:2025-10/15/content_38342943"


def test_article_conversion_preserves_core_fields() -> None:
    article = http_gmw.GMWArticle(
        title="Sample Title",
        url="https://news.gmw.cn/2025-10/15/content_38342943.htm",
        publish_time=1739558400,
        publish_time_iso=datetime(2025, 10, 15, tzinfo=timezone.utc),
        content_markdown="Sample body",
        raw_publish_text="2025-10-15",
    )
    fetched_at = datetime.now(timezone.utc)
    article_id = http_gmw.make_article_id(article.url)
    feed_row = http_gmw.article_to_feed_row(article, article_id, fetched_at=fetched_at)
    detail_row = http_gmw.article_to_detail_row(article, article_id, detail_fetched_at=fetched_at)

    assert feed_row["article_id"] == article_id
    assert feed_row["publish_time"] == article.publish_time
    assert feed_row["publish_time_iso"] == article.publish_time_iso
    assert feed_row["source"] == http_gmw.SOURCE_NAME
    assert feed_row["token"] is None

    assert detail_row["article_id"] == article_id
    assert detail_row["content_markdown"] == article.content_markdown
    assert detail_row["detail_fetched_at"] == fetched_at
