"""Shared scoring configuration and helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class ScoreWeights:
    """Weights applied to individual scoring dimensions."""

    keyword: float = 0.5
    coverage: float = 0.3
    freshness: float = 0.2


@dataclass(frozen=True)
class ScoreResult:
    """Structured result returned by scoring routines."""

    value: float
    reasons: Tuple[str, ...] = ()


DEFAULT_WEIGHTS = ScoreWeights()


def score_summary(*, text: str, article: str, weights: ScoreWeights = DEFAULT_WEIGHTS) -> ScoreResult:
    """Score a single summary.

    Replace this stub with the logic previously implemented in tooling scripts.
    The signature is keyword-only to encourage explicitness from workers.
    """

    raise NotImplementedError("score_summary needs an implementation")


__all__ = ["ScoreWeights", "ScoreResult", "DEFAULT_WEIGHTS", "score_summary"]

