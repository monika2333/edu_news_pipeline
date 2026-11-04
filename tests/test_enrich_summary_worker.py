from __future__ import annotations

from threading import Barrier
from typing import Any, Optional

import pytest

from src.business_config import business_config_context
from src.domain import SourceAliasRules
from src.workers import enrich_summary
from tests.conftest import make_endpoint_config


class _NewsSummariesNamespace:
    def __init__(self, adapter: _EnrichmentAdapter) -> None:
        self._adapter = adapter

    def fetch_pending_enrichments(self, limit: int) -> list[dict[str, Any]]:
        return self._adapter.rows[:limit]

    def complete_enrichment(
        self,
        article_id: str,
        *,
        label: str,
        confidence: Optional[float],
        llm_source: Optional[str],
    ) -> None:
        self._adapter.completed.append((article_id, label, confidence, llm_source))


class _EnrichmentAdapter:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.completed: list[tuple[str, str, Optional[float], Optional[str]]] = []
        self.news_summaries = _NewsSummariesNamespace(self)


def _row() -> dict[str, Any]:
    return {
        "article_id": "article-1",
        "title": "测试标题",
        "content_markdown": "正文内容",
        "llm_summary": "摘要内容",
    }


def test_sentiment_and_source_are_independent_concurrent_requests(monkeypatch) -> None:
    adapter = _EnrichmentAdapter([_row()])
    barrier = Barrier(2, timeout=2)
    monkeypatch.setattr(enrich_summary, "get_adapter", lambda: adapter)

    def classify(summary_text: str) -> dict[str, Any]:
        barrier.wait()
        return {"label": "positive", "confidence": 0.9}

    def detect(article: dict[str, Any]) -> dict[str, Any]:
        barrier.wait()
        return {"llm_source": "测试媒体"}

    monkeypatch.setattr(enrich_summary, "classify_sentiment", classify)
    monkeypatch.setattr(enrich_summary, "detect_source", detect)

    with business_config_context(make_endpoint_config()):
        enrich_summary.run(limit=1, concurrency=2)

    assert adapter.completed == [("article-1", "positive", 0.9, "测试媒体")]


def test_failed_request_keeps_entire_enrichment_pending(monkeypatch) -> None:
    adapter = _EnrichmentAdapter([_row()])
    monkeypatch.setattr(enrich_summary, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        enrich_summary,
        "classify_sentiment",
        lambda summary_text: {"label": "negative", "confidence": 0.8},
    )
    monkeypatch.setattr(
        enrich_summary,
        "detect_source",
        lambda article: (_ for _ in ()).throw(RuntimeError("source failed")),
    )

    with business_config_context(make_endpoint_config()):
        enrich_summary.run(limit=1, concurrency=2)

    assert adapter.completed == []


def test_source_length_guard_logs_article_id_and_metadata(monkeypatch) -> None:
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        enrich_summary,
        "detect_source",
        lambda article: {
            "llm_source": None,
            "source_guard_discarded_length": 945,
            "source_guard_triggered_attempt": 1,
        },
    )
    monkeypatch.setattr(
        enrich_summary,
        "log_info",
        lambda worker, message: messages.append((worker, message)),
    )

    result = enrich_summary._detect_article_source(_row(), SourceAliasRules())

    assert result.llm_source is None
    assert messages == [
        (
            "enrich_summary",
            "WARNING source length guard article_id=article-1 "
            "discarded_length=945 attempt=1",
        )
    ]


# ---------------------------------------------------------------------------
# 回退名单：识别结果与名单整串全等时不保存，写 NULL 回退抓取来源
# ---------------------------------------------------------------------------


def _stub_detect(monkeypatch: pytest.MonkeyPatch, llm_source: Any) -> list[tuple[str, str]]:
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        enrich_summary,
        "detect_source",
        lambda article: {"llm_source": llm_source},
    )
    monkeypatch.setattr(
        enrich_summary,
        "log_info",
        lambda worker, message: messages.append((worker, message)),
    )
    return messages


