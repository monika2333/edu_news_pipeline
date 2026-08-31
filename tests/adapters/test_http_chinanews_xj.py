from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.adapters import http_chinanews_xj


LIST_HTML = """
<html><body>
  <div class="CLtitle">
    <a href="http://www.xj.chinanews.com.cn/dizhou/2026-08-31/detail-alpha.shtml">
      和静县开展高校毕业生专场招聘会
    </a>
    [2026.08.31 12:25]
  </div>
  <div class="CLtitle">
    <a href="https://www.xj.chinanews.com.cn/dizhou/2026-08-31/detail-alpha.shtml">
      和静县开展高校毕业生专场招聘会
    </a>
    [2026.08.31 12:25]
  </div>
  <a href="/shipin/2026-08-31/detail-video.shtml">视频</a>
  <a href="/tupian/2026-08-31/detail-photo.shtml">图片</a>
  <a href="/kejiao/2026-08-31/detail-edu.shtml">科教</a>
  <a href="/xinjiang/2026-08-31/detail-xinjiang.shtml">新疆新闻</a>
  <a href="/newspaper/2026-08-31/detail-paper.shtml">报纸</a>
  <a href="/ecnsxj/2026-08-31/detail-english.shtml">Ecns Xinjiang</a>
</body></html>
"""


DETAIL_HTML = """
<html>
  <head><title>测试新闻-中新网·新疆</title></head>
  <body>
    <nav>首页 地州 新闻快报</nav>
    <div class="content" id="cont_1_1_2">
      <h1>和静县开展高校毕业生专场招聘会</h1>
      <div class="left-time"><div class="left-t">2026-08-31 12:25:20 来源：中新网新疆</div></div>
      <div class="left_zw">
        <p>这是正文第一段。</p>
        <p>这是正文第二段。</p>
        <div class="adEditor"><span>【编辑：测试编辑】</span></div>
        <div class="related">相关新闻：另一篇稿件</div>
        <p class="copyright">版权声明：未经授权不得转载。</p>
        <div id="function_code_page"></div>
      </div>
    </div>
    <footer>中新网版权所有</footer>
  </body>
</html>
"""


MULTI_ITEM_LIST_HTML = """
<html><body>
  <div class="CLtitle">
    <a href="/dizhou/2026-08-31/detail-existing-1.shtml">已有文章一</a>
    [2026.08.31 12:30]
  </div>
  <div class="CLtitle">
    <a href="/dizhou/2026-08-31/detail-existing-2.shtml">已有文章二</a>
    [2026.08.31 12:20]
  </div>
  <div class="CLtitle">
    <a href="/dizhou/2026-08-31/detail-middle-new.shtml">中间的新文章</a>
    [2026.08.31 12:10]
  </div>
  <div class="CLtitle">
    <a href="/dizhou/2026-08-31/detail-existing-3.shtml">已有文章三</a>
    [2026.08.31 12:00]
  </div>
  <div class="CLtitle">
    <a href="/dizhou/2026-08-31/detail-existing-4.shtml">已有文章四</a>
    [2026.08.31 11:50]
  </div>
  <div class="CLtitle">
    <a href="/dizhou/2026-08-31/detail-after-stop.shtml">停止点后的新文章</a>
    [2026.08.31 11:40]
  </div>
</body></html>
"""


def _multi_item_id(slug: str) -> str:
    return f"chinanewsxj:/dizhou/2026-08-31/detail-{slug}"


class _FakeResponse:
    encoding = "utf-8"
    apparent_encoding = "utf-8"
    text = MULTI_ITEM_LIST_HTML

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        del url, kwargs
        return _FakeResponse()


def test_make_article_id_uses_distinct_prefix_and_normalizes_protocol() -> None:
    http_url = "http://www.xj.chinanews.com.cn/dizhou/2026-08-31/detail-alpha.shtml"
    https_url = "https://www.xj.chinanews.com.cn/dizhou/2026-08-31/detail-alpha.shtml"

    assert http_chinanews_xj.make_article_id(http_url) == (
        "chinanewsxj:/dizhou/2026-08-31/detail-alpha"
    )
    assert http_chinanews_xj.make_article_id(http_url) == http_chinanews_xj.make_article_id(https_url)


def test_parse_list_extracts_item_filters_other_sections_and_deduplicates() -> None:
    items = http_chinanews_xj._parse_list_html(LIST_HTML)

    assert len(items) == 1
    assert items[0].title == "和静县开展高校毕业生专场招聘会"
    assert items[0].url == (
        "https://www.xj.chinanews.com.cn/dizhou/2026-08-31/detail-alpha.shtml"
    )
    assert items[0].publish_time_iso == "2026-08-31T12:25:00+08:00"
    assert items[0].section == "中新网·新疆"


def test_parse_list_skips_existing_article_ids() -> None:
    article_id = "chinanewsxj:/dizhou/2026-08-31/detail-alpha"

    assert http_chinanews_xj._parse_list_html(LIST_HTML, existing_ids={article_id}) == []


