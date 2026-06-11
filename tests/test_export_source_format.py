from __future__ import annotations

from typing import Optional

from src.domain import ExportCandidate
from src.domain.reporting.formatters import format_section_text, format_source_suffix
from src.workers.export_brief import _format_entry


def _candidate(
    *,
    source: Optional[str],
    llm_source: Optional[str],
) -> ExportCandidate:
    return ExportCandidate(
        filtered_article_id="article-1",
        raw_article_id="article-1",
        article_hash="hash-1",
        title="测试标题",
        summary="测试摘要",
        content="测试正文",
        source=source,
        llm_source=llm_source,
        score=80.0,
        original_url=None,
        published_at=None,
        sentiment_label="positive",
        is_beijing_related=True,
    )


def test_format_source_suffix_labels_detected_and_crawled_sources() -> None:
    suffix = format_source_suffix("中国青年报", "今日头条")

    assert suffix == "（识别来源：中国青年报；爬取来源：今日头条）"


def test_export_brief_entry_includes_crawled_source_with_label() -> None:
    text = _format_entry(_candidate(source="腾讯新闻", llm_source="北京日报"))

    assert "测试摘要（识别来源：北京日报；爬取来源：腾讯新闻）" in text


def test_export_brief_entry_labels_crawled_source_when_detection_missing() -> None:
    text = _format_entry(_candidate(source="光明日报", llm_source=None))

    assert "测试摘要（爬取来源：光明日报）" in text
    assert "识别来源" not in text


def test_section_formatter_uses_explicit_source_labels() -> None:
    text = format_section_text(
        {"label": "测试分组"},
        [_candidate(source="中国新闻网", llm_source="中国教育报")],
    )

    assert "测试摘要（识别来源：中国教育报；爬取来源：中国新闻网）" in text
