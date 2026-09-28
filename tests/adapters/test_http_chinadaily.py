from __future__ import annotations

from typing import Any, Dict, List

from src.adapters import http_chinadaily


# 结构对照 cn.chinadaily.com.cn 栏目页真实 DOM：div.left-liebiao 下
# 一组 div.busBox3，各含 h3>a（标题链接）和 p>b（发布时间），页尾是
# a.pagestyle 下一页链接。专稿频道与地方资讯频道共用这套结构。
def _listing_html(items: List[Dict[str, str]], next_page_href: str = "") -> str:
    boxes = "".join(
        f"""
        <div class="busBox3">
          <div>
            <div class="mr10">
              <a target="_blank" shape="rect" href="{item['href']}"></a>
            </div>
            <div>
              <h3><a target="_blank" shape="rect" href="{item['href']}">{item['title']}</a></h3>
              <p><b>{item['time']}</b></p>
            </div>
          </div>
        </div>
        """
        for item in items
    )
    next_anchor = (
        f'<a class="pagestyle" shape="rect" href="{next_page_href}">下一页</a>'
        if next_page_href
        else ""
    )
    return f"""
    <html><body>
      <div class="left-liebiao">{boxes}</div>
      {next_anchor}
    </body></html>
    """


SPECIAL_ITEMS = [
    {
        "href": "//cn.chinadaily.com.cn/a/202609/28/WS6ab9d000e4b09a165c78ca01.html",
        "title": "专稿频道新文章一",
        "time": "2026-09-28 09:00",
    },
    {
        "href": "//cn.chinadaily.com.cn/a/202609/28/WS6ab9d000e4b09a165c78ca02.html",
        "title": "专稿频道新文章二",
        "time": "2026-09-28 08:30",
    },
]

LOCAL_ITEMS = [
    {
        "href": "//cn.chinadaily.com.cn/a/202609/24/WS6ab4bacde4b09a165c78c73e.html",
        "title": "第五届中国食育大会在北京举行",
        "time": "2026-09-24 19:31",
    },
    {
        "href": "//cn.chinadaily.com.cn/a/202609/28/WS6ab9d000e4b09a165c78cb01.html",
        "title": "地方频道新文章一",
        "time": "2026-09-28 10:48",
    },
]


class _FakeResponse:
    def __init__(self, html: str) -> None:
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"
        self._html = html
        self.content = html.encode("utf-8")
        self.text = html

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self, pages_by_url: Dict[str, str]) -> None:
        self.pages_by_url = pages_by_url
        self.visited: List[str] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        del kwargs
        self.visited.append(url)
        return _FakeResponse(self.pages_by_url[url])


def _install_fake_session(monkeypatch: Any, pages_by_url: Dict[str, str]) -> _FakeSession:
    session = _FakeSession(pages_by_url)
    monkeypatch.setattr(http_chinadaily, "_session", lambda: session)
    return session


def test_parse_listing_extracts_items_times_and_next_page() -> None:
    html = _listing_html(
        LOCAL_ITEMS,
        "//cn.chinadaily.com.cn/c/zhuanti/6597728fa310af3247ffaeae/page_2.html",
    )

    items, next_page, _ = http_chinadaily._parse_listing_page(
        html, "https://cn.chinadaily.com.cn/6597728fa310af3247ffaeae", None, 5, 0
    )

    assert [it.title for it in items] == [
        "第五届中国食育大会在北京举行",
        "地方频道新文章一",
    ]
    assert items[0].publish_time_iso == "2026-09-24T19:31:00+08:00"
    assert items[0].url == (
        "https://cn.chinadaily.com.cn/a/202609/24/WS6ab4bacde4b09a165c78c73e.html"
    )
    assert next_page == (
        "https://cn.chinadaily.com.cn/c/zhuanti/6597728fa310af3247ffaeae/page_2.html"
    )


def test_list_items_crawls_all_default_channels(monkeypatch: Any) -> None:
    monkeypatch.delenv("CHINADAILY_START_URL", raising=False)
    pages_by_url = {
        http_chinadaily.DEFAULT_START_URL: _listing_html(SPECIAL_ITEMS),
        http_chinadaily.LOCAL_NEWS_START_URL: _listing_html(LOCAL_ITEMS),
    }
    _install_fake_session(monkeypatch, pages_by_url)

    items = http_chinadaily.list_items(pages=1)

    assert [it.title for it in items] == [
        "专稿频道新文章一",
        "专稿频道新文章二",
        "第五届中国食育大会在北京举行",
        "地方频道新文章一",
    ]


