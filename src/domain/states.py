from __future__ import annotations

from enum import Enum
from typing import FrozenSet


class ProcessState(str, Enum):
    """Finite set of statuses used to track article progress in the pipeline."""

    NEW = "NEW"
    SUMMARIZING = "SUMMARIZING"
    SUMMARIZED = "SUMMARIZED"
    SCORING = "SCORING"
    SCORED = "SCORED"
    EXPORTING = "EXPORTING"
    EXPORTED = "EXPORTED"


IN_PROGRESS_STATES: FrozenSet[ProcessState] = frozenset(
    {
        ProcessState.SUMMARIZING,
        ProcessState.SCORING,
        ProcessState.EXPORTING,
    }
)

TERMINAL_STATES: FrozenSet[ProcessState] = frozenset({ProcessState.SCORED, ProcessState.EXPORTED})


def is_terminal(state: ProcessState) -> bool:
    """Return True when the state represents a finished pipeline step."""

    return state in TERMINAL_STATES


__all__ = [
    "ProcessState",
    "IN_PROGRESS_STATES",
    "TERMINAL_STATES",
    "is_terminal",
]
