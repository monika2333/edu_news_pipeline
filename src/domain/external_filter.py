from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


def determine_candidate_category(is_beijing_related: Optional[bool], sentiment_label: Optional[str]) -> str:
    base = "internal" if is_beijing_related else "external"
    sentiment = (sentiment_label or "").strip().lower()
    if sentiment in {"positive", "negative"}:
        return f"{base}_{sentiment}"
    return base


@dataclass(slots=True)
class BeijingGateCandidate:
    article_id: str
    title: Optional[str]
    source: Optional[str]
    publish_time_iso: Optional[str]
    summary: str
    content: str
    sentiment_label: Optional[str]
    is_beijing_related: Optional[bool]
    is_beijing_related_llm: Optional[bool]
    external_importance_status: str
    beijing_gate_fail_count: int = 0
    beijing_gate_attempted_at: Optional[str] = None


def external_filter_candidate_from_row(row: Any) -> ExternalFilterCandidate:
    """Build a scoring candidate from a ``news_summaries`` read row.

    Shared by the console prompt test and the nightly prompt optimizer so both
    replay through exactly the same candidate shape. Pure mapping, no I/O.
    """

    return ExternalFilterCandidate(
        article_id=str(row.get("article_id") or ""),
        title=row.get("title"),
        source=row.get("source"),
        publish_time_iso=row.get("publish_time_iso"),
        summary=str(row.get("llm_summary") or ""),
        content=str(row.get("content_markdown") or ""),
        sentiment_label=row.get("sentiment_label"),
        is_beijing_related=row.get("is_beijing_related"),
        is_beijing_related_llm=row.get("is_beijing_related_llm"),
        external_importance_status=(
            str(row.get("external_importance_status") or "pending_external_filter")
        ),
        keyword_matches=tuple(row.get("keyword_matches") or ()),
    )


@dataclass(slots=True)
class ExternalFilterCandidate:
    article_id: str
    title: Optional[str]
    source: Optional[str]
    publish_time_iso: Optional[str]
    summary: str
    content: str
    sentiment_label: Optional[str]
    is_beijing_related: Optional[bool]
    is_beijing_related_llm: Optional[bool]
    external_importance_status: str
    external_filter_fail_count: int = 0
    keyword_matches: tuple[str, ...] = ()

    @property
    def candidate_category(self) -> str:
        return determine_candidate_category(self.is_beijing_related, self.sentiment_label)


@dataclass(slots=True)
class ExternalFilterResult:
    article_id: str
    score: Optional[int]
    raw_output: Optional[str]
    status: str
    error: Optional[str] = None


__all__ = [
    "BeijingGateCandidate",
    "ExternalFilterCandidate",
    "ExternalFilterResult",
    "determine_candidate_category",
    "external_filter_candidate_from_row",
]
