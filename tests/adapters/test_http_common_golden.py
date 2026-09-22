"""黄金快照：逐字锁定各来源 html_to_markdown 的现行输出，作为抽共享 helper 的行为基准。

生成方式：用各 adapter 抽取前的现行实现，对本文件的 fixture 输出逐字记录。
抽取共享 helper 后本文件预期出现两类差异，且仅限两类：
1. 图片段（`![...](...)`）消失——流水线纯文字，剥图是既定决策；
2. 裸正则家族（http_chinadaily / http_chinaeducationdaily / http_chinanews）
   收敛到 BeautifulSoup 统一版：实体被解码、script/style 文本不再泄漏、
   块级结构加空行。
出现任何其他差异，说明收敛破坏了现有行为，必须查明而不是改快照迁就。
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

_GOLDEN_B = (
    "第一段，含 & 符号与\xa0不间断空格。\n\n"
    "第二段\n\n折行后是 加粗词。\n\n"
    "![配图](http://example.com/pic.jpg)\n\n"
    "引用内容\n\n列表项一\n\n列表项二"
)
_GOLDEN_R = (
    "第一段，含 &amp; 符号与&nbsp;不间断空格。\n\n"
    "第二段\n折行后是 加粗词。\n\n"
    "var tracking = 1;.x { color: red; }引用内容列表项一列表项二"
)

CONTENT_GOLDENS = {
    "http_bbtnews": _GOLDEN_B,
    "http_beijinghao": _GOLDEN_B,
    "http_btime": _GOLDEN_B,
    "http_chinadaily": _GOLDEN_R,
    "http_chinaeducationdaily": _GOLDEN_R,
    "http_chinanews": _GOLDEN_R,
    "http_chinanews_xj": _GOLDEN_B,
    "http_stdaily": _GOLDEN_B,
    "http_xinhua": _GOLDEN_B,
}

NOISE_GOLDENS = {
    # 北京号剥离站点噪声区（分享/推荐/编辑等），北京时间只剥 iframe，
    # 所以同一段输入下两者可见文本不同是各自现行行为
    "http_beijinghao": "正文保留。",
    "http_btime": "正文保留。\n\n分享组件\n\n相关阅读\n\n编辑：某人",
}

TOPIC_GOLDENS = {
    # 科技日报图片说明取自 topic 属性；剥图决策后该行应整体消失
    "http_stdaily": "图说在前。\n\n![科技图片说明](http://example.com/std.jpg)",
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
