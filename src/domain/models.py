"""Domain dataclasses shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Sequence


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
class ExportCandidate:
    filtered_article_id: str
    raw_article_id: str
    article_hash: str
    title: Optional[str]
    summary: str
    content: str
    source: Optional[str]
    source_llm: Optional[str]
    relevance_score: float
    original_url: Optional[str]
    published_at: Optional[str]


@dataclass(slots=True)
class BriefExportRecord:
    article_id: str
    section: str
    title: Optional[str]
    summary: str
    correlation: Optional[float]
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
    "ExportCandidate",
    "BriefExportRecord",
]
