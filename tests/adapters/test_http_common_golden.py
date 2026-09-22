"""黄金快照：锁定各来源 html_to_markdown 的输出，作为共享 helper 的行为契约。

历史：本文件最初在抽取共享 helper 之前生成，逐字记录了 9 个来源的现行
输出（BeautifulSoup 族与裸正则族并存、图片转 markdown 语法）。2026-09
收敛到 `http_common.html_to_markdown` 后更新为统一值，差异仅两类（均有
既定决策背书）：图片段消失（流水线纯文字，剥图）；裸正则家族
（http_chinadaily / http_chinaeducationdaily / http_chinanews）实体解码、
script/style 文本不再泄漏、块级结构加空行。

维护规则：9 个来源在本文件的 CONTENT 快照必须始终相同——出现分叉说明
有人又在某个 adapter 里改了本地行为而不是改共享实现；NOISE/TOPIC 快照
记录的是各站 extra_unwanted 与站点特性，允许不同。
"""

from __future__ import annotations

import importlib

import pytest

CONTENT_FIXTURE = (
    '<div class="article">'
    '<p>第一段，含 &amp; 符号与&nbsp;不间断空格。</p>'
    '<p>第二段<br>折行后是 <strong>加粗词</strong>。</p>'
    '<img src="http://example.com/pic.jpg" alt="配图">'
    '<script>var tracking = 1;</script>'
    '<style>.x { color: red; }</style>'
    '<blockquote>引用内容</blockquote>'
    '<ul><li>列表项一</li><li>列表项二</li></ul>'
    '</div>'
)

NOISE_FIXTURE = (
    '<div><p>正文保留。</p>'
    '<div class="share">分享组件</div>'
    '<div class="related">相关阅读</div>'
    '<div class="editor">编辑：某人</div>'
    '<iframe src="http://example.com/embed"></iframe>'
    '</div>'
)

TOPIC_IMG_FIXTURE = (
    '<p>图说在前。</p><img src="http://example.com/std.jpg" topic="科技图片说明">'
)

_CONTENT_GOLDEN = (
    "第一段，含 & 符号与\xa0不间断空格。\n\n"
    "第二段\n\n折行后是 加粗词。\n\n"
    "引用内容\n\n列表项一\n\n列表项二"
)

CONTENT_GOLDENS = {
    "http_bbtnews": _CONTENT_GOLDEN,
    "http_beijinghao": _CONTENT_GOLDEN,
    "http_btime": _CONTENT_GOLDEN,
    "http_chinadaily": _CONTENT_GOLDEN,
    "http_chinaeducationdaily": _CONTENT_GOLDEN,
    "http_chinanews": _CONTENT_GOLDEN,
    "http_chinanews_xj": _CONTENT_GOLDEN,
    "http_stdaily": _CONTENT_GOLDEN,
    "http_xinhua": _CONTENT_GOLDEN,
}

NOISE_GOLDENS = {
    # 北京号剥离站点噪声区（分享/推荐/编辑等）；北京时间只剥 iframe，
    # share/related 等区文本保留——差异来自各自的 extra_unwanted 配置
    "http_beijinghao": "正文保留。",
    "http_btime": "正文保留。\n\n分享组件\n\n相关阅读\n\n编辑：某人",
}

TOPIC_GOLDENS = {
    # 科技日报的图片说明原本取自 topic 属性转 markdown；剥图决策后图片
    # 整体消失，该属性不再参与任何转换
    "http_stdaily": "图说在前。",
}


@pytest.mark.parametrize("module_name", sorted(CONTENT_GOLDENS))
def test_html_to_markdown_golden_content(module_name: str) -> None:
    adapter = importlib.import_module(f"src.adapters.{module_name}")

    assert adapter.html_to_markdown(CONTENT_FIXTURE) == CONTENT_GOLDENS[module_name]


@pytest.mark.parametrize("module_name", sorted(NOISE_GOLDENS))
def test_html_to_markdown_golden_site_noise(module_name: str) -> None:
    adapter = importlib.import_module(f"src.adapters.{module_name}")

    assert adapter.html_to_markdown(NOISE_FIXTURE) == NOISE_GOLDENS[module_name]


@pytest.mark.parametrize("module_name", sorted(TOPIC_GOLDENS))
def test_html_to_markdown_golden_topic_image(module_name: str) -> None:
    adapter = importlib.import_module(f"src.adapters.{module_name}")

    assert adapter.html_to_markdown(TOPIC_IMG_FIXTURE) == TOPIC_GOLDENS[module_name]


@pytest.mark.parametrize("module_name", sorted(CONTENT_GOLDENS))
def test_html_to_markdown_empty_and_none_input(module_name: str) -> None:
    adapter = importlib.import_module(f"src.adapters.{module_name}")

    assert adapter.html_to_markdown("") == ""
    assert adapter.html_to_markdown(None) == ""
