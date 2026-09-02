from __future__ import annotations

import os

from src.config import BGE_EMBEDDING_MODEL, load_environment

EMBED_MODEL = BGE_EMBEDDING_MODEL
EMBED_BODY_CHARS = 400
DEDUP_TOP_K = 3
LINK_WINDOW_DAYS = 3
LINK_TITLE_MIN = 0.70
LINK_BODY_CHARS = 120
LINK_CANDIDATE_TOP_K = 20

DEFAULT_DEDUP_LOOKBACK_DAYS = 15
DEFAULT_FEEDBACK_LOOKBACK_DAYS = 7
DEFAULT_FEEDBACK_MATCH_THRESHOLD = 0.90
DEFAULT_LINK_AUTO_THRESHOLD = 0.85
DEFAULT_LINK_REVIEW_THRESHOLD = 0.55
DEFAULT_DEDUP_RECALL_THRESHOLD = 0.90


def _env_int(name: str, default: int) -> int:
    load_environment()
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_score(name: str, default: float) -> float:
    load_environment()
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if 0.0 <= value <= 1.0 else default


def dedup_lookback_days() -> int:
    return _env_int(
        "SUBMISSION_DEDUP_LOOKBACK_DAYS",
        DEFAULT_DEDUP_LOOKBACK_DAYS,
    )


def feedback_lookback_days() -> int:
    return _env_int(
        "SUBMISSION_FEEDBACK_LOOKBACK_DAYS",
        DEFAULT_FEEDBACK_LOOKBACK_DAYS,
    )


def feedback_match_threshold() -> float:
    return _env_score(
        "SUBMISSION_FEEDBACK_MATCH_THRESHOLD",
        DEFAULT_FEEDBACK_MATCH_THRESHOLD,
    )


def link_auto_threshold() -> float:
    return _env_score(
        "SUBMISSION_LINK_AUTO_THRESHOLD",
        DEFAULT_LINK_AUTO_THRESHOLD,
    )


def link_review_threshold() -> float:
    return _env_score(
        "SUBMISSION_LINK_REVIEW_THRESHOLD",
        DEFAULT_LINK_REVIEW_THRESHOLD,
    )


def dedup_recall_threshold() -> float:
    return _env_score(
        "SUBMISSION_DEDUP_RECALL_THRESHOLD",
        DEFAULT_DEDUP_RECALL_THRESHOLD,
    )


__all__ = [
    "DEDUP_TOP_K",
    "DEFAULT_DEDUP_LOOKBACK_DAYS",
    "DEFAULT_DEDUP_RECALL_THRESHOLD",
    "DEFAULT_FEEDBACK_LOOKBACK_DAYS",
    "DEFAULT_FEEDBACK_MATCH_THRESHOLD",
    "DEFAULT_LINK_AUTO_THRESHOLD",
    "DEFAULT_LINK_REVIEW_THRESHOLD",
    "EMBED_BODY_CHARS",
    "EMBED_MODEL",
    "LINK_BODY_CHARS",
    "LINK_CANDIDATE_TOP_K",
    "LINK_TITLE_MIN",
    "LINK_WINDOW_DAYS",
    "dedup_lookback_days",
    "dedup_recall_threshold",
    "feedback_lookback_days",
    "feedback_match_threshold",
    "link_auto_threshold",
    "link_review_threshold",
]
