from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import pytest

from src.adapters import http_chinadaily
from src.adapters import http_chinaeducationdaily
from src.adapters import http_chinanews


AdapterFunction = Callable[..., dict[str, Any]]


ADAPTERS = (
    (
        "chinadaily",
        http_chinadaily.FeedItemLike,
        http_chinadaily.feed_item_to_row,
        http_chinadaily.build_detail_update,
        "中国日报",
    ),
    (
        "chinaeducationdaily",
        http_chinaeducationdaily.FeedItemLike,
        http_chinaeducationdaily.feed_item_to_row,
        http_chinaeducationdaily.build_detail_update,
        "中国教育报",
    ),
    (
        "chinanews",
        http_chinanews.FeedItemLike,
        http_chinanews.feed_item_to_row,
        http_chinanews.build_detail_update,
        "",
    ),
)


@pytest.mark.parametrize(("name", "item_type", "feed_row", "detail_row", "default_source"), ADAPTERS)
def test_feed_rows_have_complete_shared_shape(
    name: str,
    item_type: type[Any],
    feed_row: AdapterFunction,
    detail_row: AdapterFunction,
    default_source: str,
) -> None:
    del detail_row, default_source
    fetched_at = datetime(2024, 9, 10, 8, 30, tzinfo=timezone.utc)
    publish_dt = datetime.fromisoformat("2024-09-09T20:00:00+08:00")
    item = item_type(
        title=f"{name} feed title",
        url=f"https://example.com/{name}/feed",
        section=f"{name} section",
        publish_time_iso=publish_dt.isoformat(),
        raw={"name": name},
    )

    row = feed_row(item, f"{name}:article", fetched_at=fetched_at)

    assert row == {
        "token": None,
        "profile_url": None,
        "article_id": f"{name}:article",
        "title": f"{name} feed title",
        "source": f"{name} section",
        "publish_time": int(publish_dt.astimezone(timezone.utc).timestamp()),
        "publish_time_iso": publish_dt,
        "url": f"https://example.com/{name}/feed",
        "summary": None,
        "comment_count": None,
        "digg_count": None,
        "fetched_at": fetched_at,
    }
    assert "content_markdown" not in row
    assert "detail_fetched_at" not in row


DETAIL_TIME_CASES = (
    ("chinadaily", "2024-09-10T11:12:13+08:00", 123, 123),
    (
        "chinaeducationdaily",
        "2024-09-10T11:12:13+08:00",
        None,
        int(datetime.fromisoformat("2024-09-10T11:12:13+08:00").timestamp()),
    ),
    ("chinanews", None, None, None),
)


@pytest.mark.parametrize(("name", "item_type", "feed_row", "detail_row", "default_source"), ADAPTERS)
def test_detail_rows_keep_source_defaults_and_time_contracts(
    name: str,
    item_type: type[Any],
    feed_row: AdapterFunction,
    detail_row: AdapterFunction,
    default_source: str,
) -> None:
    del feed_row
    _, publish_iso, supplied_timestamp, expected_timestamp = next(
        case for case in DETAIL_TIME_CASES if case[0] == name
    )
    detail_fetched_at = datetime(2024, 9, 10, 9, 45, tzinfo=timezone.utc)
    item = item_type(
        title=" Feed title ",
        url="https://example.com/feed",
        section=None,
        publish_time_iso=None,
        raw={},
    )
    data = {
        "title": " Detail title ",
        "url": "https://example.com/detail",
        "content": "<p>First paragraph</p><p>Second paragraph</p>",
    }
    if publish_iso is not None:
        data["publish_time_iso"] = publish_iso
    if supplied_timestamp is not None:
        data["publish_time"] = supplied_timestamp

    row = detail_row(
        item,
        f"{name}:article",
        data,
        detail_fetched_at=detail_fetched_at,
    )

    assert row == {
        "token": None,
        "profile_url": None,
        "article_id": f"{name}:article",
        "title": "Detail title",
        "source": default_source,
        "publish_time": expected_timestamp,
        "publish_time_iso": datetime.fromisoformat(publish_iso) if publish_iso else None,
        "url": "https://example.com/detail",
        "summary": None,
        "comment_count": None,
        "digg_count": None,
        "content_markdown": "First paragraph\n\nSecond paragraph",
        "detail_fetched_at": detail_fetched_at,
    }
    assert "fetched_at" not in row


@pytest.mark.parametrize(("name", "item_type", "feed_row", "detail_row", "default_source"), ADAPTERS)
def test_detail_payload_source_overrides_feed_and_default_sources(
    name: str,
    item_type: type[Any],
    feed_row: AdapterFunction,
    detail_row: AdapterFunction,
    default_source: str,
) -> None:
    del feed_row, default_source
    detail_fetched_at = datetime(2024, 9, 10, 9, 45, tzinfo=timezone.utc)
    publish_dt = datetime.fromisoformat("2024-09-10T01:02:03Z")
    item = item_type(
        title=" Feed title ",
        url="https://example.com/feed",
        section="Feed section",
        publish_time_iso="2001-02-03T04:05:06+00:00",
        raw={},
    )

    row = detail_row(
        item,
        f"{name}:article",
        {
            "title": " Payload title ",
            "source": " Payload source ",
            "publish_time": 456,
            "publish_time_iso": "2024-09-10T01:02:03Z",
            "url": "https://example.com/payload",
            "content": "<p>Ignored content</p>",
            "content_markdown": "Prepared markdown",
        },
        detail_fetched_at=detail_fetched_at,
    )

    assert row == {
        "token": None,
        "profile_url": None,
        "article_id": f"{name}:article",
        "title": "Payload title",
        "source": "Payload source",
        "publish_time": 456,
        "publish_time_iso": publish_dt,
        "url": "https://example.com/payload",
        "summary": None,
        "comment_count": None,
        "digg_count": None,
        "content_markdown": "Prepared markdown",
        "detail_fetched_at": detail_fetched_at,
    }
