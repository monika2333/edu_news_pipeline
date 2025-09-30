"""Domain-level objects shared across workers and adapters."""

from __future__ import annotations

from .models import (
    ArticleInput,
    BriefExportRecord,
    ExportCandidate,
    MissingContentTarget,
    SummaryCandidate,
    SummaryForScoring,
)
from .scoring import DEFAULT_WEIGHTS, ScoreResult, ScoreWeights, score_summary
from .states import ProcessState
from .templates import BriefTemplate, DEFAULT_BRIEF_TEMPLATE

__all__ = [
    "ProcessState",
    "DEFAULT_WEIGHTS",
    "ScoreResult",
    "ScoreWeights",
    "score_summary",
    "BriefTemplate",
    "DEFAULT_BRIEF_TEMPLATE",
    "ArticleInput",
    "MissingContentTarget",
    "SummaryCandidate",
    "SummaryForScoring",
    "ExportCandidate",
    "BriefExportRecord",
]
