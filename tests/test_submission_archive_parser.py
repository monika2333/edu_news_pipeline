from __future__ import annotations

from datetime import date

import pytest

from src.domain.submission_archive_parser import (
    SubmissionArchiveParseError,
    normalize_submission_text,
    normalized_title_hash,
    parse_submission_report,
)


def test_parse_zongbao_with_sections_sources_and_urls() -> None:
    parsed = parse_submission_report(
        """
        首都教育每日舆情综报
        2026年第488期（总第3766期）
        2026年7月27日

        【重点关注舆情】
        ★ 施工甲醛超标
        7月26日，网民发帖。（小红书 http://xhslink.cn/a）
        【新闻信息纵览】
        ■ 第二条新闻
        正文（北京日报）
        """
    )

    assert parsed.detected_report_type == "zongbao"
    assert parsed.report_date == date(2026, 7, 27)
    assert parsed.compiled_date == date(2026, 7, 27)
    assert parsed.issue_no == "2026年第488期（总第3766期）"
    assert [item.marker for item in parsed.items] == ["★", "■"]
    assert [item.section for item in parsed.items] == [
        "重点关注舆情",
        "新闻信息纵览",
    ]
    assert parsed.items[0].source == "小红书"
    assert parsed.items[0].urls == ["http://xhslink.cn/a"]
    assert parsed.items[1].source == "北京日报"


def test_parse_wanbao_keeps_global_order_across_restarted_numbering() -> None:
    parsed = parse_submission_report(
        """
        首都教育舆情
        总第3766期
        2026年7月27日
        【舆情速览】
        一、第一条
        正文（北京日报）
        二、第二条
        正文（新华社）
        【舆情参考】
        一、第三条
        正文（央视新闻）
        """
    )

    assert parsed.detected_report_type == "wanbao"
    assert parsed.compiled_date == date(2026, 7, 26)
    assert [item.order_index for item in parsed.items] == [0, 1, 2]
    assert [item.marker for item in parsed.items] == ["一、", "二、", "一、"]


def test_parse_feedback_without_issue_number() -> None:
    parsed = parse_submission_report(
        """
        首都教育舆情
        2026年7月27日
        【舆情速览】
        一、反馈条目
        正文（微博 https://weibo.com/a、哔哩哔哩 https://b23.tv/b）
        """
    )

    assert parsed.detected_report_type == "feedback"
    assert parsed.issue_no is None
    assert parsed.compiled_date == date(2026, 7, 26)
    assert parsed.items[0].source == "微博 哔哩哔哩"
    assert parsed.items[0].urls == [
        "https://weibo.com/a",
        "https://b23.tv/b",
    ]


@pytest.mark.parametrize(
    ("suffix", "source", "urls"),
    [
        ("（北京日报）", "北京日报", []),
        ("（小红书 http://a）", "小红书", ["http://a"]),
        ("（小红书 http://a、http://b）", "小红书", ["http://a", "http://b"]),
        (
            "（微博 https://weibo.com/x、哔哩哔哩 https://b23.tv/y）",
            "微博 哔哩哔哩",
            ["https://weibo.com/x", "https://b23.tv/y"],
        ),
    ],
)
def test_parse_source_group_shapes(
    suffix: str,
    source: str,
    urls: list[str],
) -> None:
    parsed = parse_submission_report(
        f"""
        首都教育舆情
        2026年7月27日
        【舆情速览】
        一、测试条目
        正文{suffix}
        """
    )

    assert parsed.items[0].body == "正文"
    assert parsed.items[0].source == source
    assert parsed.items[0].urls == urls


def test_middle_full_width_parentheses_are_not_treated_as_source() -> None:
    parsed = parse_submission_report(
        """
        首都教育舆情
        2026年7月27日
        【舆情速览】
        一、作品讨论
        《Wasteland Nomads（荒野游牧者）》引发讨论
        """
    )

    assert "（荒野游牧者）" in parsed.items[0].body
    assert parsed.items[0].source is None


def test_normalization_removes_width_punctuation_and_markers() -> None:
    assert normalize_submission_text("★ ＡＢＣ！ １２３") == "ABC123"
    assert normalized_title_hash("★ 标题！") == normalized_title_hash("标题")


def test_missing_date_raises_parse_error() -> None:
    with pytest.raises(SubmissionArchiveParseError, match="报告日期"):
        parse_submission_report("首都教育舆情\n【舆情速览】\n一、条目\n正文")


def test_date_mentioned_only_in_item_body_is_not_report_date() -> None:
    with pytest.raises(SubmissionArchiveParseError, match="报告日期"):
        parse_submission_report(
            "首都教育舆情\n【舆情速览】\n一、条目\n正文提到2026年7月28日"
        )
