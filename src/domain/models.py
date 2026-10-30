"""Domain dataclasses shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
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


__all__ = [
    "ArticleInput",
    "MissingContentTarget",
    "SummaryCandidate",
    "SummaryForScoring",
    "PrimaryArticleForScoring",
    "PrimaryArticleForSummarizing",
]
