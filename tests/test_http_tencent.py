from __future__ import annotations

from typing import Any, Optional

from src.adapters import http_tencent
from src.adapters.http_tencent import AuthorEntry, _clean_html_to_markdown, parse_author_input


def test_m11_tencent_parser_preserves_legacy_bare_and_url_results() -> None:
    bare = parse_author_input("author=")
    url = parse_author_input("https://news.qq.com/omn/author/author%3D")

    assert bare.author_id == url.author_id == "author="
    assert bare.profile_url == "https://news.qq.com/omn/author/author="
    assert url.profile_url == url.raw_source


def test_clean_html_keeps_tencent_inline_card_text_in_same_paragraph() -> None:
    html = (
        '<div class="rich_media_content">'
        "<p>9月5日，<!--VERTICAL_CARD_BEGIN_0-->北京市第十七届运动会"
        "<!--VERTICAL_CARD_END_0-->社会组<!--SECURE_LINK_BEGIN_0-->无线电测向"
        "<!--SECURE_LINK_END_0-->在北京世园开幕。</p>"
        "<p>第二段正文。</p>"
        "</div>"
    )

    result = _clean_html_to_markdown(html)

    assert result == (
        "9月5日，北京市第十七届运动会社会组无线电测向在北京世园开幕。"
        "\n\n第二段正文。"
    )


def test_clean_html_preserves_br_and_image_boundaries() -> None:
    html = """
        <div class="rich_media_content">
            <p>第一行<br>第二行<span>连续文字</span></p>
            <p><img data-src="https://example.com/photo.jpg" alt="比赛现场"></p>
            <section><h2>小标题</h2><p>末段正文。</p></section>
        </div>
    """

    result = _clean_html_to_markdown(html)

    assert result == (
        "第一行\n第二行连续文字"
        "\n\n![比赛现场](https://example.com/photo.jpg)"
        "\n\n小标题"
        "\n\n末段正文。"
    )


def test_first_run_tencent_author_uses_one_page_and_ten_item_limit(
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def list_for_author(entry: AuthorEntry, **kwargs: Any) -> list[object]:
        calls.append({"author_id": entry.author_id, **kwargs})
        return [object() for _ in range(kwargs["limit"] or 0)]

    monkeypatch.setattr(http_tencent, "list_feed_items_for_author", list_for_author)

    items = http_tencent.list_feed_items(
        [
            AuthorEntry(
                "new-author",
                "https://example.test/new",
                "new-author",
                first_run_limit=10,
            )
        ],
        session=object(),
        max_pages=7,
        delay_seconds=0,
        limit=25,
        existing_ids=set(),
    )

    assert len(items) == 10
    assert calls[0]["author_id"] == "new-author"
    assert calls[0]["max_pages"] == 1
    assert calls[0]["limit"] == 10


def test_mixed_tencent_authors_keep_first_run_policy_per_author(
    monkeypatch,
) -> None:
    calls: list[tuple[str, int, Optional[int]]] = []

    def list_for_author(entry: AuthorEntry, **kwargs: Any) -> list[object]:
        calls.append((entry.author_id, kwargs["max_pages"], kwargs["limit"]))
        count = 12 if entry.author_id == "seen-author" else 10
        return [object() for _ in range(count)]

    monkeypatch.setattr(http_tencent, "list_feed_items_for_author", list_for_author)

    items = http_tencent.list_feed_items(
        [
            AuthorEntry("seen-author", "https://example.test/seen", "seen-author"),
            AuthorEntry(
                "new-author",
                "https://example.test/new",
                "new-author",
                first_run_limit=10,
            ),
        ],
        session=object(),
        max_pages=7,
        delay_seconds=0,
        limit=30,
        existing_ids=set(),
    )

    assert len(items) == 22
    assert calls == [
        ("seen-author", 7, 30),
        ("new-author", 1, 10),
    ]


def test_tencent_first_run_limit_respects_smaller_global_remaining(
    monkeypatch,
) -> None:
    calls: list[tuple[int, Optional[int]]] = []

    def list_for_author(_entry: AuthorEntry, **kwargs: Any) -> list[object]:
        calls.append((kwargs["max_pages"], kwargs["limit"]))
        return [object() for _ in range(kwargs["limit"] or 0)]

    monkeypatch.setattr(http_tencent, "list_feed_items_for_author", list_for_author)

    items = http_tencent.list_feed_items(
        [
            AuthorEntry(
                "new-author",
                "https://example.test/new",
                "new-author",
                first_run_limit=10,
            )
        ],
        session=object(),
        max_pages=7,
        delay_seconds=0,
        limit=4,
        existing_ids=set(),
    )

    assert len(items) == 4
    assert calls == [(1, 4)]


def test_tencent_zero_first_run_limit_does_not_request_an_author_page(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        http_tencent,
        "fetch_author_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not request an author page")
        ),
    )

    items = http_tencent.list_feed_items(
        [
            AuthorEntry(
                "new-author",
                "https://example.test/new",
                "new-author",
                first_run_limit=0,
            )
        ],
        session=object(),
        limit=20,
        existing_ids=set(),
    )

    assert items == []
