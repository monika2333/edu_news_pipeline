from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from src.adapters import http_toutiao
from src.adapters.http_toutiao import (
    FeedEntry,
    FeedItem,
    build_detail_update,
    feed_item_to_row,
    parse_author_input,
)


def _raw_items(start: int, count: int) -> list[dict[str, Any]]:
    return [
        {
            "title": f"Article {index}",
            "group_id": str(10_000_000_000_000_000 + index),
        }
        for index in range(start, start + count)
    ]


class _FakePage:
    async def wait_for_selector(self, _selector: str) -> None:
        return None

    async def close(self) -> None:
        return None


class _FakeContext:
    async def new_page(self) -> _FakePage:
        return _FakePage()


class _FakeBrowser:
    async def new_context(self, **_kwargs: Any) -> _FakeContext:
        return _FakeContext()

    async def close(self) -> None:
        return None


class _FakeChromium:
    async def launch(self, **_kwargs: Any) -> _FakeBrowser:
        return _FakeBrowser()


class _FakePlaywright:
    chromium = _FakeChromium()


class _FakePlaywrightManager:
    async def __aenter__(self) -> _FakePlaywright:
        return _FakePlaywright()

    async def __aexit__(self, *_args: Any) -> None:
        return None


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


def test_first_run_entry_collects_ten_items_from_only_one_feed_page(
    monkeypatch,
) -> None:
    calls: list[str] = []

    async def fetch_payload(_page, _token: str, max_behot_time: str) -> dict[str, Any]:
        calls.append(max_behot_time)
        return {
            "data": _raw_items(0, 6),
            "has_more": True,
            "next": {"max_behot_time": "next-page"},
        }

    monkeypatch.setattr(http_toutiao, "_fetch_feed_page_payload", fetch_payload)

    items, reached_existing = asyncio.run(
        http_toutiao._collect_feed_from_page(
            object(),
            "new-token",
            "https://example.test/new-token",
            10,
            set(),
            first_page_only=True,
        )
    )

    assert len(items) == 6
    assert calls == ["0"]
    assert reached_existing is False


def test_reenabled_seen_account_keeps_multi_page_backfill_behavior(
    monkeypatch,
) -> None:
    payloads = [
        {
            "data": _raw_items(0, 6),
            "has_more": True,
            "next": {"max_behot_time": "page-2"},
        },
        {
            "data": _raw_items(6, 6),
            "has_more": False,
            "next": {"max_behot_time": "0"},
        },
    ]
    calls: list[str] = []

    async def fetch_payload(_page, _token: str, max_behot_time: str) -> dict[str, Any]:
        calls.append(max_behot_time)
        return payloads.pop(0)

    monkeypatch.setattr(http_toutiao, "_fetch_feed_page_payload", fetch_payload)

    items, reached_existing = asyncio.run(
        http_toutiao._collect_feed_from_page(
            object(),
            "seen-token",
            "https://example.test/seen-token",
            None,
            set(),
        )
    )

    assert len(items) == 12
    assert calls == ["0", "page-2"]
    assert reached_existing is False


def test_seen_account_still_paginates_until_consecutive_existing_stop(
    monkeypatch,
) -> None:
    existing = {
        str(10_000_000_000_000_000 + index)
        for index in range(10, 15)
    }
    payloads = [
        {
            "data": _raw_items(0, 2),
            "has_more": True,
            "next": {"max_behot_time": "page-2"},
        },
        {
            "data": _raw_items(10, 5),
            "has_more": True,
            "next": {"max_behot_time": "page-3"},
        },
    ]
    calls: list[str] = []

    async def fetch_payload(_page, _token: str, max_behot_time: str) -> dict[str, Any]:
        calls.append(max_behot_time)
        return payloads.pop(0)

    monkeypatch.setattr(http_toutiao, "_fetch_feed_page_payload", fetch_payload)

    items, reached_existing = asyncio.run(
        http_toutiao._collect_feed_from_page(
            object(),
            "seen-token",
            "https://example.test/seen-token",
            None,
            existing,
        )
    )

    assert len(items) == 2
    assert calls == ["0", "page-2"]
    assert reached_existing is True


def test_first_run_limit_and_global_remaining_use_the_smaller_value(
    monkeypatch,
) -> None:
    calls: list[tuple[Optional[int], bool]] = []

    async def collect(
        _page,
        _token: str,
        _profile_url: str,
        limit: Optional[int],
        _existing_ids: set[str],
        *,
        first_page_only: bool = False,
    ) -> tuple[list[object], bool]:
        calls.append((limit, first_page_only))
        return [object() for _ in range(limit or 0)], False

    async def goto(_page, _profile_url: str) -> None:
        return None

    monkeypatch.setattr(http_toutiao, "async_playwright", _FakePlaywrightManager)
    monkeypatch.setattr(http_toutiao, "_collect_feed_from_page", collect)
    monkeypatch.setattr(http_toutiao, "_goto_with_retries", goto)

    items = asyncio.run(
        http_toutiao.fetch_feed_items(
            [FeedEntry("new-token", "https://example.test/new", first_run_limit=10)],
            limit=4,
            show_browser=False,
            existing_ids=set(),
        )
    )

    assert len(items) == 4
    assert calls == [(4, True)]


def test_mixed_first_run_and_seen_entries_keep_independent_policies(
    monkeypatch,
) -> None:
    calls: list[tuple[str, Optional[int], bool]] = []

    async def collect(
        _page,
        token: str,
        _profile_url: str,
        limit: Optional[int],
        _existing_ids: set[str],
        *,
        first_page_only: bool = False,
    ) -> tuple[list[object], bool]:
        calls.append((token, limit, first_page_only))
        count = 10 if token == "new-token" else 2
        return [object() for _ in range(count)], False

    async def goto(_page, _profile_url: str) -> None:
        return None

    monkeypatch.setattr(http_toutiao, "async_playwright", _FakePlaywrightManager)
    monkeypatch.setattr(http_toutiao, "_collect_feed_from_page", collect)
    monkeypatch.setattr(http_toutiao, "_goto_with_retries", goto)

    items = asyncio.run(
        http_toutiao.fetch_feed_items(
            [
                FeedEntry("new-token", "https://example.test/new", first_run_limit=10),
                FeedEntry("seen-token", "https://example.test/seen"),
            ],
            limit=20,
            show_browser=False,
            existing_ids=set(),
        )
    )

    assert len(items) == 12
    assert calls == [
        ("new-token", 10, True),
        ("seen-token", 10, False),
    ]


def test_m11_toutiao_parser_preserves_legacy_token_and_url_results() -> None:
    assert parse_author_input("token-123") == (
        "token-123",
        "https://www.toutiao.com/c/user/token/token-123/",
    )
    assert parse_author_input(
        "https://www.toutiao.com/c/user/token/url-token"
    ) == (
        "url-token",
        "https://www.toutiao.com/c/user/token/url-token/",
    )


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
