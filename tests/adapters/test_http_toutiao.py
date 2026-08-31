from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.adapters.http_toutiao import FeedItem, build_detail_update, feed_item_to_row


def _feed_item(**overrides: object) -> FeedItem:
    values: dict[str, Any] = {
        "token": "feed-token",
        "profile_url": "https://example.com/profile/feed-token/",
        "title": " Feed title ",
        "summary": "Feed summary",
        "source": " Feed source ",
        "publish_time": 1_725_888_000,
        "publish_time_iso": "2024-09-09T20:00:00+08:00",
        "article_url": " https://example.com/feed-article ",
        "comment_count": 12,
        "digg_count": 34,
        "raw": {"group_id": "1234567890123456"},
    }
    values.update(overrides)
    return FeedItem(**values)


def test_feed_item_to_row_preserves_all_fields() -> None:
    fetched_at = datetime(2024, 9, 10, 8, 30, tzinfo=timezone.utc)
    item = _feed_item()

    row = feed_item_to_row(item, "1234567890123456", fetched_at=fetched_at)

    assert row == {
        "token": "feed-token",
        "profile_url": "https://example.com/profile/feed-token/",
        "article_id": "1234567890123456",
        "title": " Feed title ",
        "source": " Feed source ",
        "publish_time": 1_725_888_000,
        "publish_time_iso": datetime.fromisoformat("2024-09-09T20:00:00+08:00"),
        "url": " https://example.com/feed-article ",
        "summary": "Feed summary",
        "comment_count": 12,
        "digg_count": 34,
        "fetched_at": fetched_at,
    }


def test_build_detail_update_prefers_detail_fields_and_skips_invalid_publish_time() -> None:
    detail_fetched_at = datetime(2024, 9, 10, 9, 45, tzinfo=timezone.utc)
    item = _feed_item(publish_time="1725888000")
    data = {
        "title": " Detail title ",
        "source": " Detail source ",
        "detail_source": " Ignored detail source ",
        "publish_time": "not-an-integer",
        "publish_time_iso": "2024-09-10T10:11:12+08:00",
        "url": " https://example.com/detail-article ",
        "content": "<p>First paragraph</p><p>Second paragraph</p>",
    }

    row = build_detail_update(
        item,
        "1234567890123456",
        data,
        detail_fetched_at=detail_fetched_at,
    )

    assert row == {
        "token": "feed-token",
        "profile_url": "https://example.com/profile/feed-token/",
        "article_id": "1234567890123456",
        "title": "Detail title",
        "source": "Detail source",
        "publish_time": 1_725_888_000,
        "publish_time_iso": datetime.fromisoformat("2024-09-10T10:11:12+08:00"),
        "url": "https://example.com/detail-article",
        "summary": "Feed summary",
        "comment_count": 12,
        "digg_count": 34,
        "content_markdown": "First paragraph\n\nSecond paragraph",
        "detail_fetched_at": detail_fetched_at,
    }
    assert "fetched_at" not in row


def test_build_detail_update_uses_detail_source_candidate() -> None:
    detail_fetched_at = datetime(2024, 9, 10, 9, 45, tzinfo=timezone.utc)
    item = _feed_item()

    row = build_detail_update(
        item,
        "1234567890123456",
        {"detail_source": " Detail-only source ", "content": "Body"},
        detail_fetched_at=detail_fetched_at,
    )

    assert row["source"] == "Detail-only source"


def test_build_detail_update_falls_back_entirely_to_feed_item() -> None:
    detail_fetched_at = datetime(2024, 9, 10, 9, 45, tzinfo=timezone.utc)
    item = _feed_item(
        publish_time=1_725_888_000,
        publish_time_iso="2001-02-03T04:05:06+00:00",
    )

    row = build_detail_update(
        item,
        "1234567890123456",
        {},
        detail_fetched_at=detail_fetched_at,
    )

    expected_iso = datetime.fromtimestamp(1_725_888_000, tz=timezone.utc).astimezone()
    assert row == {
        "token": "feed-token",
        "profile_url": "https://example.com/profile/feed-token/",
        "article_id": "1234567890123456",
        "title": "Feed title",
        "source": "Feed source",
        "publish_time": 1_725_888_000,
        "publish_time_iso": expected_iso,
        "url": "https://example.com/feed-article",
        "summary": "Feed summary",
        "comment_count": 12,
        "digg_count": 34,
        "content_markdown": "",
        "detail_fetched_at": detail_fetched_at,
    }
    assert row["publish_time_iso"] != datetime.fromisoformat("2001-02-03T04:05:06+00:00")
    assert "fetched_at" not in row
