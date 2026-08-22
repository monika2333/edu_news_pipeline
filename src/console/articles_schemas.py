from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.domain.report_type import SubmissionDocType


class NewsArticleArchiveLink(BaseModel):
    """一条已确认的报送存档回链。"""

    item_id: Optional[str] = None
    report_type: SubmissionDocType
    report_date: Optional[date] = None
    link_status: Literal["matched"]
    title: Optional[str] = None
    body: Optional[str] = None
    source: Optional[str] = None


class NewsArticleManualDecision(BaseModel):
    workspace: Literal["admin", "duty"]
    actor: Optional[str] = None
    decided_at: Optional[datetime] = None
    decision: str


class NewsArticleAttribution(BaseModel):
    level: Literal[
        "keyword_missed",
        "relevance_below",
        "importance_below",
        "not_reviewed",
        "discarded",
    ]
    ingested_at: datetime
    ingested_at_source: Literal[
        "news_summaries.created_at",
        "raw_articles.fetched_at",
    ]
    relevance_score: Optional[float] = None
    importance_score: Optional[float] = None
    manual_decisions: list[NewsArticleManualDecision] = Field(default_factory=list)
    matched_article_title: Optional[str] = None


class NewsArticleScoreFeedback(BaseModel):
    """文章当前重要性评分上下文对应的编辑反馈（无反馈时为 None）。"""

    feedback_type: Literal["too_high", "too_low"]
    notes: Optional[str] = None


class NewsArticleSearchItem(BaseModel):
    article_id: str
    title: Optional[str] = None
    source: Optional[str] = None
    publish_time: Optional[int] = None
    publish_time_iso: Optional[datetime] = None
    url: Optional[str] = None
    llm_summary: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)
    llm_keywords: list[str] = Field(default_factory=list)
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
    attribution: NewsArticleAttribution
    archive_links: list[NewsArticleArchiveLink] = Field(default_factory=list)
    score_feedback: Optional[NewsArticleScoreFeedback] = None


class NewsArticleContentResponse(BaseModel):
    article_id: str
    title: Optional[str] = None
    source: Optional[str] = None
    url: Optional[str] = None
    created_at: Optional[datetime] = None
    content_markdown: Optional[str] = None


class NewsArticleIngestStatusResponse(BaseModel):
    """请求范围内的最新收录时间；无数据或数据库不可用时为 None。"""

    latest_created_at: Optional[datetime] = None


class NewsArticleSearchResponse(BaseModel):
    items: list[NewsArticleSearchItem] = Field(default_factory=list)
    limit: int
    has_more: bool
    next_cursor: Optional[str] = None
    lookback_days: int
    window_start: datetime


__all__ = [
    "NewsArticleArchiveLink",
    "NewsArticleAttribution",
    "NewsArticleContentResponse",
    "NewsArticleIngestStatusResponse",
    "NewsArticleManualDecision",
    "NewsArticleScoreFeedback",
    "NewsArticleSearchItem",
    "NewsArticleSearchResponse",
]
