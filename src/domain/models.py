"""Domain dataclasses shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Sequence, TypedDict


@dataclass(slots=True)
class ArticleInput:
    article_id: Optional[str]
    title: Optional[str]
    source: Optional[str]
    publish_time: Optional[int]
    original_url: Optional[str]
    content: Optional[str]
    raw_payload: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MissingContentTarget:
    raw_article_id: str
    article_hash: str
    original_url: Optional[str]


@dataclass(slots=True)
class SummaryCandidate:
    raw_article_id: str
    article_hash: str
    title: Optional[str]
    source: Optional[str]
    published_at: Optional[str]
    original_url: Optional[str]
    content: str
    existing_summary: Optional[str]
    filtered_article_id: Optional[str]
    processed_payload: Dict[str, Any]


@dataclass(slots=True)
class SummaryForScoring:
    article_id: str
    content: str
    summary: str


@dataclass(slots=True)
class PrimaryArticleForScoring:
    article_id: str
    content: str
    title: Optional[str]
    source: Optional[str]
    publish_time: Optional[int]
    publish_time_iso: Optional[str]
    url: Optional[str]
    keywords: Sequence[str] = field(default_factory=list)
    content_hash: Optional[str] = None
    simhash: Optional[str] = None
    raw_relevance_score: Optional[float] = None
    keyword_bonus_score: Optional[float] = None
    score_details: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PrimaryArticleForSummarizing:
    article_id: str
    content: str
    title: Optional[str]
    source: Optional[str]
    publish_time: Optional[int]
    publish_time_iso: Optional[str]
    url: Optional[str]
    keywords: Sequence[str] = field(default_factory=list)
    score: Optional[float] = None
    raw_relevance_score: Optional[float] = None
    keyword_bonus_score: Optional[float] = None
    score_details: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExportCandidate:
    filtered_article_id: str
    raw_article_id: str
    article_hash: str
    title: Optional[str]
    summary: str
    content: str
    source: Optional[str]
    llm_source: Optional[str]
    score: float
    original_url: Optional[str]
    published_at: Optional[str]
    sentiment_label: Optional[str] = None
    sentiment_confidence: Optional[float] = None
    is_beijing_related: Optional[bool] = None
    raw_relevance_score: Optional[float] = None
    keyword_bonus_score: Optional[float] = None
    score_details: Dict[str, Any] = field(default_factory=dict)
    external_importance_score: Optional[float] = None
    external_importance_checked_at: Optional[str] = None


@dataclass(slots=True)
class BriefExportRecord:
    article_id: str
    section: str
    title: Optional[str]
    summary: str
    score: Optional[float]
    original_url: Optional[str]
    published_at: Optional[str]
    source: Optional[str]
    generated_at: Optional[datetime] = None
    keywords: Sequence[str] | None = None


__all__ = [
    "ArticleInput",
    "MissingContentTarget",
    "SummaryCandidate",
    "SummaryForScoring",
    "PrimaryArticleForScoring",
    "PrimaryArticleForSummarizing",
    "ExportCandidate",
    "BriefExportRecord",
]