def test_parse_detail_extracts_title_and_content_without_page_noise() -> None:
    data = http_chinanews_xj._parse_detail_html(
        DETAIL_HTML,
        "http://www.xj.chinanews.com.cn/dizhou/2026-08-31/detail-alpha.shtml",
    )

    assert data["title"] == "和静县开展高校毕业生专场招聘会"
    assert data["source"] == "中新网新疆"
    assert data["publish_time_iso"] == "2026-08-31T12:25:20+08:00"
    assert data["url"].startswith("https://")
    assert "这是正文第一段" in data["content_markdown"]
    assert "这是正文第二段" in data["content_markdown"]
    assert "首页" not in data["content_markdown"]
    assert "编辑" not in data["content_markdown"]
    assert "相关新闻" not in data["content_markdown"]
    assert "版权声明" not in data["content_markdown"]


def test_linked_page_rows_keep_shape_and_default_source() -> None:
    item = http_chinanews_xj.FeedItemLike(
        title="测试新闻",
        url="https://www.xj.chinanews.com.cn/dizhou/2026-08-31/detail-alpha.shtml",
        section=None,
        publish_time_iso="2026-08-31T12:25:00+08:00",
        raw={},
    )
    article_id = http_chinanews_xj.make_article_id(item.url)
    fetched_at = datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc)

    feed_row = http_chinanews_xj.feed_item_to_row(item, article_id, fetched_at=fetched_at)
    detail_row = http_chinanews_xj.build_detail_update(
        item,
        article_id,
        {"title": "", "source": "", "content": "<p>正文</p>", "url": item.url},
        detail_fetched_at=fetched_at,
    )

    assert feed_row["source"] == "中新网·新疆"
    assert detail_row["source"] == "中新网·新疆"
    assert detail_row["content_markdown"] == "正文"
    assert feed_row["article_id"] == detail_row["article_id"] == article_id
    assert set(feed_row) == {
        "token",
        "profile_url",
        "article_id",
        "title",
        "source",
        "publish_time",
        "publish_time_iso",
        "url",
        "summary",
        "comment_count",
        "digg_count",
        "fetched_at",
    }
    assert set(detail_row) == {
        "token",
        "profile_url",
        "article_id",
        "title",
        "source",
        "publish_time",
        "publish_time_iso",
        "url",
        "summary",
        "comment_count",
        "digg_count",
        "content_markdown",
        "detail_fetched_at",
    }


def test_parse_list_stops_after_consecutive_existing_threshold() -> None:
    existing_ids = {_multi_item_id("existing-1"), _multi_item_id("existing-2")}

    items = http_chinanews_xj._parse_list_html(
        MULTI_ITEM_LIST_HTML,
        existing_ids=existing_ids,
        existing_consecutive_stop=2,
    )

    assert items == []


def test_new_item_resets_consecutive_existing_count() -> None:
    existing_ids = {
        _multi_item_id("existing-1"),
        _multi_item_id("existing-2"),
        _multi_item_id("existing-3"),
        _multi_item_id("existing-4"),
    }

    items = http_chinanews_xj._parse_list_html(
        MULTI_ITEM_LIST_HTML,
        existing_ids=existing_ids,
        existing_consecutive_stop=3,
    )

    assert [item.title for item in items] == ["中间的新文章", "停止点后的新文章"]


def test_zero_threshold_scans_all_existing_items(monkeypatch: Any) -> None:
    existing_ids = {
        _multi_item_id("existing-1"),
        _multi_item_id("existing-2"),
        _multi_item_id("middle-new"),
        _multi_item_id("existing-3"),
        _multi_item_id("existing-4"),
        _multi_item_id("after-stop"),
    }
    visited_urls: list[str] = []
    original_make_article_id = http_chinanews_xj.make_article_id

    def track_make_article_id(url: str) -> str:
        visited_urls.append(url)
        return original_make_article_id(url)

    monkeypatch.setattr(http_chinanews_xj, "make_article_id", track_make_article_id)

    items = http_chinanews_xj._parse_list_html(
        MULTI_ITEM_LIST_HTML,
        existing_ids=existing_ids,
        existing_consecutive_stop=0,
    )

    assert items == []
    assert len(visited_urls) == 6


def test_invalid_environment_threshold_falls_back_to_five(monkeypatch: Any) -> None:
    existing_ids = {
        _multi_item_id("existing-1"),
        _multi_item_id("existing-2"),
        _multi_item_id("middle-new"),
        _multi_item_id("existing-3"),
        _multi_item_id("existing-4"),
    }
    monkeypatch.setenv("CHINANEWS_XJ_EXISTING_CONSECUTIVE_STOP", "abc")
    monkeypatch.setattr(http_chinanews_xj, "_session", _FakeSession)

    items = http_chinanews_xj.list_items(existing_ids=existing_ids)

    assert items == []


def test_negative_environment_threshold_disables_stop(monkeypatch: Any) -> None:
    existing_ids = {
        _multi_item_id("existing-1"),
        _multi_item_id("existing-2"),
        _multi_item_id("middle-new"),
        _multi_item_id("existing-3"),
        _multi_item_id("existing-4"),
    }
    monkeypatch.setenv("CHINANEWS_XJ_EXISTING_CONSECUTIVE_STOP", "-1")
    monkeypatch.setattr(http_chinanews_xj, "_session", _FakeSession)

    items = http_chinanews_xj.list_items(existing_ids=existing_ids)

    assert [item.title for item in items] == ["停止点后的新文章"]
