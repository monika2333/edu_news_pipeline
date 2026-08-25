from __future__ import annotations

from datetime import datetime, timezone

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
