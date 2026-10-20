from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from src.adapters import http_21jingji


LIST_HTML = """
<div class="col-l">
  <div class="news">
    <a href="https://m.21jingji.com/article/20261020/herald/a114daa88e7483ecabfadc0cac1cdc7f.html"
       title="首批重大应用场景项目清单来了">
      <div class="title">
        <h2>首批重大应用场景项目清单来了</h2>
        <span>3小时前　　周潇枭</span>
      </div>
    </a>
    <a href="//m.21jingji.com/article/20261020/herald/7ca2df1e6e09192d5efec9dc72d519cd.html">
      <div class="title">
        <h2>要素综合改革试点首批创新举措发布</h2>
        <span>30分钟前　　周慧</span>
      </div>
    </a>
    <a href="/article/20261020/herald/existing.html" title="已经入库的稿件">
      <div class="title"><h2>已经入库的稿件</h2><span>1小时前</span></div>
    </a>
    <a href="https://example.com/article/20261020/herald/evil.html" title="外站文章">
      <div class="title"><h2>外站文章</h2></div>
    </a>
    <a href="javascript:;">查看更多</a>
  </div>
</div>
<div class="col-r">
  <a href="https://m.21jingji.com/article/20261015/herald/hot1.html" title="热文排行">
    热文排行
  </a>
</div>
"""

DETAIL_HTML = """
<html>
<head><title>首批重大应用场景项目清单来了 - 21财经</title></head>
<body>
  <div class="newsAuthor">
    <div class="author-infos">
      <span>周潇枭 2026-10-20 12:43</span>
    </div>
  </div>
  <div class="txtContent">
    <div class="rela-box"><span>关联内容</span>不该进正文的关联文章</div>
    <div class="main_content">
      <p>这是正文第一段。</p>
      <p><img src="https://example.com/现场.jpg"/>（发布会现场图）</p>
      <style>.ad { display: none; }</style>
      <p>这是正文第二段。</p>
    </div>
    <p>（作者：周潇枭 编辑：李博）</p>
    <p class="copyright">版权声明，不应进入正文。</p>
  </div>
</body>
</html>
"""


