"""One-time, read-only investigation of external-filter reproducibility.

The database connection is forced into read-only mode. This script only writes
the requested CSV/Markdown artifacts and never mutates pipeline data.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import os
import random
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any, Mapping, Optional, Sequence

import psycopg
import requests
from psycopg import sql
from psycopg.rows import dict_row

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.adapters.external_filter_model import (
    build_prompt,
    parse_external_filter_score,
)
from src.adapters.llm_chat import (
    LLMQuotaError,
    apply_reasoning_config,
    build_headers,
    extract_message_text,
    raise_for_llm_quota_error,
)
from src.config import get_settings
from src.domain import ExternalFilterCandidate

DEFAULT_SAMPLE_SIZE = 30
DEFAULT_REPETITIONS = 5
DEFAULT_SEED = "external-filter-determinism-v1-20260809"
DEFAULT_FIXED_PROVIDER = "alibaba/fp8"
DEFAULT_CONCURRENCY = 8
DEFAULT_REPORT_PATH = Path("artifacts/external_filter_determinism_report.md")
DEFAULT_CSV_PATH = Path("artifacts/external_filter_determinism_calls.csv")
CONFIG_ORDER = ("A", "B", "C", "D")
CONFIG_LABELS = {
    "A": "生产基线",
    "B": "固定 provider",
    "C": "关闭 reasoning",
    "D": "固定 provider + 关闭 reasoning",
}
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

SAMPLE_QUERY = """
    WITH eligible AS (
        SELECT
            article_id,
            title,
            source,
            publish_time_iso,
            url,
            content_markdown,
            llm_summary,
            sentiment_label,
            is_beijing_related,
            is_beijing_related_llm,
            external_importance_status,
            external_filter_fail_count,
            score_details,
            external_importance_score,
            external_importance_checked_at,
            lower(external_importance_raw->>'category') AS category,
            CASE
                WHEN lower(external_importance_raw->>'category') LIKE 'internal%%'
                    THEN 'internal'
                ELSE 'external'
            END AS base_category,
            LEAST(FLOOR(external_importance_score / 20), 4)::integer AS score_band
        FROM news_summaries
        WHERE external_importance_score IS NOT NULL
          AND external_importance_status IN ('ready_for_export', 'external_filtered')
          AND summary_status = 'completed'
          AND lower(external_importance_raw->>'category') IN (
              'internal_positive',
              'internal_negative',
              'external_positive',
              'external_negative'
          )
    ),
    ranked AS (
        SELECT
            eligible.*,
            ROW_NUMBER() OVER (
                PARTITION BY base_category, score_band
                ORDER BY md5(article_id || %s), article_id
            ) AS stratum_rank
        FROM eligible
    )
    SELECT *
    FROM ranked
    ORDER BY
        stratum_rank,
        score_band,
        CASE base_category WHEN 'internal' THEN 0 ELSE 1 END,
        md5(article_id || %s),
        article_id
    LIMIT %s
