from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(slots=True)
class BeijingGateCandidate:
    article_id: str
    title: Optional[str]
    source: Optional[str]
    publish_time_iso: Optional[str]
    summary: str
    content: str
    sentiment_label: Optional[str]
    is_beijing_related: Optional[bool]
    is_beijing_related_llm: Optional[bool]
    external_importance_status: str
    beijing_gate_fail_count: int = 0
    beijing_gate_attempted_at: Optional[str] = None


@dataclass(slots=True)
class ExternalFilterCandidate:
    article_id: str
    title: Optional[str]
    source: Optional[str]
    publish_time_iso: Optional[str]
    summary: str
    content: str
    sentiment_label: Optional[str]
    is_beijing_related: Optional[bool]
    is_beijing_related_llm: Optional[bool]
    external_importance_status: str
    external_filter_fail_count: int = 0
    keyword_matches: tuple[str, ...] = ()

    @property
    def candidate_category(self) -> str:
        return "internal" if self.is_beijing_related else "external"


@dataclass(slots=True)
class ExternalFilterResult:
    article_id: str
    score: Optional[int]
    raw_output: Optional[str]
    status: str
    error: Optional[str] = None


__all__ = ["BeijingGateCandidate", "ExternalFilterCandidate", "ExternalFilterResult"]
