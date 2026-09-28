from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.adapters import http_qianlong


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self, listings: dict[str, bytes]) -> None:
        self._listings = listings

    def get(self, url: str) -> _FakeResponse:
        return _FakeResponse(self._listings[url])

    def close(self) -> None:
        return None


def test_default_channels_include_qianlong_education() -> None:
    assert http_qianlong.DEFAULT_BASE_URLS == (
        "https://beijing.qianlong.com/",
        "https://edu.qianlong.com/",
    )


def test_fetch_articles_interleaves_default_channels(monkeypatch) -> None:
    beijing_url = "https://beijing.qianlong.com/2026/0825/8717001.shtml"
    edu_url = "https://edu.qianlong.com/2026/0825/8717002.shtml"
    listings = {
        http_qianlong.DEFAULT_BASE_URL: f'<a href="{beijing_url}">北京</a>'.encode(),
        http_qianlong.DEFAULT_EDU_BASE_URL: f'<a href="{edu_url}">教育</a>'.encode(),
    }
    session = _FakeSession(listings)

    def fake_extract_article(_session: _FakeSession, url: str) -> http_qianlong.QianlongArticle:
        return http_qianlong.QianlongArticle(
            title=url,
            url=url,
            publish_time=1,
            publish_time_iso=datetime(2026, 8, 25, tzinfo=timezone.utc),
            content_markdown="正文",
            raw_publish_text="2026-08-25 10:00",
        )

    monkeypatch.setattr(http_qianlong, "_create_session", lambda _timeout: session)
    monkeypatch.setattr(http_qianlong, "_extract_article", fake_extract_article)

    articles = http_qianlong.fetch_articles(limit=2, pages=1)

    assert [article.url for article in articles] == [beijing_url, edu_url]


def test_explicit_base_url_keeps_single_channel_override(monkeypatch) -> None:
    edu_url = "https://edu.qianlong.com/2026/0825/8717002.shtml"
    listings = {
        http_qianlong.DEFAULT_EDU_BASE_URL: f'<a href="{edu_url}">教育</a>'.encode(),
    }
    session = _FakeSession(listings)

    def fake_extract_article(_session: _FakeSession, url: str) -> http_qianlong.QianlongArticle:
        return http_qianlong.QianlongArticle(
            title=url,
            url=url,
            publish_time=1,
            publish_time_iso=datetime(2026, 8, 25, tzinfo=timezone.utc),
            content_markdown="正文",
            raw_publish_text="2026-08-25 10:00",
        )

    monkeypatch.setattr(http_qianlong, "_create_session", lambda _timeout: session)
    monkeypatch.setattr(http_qianlong, "_extract_article", fake_extract_article)

    articles = http_qianlong.fetch_articles(
        limit=1,
        base_url=http_qianlong.DEFAULT_EDU_BASE_URL,
        pages=1,
    )

    assert [article.url for article in articles] == [edu_url]


def _fake_extract(url: str) -> http_qianlong.QianlongArticle:
    return http_qianlong.QianlongArticle(
        title=url,
        url=url,
        publish_time=1,
        publish_time_iso=datetime(2026, 8, 25, tzinfo=timezone.utc),
        content_markdown="正文",
        raw_publish_text="2026-08-25 10:00",
    )


def test_fetch_articles_entries_override_channel_list(monkeypatch) -> None:
    beijing_url = "https://beijing.qianlong.com/2026/0825/8717001.shtml"
    edu_url = "https://edu.qianlong.com/2026/0825/8717002.shtml"
    listings = {
        "https://beijing.qianlong.com": f'<a href="{beijing_url}">北京</a>'.encode(),
        "https://edu.qianlong.com": f'<a href="{edu_url}">教育</a>'.encode(),
    }
    session = _FakeSession(listings)
    monkeypatch.setattr(http_qianlong, "_create_session", lambda _timeout: session)
    monkeypatch.setattr(http_qianlong, "_extract_article", lambda _s, url: _fake_extract(url))

    entries = [
        http_qianlong.ChannelEntry(url="https://edu.qianlong.com"),
        http_qianlong.ChannelEntry(url="https://edu.qianlong.com"),
        http_qianlong.parse_channel_input("beijing.qianlong.com"),
    ]

    articles = http_qianlong.fetch_articles(limit=5, pages=1, entries=entries)

    assert [article.url for article in articles] == [edu_url, beijing_url]


def test_fetch_articles_empty_entries_crawls_nothing(monkeypatch) -> None:
    def fail_extract(_session, url):  # pragma: no cover - 不应被调用
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr(http_qianlong, "_create_session", lambda _timeout: _FakeSession({}))
    monkeypatch.setattr(http_qianlong, "_extract_article", fail_extract)

    assert http_qianlong.fetch_articles(limit=5, pages=1, entries=[]) == []


def test_parse_channel_input_normalizes_qianlong_urls() -> None:
    for raw, expected in (
        ("https://beijing.qianlong.com/", "https://beijing.qianlong.com"),
        ("https://edu.qianlong.com", "https://edu.qianlong.com"),
        ("//edu.qianlong.com/edu/", "https://edu.qianlong.com/edu"),
        ("beijing.qianlong.com", "https://beijing.qianlong.com"),
        ("https://edu.qianlong.com/list.shtml", "https://edu.qianlong.com/list.shtml"),
    ):
        entry = http_qianlong.parse_channel_input(raw)
        assert entry.url == expected
        assert entry.raw_source == raw.strip().lstrip("\ufeff")


def test_parse_channel_input_rejects_blank_and_foreign_hosts() -> None:
    with pytest.raises(ValueError):
        http_qianlong.parse_channel_input("   ")
    for raw in (
        "https://example.com/",
        "https://qianlong.com.evil.com/",
        "https://www.chinadaily.com.cn/",
    ):
        with pytest.raises(ValueError):
            http_qianlong.parse_channel_input(raw)