def test_fallback_hit_on_raw_name_before_normalization(monkeypatch) -> None:
    # M2 的对照组：名单写归一化前的原始名称「现代教育报客户端」；
    # 剥后缀后的「现代教育报」不在名单中，只有归一化前命中。
    rules = SourceAliasRules(suffixes=("客户端",), fallback_names=("现代教育报客户端",))
    messages = _stub_detect(monkeypatch, "现代教育报客户端")

    result = enrich_summary._detect_article_source(
        {"article_id": "article-9", "content_markdown": "正文"},
        rules,
    )

    # 严格 is None：写空字符串会让 SQL COALESCE 的抓取来源回退失效
    assert result.llm_source is None
    assert not result.llm_source == ""
    joined = "\n".join(message for _, message in messages)
    assert "article-9" in joined
    assert "original='现代教育报客户端'" in joined
    assert "normalized='现代教育报'" in joined


def test_fallback_hit_on_normalized_name(monkeypatch) -> None:
    # M3 的对照组：名单写标准名称「现代教育报」，识别结果带后缀；
    # 只有归一化后的名称命中。
    rules = SourceAliasRules(suffixes=("客户端",), fallback_names=("现代教育报",))
    messages = _stub_detect(monkeypatch, "现代教育报客户端")

    result = enrich_summary._detect_article_source(
        {"article_id": "article-9", "content_markdown": "正文"},
        rules,
    )

    assert result.llm_source is None
    assert any("FALLBACK source name" in message for _, message in messages)


def test_fallback_hit_after_alias_mapping(monkeypatch) -> None:
    # 别名把「现教报」映射成「现代教育报」后命中名单 → 回退
    rules = SourceAliasRules(
        aliases={"现教报": "现代教育报"},
        fallback_names=("现代教育报",),
    )
    _stub_detect(monkeypatch, "现教报")

    result = enrich_summary._detect_article_source(
        {"article_id": "article-10", "content_markdown": "正文"},
        rules,
    )

    assert result.llm_source is None


def test_fallback_miss_keeps_normalized_result_and_existing_logs(monkeypatch) -> None:
    # 未命中时保持既有行为：结果 = 归一化结果，日志只有 NORMALIZED 一条
    rules = SourceAliasRules(
        suffixes=("客户端",),
        aliases={"北京号": "北京日报"},
        fallback_names=("现代教育报",),
    )
    messages = _stub_detect(monkeypatch, "北京号客户端")

    result = enrich_summary._detect_article_source(
        {"article_id": "article-1", "content_markdown": "正文"},
        rules,
    )

    assert result.llm_source == "北京日报"
    assert messages == [
        (
            "enrich_summary",
            "NORMALIZED source name article_id=article-1 "
            "original='北京号客户端' normalized='北京日报'",
        )
    ]


def test_fallback_empty_list_keeps_recognition(monkeypatch) -> None:
    # 名单为空：任何识别结果都不回退
    rules = SourceAliasRules(suffixes=("客户端",))
    messages = _stub_detect(monkeypatch, "现代教育报客户端")

    result = enrich_summary._detect_article_source(
        {"article_id": "article-1", "content_markdown": "正文"},
        rules,
    )

    assert result.llm_source == "现代教育报"
    assert not any("FALLBACK" in message for _, message in messages)


def test_fallback_skipped_when_recognition_is_empty(monkeypatch) -> None:
    # 「未知」与超长内容由 adapter 在返回前统一转成 None（超长还带 guard 元数据），
    # worker 拿到的识别结果为空时不做回退判定，行为与现在相同且不报错。
    rules = SourceAliasRules(fallback_names=("现代教育报",))
    messages = _stub_detect(monkeypatch, None)

    result = enrich_summary._detect_article_source(
        {"article_id": "article-1", "content_markdown": "正文"},
        rules,
    )

    assert result.llm_source is None
    assert not any("FALLBACK" in message for _, message in messages)


def test_null_llm_source_display_falls_back_to_crawl_source() -> None:
    # 回退写 NULL 后，控制台展示字段必须等于抓取来源（读取路径零改动）
    from src.console.manual_filter_helpers import _attach_source_fields

    record = _attach_source_fields(
        {"llm_source": None, "manual_llm_source": None, "source": "新华网"}
    )

    assert record["llm_source_display"] == "新华网"
    assert record["llm_source_raw"] == ""
    assert record["llm_source_manual"] == ""
