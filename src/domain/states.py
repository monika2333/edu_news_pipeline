from enum import Enum


class ProcessState(str, Enum):
    """Finite set of statuses used to track article progress in the pipeline."""

    NEW = "NEW"
    SUMMARIZING = "SUMMARIZING"
    SUMMARIZED = "SUMMARIZED"
    SCORING = "SCORING"
    SCORED = "SCORED"
    EXPORTING = "EXPORTING"
    EXPORTED = "EXPORTED"


__all__ = ["ProcessState"]