def test_list_items_shared_limit_spans_channels(monkeypatch: Any) -> None:
    monkeypatch.delenv("CHINADAILY_START_URL", raising=False)
    pages_by_url = {
        http_chinadaily.DEFAULT_START_URL: _listing_html(SPECIAL_ITEMS),
        http_chinadaily.LOCAL_NEWS_START_URL: _listing_html(LOCAL_ITEMS),
    }
    _install_fake_session(monkeypatch, pages_by_url)

    items = http_chinadaily.list_items(limit=3, pages=1)

    assert [it.title for it in items] == [
        "专稿频道新文章一",
        "专稿频道新文章二",
        "第五届中国食育大会在北京举行",
    ]


def test_list_items_deduplicates_same_article_across_channels(monkeypatch: Any) -> None:
    monkeypatch.delenv("CHINADAILY_START_URL", raising=False)
    shared = [SPECIAL_ITEMS[0]]
    pages_by_url = {
        http_chinadaily.DEFAULT_START_URL: _listing_html(shared),
        http_chinadaily.LOCAL_NEWS_START_URL: _listing_html(shared + [LOCAL_ITEMS[1]]),
    }
    _install_fake_session(monkeypatch, pages_by_url)

    items = http_chinadaily.list_items(pages=1)

    assert [it.title for it in items] == [
        "专稿频道新文章一",
        "地方频道新文章一",
    ]


def test_existing_early_stop_applies_per_channel(monkeypatch: Any) -> None:
    monkeypatch.delenv("CHINADAILY_START_URL", raising=False)
    monkeypatch.setenv("CHINADAILY_EXISTING_CONSECUTIVE_STOP", "2")
    pages_by_url = {
        http_chinadaily.DEFAULT_START_URL: _listing_html(
            [
                {"href": "//cn.chinadaily.com.cn/a/202609/27/WSold1.html", "title": "已有一", "time": "2026-09-27 09:00"},
                {"href": "//cn.chinadaily.com.cn/a/202609/27/WSold2.html", "title": "已有二", "time": "2026-09-27 08:00"},
                {"href": "//cn.chinadaily.com.cn/a/202609/27/WSnew1.html", "title": "不该被抓到", "time": "2026-09-27 07:00"},
            ]
        ),
        http_chinadaily.LOCAL_NEWS_START_URL: _listing_html(LOCAL_ITEMS),
    }
    _install_fake_session(monkeypatch, pages_by_url)
    existing_ids = {
        http_chinadaily.make_article_id("https://cn.chinadaily.com.cn/a/202609/27/WSold1.html"),
        http_chinadaily.make_article_id("https://cn.chinadaily.com.cn/a/202609/27/WSold2.html"),
    }

    items = http_chinadaily.list_items(pages=1, existing_ids=existing_ids)

    assert [it.title for it in items] == [
        "第五届中国食育大会在北京举行",
        "地方频道新文章一",
    ]


def test_env_override_single_url_keeps_legacy_behavior(monkeypatch: Any) -> None:
    monkeypatch.setenv("CHINADAILY_START_URL", http_chinadaily.DEFAULT_START_URL)
    pages_by_url = {
        http_chinadaily.DEFAULT_START_URL: _listing_html(SPECIAL_ITEMS),
        http_chinadaily.LOCAL_NEWS_START_URL: _listing_html(LOCAL_ITEMS),
    }
    session = _install_fake_session(monkeypatch, pages_by_url)

    items = http_chinadaily.list_items(pages=1)

    assert [it.title for it in items] == ["专稿频道新文章一", "专稿频道新文章二"]
    assert session.visited == [http_chinadaily.DEFAULT_START_URL]


def test_env_override_accepts_multiple_urls(monkeypatch: Any) -> None:
    monkeypatch.setenv(
        "CHINADAILY_START_URL",
        f"{http_chinadaily.LOCAL_NEWS_START_URL}, {http_chinadaily.DEFAULT_START_URL}",
    )
    pages_by_url = {
        http_chinadaily.DEFAULT_START_URL: _listing_html(SPECIAL_ITEMS),
        http_chinadaily.LOCAL_NEWS_START_URL: _listing_html(LOCAL_ITEMS),
    }
    _install_fake_session(monkeypatch, pages_by_url)

    items = http_chinadaily.list_items(pages=1)

    assert [it.title for it in items] == [
        "第五届中国食育大会在北京举行",
        "地方频道新文章一",
        "专稿频道新文章一",
        "专稿频道新文章二",
    ]


def test_resolve_start_urls_deduplicates_and_falls_back_to_defaults(monkeypatch: Any) -> None:
    monkeypatch.setenv(
        "CHINADAILY_START_URL",
        f"{http_chinadaily.DEFAULT_START_URL} {http_chinadaily.DEFAULT_START_URL}",
    )
    assert http_chinadaily._resolve_start_urls() == [http_chinadaily.DEFAULT_START_URL]

    monkeypatch.setenv("CHINADAILY_START_URL", " , ")
    assert http_chinadaily._resolve_start_urls() == list(http_chinadaily.DEFAULT_START_URLS)
