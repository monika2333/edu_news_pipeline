from __future__ import annotations

from typing import NoReturn, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.console import articles_service, score_feedback_service
from src.console.articles_schemas import (
    NewsArticleContentResponse,
    NewsArticleIngestStatusResponse,
    NewsArticleSearchResponse,
)
from src.console.auth_service import ConsoleUser
from src.console.score_feedback_schemas import (
    ClearScoreFeedbackRequest,
    ScoreFeedbackRequest,
    ScoreFeedbackResponse,
)
from src.console.security import require_console_user

router = APIRouter(prefix="/api/articles", tags=["articles"])


def _raise_score_feedback_http_error(exc: ValueError) -> NoReturn:
    if isinstance(exc, score_feedback_service.ScoreFeedbackNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, score_feedback_service.ScoreFeedbackContextError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/search",
    response_model=NewsArticleSearchResponse,
    summary="Search articles and explain their pipeline outcome",
)
def search_articles_api(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = Query(None, max_length=1000),
    lookback_days: int = Query(
        articles_service.DEFAULT_ARTICLE_SEARCH_LOOKBACK_DAYS,
        ge=1,
        le=articles_service.MAX_ARTICLE_SEARCH_LOOKBACK_DAYS,
    ),
) -> NewsArticleSearchResponse:
    normalized_query = q.strip()
    if not normalized_query:
        raise HTTPException(status_code=422, detail="Search query must not be blank")
    try:
        result = articles_service.search_articles(
            query=normalized_query,
            limit=limit,
            lookback_days=lookback_days,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return NewsArticleSearchResponse.model_validate(result)


@router.get(
    "/content",
    response_model=NewsArticleContentResponse,
    summary="Fetch article content markdown",
)
def get_article_content_api(
    article_id: str = Query(..., min_length=1, max_length=400),
) -> NewsArticleContentResponse:
    result = articles_service.get_article_content(article_id=article_id)
    return NewsArticleContentResponse.model_validate(result)


@router.put("/score-feedback", response_model=ScoreFeedbackResponse)
def save_article_score_feedback_api(
    req: ScoreFeedbackRequest,
    user: ConsoleUser = Depends(require_console_user),
) -> ScoreFeedbackResponse:
    """Create or update score feedback from anywhere an article shows up.

    与工作区接口的区别：全库检索可能命中不在当前人工筛选/值班范围内的文章，
    这里只做登录校验，反馈仍绑定文章当前的重要性评分上下文并记录提交人。
    """
    try:
        feedback = score_feedback_service.save_score_feedback(
            article_id=req.article_id,
            feedback_type=req.feedback_type,
            notes=req.notes,
            actor=user,
        )
    except ValueError as exc:
        _raise_score_feedback_http_error(exc)
    return ScoreFeedbackResponse(score_feedback=feedback)


@router.post("/score-feedback/clear", response_model=ScoreFeedbackResponse)
def clear_article_score_feedback_api(
    req: ClearScoreFeedbackRequest,
) -> ScoreFeedbackResponse:
    """Clear score feedback for the article's current external score."""
    try:
        score_feedback_service.clear_score_feedback(article_id=req.article_id)
    except ValueError as exc:
        _raise_score_feedback_http_error(exc)
    return ScoreFeedbackResponse(score_feedback=None)


@router.get(
    "/ingest-status",
    response_model=NewsArticleIngestStatusResponse,
    summary="Latest article ingest timestamp",
)
def get_ingest_status_api() -> NewsArticleIngestStatusResponse:
    """返回全库最新收录时间，管理员与值班编辑共用（挂在受保护而非仅管理员的路由上）。"""
    result = articles_service.get_latest_ingest_status()
    return NewsArticleIngestStatusResponse.model_validate(result)


@router.get(
    "/{article_id}/content",
    response_model=NewsArticleContentResponse,
    summary="Fetch article content markdown",
)
def get_article_content_legacy_api(article_id: str) -> NewsArticleContentResponse:
    result = articles_service.get_article_content(article_id=article_id)
    return NewsArticleContentResponse.model_validate(result)


__all__ = ["router"]
