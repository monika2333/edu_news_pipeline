"""Domain-level objects shared across workers and adapters."""

from __future__ import annotations

from .states import ProcessState
from .scoring import DEFAULT_WEIGHTS, ScoreResult, ScoreWeights, score_summary
from .templates import BriefTemplate, DEFAULT_BRIEF_TEMPLATE

__all__ = [
    "ProcessState",
    "DEFAULT_WEIGHTS",
    "ScoreResult",
    "ScoreWeights",
    "score_summary",
    "BriefTemplate",
    "DEFAULT_BRIEF_TEMPLATE",
]
