from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from src.console.articles_schemas import NewsArticleContentResponse, NewsArticleSearchResponse
from src.console import articles_service

router = APIRouter(prefix="/api/articles", tags=["articles"])


@router.get(
    "/search",
    response_model=NewsArticleSearchResponse,
    summary="Search summarized news articles",
)
def search_articles_api(
    q: Optional[str] = Query(None, min_length=1, max_length=200),
    page: int = Query(1, ge=1, le=200),
    limit: int = Query(20, ge=1, le=100),
) -> NewsArticleSearchResponse:
    result = articles_service.search_articles(
        query=q,
        page=page,
        limit=limit,
    )
    return NewsArticleSearchResponse.model_validate(result)


@router.get(
    "/{article_id}/content",
    response_model=NewsArticleContentResponse,
    summary="Fetch article content markdown",
)
def get_article_content_api(article_id: str) -> NewsArticleContentResponse:
    result = articles_service.get_article_content(article_id=article_id)
    return NewsArticleContentResponse.model_validate(result)


__all__ = ["router"]
