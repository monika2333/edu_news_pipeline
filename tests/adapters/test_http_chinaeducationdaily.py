from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from src.adapters import http_chinaeducationdaily as jyb


# 神州学人（chisa.edu.cn）模板：h1 是站点 logo，标题区为
# .subtitle(引题位，常空) + .title + .subtitle(副题)，发布时间藏在 script 变量里
CHISA_DETAIL_HTML = """
<html>
  <head><title>深刻把握教育面临的新使命新挑战——2026年全国教育工作会议系列评论之一 - 神州学人网</title></head>
  <body>
    <h1 class="cf"><img src="/resources/images/logo.png" alt=""/></h1>
    <div class="new_title">
      <div class="subtitle"></div>
      <div class="title">深刻把握教育面临的新使命新挑战</div>
      <div class="subtitle">——2026年全国教育工作会议系列评论之一</div>
      <div class="info clearfix">
        <div class="word">
          <span>发布时间：
            <script>var customtime = '2026年01月13日';</script>
          </span>
          <span>来源：《中国教育报》2026年01月10日 第01版</span>
        </div>
      </div>
    </div>
    <div class="xl_text"><p>正文第一段。</p><p>正文第二段。</p></div>
  </body>
</html>
"""

# 中国教育新闻网（jyb.cn）模板：真实 h1 + h2 里的连字符日期
JYB_DETAIL_HTML = """
<html>
  <head><title>违规收集个人信息！这些教育类App被通报 - 中国教育新闻网</title></head>
  <body>
    <div class="xl_title">
      <h1>违规收集个人信息！这些教育类App被通报</h1>
      <h2>
        <span>发布时间：2026-09-21</span>
        <span>作者：张岗</span>
        <span>来源：全国教育辟谣平台</span>
      </h2>
    </div>
    <div class="xl_text"><p>正文。</p></div>
  </body>
</html>
"""


class _FakeResponse:
    def __init__(self, html: str) -> None:
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"
        self.text = html

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self, html: str) -> None:
        self._html = html

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        del url, kwargs
        return _FakeResponse(self._html)


def _title_of(html: str) -> str | None:
    return jyb._extract_detail_title(BeautifulSoup(html, "html.parser"))


def test_fetch_detail_on_chisa_template_appends_subtitle_and_uses_script_date(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(jyb, "_session", lambda: _FakeSession(CHISA_DETAIL_HTML))

    data = jyb.fetch_detail("http://www.chisa.edu.cn/opinion/202601/t20260113_2111436563.html")

    assert data["title"] == (
        "深刻把握教育面临的新使命新挑战——2026年全国教育工作会议系列评论之一"
    )
    # script 里的站点发布时间优先于可见文本中的报纸版次日期（2026年01月10日）
    assert data["publish_time_iso"] == "2026-01-13T00:00:00+08:00"
    assert "正文第一段" in data["content_markdown"]
    assert "正文第二段" in data["content_markdown"]


def test_fetch_detail_on_jyb_h1_template_keeps_plain_title(monkeypatch: Any) -> None:
    monkeypatch.setattr(jyb, "_session", lambda: _FakeSession(JYB_DETAIL_HTML))

    data = jyb.fetch_detail("http://www.jyb.cn/rmtzcg/xwy/wzxw/202609/t20260921_2111523823.html")

    assert data["title"] == "违规收集个人信息！这些教育类App被通报"
    assert data["publish_time_iso"] == "2026-09-21T00:00:00+08:00"


def test_title_without_subtitle_stays_unchanged() -> None:
    html = """
    <html><body>
      <div class="new_title">
        <div class="title">只有主标题的报道</div>
      </div>
    </body></html>
    """

    assert _title_of(html) == "只有主标题的报道"


def test_title_ignores_kicker_before_main_title() -> None:
    html = """
    <html><body>
      <div class="new_title">
        <div class="subtitle">评论员观察</div>
        <div class="title">主标题在这里</div>
      </div>
    </body></html>
    """

    assert _title_of(html) == "主标题在这里"


def test_title_skips_empty_subtitle_and_takes_following_one() -> None:
    html = """
    <html><body>
      <div class="new_title">
        <div class="title">主标题在这里</div>
        <div class="subtitle"></div>
        <div class="subtitle">——系列评论之二</div>
      </div>
    </body></html>
    """

    assert _title_of(html) == "主标题在这里——系列评论之二"


def test_h1_with_sibling_h3_h4_keeps_pre_post_title() -> None:
    html = """
    <html><body>
      <div class="title_box">
        <h3>引题前缀</h3>
        <h1>主标题</h1>
        <h4>副题后缀</h4>
      </div>
    </body></html>
    """

    assert _title_of(html) == "引题前缀主标题副题后缀"


def test_extract_iso_supports_cjk_date_with_single_digits_and_time() -> None:
    assert jyb._extract_iso_from_text("发布时间：2026年1月3日") == "2026-01-03T00:00:00+08:00"
    assert (
        jyb._extract_iso_from_text("发布时间：2026年1月3日 09:45") == "2026-01-03T09:45:00+08:00"
    )


def test_extract_iso_picks_earliest_match_across_formats() -> None:
    assert (
        jyb._extract_iso_from_text("刊于2026年1月3日的报道，更新于2026-02-05")
        == "2026-01-03T00:00:00+08:00"
    )
    assert (
        jyb._extract_iso_from_text("更新于2026-02-05，原刊于2026年1月3日")
        == "2026-02-05T00:00:00+08:00"
    )


def test_extract_iso_returns_none_without_date() -> None:
    assert jyb._extract_iso_from_text("这里没有任何日期信息") is None


def test_html_to_markdown_strips_images_with_text() -> None:
    markdown = jyb.html_to_markdown('<p>正文段落。</p><img src="http://x/a.jpg" alt="配图"/>')

    assert "正文段落。" in markdown
    assert "![" not in markdown
    assert "<" not in markdown
