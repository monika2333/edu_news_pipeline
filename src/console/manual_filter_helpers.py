"""
manual_filter_helpers.py

Shared constants and helper functions for manual filter service.
These utilities depend only on src.domain, not other src.console modules.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional

from src.domain.report_type import (
    DEFAULT_REPORT_TYPE,
    NEWS_REPORT_TYPES as VALID_REPORT_TYPES,
    coerce_report_type as _normalize_report_type,
)


# ─────────────────────────────────────────────────────────────────────────────
# Candidate refine filters（时段 / 报送重复标签 / 分数）
# ─────────────────────────────────────────────────────────────────────────────
def _optional_int_in_range(value: Any, *, low: int, high: int, field: str) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
        # 拒绝 7.5 这类小数：int() 会静默截断，筛选语义会悄悄漂移
        if float(value) != parsed:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"{field} 必须是整数") from None
    if parsed < low or parsed > high:
        raise ValueError(f"{field} 必须在 {low} 到 {high} 之间")
    return parsed


def _optional_float(value: Any, field: str) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} 必须是数字") from None


def normalize_candidate_refine_filters(
    *,
    hour_from: Any = None,
    hour_to: Any = None,
    duplicate_state: Any = None,
    min_score: Any = None,
    max_score: Any = None,
) -> Dict[str, Any]:
    """归一化三个细化筛选参数；非法值抛 ValueError（路由层转 422）。

    返回的 duplicate_state 只会是 None / "untagged" / "tagged"；
    "all" 视为未启用。
    """
    normalized_state = (str(duplicate_state) if duplicate_state else "") or ""
    if normalized_state in ("", "all"):
        normalized_state = None
    elif normalized_state not in ("untagged", "tagged"):
        raise ValueError("duplicate_state 只支持 all / untagged / tagged")
    hour_from_val = _optional_int_in_range(hour_from, low=0, high=23, field="hour_from")
    hour_to_val = _optional_int_in_range(hour_to, low=0, high=23, field="hour_to")
    min_score_val = _optional_float(min_score, "min_score")
    max_score_val = _optional_float(max_score, "max_score")
    if (
        min_score_val is not None
        and max_score_val is not None
        and min_score_val > max_score_val
    ):
        raise ValueError("min_score 不能大于 max_score")
    return {
        "hour_from": hour_from_val,
        "hour_to": hour_to_val,
        "duplicate_state": normalized_state,
        "min_score": min_score_val,
        "max_score": max_score_val,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Discard page filters（放弃页：分类 / 放弃时间 / 分数 / 关键词）
# ─────────────────────────────────────────────────────────────────────────────
DISCARD_FILTER_KEYS = (
    "q",
    "region",
    "sentiment",
    "min_score",
    "max_score",
    "decided_since",
    "batch_decided_at",
)


def _optional_date(value: Any, field: str) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{field} 必须是日期（YYYY-MM-DD）") from None


def _optional_aware_datetime(value: Any, field: str) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"{field} 必须是带时区的时间") from None
    # 不带时区的时间在「同一批」精确比较中产生歧义，直接拒绝
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(f"{field} 必须带时区")
    return parsed


def normalize_discard_filters(
    *,
    region: Any = None,
    sentiment: Any = None,
    q: Any = None,
    min_score: Any = None,
    max_score: Any = None,
    decided_since: Any = None,
    batch_decided_at: Any = None,
) -> Dict[str, Any]:
    """归一化放弃页筛选条件；非法值抛 ValueError（路由层转 422）。

    管理员 `/discarded`、`/bulk-restore` 与值班 `/reviews`、`/bulk-restore`
    四个入口共用，保证两端条件的合法性判定只有一份。
    """
    normalized_region = (str(region) if region is not None else "") or ""
    if normalized_region not in ("", "internal", "external"):
        raise ValueError("region 只支持 internal / external")
    normalized_sentiment = (str(sentiment) if sentiment is not None else "") or ""
    if normalized_sentiment not in ("", "positive", "negative"):
        raise ValueError("sentiment 只支持 positive / negative")
    min_score_val = _optional_float(min_score, "min_score")
    max_score_val = _optional_float(max_score, "max_score")
    if (
        min_score_val is not None
        and max_score_val is not None
        and min_score_val > max_score_val
    ):
        raise ValueError("min_score 不能大于 max_score")
    return {
        "q": (str(q) if q is not None else "").strip() or None,
        "region": normalized_region or None,
        "sentiment": normalized_sentiment or None,
        "min_score": min_score_val,
        "max_score": max_score_val,
        "decided_since": _optional_date(decided_since, "decided_since"),
        "batch_decided_at": _optional_aware_datetime(
            batch_decided_at, "batch_decided_at"
        ),
    }


def has_discard_filters(filters: Mapping[str, Any]) -> bool:
    """判定放弃页筛选条件是否至少启用了一个（按条件恢复的前置校验）。"""
    return any(filters.get(key) not in (None, "") for key in DISCARD_FILTER_KEYS)


BULK_RESTORE_CONDITION_MESSAGE = "按条件恢复至少需要一个筛选条件"


def ensure_bulk_restore_has_condition(filters: Mapping[str, Any]) -> None:
    """无条件时拒绝按条件恢复，否则等于把全部放弃历史倒回待处理池。"""
    if not has_discard_filters(filters):
        raise ValueError(BULK_RESTORE_CONDITION_MESSAGE)


# ─────────────────────────────────────────────────────────────────────────────
# ID normalization
# ─────────────────────────────────────────────────────────────────────────────
def _normalize_ids(ids: Iterable[str]) -> List[str]:
    seen = {}
    for raw in ids or []:
        if not raw:
            continue
        key = str(raw).strip()
        if not key:
            continue
        seen[key] = True
    return list(seen.keys())


# ─────────────────────────────────────────────────────────────────────────────
# Score details / bonus keywords
# ─────────────────────────────────────────────────────────────────────────────
def _bonus_keywords(score_details: Any) -> List[str]:
    if not isinstance(score_details, dict):
        return []
    matched = score_details.get("matched_rules")
    if not isinstance(matched, list):
        return []
    labels: List[str] = []
    for rule in matched:
        if not isinstance(rule, dict):
            continue
        label = rule.get("label") or rule.get("rule_id")
        if label:
            labels.append(str(label))
    return labels


# ─────────────────────────────────────────────────────────────────────────────
# Source field helpers
# ─────────────────────────────────────────────────────────────────────────────
def _resolved_llm_source(record: Dict[str, Any]) -> str:
    """
    Prefer manual override, then LLM-detected, then raw source.
    """
    manual = (record.get("manual_llm_source") or "").strip()
    llm = (record.get("llm_source") or "").strip()
    source = (record.get("source") or "").strip()
    return manual or llm or source


def _attach_source_fields(record: Dict[str, Any]) -> Dict[str, Any]:
    record["llm_source_manual"] = (record.get("manual_llm_source") or "").strip()
    record["llm_source_raw"] = (record.get("llm_source") or "").strip()
    record["llm_source_display"] = _resolved_llm_source(record)
    return record


# ─────────────────────────────────────────────────────────────────────────────
# Group field helpers
# ─────────────────────────────────────────────────────────────────────────────
def _attach_group_fields(record: Dict[str, Any]) -> Dict[str, Any]:
    region = "internal" if record.get("is_beijing_related") else "external"
    sentiment = "negative" if (record.get("sentiment_label") or "").lower() == "negative" else "positive"
    record["region"] = region
    record["sentiment_key"] = sentiment
    record["group_key"] = f"{region}_{sentiment}"
    return record