class FakeResponse:
    def __init__(self, text: str, *, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"
        self.content = text.encode("utf-8")

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return FakeResponse(self.pages.get(url, ""))

    def close(self) -> None:
        self.closed = True


def test_parse_channel_input_normalizes_www_channel_urls() -> None:
    expected = "https://www.21jingji.com/channel/politics/"
    for raw in (
        "https://www.21jingji.com/channel/politics/",
        "https://www.21jingji.com/channel/politics",
        "//www.21jingji.com/channel/politics",
        "/channel/politics/",
        "channel/politics",
    ):
        entry = http_21jingji.parse_channel_input(raw)
        assert entry.url == expected
        assert entry.raw_source == raw.strip()


def test_parse_channel_input_rejects_article_pages_and_foreign_hosts() -> None:
    with pytest.raises(ValueError, match="文章页"):
        http_21jingji.parse_channel_input(
            "https://www.21jingji.com/article/20261020/herald/a114daa8.html"
        )
    with pytest.raises(ValueError, match="域名"):
        http_21jingji.parse_channel_input("https://example.com/channel/politics/")
    with pytest.raises(ValueError, match="频道栏目页"):
        http_21jingji.parse_channel_input("https://www.21jingji.com/")
    with pytest.raises(ValueError):
        http_21jingji.parse_channel_input("   ")


def test_parse_list_uses_main_column_and_ignores_sidebar() -> None:
    items = http_21jingji._parse_list_html(LIST_HTML)

    assert [item.title for item in items] == [
        "首批重大应用场景项目清单来了",
        "要素综合改革试点首批创新举措发布",
        "已经入库的稿件",
    ]
    assert items[0].url == (
        "https://m.21jingji.com/article/20261020/herald/"
        "a114daa88e7483ecabfadc0cac1cdc7f.html"
    )
    assert items[1].url == (
        "https://m.21jingji.com/article/20261020/herald/"
        "7ca2df1e6e09192d5efec9dc72d519cd.html"
    )
    assert items[2].url == (
        "https://www.21jingji.com/article/20261020/herald/existing.html"
    )
    assert all(item.section == "21世纪经济报道" for item in items)
    assert items[0].raw["publish_text"] == "3小时前　　周潇枭"
    assert items[0].publish_time_iso is None
    assert http_21jingji.make_article_id(items[0].url) == (
        "21jingji:a114daa88e7483ecabfadc0cac1cdc7f"
    )
    assert "热文排行" not in [item.title for item in items]
    assert "外站文章" not in [item.title for item in items]


def test_parse_list_skips_existing_ids() -> None:
    existing = {
        http_21jingji.make_article_id(
            "https://m.21jingji.com/article/20261020/herald/"
            "a114daa88e7483ecabfadc0cac1cdc7f.html"
        )
    }

    items = http_21jingji._parse_list_html(LIST_HTML, existing_ids=existing)

    assert [item.title for item in items] == [
        "要素综合改革试点首批创新举措发布",
        "已经入库的稿件",
    ]


def test_list_items_fetches_one_page_per_channel_and_honors_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = http_21jingji.parse_channel_input(
        "https://www.21jingji.com/channel/politics/"
    )
    session = FakeSession({entry.url: LIST_HTML})
    monkeypatch.setattr(http_21jingji, "_session", lambda: session)

    items = http_21jingji.list_items(entries=[entry], limit=2, pages=3)

    assert [item.title for item in items] == [
        "首批重大应用场景项目清单来了",
        "要素综合改革试点首批创新举措发布",
    ]
    # 站点没有服务端翻页，pages 只警告，不重复请求同一页
    assert [call["url"] for call in session.calls] == [entry.url]


def test_list_items_skips_ids_already_known(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = http_21jingji.parse_channel_input(
        "https://www.21jingji.com/channel/politics/"
    )
    session = FakeSession({entry.url: LIST_HTML})
    monkeypatch.setattr(http_21jingji, "_session", lambda: session)
    existing = {
        http_21jingji.make_article_id(
            "https://m.21jingji.com/article/20261020/herald/"
            "a114daa88e7483ecabfadc0cac1cdc7f.html"
        )
    }

    items = http_21jingji.list_items(entries=[entry], existing_ids=existing)

    assert [item.title for item in items] == [
        "要素综合改革试点首批创新举措发布",
        "已经入库的稿件",
    ]


def test_list_items_returns_empty_for_non_positive_limit() -> None:
    entry = http_21jingji.parse_channel_input(
        "https://www.21jingji.com/channel/politics/"
    )

    assert http_21jingji.list_items(entries=[entry], limit=0) == []


def test_detail_parses_title_publish_time_and_main_content() -> None:
    url = (
        "https://m.21jingji.com/article/20261020/herald/"
        "a114daa88e7483ecabfadc0cac1cdc7f.html"
    )

    data = http_21jingji._parse_detail_html(DETAIL_HTML, url)

    assert data["title"] == "首批重大应用场景项目清单来了"
    assert data["source"] == "21世纪经济报道"
    assert data["publish_time_iso"] == "2026-10-20T12:43:00+08:00"
    assert data["url"] == url
    assert "这是正文第一段" in data["content_markdown"]
    assert "这是正文第二段" in data["content_markdown"]
    assert "https://example.com/现场.jpg" not in data["content_markdown"]
    assert "关联内容" not in data["content_markdown"]
    assert "版权声明" not in data["content_markdown"]
    assert "（作者：周潇枭 编辑：李博）" not in data["content_markdown"]


def test_detail_rejects_missing_main_content() -> None:
    with pytest.raises(RuntimeError, match="Unable to find"):
        http_21jingji._parse_detail_html(
            "<html><body><h1>只有标题</h1></body></html>",
            "https://m.21jingji.com/article/20261020/herald/missing.html",
        )


def test_fetch_detail_uses_session_and_normalizes_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = (
        "https://m.21jingji.com/article/20261020/herald/"
        "a114daa88e7483ecabfadc0cac1cdc7f.html?from=timeline"
    )
    session = FakeSession({url.split("?")[0]: DETAIL_HTML})
    monkeypatch.setattr(http_21jingji, "_session", lambda: session)

    data = http_21jingji.fetch_detail(url)

    assert data["title"] == "首批重大应用场景项目清单来了"
    assert [call["url"] for call in session.calls] == [url.split("?")[0]]


def test_row_builders_force_newspaper_name_as_source() -> None:
    item = http_21jingji.FeedItemLike(
        title="测试新闻",
        url="https://m.21jingji.com/article/20261020/herald/testarticle.html",
        section="宏观",
        publish_time_iso=None,
        raw={},
    )
    fetched_at = datetime(2026, 10, 20, 4, 0, tzinfo=timezone.utc)

    feed_row = http_21jingji.feed_item_to_row(
        item,
        "21jingji:testarticle",
        fetched_at=fetched_at,
    )
    detail_row = http_21jingji.build_detail_update(
        item,
        "21jingji:testarticle",
        {
            "title": "测试新闻",
            "source": "另一个栏目名",
            "publish_time_iso": "2026-10-20T12:43:00+08:00",
            "content": "<p>正文</p>",
        },
        detail_fetched_at=fetched_at,
    )

    assert feed_row["source"] == detail_row["source"] == "21世纪经济报道"
    assert feed_row["summary"] is None
    assert detail_row["publish_time_iso"].isoformat() == "2026-10-20T12:43:00+08:00"
    assert detail_row["content_markdown"] == "正文"
