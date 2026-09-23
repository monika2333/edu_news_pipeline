from __future__ import annotations

from datetime import datetime, timezone

from src.adapters import http_chinanews


LIST_HTML = """
<div class="content_list">
  <li>
    <span class="dd_lm">[新疆]</span>
    <span class="dd_bt">
      <a href="/sh/2026/09-01/123456.shtml">测试新闻</a>
    </span>
    <span class="dd_time">09-01 10:30</span>
  </li>
</div>
"""

DETAIL_HTML = """
<html>
  <head><meta name="source" content="和田市融媒体中心"></head>
  <body>
    <h1>测试新闻</h1>
    <div id="source_baidu">来源：和田市融媒体中心</div>
    <div id="p-detail">
      <p>这是用于测试的新闻正文第一段，正文长度需要足够长。</p>
      <p>这是用于测试的新闻正文第二段，网页署名不应成为原始来源。</p>
    </div>
  </body>
</html>
"""

LEFT_ZW_HTML = """
<html>
  <head><title>测试新闻-中新网</title></head>
  <body>
    <h1>测试新闻</h1>
    <div class="left_zw">
      <p>这是用于测试的普通文章页正文第一段，正文长度需要足够长。</p>
      <p>这是用于测试的普通文章页正文第二段，验证主容器路径不受视频页改动影响。</p>
    </div>
  </body>
</html>
"""

# 视频页（/shipin/）骨架，抓取自 2026-09-23 的 news1069974.shtml：
# 没有 h1，标题取 og:title；meta description 就是标题本身；正文在 .content_desc。
VIDEO_URL = "https://www.chinanews.com.cn/cul/shipin/2026/09-23/news1069974.shtml"
VIDEO_HTML = """
<html>
  <head>
    <title>北京电影学院院长： 我们不生产大明星-中新网视频</title>
    <meta property="og:title" content="北京电影学院院长： 我们不生产大明星">
    <meta name="description" content="北京电影学院院长： 我们不生产大明星">
  </head>
  <body>
    <div class="videoplay">
      <div class="w1280" id="playerwrap">
        <div class="content_title">
          <div class="title">北京电影学院院长： 我们不生产大明星</div>
          <div class="left"><p>发布时间：2026年09月23日 20:03 来源：中国新闻网</p></div>
        </div>
        <div class="video-pic"></div>
        <div class="content_desc">
          <p>9月23日，2026北京文化论坛“文化遗产与文艺创作”平行论坛在北京举办。参与论坛的北京电影学院院长扈强在会后接受中新社记者采访。扈强说，北京电影学院不生产大明星，也生产不了艺术家。时代造就艺术家，不是哪一所院校能培养出来的。明星是资本造就的，电影学院能做的就是用好的教育方式，为社会培养合格的人。(记者 范思忆 制作 方敏）</p>
          <p class="content_editor"><span>责任编辑：【叶攀】</span></p>
        </div>
        <div class="banquan">版权声明：中新视频版权属中新社所有。</div>
      </div>
    </div>
  </body>
</html>
"""

NO_CONTAINER_HTML = """
<html>
  <head>
    <title>某视频页-中新网</title>
    <meta name="description" content="这是一段足够长的页面描述文字，用于验证在没有任何正文容器命中时会回退到该描述而不是整页噪声。">
  </head>
  <body><div>导航</div></body>
</html>
"""


def test_parse_page_items_use_fixed_source() -> None:
    items, _, _ = http_chinanews._parse_page_items(LIST_HTML, None, 5, 0)

    assert len(items) == 1
    assert items[0].section == "中国新闻网"


def test_parse_detail_ignores_source_from_page_content() -> None:
    data = http_chinanews._parse_detail_html(
        DETAIL_HTML,
        "https://www.chinanews.com.cn/sh/2026/09-01/123456.shtml",
    )

    assert data["source"] == "中国新闻网"


def test_parse_detail_left_zw_body_still_parses() -> None:
    data = http_chinanews._parse_detail_html(
        LEFT_ZW_HTML,
        "https://www.chinanews.com.cn/gn/2026/09-23/10702563.shtml",
    )

    assert data["title"] == "测试新闻"
    assert "正文第二段" in data["content_markdown"]


def test_parse_detail_video_page_extracts_content_desc_body() -> None:
    data = http_chinanews._parse_detail_html(VIDEO_HTML, VIDEO_URL)

    assert data["title"] == "北京电影学院院长： 我们不生产大明星"
    # 正文专有句子，meta description 里没有
    assert "时代造就艺术家" in data["content"]
    assert "时代造就艺术家" in data["content_markdown"]


def test_parse_detail_video_page_body_is_not_meta_description() -> None:
    # 视频页 description 就是标题；回退到它会得到 18 字符的伪正文
    data = http_chinanews._parse_detail_html(VIDEO_HTML, VIDEO_URL)

    assert len(data["content"]) > 100
    assert data["content_markdown"].strip() != data["title"]


def test_parse_detail_video_page_strips_editor_sign_off() -> None:
    data = http_chinanews._parse_detail_html(VIDEO_HTML, VIDEO_URL)

    assert "责任编辑" not in data["content"]
    assert "责任编辑" not in data["content_markdown"]


def test_parse_detail_without_containers_falls_back_to_description() -> None:
    data = http_chinanews._parse_detail_html(NO_CONTAINER_HTML, VIDEO_URL)

    assert "页面描述文字" in data["content_markdown"]


def test_row_builders_force_china_news_as_source() -> None:
    item = http_chinanews.FeedItemLike(
        title="测试新闻",
        url="https://www.chinanews.com.cn/sh/2026/09-01/123456.shtml",
        section="具体栏目名称",
        publish_time_iso=None,
        raw={},
    )
    fetched_at = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)

    feed_row = http_chinanews.feed_item_to_row(
        item,
        "chinanews:/sh/2026/09-01/123456",
        fetched_at=fetched_at,
    )
    detail_row = http_chinanews.build_detail_update(
        item,
        "chinanews:/sh/2026/09-01/123456",
        {"source": "另一个具体来源", "content": "<p>正文</p>"},
        detail_fetched_at=fetched_at,
    )

    assert feed_row["source"] == detail_row["source"] == "中国新闻网"
