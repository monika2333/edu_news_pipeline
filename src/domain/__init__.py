"""Domain-level objects shared across workers and adapters."""

from __future__ import annotations

from .external_filter import (
    BeijingGateCandidate,
    ExternalFilterCandidate,
    ExternalFilterResult,
    determine_candidate_category,
)
from .models import (
    ArticleInput,
    MissingContentTarget,
    PrimaryArticleForScoring,
    PrimaryArticleForSummarizing,
    SummaryCandidate,
    SummaryForScoring,
)
from .prompt import (
    MAX_PROMPT_CONTENT_LENGTH,
    MAX_PROMPT_NOTE_LENGTH,
    PROMPT_BY_KEY,
    PROMPT_DEFINITIONS,
    PROMPT_KEYS,
    PROMPT_KIND_DECISION,
    PROMPT_KIND_SCORE,
    PromptDefinition,
    PromptTemplate,
    SCORING_PROMPT_KEYS,
    format_prompt_version,
    parse_prompt_version,
    prompt_definition,
)
from .region import is_beijing_related
from .scoring import DEFAULT_WEIGHTS, ScoreResult, ScoreWeights, score_summary
from .source_aliases import (
    SourceAliasRules,
    normalize_source_name,
)
from .states import IN_PROGRESS_STATES, TERMINAL_STATES, ProcessState, is_terminal

__all__ = [
    "ArticleInput",
    "DEFAULT_WEIGHTS",
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
    "SourceAliasRules",
    "normalize_source_name",
    "is_beijing_related",
    "BeijingGateCandidate",
    "ExternalFilterCandidate",
    "ExternalFilterResult",
    "determine_candidate_category",
    "MAX_PROMPT_CONTENT_LENGTH",
    "MAX_PROMPT_NOTE_LENGTH",
    "PROMPT_BY_KEY",
    "PROMPT_DEFINITIONS",
    "PROMPT_KEYS",
    "PROMPT_KIND_DECISION",
    "PROMPT_KIND_SCORE",
    "PromptDefinition",
    "PromptTemplate",
    "SCORING_PROMPT_KEYS",
    "format_prompt_version",
    "parse_prompt_version",
    "prompt_definition",
]
