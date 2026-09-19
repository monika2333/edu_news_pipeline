"""Business rules for normalizing LLM-detected source names."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional


@dataclass(frozen=True, slots=True)
class SourceAliasRules:
    """Validated suffix and exact-alias rules for source normalization."""

    suffixes: tuple[str, ...] = ()
    aliases: Mapping[str, str] = field(default_factory=dict)


def normalize_source_name(
    source: Optional[str],
    rules: SourceAliasRules,
) -> Optional[str]:
    """Strip one configured suffix, then apply an exact alias replacement."""
    if source is None or not source.strip():
        return source

    normalized = source
    for suffix in rules.suffixes:
        if normalized.endswith(suffix):
            without_suffix = normalized[: -len(suffix)]
            if not without_suffix:
                return source
            normalized = without_suffix
            break

    return rules.aliases.get(normalized, normalized)


__all__ = ["SourceAliasRules", "normalize_source_name"]
