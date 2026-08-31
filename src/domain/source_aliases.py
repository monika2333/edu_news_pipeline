"""Business rules for normalizing LLM-detected source names."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional


@dataclass(frozen=True, slots=True)
class SourceAliasRules:
    """Validated suffix and exact-alias rules for source normalization."""

    suffixes: tuple[str, ...] = ()
    aliases: Mapping[str, str] = field(default_factory=dict)


def load_source_aliases(path: Optional[Path]) -> SourceAliasRules:
    """Load source normalization rules, returning empty rules on any failure."""
    if path is None:
        return SourceAliasRules()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return SourceAliasRules()

    if not isinstance(payload, dict):
        return SourceAliasRules()

    raw_suffixes = payload.get("suffixes")
    raw_aliases = payload.get("aliases")
    if not isinstance(raw_suffixes, list) or not isinstance(raw_aliases, dict):
        return SourceAliasRules()
    if not all(isinstance(suffix, str) and suffix for suffix in raw_suffixes):
        return SourceAliasRules()
    if not all(
        isinstance(alias, str)
        and alias
        and isinstance(canonical_name, str)
        and canonical_name
        for alias, canonical_name in raw_aliases.items()
    ):
        return SourceAliasRules()

    return SourceAliasRules(
        suffixes=tuple(raw_suffixes),
        aliases=dict(raw_aliases),
    )


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


__all__ = ["SourceAliasRules", "load_source_aliases", "normalize_source_name"]
