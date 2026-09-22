from __future__ import annotations

import requests

from src.adapters import http_common


class _FakeResponse:
    def __init__(self, encoding: str | None, apparent: str | None = None, text: str = "页面文本") -> None:
        self.encoding = encoding
        self.apparent_encoding = apparent
        self.text = text


def test_build_session_applies_headers_and_trust_env_default() -> None:
    session = http_common.build_session({"User-Agent": "UA/1.0", "Referer": "https://a.example"})

    assert session.headers["User-Agent"] == "UA/1.0"
    assert session.headers["Referer"] == "https://a.example"
    # requests 默认信任系统代理，未显式关代理的来源保持该行为
    assert session.trust_env is True


def test_build_session_can_disable_trust_env() -> None:
    session = http_common.build_session({"User-Agent": "UA/1.0"}, trust_env=False)

    assert session.trust_env is False


def test_decode_response_keeps_declared_encoding() -> None:
    resp = _FakeResponse(encoding="utf-8", text="正文")

    assert http_common.decode_response(resp) == "正文"
    assert resp.encoding == "utf-8"


def test_decode_response_falls_back_to_apparent_encoding() -> None:
    resp = _FakeResponse(encoding=None, apparent="gb2312")

    assert http_common.decode_response(resp) == "页面文本"
    assert resp.encoding == "gb2312"


def test_decode_response_overrides_misreported_iso_8859_1() -> None:
    resp = _FakeResponse(encoding="iso-8859-1", apparent="gb18030")

    assert http_common.decode_response(resp) == "页面文本"
    assert resp.encoding == "gb18030"


def test_decode_response_without_any_encoding_uses_utf8() -> None:
    resp = _FakeResponse(encoding=None, apparent=None)

    assert http_common.decode_response(resp) == "页面文本"
    assert resp.encoding == "utf-8"


def test_html_to_markdown_strips_images_and_scripts() -> None:
    html = (
        '<div><p>第一段。</p><img src="http://x/a.jpg" alt="配图">'
        "<script>var t = 1;</script><style>.a{}</style>"
        "<p>末段<br>折行。</p></div>"
    )

    assert http_common.html_to_markdown(html) == "第一段。\n\n末段\n\n折行。"


def test_html_to_markdown_extra_unwanted_removes_site_noise() -> None:
    html = '<div><p>正文。</p><div class="share">分享</div><iframe src="http://x"></iframe></div>'

    assert http_common.html_to_markdown(html) == "正文。\n\n分享"
    assert (
        http_common.html_to_markdown(html, extra_unwanted="iframe, .share") == "正文。"
    )


def test_html_to_markdown_handles_empty_and_none() -> None:
    assert http_common.html_to_markdown("") == ""
    assert http_common.html_to_markdown(None) == ""


def test_strip_site_suffix_removes_suffix_and_trailing_channel() -> None:
    assert http_common.strip_site_suffix("标题 - 中国新闻网 - 要闻", "中国新闻网") == "标题"
    assert http_common.strip_site_suffix("标题_中新网", "中国新闻网", "中新网") == "标题"


def test_strip_site_suffix_without_match_returns_stripped_title() -> None:
    assert http_common.strip_site_suffix("  纯标题  ", "中国新闻网") == "纯标题"
    assert http_common.strip_site_suffix("标题", ) == "标题"


def test_build_session_returns_real_requests_session() -> None:
    session = http_common.build_session({})

    assert isinstance(session, requests.Session)