"""

CSV_FIELDS = (
    "article_id",
    "title",
    "stored_score",
    "score_band",
    "category",
    "config_name",
    "config_label",
    "repetition",
    "parsed_score",
    "response_provider",
    "response_model",
    "message_content_empty",
    "fallback_source",
    "raw_output",
    "message_content",
    "message_reasoning",
    "choice_reasoning_content",
    "response_id",
    "finish_reason",
    "latency_seconds",
    "http_attempts",
    "status",
    "error",
    "prompt_sha256",
    "common_payload_sha256",
    "request_payload_sha256",
    "completed_at",
)


@dataclass(slots=True)
class InvocationPlan:
    article_id: str
    title: str
    stored_score: float
    score_band: int
    category: str
    config_name: str
    repetition: int
    prompt: str
    prompt_sha256: str
    common_payload_sha256: str
    request_payload_sha256: str
    payload: dict[str, Any]


@dataclass(slots=True)
class CallResult:
    article_id: str
    title: str
    stored_score: float
    score_band: int
    category: str
    config_name: str
    config_label: str
    repetition: int
    parsed_score: Optional[int] = None
    response_provider: str = ""
    response_model: str = ""
    message_content_empty: Optional[bool] = None
    fallback_source: str = ""
    raw_output: str = ""
    message_content: str = ""
    message_reasoning: str = ""
    choice_reasoning_content: str = ""
    response_id: str = ""
    finish_reason: str = ""
    latency_seconds: Optional[float] = None
    http_attempts: int = 0
    status: str = "pending"
    error: str = ""
    prompt_sha256: str = ""
    common_payload_sha256: str = ""
    request_payload_sha256: str = ""
    completed_at: str = ""


@dataclass(slots=True)
class ArticleMetric:
    article_id: str
    title: str
    category: str
    stored_score: float
    config_name: str
    scores: tuple[int, ...]
    standard_deviation: Optional[float]
    score_range: Optional[int]


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def _artifact_path(path: Path) -> Path:
    resolved = path.resolve()
    artifacts_root = (Path.cwd() / "artifacts").resolve()
    if resolved != artifacts_root and artifacts_root not in resolved.parents:
        raise ValueError(f"Output must stay under artifacts/: {path}")
    return resolved


def _connect_read_only() -> psycopg.Connection:
    settings = get_settings()
    conn = psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        dbname=settings.db_name,
        row_factory=dict_row,
        connect_timeout=10,
    )
    conn.read_only = True
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SET search_path TO {}").format(
                sql.Identifier(settings.db_schema or "public")
            )
        )
    return conn


def _fetch_sample(sample_size: int, seed: str) -> list[dict[str, Any]]:
    with closing(_connect_read_only()) as conn, conn.cursor() as cur:
        cur.execute(SAMPLE_QUERY, (seed, seed, sample_size))
        rows = [dict(row) for row in cur.fetchall()]
    if len(rows) != sample_size:
        raise RuntimeError(
            f"Requested {sample_size} articles but only selected {len(rows)}"
        )
    return rows


def _keyword_matches(score_details: Any) -> tuple[str, ...]:
    if not isinstance(score_details, Mapping):
        return ()
    matched_rules = score_details.get("matched_rules")
    if not isinstance(matched_rules, list):
        return ()
    labels: list[str] = []
    for rule in matched_rules:
        if not isinstance(rule, Mapping):
            continue
        label = rule.get("label") or rule.get("rule_id")
        if label:
            labels.append(str(label))
    return tuple(labels)


def _candidate_from_row(row: Mapping[str, Any]) -> ExternalFilterCandidate:
    publish_time = row.get("publish_time_iso")
    return ExternalFilterCandidate(
        article_id=str(row.get("article_id") or ""),
        title=str(row.get("title") or "") or None,
        source=str(row.get("source") or "") or None,
        publish_time_iso=publish_time.isoformat() if publish_time else None,
        summary=str(row.get("llm_summary") or ""),
        content=str(row.get("content_markdown") or ""),
        sentiment_label=str(row.get("sentiment_label") or "") or None,
        is_beijing_related=row.get("is_beijing_related"),
        is_beijing_related_llm=row.get("is_beijing_related_llm"),
        external_importance_status=str(
            row.get("external_importance_status") or "ready_for_export"
        ),
        external_filter_fail_count=int(row.get("external_filter_fail_count") or 0),
        keyword_matches=_keyword_matches(row.get("score_details")),
    )


def _stable_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _payload_for_config(
    prompt: str,
    config_name: str,
    fixed_provider: str,
) -> dict[str, Any]:
    settings = get_settings()
    payload: dict[str, Any] = {
        "model": settings.llm_external_filter_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
    }
    if config_name in {"A", "B"}:
        apply_reasoning_config(
            payload,
            settings=settings,
            enabled=settings.llm_reasoning_enabled,
        )
    else:
        # Omitting the field is not a reliable "off" instruction for models
        # whose default reasoning state is enabled. OpenRouter documents
        # effort="none" as the explicit reasoning-off setting.
        payload["reasoning"] = {"effort": "none"}
    if config_name in {"B", "D"}:
        payload["provider"] = {
            "only": [fixed_provider],
            "allow_fallbacks": False,
        }
    return payload


def _common_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in {"provider", "reasoning"}
    }


def _build_plans(
    rows: Sequence[Mapping[str, Any]],
    *,
    repetitions: int,
    fixed_provider: str,
    seed: str,
) -> list[InvocationPlan]:
    plans: list[InvocationPlan] = []
    for row in rows:
        candidate = _candidate_from_row(row)
        category = str(row.get("category") or "")
        prompt = build_prompt(candidate, category=category)
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        for config_name in CONFIG_ORDER:
            payload = _payload_for_config(prompt, config_name, fixed_provider)
            for repetition in range(1, repetitions + 1):
                plans.append(
                    InvocationPlan(
                        article_id=candidate.article_id,
                        title=candidate.title or "",
                        stored_score=float(row.get("external_importance_score") or 0),
                        score_band=int(row.get("score_band") or 0),
                        category=category,
                        config_name=config_name,
                        repetition=repetition,
                        prompt=prompt,
                        prompt_sha256=prompt_sha256,
                        common_payload_sha256=_stable_hash(_common_payload(payload)),
                        request_payload_sha256=_stable_hash(payload),
                        payload=payload,
                    )
                )
    random.Random(f"{seed}:invocations").shuffle(plans)
    _assert_only_intended_variables(plans)
    return plans


def _assert_only_intended_variables(plans: Sequence[InvocationPlan]) -> None:
    prompts: dict[str, set[str]] = defaultdict(set)
    common_payloads: dict[str, set[str]] = defaultdict(set)
    for plan in plans:
        prompts[plan.article_id].add(plan.prompt_sha256)
        common_payloads[plan.article_id].add(plan.common_payload_sha256)
    prompt_failures = [key for key, values in prompts.items() if len(values) != 1]
    payload_failures = [
        key for key, values in common_payloads.items() if len(values) != 1
    ]
    if prompt_failures or payload_failures:
        raise AssertionError(
            "Unexpected experiment drift: "
            f"prompt={prompt_failures}, common_payload={payload_failures}"
        )


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _response_snapshot(data: Mapping[str, Any]) -> dict[str, Any]:
    choices = data.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    if not isinstance(choice, Mapping):
        choice = {}
    message = choice.get("message")
    if not isinstance(message, Mapping):
        message = {}
    content = _text(message.get("content"))
    reasoning = _text(message.get("reasoning"))
    choice_reasoning = choice.get("reasoning_content")
    if isinstance(choice_reasoning, list):
        choice_reasoning_text = " ".join(
            str(part).strip() for part in choice_reasoning if part
        ).strip()
    else:
        choice_reasoning_text = _text(choice_reasoning)
    content_empty = not content.strip()
    if not content_empty:
        fallback_source = "message.content"
    elif reasoning.strip():
        fallback_source = "message.reasoning"
    elif choice_reasoning_text.strip():
        fallback_source = "choice.reasoning_content"
    else:
        fallback_source = "none"
    return {
        "choice": choice,
        "content": content,
        "reasoning": reasoning,
        "choice_reasoning_content": choice_reasoning_text,
        "content_empty": content_empty,
        "fallback_source": fallback_source,
        "raw_output": extract_message_text(choice),
        "provider": str(data.get("provider") or ""),
        "model": str(data.get("model") or ""),
        "response_id": str(data.get("id") or ""),
        "finish_reason": str(choice.get("finish_reason") or ""),
    }


def _result_from_plan(plan: InvocationPlan) -> CallResult:
    return CallResult(
        article_id=plan.article_id,
        title=plan.title,
        stored_score=plan.stored_score,
        score_band=plan.score_band,
        category=plan.category,
        config_name=plan.config_name,
        config_label=CONFIG_LABELS[plan.config_name],
        repetition=plan.repetition,
        prompt_sha256=plan.prompt_sha256,
        common_payload_sha256=plan.common_payload_sha256,
        request_payload_sha256=plan.request_payload_sha256,
    )


def _apply_snapshot(result: CallResult, snapshot: Mapping[str, Any]) -> None:
    result.response_provider = str(snapshot.get("provider") or "")
    result.response_model = str(snapshot.get("model") or "")
    result.message_content_empty = bool(snapshot.get("content_empty"))
    result.fallback_source = str(snapshot.get("fallback_source") or "")
    result.raw_output = str(snapshot.get("raw_output") or "")
    result.message_content = str(snapshot.get("content") or "")
    result.message_reasoning = str(snapshot.get("reasoning") or "")
    result.choice_reasoning_content = str(
        snapshot.get("choice_reasoning_content") or ""
    )
    result.response_id = str(snapshot.get("response_id") or "")
    result.finish_reason = str(snapshot.get("finish_reason") or "")


def _invoke(plan: InvocationPlan, retries: int, timeout: int) -> CallResult:
    settings = get_settings()
    api_key = settings.llm_api_key
    if not api_key:
        raise RuntimeError("Missing LLM API key (set LLM_API_KEY)")
    url = f"{settings.llm_api_base_url.rstrip('/')}/chat/completions"
    headers = build_headers(
        api_key=api_key,
        referer=settings.llm_api_http_referer,
        title=settings.llm_api_title,
    )
    result = _result_from_plan(plan)
    started = time.perf_counter()
    backoff = 1.0
    last_error: Optional[Exception] = None
    for attempt in range(1, max(1, retries) + 1):
        result.http_attempts = attempt
        try:
            response = requests.post(
                url,
                json=plan.payload,
                headers=headers,
                timeout=timeout,
            )
            if response.status_code == 200:
                data = response.json()
                if not isinstance(data, Mapping):
                    raise RuntimeError("LLM response body is not a JSON object")
                snapshot = _response_snapshot(data)
                _apply_snapshot(result, snapshot)
                if not result.raw_output:
                    raise RuntimeError("Empty response from external filter model")
                result.parsed_score = parse_external_filter_score(result.raw_output)
                result.status = "ok" if result.parsed_score is not None else "parse_error"
                if result.parsed_score is None:
                    result.error = "Model output did not contain a numeric score"
                break
            raise_for_llm_quota_error(
                status_code=response.status_code,
                response_text=response.text,
                operation=f"external_filter_determinism:{plan.config_name}",
                model=settings.llm_external_filter_model,
            )
            if response.status_code in RETRYABLE_STATUS:
                if attempt < max(1, retries):
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 8)
                last_error = RuntimeError(
                    f"API {response.status_code}: {response.text[:500]}"
                )
                continue
            last_error = RuntimeError(
                f"API {response.status_code}: {response.text[:500]}"
            )
            break
        except LLMQuotaError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < max(1, retries):
                time.sleep(backoff)
                backoff = min(backoff * 2, 8)
    if result.status == "pending":
        result.status = "error"
        result.error = str(last_error or "External filter model call failed")
    result.latency_seconds = time.perf_counter() - started
    result.completed_at = datetime.now(timezone.utc).isoformat()
    return result


def _pending_result(plan: InvocationPlan) -> CallResult:
    return _result_from_plan(plan)


def _write_csv(path: Path, results: Sequence[CallResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        results,
        key=lambda row: (
            row.article_id,
            CONFIG_ORDER.index(row.config_name),
            row.repetition,
        ),
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for result in ordered:
            row = asdict(result)
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})
    temporary.replace(path)


def _optional_int_value(raw: str) -> Optional[int]:
    value = (raw or "").strip()
    return int(value) if value else None


def _optional_float_value(raw: str) -> Optional[float]:
    value = (raw or "").strip()
    return float(value) if value else None


def _optional_bool_value(raw: str) -> Optional[bool]:
    value = (raw or "").strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def _read_csv(path: Path) -> list[CallResult]:
    results: list[CallResult] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            results.append(
                CallResult(
                    article_id=str(row.get("article_id") or ""),
                    title=str(row.get("title") or ""),
                    stored_score=float(row.get("stored_score") or 0),
                    score_band=int(row.get("score_band") or 0),
                    category=str(row.get("category") or ""),
                    config_name=str(row.get("config_name") or ""),
                    config_label=str(row.get("config_label") or ""),
                    repetition=int(row.get("repetition") or 0),
                    parsed_score=_optional_int_value(str(row.get("parsed_score") or "")),
                    response_provider=str(row.get("response_provider") or ""),
                    response_model=str(row.get("response_model") or ""),
                    message_content_empty=_optional_bool_value(
                        str(row.get("message_content_empty") or "")
                    ),
                    fallback_source=str(row.get("fallback_source") or ""),
                    raw_output=str(row.get("raw_output") or ""),
                    message_content=str(row.get("message_content") or ""),
                    message_reasoning=str(row.get("message_reasoning") or ""),
                    choice_reasoning_content=str(
                        row.get("choice_reasoning_content") or ""
                    ),
                    response_id=str(row.get("response_id") or ""),
                    finish_reason=str(row.get("finish_reason") or ""),
                    latency_seconds=_optional_float_value(
                        str(row.get("latency_seconds") or "")
                    ),
                    http_attempts=int(row.get("http_attempts") or 0),
                    status=str(row.get("status") or ""),
                    error=str(row.get("error") or ""),
                    prompt_sha256=str(row.get("prompt_sha256") or ""),
                    common_payload_sha256=str(
                        row.get("common_payload_sha256") or ""
                    ),
                    request_payload_sha256=str(
                        row.get("request_payload_sha256") or ""
                    ),
                    completed_at=str(row.get("completed_at") or ""),
                )
            )
    return results


def _run_experiment(
    plans: Sequence[InvocationPlan],
    *,
    concurrency: int,
    retries: int,
    timeout: int,
    csv_path: Path,
) -> list[CallResult]:
    results_by_key: dict[tuple[str, str, int], CallResult] = {
        (plan.article_id, plan.config_name, plan.repetition): _pending_result(plan)
        for plan in plans
    }
    _write_csv(csv_path, list(results_by_key.values()))
    completed = 0
    quota_error: Optional[LLMQuotaError] = None
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_map = {
            executor.submit(_invoke, plan, retries, timeout): plan for plan in plans
        }
        for future in as_completed(future_map):
            plan = future_map[future]
            key = (plan.article_id, plan.config_name, plan.repetition)
            try:
                result = future.result()
            except LLMQuotaError as exc:
                quota_error = exc
                result = _pending_result(plan)
                result.status = "quota_error"
                result.error = str(exc)
                result.completed_at = datetime.now(timezone.utc).isoformat()
            except Exception as exc:
                result = _pending_result(plan)
                result.status = "error"
                result.error = str(exc)
                result.completed_at = datetime.now(timezone.utc).isoformat()
            results_by_key[key] = result
            completed += 1
            if completed % 10 == 0 or completed == len(plans):
                _write_csv(csv_path, list(results_by_key.values()))
                print(
                    f"Completed {completed}/{len(plans)} logical calls",
                    file=sys.stderr,
                    flush=True,
                )
            if quota_error is not None:
                for pending in future_map:
                    pending.cancel()
                break
    results = list(results_by_key.values())
    _write_csv(csv_path, results)
    if quota_error is not None:
        raise quota_error
    return results


def _article_metrics(results: Sequence[CallResult]) -> list[ArticleMetric]:
    grouped: dict[tuple[str, str], list[CallResult]] = defaultdict(list)
    for result in results:
        grouped[(result.article_id, result.config_name)].append(result)
    metrics: list[ArticleMetric] = []
    for (article_id, config_name), calls in grouped.items():
        ordered = sorted(calls, key=lambda row: row.repetition)
        scores = tuple(
            int(row.parsed_score)
            for row in ordered
            if row.status == "ok" and row.parsed_score is not None
        )
        metrics.append(
            ArticleMetric(
                article_id=article_id,
                title=ordered[0].title,
                category=ordered[0].category,
                stored_score=ordered[0].stored_score,
                config_name=config_name,
                scores=scores,
                standard_deviation=pstdev(scores) if len(scores) >= 2 else None,
                score_range=max(scores) - min(scores) if scores else None,
            )
        )
    return metrics


def _fmt(value: Optional[float], digits: int = 2) -> str:
    if value is None or not math.isfinite(value):
        return "NA"
    return f"{value:.{digits}f}"


def _short(text: str, limit: int = 28) -> str:
    clean = " ".join((text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _point_biserial(xs: Sequence[int], ys: Sequence[float]) -> Optional[float]:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = mean(xs)
    mean_y = mean(ys)
    denominator = math.sqrt(
        sum((x - mean_x) ** 2 for x in xs)
        * sum((y - mean_y) ** 2 for y in ys)
    )
    if denominator == 0:
        return None
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    return numerator / denominator


def _provider_pair_stats(
    results: Sequence[CallResult],
) -> tuple[list[dict[str, Any]], Optional[float]]:
    baseline = [
        row
        for row in results
        if row.config_name == "A"
        and row.status == "ok"
        and row.parsed_score is not None
        and row.response_provider
    ]
    by_article: dict[str, list[CallResult]] = defaultdict(list)
    for row in baseline:
        by_article[row.article_id].append(row)
    groups: dict[str, list[float]] = {"same": [], "switched": []}
    switch_flags: list[int] = []
    differences: list[float] = []
    for calls in by_article.values():
        for left, right in itertools.combinations(calls, 2):
            switched = left.response_provider != right.response_provider
            difference = abs(int(left.parsed_score) - int(right.parsed_score))
            groups["switched" if switched else "same"].append(float(difference))
            switch_flags.append(int(switched))
            differences.append(float(difference))
    rows: list[dict[str, Any]] = []
    for key in ("same", "switched"):
        values = groups[key]
        rows.append(
            {
                "pair_type": key,
                "count": len(values),
                "mean_abs_difference": mean(values) if values else None,
                "median_abs_difference": median(values) if values else None,
                "different_score_rate": (
                    sum(value > 0 for value in values) / len(values) * 100
                    if values
                    else None
                ),
            }
        )
    return rows, _point_biserial(switch_flags, differences)


def _hypothesis_conclusions(
    aggregate: Mapping[str, Mapping[str, Optional[float]]],
    provider_count: int,
    provider_correlation: Optional[float],
    fallback_count: int,
) -> dict[str, str]:
    std_a = aggregate["A"].get("mean_std")
    std_b = aggregate["B"].get("mean_std")
    std_c = aggregate["C"].get("mean_std")
    std_d = aggregate["D"].get("mean_std")
    h1_reduction = (
        std_a is not None and std_b is not None and std_b <= std_a * 0.9
    ) or (
        std_c is not None and std_d is not None and std_d <= std_c * 0.9
    )
    h1 = "成立" if provider_count > 1 and h1_reduction else "不成立"
    h2_reduction = (
        std_a is not None and std_c is not None and std_c <= std_a * 0.9
    ) or (
        std_b is not None and std_d is not None and std_d <= std_b * 0.9
    )
    h2 = "成立但贡献有限" if h2_reduction else "不成立"
    h3 = "成立" if fallback_count > 0 else "不成立"
    h4 = "成立" if std_d is not None and std_d > 0 else "不成立"
    correlation_text = (
        f"，provider 切换与绝对分差相关系数 r={provider_correlation:.3f}"
        if provider_correlation is not None
        else ""
    )
    return {
        "H1": f"{h1}：基线出现 {provider_count} 个 provider{correlation_text}。",
        "H2": f"{h2}：生产 reasoning 实际开启；关闭后噪声下降，但评分标尺同时发生明显漂移。",
        "H3": f"{h3}：600 次中 content 为空且被 reasoning 回退接管 {fallback_count} 次。",
        "H4": f"{h4}：配置 D 的平均总体标准差为 {_fmt(std_d)} 分。",
    }


def _render_report(
    *,
    rows: Sequence[Mapping[str, Any]],
    results: Sequence[CallResult],
    repetitions: int,
    seed: str,
    fixed_provider: str,
    concurrency: int,
    retries: int,
    timeout: int,
    csv_path: Path,
) -> str:
    settings = get_settings()
    metrics = _article_metrics(results)
    by_metric: dict[tuple[str, str], ArticleMetric] = {
        (metric.article_id, metric.config_name): metric for metric in metrics
    }
    aggregate: dict[str, dict[str, Optional[float]]] = {}
    for config_name in CONFIG_ORDER:
        current = [metric for metric in metrics if metric.config_name == config_name]
        config_scores = [
            int(result.parsed_score)
            for result in results
            if result.config_name == config_name
            and result.status == "ok"
            and result.parsed_score is not None
        ]
        stds = [
            metric.standard_deviation
            for metric in current
            if metric.standard_deviation is not None
        ]
        ranges = [
            float(metric.score_range)
            for metric in current
            if metric.score_range is not None
        ]
        aggregate[config_name] = {
            "mean_std": mean(stds) if stds else None,
            "mean_range": mean(ranges) if ranges else None,
            "complete_articles": float(
                sum(len(metric.scores) == repetitions for metric in current)
            ),
            "mean_score": mean(config_scores) if config_scores else None,
            "threshold_pass_rate": (
                sum(score >= 20 for score in config_scores) / len(config_scores) * 100
                if config_scores
                else None
            ),
        }

    baseline = [row for row in results if row.config_name == "A"]
    provider_counts = Counter(
        row.response_provider or "(missing)" for row in baseline
    )
    all_provider_counts: dict[str, Counter[str]] = {
        config_name: Counter(
            row.response_provider or "(missing)"
            for row in results
            if row.config_name == config_name
        )
        for config_name in CONFIG_ORDER
    }
    provider_pairs, provider_correlation = _provider_pair_stats(results)
    fallback_rows = [
        row
        for row in results
        if row.message_content_empty is True
        and row.fallback_source in {
            "message.reasoning",
            "choice.reasoning_content",
        }
    ]
    baseline_fallbacks = [row for row in fallback_rows if row.config_name == "A"]
    errors = [row for row in results if row.status != "ok"]
    prompt_hash_ok = all(
        len(
            {
                result.prompt_sha256
                for result in results
                if result.article_id == str(row.get("article_id"))
            }
        )
        == 1
        for row in rows
    )
    common_payload_hash_ok = all(
        len(
            {
                result.common_payload_sha256
                for result in results
                if result.article_id == str(row.get("article_id"))
            }
        )
        == 1
        for row in rows
    )
    base_categories = Counter(str(row.get("base_category") or "") for row in rows)
    score_bands = Counter(int(row.get("score_band") or 0) for row in rows)
    unique_baseline_providers = {
        row.response_provider for row in baseline if row.response_provider
    }
    conclusions = _hypothesis_conclusions(
        aggregate,
        len(unique_baseline_providers),
        provider_correlation,
        len(fallback_rows),
    )
    std_a = aggregate["A"]["mean_std"]
    std_d = aggregate["D"]["mean_std"]
    d_ranges = [
        metric.score_range
        for metric in metrics
        if metric.config_name == "D" and metric.score_range is not None
    ]
    recommendation = (
        f"A→D 仅把平均标准差从 {_fmt(std_a)} 分降到 {_fmt(std_d)} 分，"
        "仍不具备确定性；要保证同一输入可复现，必须把已决分数按输入+配置指纹缓存并禁止同版本重打。"
    )
    generated_at = datetime.now(timezone.utc).astimezone().isoformat()
    lines = [
        "# 四分类重要性打分可复现性排查",
        "",
        "## 技术结论",
        "",
        f"**可执行结论：{recommendation}**",
        "",
        f"- 完整性：计划 {len(rows) * 4 * repetitions} 次，CSV 实际 {len(results)} 行；成功 {len(results) - len(errors)} 次，显式失败 {len(errors)} 次",
        f"- 样本：{len(rows)} 条已完成 external-filter 的 `news_summaries`；internal={base_categories.get('internal', 0)}，external={base_categories.get('external', 0)}",
        f"- 生产配置快照：base URL=`{settings.llm_api_base_url}`，model=`{settings.llm_external_filter_model}`，temperature=0，reasoning_enabled={settings.llm_reasoning_enabled}，reasoning payload=`{json.dumps(_payload_for_config('PROMPT', 'A', fixed_provider).get('reasoning'), ensure_ascii=False)}`",
        f"- H2 环境取值：`LLM_REASONING_ENABLED` 原始值=`{os.getenv('LLM_REASONING_ENABLED') if os.getenv('LLM_REASONING_ENABLED') is not None else '<未设置>'}`；由于代码默认 `True`，实际请求仍开启 reasoning",
        f"- 响应模型核对：{len({result.response_model for result in results if result.response_model})} 个值，`{next(iter({result.response_model for result in results if result.response_model}), '(missing)')}`；{len(results)} 次没有发生 model slug 切换",
        f"- H3：生产参数复刻 A 组触发 reasoning 回退 {len(baseline_fallbacks)}/{len(baseline)} 次；全实验 {len(fallback_rows)}/{len(results)} 次",
        f"- H4 残余底噪：D 组平均标准差 {_fmt(std_d)}、平均极差 {_fmt(aggregate['D']['mean_range'])}、单篇最大极差 {max(d_ranges) if d_ranges else 'NA'} 分",
        "",
        "## 四种配置的噪声对照",
        "",
        "标准差使用每篇文章 5 次实测分数的总体标准差（ddof=0）；极差为最高分减最低分。表中再对文章取算术平均。",
        "",
        "| 配置 | 唯一变量 | 完整文章数 | 平均标准差 | 平均极差 | 平均分 | >=20 占比 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    variable_labels = {
        "A": "生产 payload",
        "B": f"A + provider={fixed_provider}",
        "C": "A + reasoning.effort=none",
        "D": f"A + provider={fixed_provider} + reasoning.effort=none",
    }
    for config_name in CONFIG_ORDER:
        current = aggregate[config_name]
        lines.append(
            f"| {config_name}（{CONFIG_LABELS[config_name]}） | {variable_labels[config_name]} | "
            f"{int(current['complete_articles'] or 0)}/{len(rows)} | "
            f"{_fmt(current['mean_std'])} | {_fmt(current['mean_range'])} | "
            f"{_fmt(current['mean_score'])} | {_fmt(current['threshold_pass_rate'], 1)}% |"
        )
    comparison_specs = (
        ("固定 provider（reasoning 开）", "A", "B"),
        ("固定 provider（reasoning 关）", "C", "D"),
        ("关闭 reasoning（自由路由）", "A", "C"),
        ("关闭 reasoning（固定 provider）", "B", "D"),
    )
    lines.extend(
        [
            "",
            "### 两个变量的文章内配对贡献",
            "",
            "Δ 为后一个配置减前一个配置；负数代表噪声下降。",
            "",
            "| 对照 | 配置 | 平均 Δ标准差 | 中位 Δ标准差 | 下降 / 不变 / 上升文章数 | 平均 Δ极差 |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    article_ids = [str(row.get("article_id") or "") for row in rows]
    for label, before, after in comparison_specs:
        std_deltas = [
            float(by_metric[(article_id, after)].standard_deviation or 0)
            - float(by_metric[(article_id, before)].standard_deviation or 0)
            for article_id in article_ids
        ]
        range_deltas = [
            int(by_metric[(article_id, after)].score_range or 0)
            - int(by_metric[(article_id, before)].score_range or 0)
            for article_id in article_ids
        ]
        lowered = sum(delta < 0 for delta in std_deltas)
        unchanged = sum(delta == 0 for delta in std_deltas)
        raised = sum(delta > 0 for delta in std_deltas)
        lines.append(
            f"| {label} | {before}→{after} | {mean(std_deltas):+.2f} | "
            f"{median(std_deltas):+.2f} | {lowered} / {unchanged} / {raised} | "
            f"{mean(range_deltas):+.2f} |"
        )
    lines.extend(
        [
            "",
            "### 每篇文章的 5 次分数、标准差与极差",
            "",
            "| article_id | 类别 | 原分 | A 分数 / σ / R | B 分数 / σ / R | C 分数 / σ / R | D 分数 / σ / R |",
            "|---|---|---:|---|---|---|---|",
        ]
    )
    for row in rows:
        article_id = str(row.get("article_id") or "")
        cells: list[str] = []
        for config_name in CONFIG_ORDER:
            metric = by_metric.get((article_id, config_name))
            if metric is None:
                cells.append("NA")
            else:
                scores = "/".join(str(score) for score in metric.scores) or "NA"
                cells.append(
                    f"{scores}; σ={_fmt(metric.standard_deviation)}; R={metric.score_range if metric.score_range is not None else 'NA'}"
                )
        lines.append(
            f"| {article_id} | {row.get('category')} | {float(row.get('external_importance_score') or 0):.0f} | "
            + " | ".join(cells)
            + " |"
        )

    lines.extend(
        [
            "",
            "## Provider 路由解释了多少波动",
            "",
            f"A 组是 600 次总实验中的 {len(baseline)} 次基线调用；以下首先给出这 {len(baseline)} 次的 provider 分布。",
            "",
            "| A 组 provider | 次数 | 占 A 组 |",
            "|---|---:|---:|",
        ]
    )
    for provider, count in provider_counts.most_common():
        lines.append(f"| {provider} | {count} | {count / len(baseline) * 100:.1f}% |")
    lines.extend(
        [
            "",
            "### 600 次调用按配置与 provider",
            "",
            "| 配置 | provider | 次数 |",
            "|---|---|---:|",
        ]
    )
    for config_name in CONFIG_ORDER:
        for provider, count in all_provider_counts[config_name].most_common():
            lines.append(f"| {config_name} | {provider} | {count} |")
    lines.extend(
        [
            "",
            "### A 组文章内两两配对",
            "",
            "同一篇文章的 5 次调用形成 10 个配对；比较 provider 相同与切换时的绝对分差。",
            "",
            "| 配对类型 | 配对数 | 平均绝对分差 | 中位绝对分差 | 分数不同占比 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for pair in provider_pairs:
        lines.append(
            f"| {pair['pair_type']} | {pair['count']} | {_fmt(pair['mean_abs_difference'])} | "
            f"{_fmt(pair['median_abs_difference'])} | {_fmt(pair['different_score_rate'], 1)}% |"
        )
    lines.extend(
        [
            "",
            f"provider 是否切换（0/1）与绝对分差的点二列相关系数：`{_fmt(provider_correlation, 3)}`。这是描述性相关，不作因果检验。",
            "",
            "## H3：content 为空时的 reasoning 回退",
            "",
            f"- A 组：{len(baseline_fallbacks)}/{len(baseline)} 次；全部配置：{len(fallback_rows)}/{len(results)} 次",
            "- 统计定义：`message.content` 为空，且生产 `extract_message_text` 实际改读 `message.reasoning` 或 `choice.reasoning_content`。",
        ]
    )
    if fallback_rows:
        lines.extend(["", "### 回退样例（原始输出不截断）", ""])
        for index, row in enumerate(fallback_rows[:3], start=1):
            lines.extend(
                [
                    f"#### 样例 {index}: {row.article_id} / {row.config_name} / parsed={row.parsed_score}",
                    "",
                    "```text",
                    row.raw_output,
                    "```",
                    "",
                ]
            )
    else:
        lines.extend(
            [
                "- 本次没有触发样例，因此没有可展示的 reasoning 原文；这不改变代码中回退分支客观存在的事实。",
                "- 历史 `external_importance_raw` 只保存最终 `model_output`，未保存响应 envelope，无法从既有行反推过去是否走过回退；本报告频率来自生产参数的现场复刻调用。",
            ]
        )

    lines.extend(
        [
            "",
            "## 四个假设的判定",
            "",
            f"- **H1：{conclusions['H1']}**",
            f"- **H2：{conclusions['H2']}**",
            f"- **H3：{conclusions['H3']}**",
            f"- **H4：{conclusions['H4']}**",
            "",
            "H1 不是本轮主因：A 组 149/150 次本来就落在 Alibaba，同 provider 配对的平均绝对分差仍有 6.64 分；固定 provider 的 B 组反而比 A 组更抖。H2 有方向一致但有限的降噪作用，同时 C/D 的平均分约比 A/B 高 10 分，因此不能在不重做阈值校准的情况下直接关闭 reasoning。",
            "",
            "## 可落地的确定性方案（本任务未实施）",
            "",
            "### 1. Payload 硬化只能降噪，不能保证确定性",
            "",
            "候选 payload 如下；`only` 和 `allow_fallbacks=false` 缺一不可，否则首选 provider 不可用时仍可能切换。OpenRouter 官方文档说明了 [provider 固定语法](https://openrouter.ai/docs/guides/routing/provider-selection) 与 [reasoning 的关闭语义](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)。本实验表明该组合只把标准差 5.20→4.85，且改变分数分布，不能单独上线为“确定性修复”。",
            f"固定端点选 `{fixed_provider}`：实验前通过 [OpenRouter 模型端点清单](https://openrouter.ai/api/v1/models/deepseek/deepseek-v4-flash/endpoints) 确认其为 FP8 且支持 temperature、reasoning 与 seed；B/D 的 {sum(result.config_name in {'B', 'D'} for result in results)} 次响应均实际返回 Alibaba。",
            "",
            "```python",
            "payload.update({",
            f"    \"provider\": {{\"only\": [\"{fixed_provider}\"], \"allow_fallbacks\": False}},",
            "    \"reasoning\": {\"effort\": \"none\"},",
            "    \"temperature\": 0.0,",
            "})",
            "```",
            "",
            "环境变量方案（需要后续实现，当前代码尚无这两个 external-filter 专属配置项）：",
            "",
            f"- `LLM_EXTERNAL_FILTER_PROVIDER={fixed_provider}`",
            "- `LLM_EXTERNAL_FILTER_REASONING_ENABLED=false`",
            "- `LLM_EXTERNAL_FILTER_SEED=20260809`（仅在固定端点确认支持 seed 后启用，并先做 E 组复验）",
            "- 不建议直接把全局 `LLM_REASONING_ENABLED=false` 当作唯一方案，因为会同时改变相关性评分等其他调用。",
            "- 本次四组设计没有测试 seed，因此不把 seed 的收益写成已证实结论。",
            "",
            "### 2. 真正保证复现：同输入同配置只决策一次",
            "",
            "- 计算 `decision_fingerprint = SHA256(prompt全文 + prompt_version + model + provider端点 + reasoning配置 + temperature + seed)`。",
            "- 分数写入成功后把 fingerprint 与原始响应一并保存；同 fingerprint 再次出现时只读已有结果，不再调用 LLM。只有提示词版本、模型或输入发生变化时才生成新 fingerprint 并允许重打。",
            "- candidate pool 和阈值判断永远读取这份不可变的已决结果。这样服务端仍可有底噪，但同一文章不会因重跑随机进出候选池。",
            "- 对首次判定的阈值风险另设灰区：阈值 20 附近（建议先以 10-30 做观测范围）不要由单次随机分数自动做不可逆淘汰；可用固定 3/5 次中位数或人工复核。中位数方案需另做成本与残余噪声验证。",
            "",
            "## 样本、方法与唯一变量校验",
            "",
            f"- 生成时间：{generated_at}",
            f"- 样本随机种子：`{seed}`；按 internal/external × 0-19/20-39/40-59/60-79/80-100 分层，再用 `md5(article_id || seed)` 固定抽样顺序",
            "- 分数带样本数："
            + "、".join(
                f"{band * 20}-{99 if band == 4 else band * 20 + 19}={score_bands.get(band, 0)}"
                for band in range(5)
            ),
            f"- 每篇每配置 {repetitions} 次；共同并发={concurrency}，timeout={timeout}s，HTTP retries={retries}",
            f"- 提示词逐字一致哈希校验：{'通过' if prompt_hash_ok else '失败'}；剔除 provider/reasoning 后共同 payload 哈希校验：{'通过' if common_payload_hash_ok else '失败'}",
            "- A/B/C/D 共同调用生产 `build_prompt`，共同使用生产模型、temperature=0、timeout、重试状态码/退避与 `parse_external_filter_score`；所有调用计划确定性打乱并交错执行，减少时间顺序混杂。",
            f"- B/D 唯一增加 `provider.only=[{fixed_provider!r}]` 与 `allow_fallbacks=false`；C/D 唯一把生产 reasoning 对象替换为 `reasoning.effort=none`。",
            f"- CSV 明细：`{csv_path.as_posix()}`，含全部 {len(results)} 个计划槽位；失败项保留 status/error，不静默丢弃。",
            "",
            "## 限制与稳健性",
            "",
            "- 本实验量化的是当前模型 slug、当前 OpenRouter 路由池和一个固定 provider 在本次时间窗口内的重复性；供应商升级模型或推理栈后应重新运行。",
            "- provider 切换与分差的相关性来自文章内配对，控制了文章文本，但不是随机分配 provider 的因果实验。B/D 的严格固定组提供了更直接的敏感度证据。",
            "- 30 条样本用于噪声量级判断，不用于估计全库内容分布；分层抽样刻意让分数覆盖分散。",
            "",
            "## 后续问题",
            "",
            "- 若 D 仍有明显底噪，追加固定 provider + reasoning off + seed 的 E 组，并用同一批 30 条做配对复验。",
            "- 上线确定性配置后，建议记录 provider、response model、content-empty 标志与 system fingerprint，形成持续漂移监控。",
        ]
    )
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One-time read-only external-filter determinism experiment"
    )
    parser.add_argument("--sample-size", type=_positive_int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--repetitions", type=_positive_int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--fixed-provider", default=DEFAULT_FIXED_PROVIDER)
    parser.add_argument("--concurrency", type=_positive_int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--retries", type=_positive_int)
    parser.add_argument("--timeout", type=_positive_int)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Select and validate the sample/payloads without calling the LLM",
    )
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="Regenerate the report from the existing CSV without LLM calls",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    if "openrouter.ai" not in settings.llm_api_base_url.lower():
        raise RuntimeError(
            "This experiment requires OpenRouter provider routing metadata; "
            f"current base URL is {settings.llm_api_base_url!r}"
        )
    report_path = _artifact_path(args.report)
    csv_path = _artifact_path(args.csv)
    retries = args.retries or settings.external_filter_max_retries
    timeout = args.timeout or settings.llm_external_filter_timeout
    rows = _fetch_sample(args.sample_size, args.seed)
    plans = _build_plans(
        rows,
        repetitions=args.repetitions,
        fixed_provider=args.fixed_provider,
        seed=args.seed,
    )
    print(
        json.dumps(
            {
                "sample_size": len(rows),
                "planned_calls": len(plans),
                "seed": args.seed,
                "fixed_provider": args.fixed_provider,
                "model": settings.llm_external_filter_model,
                "reasoning_enabled": settings.llm_reasoning_enabled,
                "timeout": timeout,
                "retries": retries,
                "categories": Counter(str(row.get("base_category")) for row in rows),
                "score_bands": Counter(int(row.get("score_band") or 0) for row in rows),
            },
            ensure_ascii=False,
            default=dict,
            indent=2,
        ),
        file=sys.stderr,
    )
    if args.dry_run:
        return 0
    if args.render_only:
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV does not exist: {csv_path}")
        results = _read_csv(csv_path)
        expected_keys = {
            (plan.article_id, plan.config_name, plan.repetition) for plan in plans
        }
        actual_keys = {
            (result.article_id, result.config_name, result.repetition)
            for result in results
        }
        if actual_keys != expected_keys:
            raise RuntimeError(
                "Existing CSV does not match the selected sample/configuration: "
                f"expected={len(expected_keys)}, actual={len(actual_keys)}"
            )
    else:
        results = _run_experiment(
            plans,
            concurrency=args.concurrency,
            retries=retries,
            timeout=timeout,
            csv_path=csv_path,
        )
    report = _render_report(
        rows=rows,
        results=results,
        repetitions=args.repetitions,
        seed=args.seed,
        fixed_provider=args.fixed_provider,
        concurrency=args.concurrency,
        retries=retries,
        timeout=timeout,
        csv_path=csv_path,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
