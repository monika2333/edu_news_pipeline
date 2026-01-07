from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class NewsArticle(BaseModel):
    article_id: str
    title: Optional[str] = None
    source: Optional[str] = None
    publish_time: Optional[int] = None
    publish_time_iso: Optional[datetime] = None
    url: Optional[str] = None
    content_markdown: Optional[str] = None
    llm_summary: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    llm_keywords: List[str] = Field(default_factory=list)
    score: Optional[float] = None
    raw_relevance_score: Optional[float] = None
    keyword_bonus_score: Optional[float] = None
    sentiment_label: Optional[str] = None
    sentiment_confidence: Optional[float] = None
    status: Optional[str] = None
    summary_status: Optional[str] = None
    external_importance_status: Optional[str] = None
    external_importance_score: Optional[float] = None
    is_beijing_related: Optional[bool] = None
    is_beijing_related_llm: Optional[bool] = None
    external_importance_checked_at: Optional[datetime] = None
    summary_generated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class NewsArticleSearchResponse(BaseModel):
    items: List[NewsArticle] = Field(default_factory=list)
    total: int
    limit: int
    page: int
    pages: int


__all__ = ["NewsArticle", "NewsArticleSearchResponse"]
