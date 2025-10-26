"""Domain-level objects shared across workers and adapters."""

from __future__ import annotations

from .models import (
    ArticleInput,
    BriefExportRecord,
    ExportCandidate,
    MissingContentTarget,
    PrimaryArticleForScoring,
    PrimaryArticleForSummarizing,
    SummaryCandidate,
    SummaryForScoring,
)
from .scoring import DEFAULT_WEIGHTS, ScoreResult, ScoreWeights, score_summary
from .states import IN_PROGRESS_STATES, TERMINAL_STATES, ProcessState, is_terminal
from .templates import BriefTemplate, DEFAULT_BRIEF_TEMPLATE
from .region import load_beijing_keywords, is_beijing_related
from .external_filter import ExternalFilterCandidate, ExternalFilterResult

__all__ = [
    "ArticleInput",
    "BriefExportRecord",
    "BriefTemplate",
    "DEFAULT_BRIEF_TEMPLATE",
    "DEFAULT_WEIGHTS",
    "ExportCandidate",
    "PrimaryArticleForScoring",
    "PrimaryArticleForSummarizing",
    "IN_PROGRESS_STATES",
    "ProcessState",
    "TERMINAL_STATES",
    "ScoreResult",
    "ScoreWeights",
    "MissingContentTarget",
    "SummaryCandidate",
    "SummaryForScoring",
    "is_terminal",
    "score_summary",
    "load_beijing_keywords",
    "is_beijing_related",
    "ExternalFilterCandidate",
    "ExternalFilterResult",
]
