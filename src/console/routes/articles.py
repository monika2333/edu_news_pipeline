from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Query

from src.console.schemas.article import NewsArticleSearchResponse
from src.console.services import articles as articles_service

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
    source: Optional[List[str]] = Query(None),
    sentiment: Optional[List[str]] = Query(None),
    status: Optional[List[str]] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
) -> NewsArticleSearchResponse:
    result = articles_service.search_articles(
        query=q,
        page=page,
        limit=limit,
        sources=source,
        sentiments=sentiment,
        statuses=status,
        start_date=start_date,
        end_date=end_date,
    )
    return NewsArticleSearchResponse.model_validate(result)


__all__ = ["router"]
