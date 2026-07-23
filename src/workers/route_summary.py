from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from src.adapters.db_postgres_core import get_adapter
from src.config import get_settings
from src.domain import is_beijing_related, load_beijing_keywords
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "route_summary"


@dataclass(slots=True)
class RoutingStats:
    success: int = 0
    failed: int = 0
    skipped: int = 0
    failure_ids: list[str] = field(default_factory=list)


def _determine_route(
    article: dict[str, Any],
    beijing_keywords: list[str],
) -> tuple[Optional[bool], str]:
    beijing_related: Optional[bool] = None
    if beijing_keywords:
        detection_payload = [
            str(article.get("llm_summary") or "").strip(),
            str(article.get("title") or "").strip(),
            str(article.get("content_markdown") or "").strip(),
        ]
        beijing_related = is_beijing_related(detection_payload, beijing_keywords)

    sentiment = str(article.get("sentiment_label") or "").strip().lower()
    if sentiment not in {"positive", "negative"}:
        raise ValueError(f"Unsupported sentiment label: {sentiment or '<empty>'}")
    if beijing_related is True:
        return True, "pending_beijing_gate"
    return beijing_related, "pending_external_filter"


def run(limit: int = 500) -> None:
    settings = get_settings()
    adapter = get_adapter()
    limit_value = limit if limit and limit > 0 else None
    if settings.process_limit is not None:
        limit_value = (
            settings.process_limit
            if limit_value is None
            else min(limit_value, settings.process_limit)
        )
    beijing_keywords = load_beijing_keywords(settings.beijing_keywords_path)

    with worker_session(WORKER, limit=limit_value):
        rows = adapter.fetch_pending_summary_routes(limit_value)
        if not rows:
            log_info(WORKER, "No pending summary routes found.")
            log_summary(WORKER, ok=0, failed=0)
            return

        stats = RoutingStats()
        for article in rows:
            article_id = str(article.get("article_id") or "").strip()
            if not article_id:
                stats.skipped += 1
                continue
            try:
                beijing_related, status = _determine_route(article, beijing_keywords)
                adapter.complete_summary_routing(
                    article_id,
                    beijing_related=beijing_related,
                    status=status,
                )
                stats.success += 1
                log_info(
                    WORKER,
                    f"OK {article_id} beijing={beijing_related} -> {status}",
                )
            except Exception as exc:
                stats.failed += 1
                stats.failure_ids.append(article_id)
                log_error(WORKER, article_id, exc)

        log_summary(
            WORKER,
            ok=stats.success,
            failed=stats.failed,
            skipped=stats.skipped or None,
        )
        if stats.failure_ids:
            log_info(WORKER, f"failed ids: {', '.join(stats.failure_ids)}")


__all__ = ["run"]
