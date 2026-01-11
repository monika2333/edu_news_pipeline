"""
manual_filter_decisions.py

Decision and update logic for bulk status changes, ranking, and edits.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from src.adapters.db_postgres_core import get_adapter

from .manual_filter_helpers import (
    DEFAULT_REPORT_TYPE,
    _normalize_ids,
    _normalize_report_type,
)
from .manual_filter_cluster import _invalidate_cluster_cache, _prune_cluster_cache

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Apply decision (no rank)
# ─────────────────────────────────────────────────────────────────────────────
def _apply_decision(
    *,
    status: str,
    ids: Sequence[str],
    actor: Optional[str] = None,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> int:
    adapter = get_adapter()
    now_ts = datetime.now(timezone.utc)
    target_report_type = _normalize_report_type(report_type)
    payload: List[Dict[str, Any]] = []
    for article_id in ids:
        if not article_id:
            continue
        payload.append(
            {
                "article_id": article_id,
                "status": status,
                "rank": None,
                "report_type": target_report_type,
                "decided_by": actor,
                "decided_at": now_ts,
            }
        )
    if not payload:
        return 0
    return adapter.update_manual_review_statuses(payload, report_type=target_report_type)  # type: ignore[attr-defined]


# ─────────────────────────────────────────────────────────────────────────────
# Next rank helper
# ─────────────────────────────────────────────────────────────────────────────
def _next_rank(status: str, *, report_type: str) -> float:
    adapter = get_adapter()
    target_report_type = _normalize_report_type(report_type)
    return adapter.manual_review_max_rank(status, report_type=target_report_type)  # type: ignore[attr-defined]


# ─────────────────────────────────────────────────────────────────────────────
# Apply ranked decision
# ─────────────────────────────────────────────────────────────────────────────
def _apply_ranked_decision(
    *,
    status: str,
    ids: Sequence[str],
    actor: Optional[str],
    start_rank: float,
    report_type: str,
) -> int:
    adapter = get_adapter()
    now_ts = datetime.now(timezone.utc)
    target_report_type = _normalize_report_type(report_type)
    payload: List[Dict[str, Any]] = []
    rank = start_rank
    for article_id in ids:
        if not article_id:
            continue
        payload.append(
            {
                "article_id": article_id,
                "status": status,
                "rank": rank,
                "report_type": target_report_type,
                "decided_by": actor,
                "decided_at": now_ts,
            }
        )
        rank += 1
    if not payload:
        return 0
    return adapter.update_manual_review_statuses(payload, report_type=target_report_type)  # type: ignore[attr-defined]


# ─────────────────────────────────────────────────────────────────────────────
# Bulk decide
# ─────────────────────────────────────────────────────────────────────────────
def bulk_decide(
    *,
    selected_ids: Sequence[str],
    backup_ids: Sequence[str],
    discarded_ids: Sequence[str],
    pending_ids: Sequence[str] = (),
    actor: Optional[str] = None,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> Dict[str, int]:
    selected = _normalize_ids(selected_ids)
    backups = _normalize_ids(backup_ids)
    discarded = _normalize_ids(discarded_ids)
    pending = _normalize_ids(pending_ids)
    target_report_type = _normalize_report_type(report_type)
    logger.info(
        "Applying decisions: selected=%s backup=%s discarded=%s pending=%s actor=%s report_type=%s",
        len(selected),
        len(backups),
        len(discarded),
        len(pending),
        actor,
        target_report_type,
    )
    selected_rank_base = _next_rank("selected", report_type=target_report_type)
    backup_rank_base = _next_rank("backup", report_type=target_report_type)
    updated_selected = _apply_ranked_decision(
        status="selected",
        ids=selected,
        actor=actor,
        start_rank=selected_rank_base + 1,
        report_type=target_report_type,
    )
    updated_backup = _apply_ranked_decision(
        status="backup",
        ids=backups,
        actor=actor,
        start_rank=backup_rank_base + 1,
        report_type=target_report_type,
    )
    updated_discarded = _apply_decision(status="discarded", ids=discarded, actor=actor, report_type=target_report_type)
    updated_pending = reset_to_pending(pending, actor=actor, report_type=target_report_type)
    logger.info(
        "Decision result: selected=%s backup=%s discarded=%s pending=%s",
        updated_selected,
        updated_backup,
        updated_discarded,
        updated_pending,
    )
    _prune_cluster_cache(selected + backups + discarded + pending)
    return {
        "selected": updated_selected,
        "backup": updated_backup,
        "discarded": updated_discarded,
        "pending": updated_pending,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Update ranks
# ─────────────────────────────────────────────────────────────────────────────
def update_ranks(
    *,
    selected_order: Sequence[str],
    backup_order: Sequence[str],
    actor: Optional[str] = None,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> Dict[str, int]:
    """
    Persist manual ordering for review lists and keep statuses in sync with list membership.
    """
    adapter = get_adapter()
    now_ts = datetime.now(timezone.utc)
    target_report_type = _normalize_report_type(report_type)
    payload: List[Dict[str, Any]] = []
    selected_ids = _normalize_ids(selected_order)
    backup_ids = _normalize_ids(backup_order)

    for index, aid in enumerate(selected_ids, start=1):
        payload.append(
            {
                "article_id": aid,
                "status": "selected",
                "rank": float(index),
                "report_type": target_report_type,
                "decided_by": actor,
                "decided_at": now_ts,
            }
        )
    for index, aid in enumerate(backup_ids, start=1):
        payload.append(
            {
                "article_id": aid,
                "status": "backup",
                "rank": float(index),
                "report_type": target_report_type,
                "decided_by": actor,
                "decided_at": now_ts,
            }
        )

    if not payload:
        return {"selected": 0, "backup": 0}

    updated_rows = adapter.update_manual_review_statuses(payload, report_type=target_report_type)  # type: ignore[attr-defined]
    logger.info(
        "Updated manual ranks: selected=%s backup=%s rows=%s report_type=%s",
        len(selected_ids),
        len(backup_ids),
        updated_rows,
        target_report_type,
    )
    return {"selected": len(selected_ids), "backup": len(backup_ids), "updated_rows": updated_rows}


# ─────────────────────────────────────────────────────────────────────────────
# Reset to pending
# ─────────────────────────────────────────────────────────────────────────────
def reset_to_pending(ids: Sequence[str], *, actor: Optional[str] = None, report_type: str = DEFAULT_REPORT_TYPE) -> int:
    target_ids = _normalize_ids(ids)
    if not target_ids:
        return 0
    target_report_type = _normalize_report_type(report_type)
    logger.info("Resetting to pending: count=%s actor=%s report_type=%s", len(target_ids), actor, target_report_type)
    adapter = get_adapter()
    return adapter.reset_manual_reviews_to_pending(target_ids, actor=actor, report_type=target_report_type)  # type: ignore[attr-defined]


# ─────────────────────────────────────────────────────────────────────────────
# Save edits
# ─────────────────────────────────────────────────────────────────────────────
def save_edits(edits: Dict[str, Dict[str, Any]], *, actor: Optional[str] = None, report_type: str = DEFAULT_REPORT_TYPE) -> int:
    adapter = get_adapter()
    if not edits:
        return 0
    target_report_type = _normalize_report_type(report_type)
    normalized: Dict[str, Dict[str, Any]] = {}
    for aid, payload in (edits or {}).items():
        summary = payload.get("summary")
        llm_source = payload.get("llm_source")
        notes = payload.get("notes")
        score = payload.get("score")
        normalized[aid] = {
            "summary": summary,
            "manual_llm_source": (llm_source or "").strip() if llm_source is not None else None,
            "notes": notes,
            "score": score,
            "report_type": target_report_type,
        }
    logger.info("Saving manual edits: count=%s actor=%s report_type=%s", len(edits), actor, target_report_type)
    updated = adapter.update_manual_review_summaries(normalized, actor=actor, report_type=target_report_type)  # type: ignore[attr-defined]
    if updated:
        _invalidate_cluster_cache()
    return updated
