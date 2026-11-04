"""Business rules for normalizing LLM-detected source names."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional


@dataclass(frozen=True, slots=True)
class SourceAliasRules:
    """Validated suffix and exact-alias rules for source normalization.

    ``fallback_names`` 是回退名单：识别结果与名单中的名称整串全等时，
    该识别结果不保存（写 ``NULL``），展示层自然回退到抓取来源。
    """

    suffixes: tuple[str, ...] = ()
    aliases: Mapping[str, str] = field(default_factory=dict)
    fallback_names: tuple[str, ...] = ()


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


def matches_fallback_name(
    raw_source: Optional[str],
    normalized_source: Optional[str],
    rules: SourceAliasRules,
) -> bool:
    """True when either name equals a configured fallback name exactly.

    归一化前后的名称都要比对：名单写原始名称（如「现代教育报客户端」）
    或标准名称（如「现代教育报」）都应生效。匹配只做整串全等，
    大小写与符号一律不做处理——书名号包裹、机构名派生（「XX报社」）
    等不常见情况留给人工回退。
    """

    if not rules.fallback_names:
        return False
    for candidate in (raw_source, normalized_source):
        if candidate is not None and candidate in rules.fallback_names:
            return True
    return False


__all__ = ["SourceAliasRules", "matches_fallback_name", "normalize_source_name"]
