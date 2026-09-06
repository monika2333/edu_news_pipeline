from __future__ import annotations

from src.adapters.http_tencent import _clean_html_to_markdown


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
