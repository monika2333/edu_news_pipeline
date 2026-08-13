from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from src.console import articles_service
from src.console.articles_schemas import NewsArticleContentResponse, NewsArticleSearchResponse

router = APIRouter(prefix="/api/articles", tags=["articles"])


@router.get(
    "/search",
    response_model=NewsArticleSearchResponse,
    summary="Search articles and explain their pipeline outcome",
)
def search_articles_api(
    q: str = Query(..., min_length=1, max_length=200),
    page: int = Query(1, ge=1, le=200),
    limit: int = Query(20, ge=1, le=100),
    lookback_days: int = Query(
        articles_service.DEFAULT_ARTICLE_SEARCH_LOOKBACK_DAYS,
        ge=1,
        le=articles_service.MAX_ARTICLE_SEARCH_LOOKBACK_DAYS,
    ),
) -> NewsArticleSearchResponse:
    normalized_query = q.strip()
    if not normalized_query:
        raise HTTPException(status_code=422, detail="Search query must not be blank")
    result = articles_service.search_articles(
        query=normalized_query,
        page=page,
        limit=limit,
        lookback_days=lookback_days,
    )
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


@router.get(
    "/{article_id}/content",
    response_model=NewsArticleContentResponse,
    summary="Fetch article content markdown",
)
def get_article_content_legacy_api(article_id: str) -> NewsArticleContentResponse:
    result = articles_service.get_article_content(article_id=article_id)
    return NewsArticleContentResponse.model_validate(result)


__all__ = ["router"]
