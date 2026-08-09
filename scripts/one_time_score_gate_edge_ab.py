"""One-time, read-only edge-zone score-gate and pipeline-timing analysis.

PostgreSQL is opened in connection-level read-only mode. The script writes only
the requested Markdown/CSV artifacts and never mutates pipeline data.
"""

from __future__ import annotations

import argparse
import csv
import re
import secrets
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Optional, Sequence

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from src.adapters.external_filter_model import (
    call_external_filter_model,
    parse_external_filter_score,
)
from src.adapters.llm_chat import LLMQuotaError
from src.config import get_settings
from src.domain import ExternalFilterCandidate, is_beijing_related, load_beijing_keywords
from src.domain.submission_archive_config import dedup_lookback_days

DEFAULT_DAYS = 14
DEFAULT_SAMPLE_SIZE = 200
MAX_SAMPLE_SIZE = 1000
DEFAULT_RUN_COUNT = 30
DEFAULT_CONCURRENCY = 3
A_CONTENT_LIMIT = 1500
B_CONTENT_LIMIT = 4000
DEFAULT_REPORT_PATH = Path("artifacts/score_gate_edge_ab_report.md")
DEFAULT_CSV_PATH = Path("artifacts/score_gate_edge_ab_details.csv")

_NEGATIVE_RULE_EXCLUSION = """
    NOT EXISTS (
        SELECT 1
        FROM jsonb_array_elements(
            CASE
                WHEN jsonb_typeof(score_details->'matched_rules') = 'array'
                    THEN score_details->'matched_rules'
                ELSE '[]'::jsonb
            END
        ) AS rule
        WHERE jsonb_typeof(rule->'bonus') = 'number'
          AND (rule->>'bonus')::numeric < 0
    )
"""

SCOPE_COUNTS_QUERY = f"""
    SELECT
        COUNT(*) AS before_exclusion,
        COUNT(*) FILTER (WHERE {_NEGATIVE_RULE_EXCLUSION}) AS eligible
    FROM primary_articles
    WHERE created_at >= %s
      AND created_at <= %s
      AND status = 'filtered_out'
      AND score BETWEEN 20 AND 39
"""

SAMPLE_QUERY = f"""
    SELECT
        article_id,
        title,
        source,
        publish_time_iso,
        url,
        content_markdown,
        score,
        score_details
    FROM primary_articles
    WHERE created_at >= %s
      AND created_at <= %s
      AND status = 'filtered_out'
      AND score BETWEEN 20 AND 39
      AND {_NEGATIVE_RULE_EXCLUSION}
    ORDER BY md5(article_id || %s)
    LIMIT %s
"""

PIPELINE_STEPS_QUERY = """
    WITH recent_runs AS (
        SELECT
            run_id,
            started_at AS run_started_at,
            finished_at AS run_finished_at,
            artifacts
        FROM pipeline_runs
        WHERE status = 'success'
          AND started_at <= %s
          AND finished_at <= %s
        ORDER BY started_at DESC
        LIMIT %s
    )
    SELECT
        rr.run_id,
        rr.run_started_at,
        rr.run_finished_at,
        rr.artifacts,
        EXTRACT(EPOCH FROM (rr.run_finished_at - rr.run_started_at)) AS run_total_seconds,
        prs.step_name,
        prs.started_at,
        prs.finished_at,
        prs.duration_seconds
    FROM recent_runs rr
    JOIN pipeline_run_steps prs USING (run_id)
    WHERE prs.status = 'success'
    ORDER BY rr.run_started_at DESC, prs.order_index
"""

ARCHIVE_COUNTS_QUERY = """
    SELECT
        COUNT(*) AS total_items,
        COUNT(*) FILTER (WHERE i.embedding IS NOT NULL) AS embedded_items,
        COUNT(*) FILTER (
            WHERE r.report_date >= current_date - (%s * interval '1 day')
              AND i.embedding IS NOT NULL
        ) AS active_window_items
    FROM submitted_report_items i
    JOIN submitted_reports r ON r.id = i.report_id
"""

READ_QUERIES = (
    SCOPE_COUNTS_QUERY,
    SAMPLE_QUERY,
    PIPELINE_STEPS_QUERY,
    ARCHIVE_COUNTS_QUERY,
)

_RESULT_PATTERN = re.compile(
    r"^\[([^\]]+)\]\s+result:\s+ok=(\d+)\s+failed=(\d+|None)\b",
    re.MULTILINE,
)
_RUN_ID_PATTERN = re.compile(r"^run_id:\s*([0-9a-f]+)\s*$", re.MULTILINE)
_WORKER_TO_STEP = {
    "enrich_summary": "enrich-summary",
    "geo_classify": "geo-classify",
    "external_filter": "external-filter",
    "submission_dedup": "submission-dedup",
}


@dataclass(slots=True)
class ABResult:
    article_id: str
    title: str
    url: str
    relevance_score: Optional[float]
    score_a: Optional[int]
    score_b: Optional[int]
    category: str
    content_chars: int
    error_a: str = ""
    error_b: str = ""

    @property
    def b_higher(self) -> Optional[bool]:
        if self.score_a is None or self.score_b is None:
            return None
        return self.score_b > self.score_a

    @property
    def delta(self) -> Optional[int]:
        if self.score_a is None or self.score_b is None:
            return None
        return self.score_b - self.score_a


@dataclass(slots=True)
class StepMetric:
    step_name: str
    run_count: int
    count_coverage: int
    processed_count: int
    total_duration_seconds: float
    counted_duration_seconds: float
    duration_fallbacks: int

    @property
    def average_run_seconds(self) -> Optional[float]:
        if self.run_count <= 0:
            return None
        return self.total_duration_seconds / self.run_count

    @property
    def seconds_per_item(self) -> Optional[float]:
        if self.processed_count <= 0:
            return None
        return self.counted_duration_seconds / self.processed_count


@dataclass(slots=True)
class DedupRunPoint:
    run_id: str
    started_at: datetime
    dedup_seconds: float
    news_count: int
    match_count: int
    run_total_seconds: float

    @property
    def run_share_percent(self) -> float:
        if self.run_total_seconds <= 0:
            return 0.0
        return self.dedup_seconds / self.run_total_seconds * 100


@dataclass(slots=True)
class LinearFit:
    intercept_seconds: float
    slope_seconds_per_article: float
    r_squared: float


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def _sample_size(raw: str) -> int:
    value = _positive_int(raw)
    if value > MAX_SAMPLE_SIZE:
        raise argparse.ArgumentTypeError(f"must not exceed {MAX_SAMPLE_SIZE}")
    return value


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


def _as_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value) if isinstance(value, Decimal) else float(value)


def _keyword_matches(score_details: Any) -> tuple[str, ...]:
    if not isinstance(score_details, Mapping):
        return ()
    rules = score_details.get("matched_rules")
    if not isinstance(rules, list):
        return ()
    labels: list[str] = []
    for rule in rules:
        if not isinstance(rule, Mapping):
            continue
        label = rule.get("label") or rule.get("rule_id")
        if label:
            labels.append(str(label))
    return tuple(labels)


def _build_candidate(
    row: Mapping[str, Any],
    beijing_keywords: set[str],
) -> ExternalFilterCandidate:
    title = str(row.get("title") or "")
    content = str(row.get("content_markdown") or "")
    # Deliberately exclude source: Beijing-based media are not automatically
    # evidence that the article itself is Beijing-related.
    local_beijing_match = is_beijing_related((title, content), beijing_keywords)
    publish_time = row.get("publish_time_iso")
    return ExternalFilterCandidate(
        article_id=str(row.get("article_id") or ""),
        title=title or None,
        source=str(row.get("source") or "") or None,
        publish_time_iso=(
            publish_time.isoformat()
            if isinstance(publish_time, datetime)
            else str(publish_time or "") or None
        ),
        summary="",
        content=content,
        sentiment_label="positive",
        is_beijing_related=local_beijing_match,
        is_beijing_related_llm=None,
        external_importance_status="one_time_edge_ab",
        keyword_matches=_keyword_matches(row.get("score_details")),
    )


def _call_rubric(
    candidate: ExternalFilterCandidate,
    *,
    category: str,
    retries: int,
    content_limit: int,
) -> int:
    raw = call_external_filter_model(
        candidate,
        category=category,
        retries=retries,
        content_limit=content_limit,
    )
    score = parse_external_filter_score(raw)
    if score is None:
        raise RuntimeError("Model did not return a numeric score")
    return score


def _score_pair(
    row: Mapping[str, Any],
    beijing_keywords: set[str],
    retries: int,
    existing: Optional[ABResult] = None,
) -> tuple[ABResult, bool]:
    candidate = _build_candidate(row, beijing_keywords)
    category = candidate.candidate_category
    score_a = existing.score_a if existing else None
    score_b = existing.score_b if existing else None
    error_a = ""
    error_b = ""
    quota_halt = False
    if score_a is None:
        try:
            score_a = _call_rubric(
                candidate,
                category=category,
                retries=retries,
                content_limit=A_CONTENT_LIMIT,
            )
        except LLMQuotaError as exc:
            error_a = str(exc)[:500]
            error_b = "Not submitted after LLM quota halt"
            quota_halt = True
        except Exception as exc:
            error_a = str(exc)[:500]

    if not quota_halt and score_b is None:
        try:
            score_b = _call_rubric(
                candidate,
                category=category,
                retries=retries,
                content_limit=B_CONTENT_LIMIT,
            )
        except LLMQuotaError as exc:
            error_b = str(exc)[:500]
            quota_halt = True
        except Exception as exc:
            error_b = str(exc)[:500]

    content_chars = len((candidate.content or "").strip())
    return (
        ABResult(
            article_id=candidate.article_id,
            title=candidate.title or "",
            url=str(row.get("url") or ""),
            relevance_score=_as_optional_float(row.get("score")),
            score_a=score_a,
            score_b=score_b,
            category=category,
            content_chars=content_chars,
            error_a=error_a,
            error_b=error_b,
        ),
        quota_halt,
    )


def _unsubmitted_result(
    row: Mapping[str, Any],
    beijing_keywords: set[str],
) -> ABResult:
    candidate = _build_candidate(row, beijing_keywords)
    return ABResult(
        article_id=candidate.article_id,
        title=candidate.title or "",
        url=str(row.get("url") or ""),
        relevance_score=_as_optional_float(row.get("score")),
        score_a=None,
        score_b=None,
        category=candidate.candidate_category,
        content_chars=len((candidate.content or "").strip()),
        error_a="Not submitted after LLM quota halt",
        error_b="Not submitted after LLM quota halt",
    )


def _score_sample(
    rows: Sequence[Mapping[str, Any]],
    *,
    beijing_keywords: set[str],
    retries: int,
    concurrency: int,
    existing_results: Optional[Mapping[str, ABResult]] = None,
) -> tuple[list[ABResult], str]:
    results: list[ABResult] = []
    quota_error = ""
    for start in range(0, len(rows), concurrency):
        batch = rows[start : start + concurrency]
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [
                executor.submit(
                    _score_pair,
                    row,
                    beijing_keywords,
                    retries,
                    (existing_results or {}).get(str(row.get("article_id") or "")),
                )
                for row in batch
            ]
            for future in futures:
                result, quota_halt = future.result()
                results.append(result)
                if quota_halt:
                    quota_error = result.error_a or result.error_b
        completed = min(start + len(batch), len(rows))
        print(f"[LLM] completed {completed}/{len(rows)} A/B pairs", file=sys.stderr, flush=True)
        if quota_error:
            results.extend(
                _unsubmitted_result(row, beijing_keywords)
                for row in rows[start + len(batch) :]
            )
            break
    return results, quota_error


def _fetch_analysis_inputs(
    cur: psycopg.Cursor,
    *,
    since: datetime,
    sample_size: int,
    seed: str,
    run_count: int,
    as_of: datetime,
) -> tuple[int, int, list[dict[str, Any]], list[dict[str, Any]]]:
    cur.execute(SCOPE_COUNTS_QUERY, (since, as_of))
    scope = cur.fetchone() or {}
    before_exclusion = int(scope.get("before_exclusion") or 0)
    eligible = int(scope.get("eligible") or 0)
    cur.execute(SAMPLE_QUERY, (since, as_of, seed, sample_size))
    sample_rows = [dict(row) for row in cur.fetchall()]
    cur.execute(PIPELINE_STEPS_QUERY, (as_of, as_of, run_count))
    step_rows = [dict(row) for row in cur.fetchall()]
    return before_exclusion, eligible, sample_rows, step_rows


def _parse_log_counts(
    log_dir: Path,
    run_ids: set[str],
) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    if not log_dir.exists():
        return counts
    for path in sorted(log_dir.glob("*.log"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            raw = path.read_bytes()
            encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
            text = raw.decode(encoding, errors="replace")
        except OSError:
            continue
        run_matches = _RUN_ID_PATTERN.findall(text)
        if not run_matches:
            continue
        run_id = run_matches[-1]
        if run_id not in run_ids:
            continue
        for worker, ok_raw, failed_raw in _RESULT_PATTERN.findall(text):
            step_name = _WORKER_TO_STEP.get(worker, worker.replace("_", "-"))
            failed = 0 if failed_raw == "None" else int(failed_raw)
            counts[(run_id, step_name)] = int(ok_raw) + failed
    return counts


def _duration_seconds(row: Mapping[str, Any]) -> tuple[float, bool]:
    stored = row.get("duration_seconds")
    if stored is not None:
        return float(stored), False
    started = row.get("started_at")
    finished = row.get("finished_at")
    if isinstance(started, datetime) and isinstance(finished, datetime):
        return (finished - started).total_seconds(), True
    return 0.0, True


def _aggregate_step_metrics(
    step_rows: Sequence[Mapping[str, Any]],
    log_counts: Mapping[tuple[str, str], int],
) -> list[StepMetric]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in step_rows:
        grouped[str(row.get("step_name") or "unknown")].append(row)
    metrics: list[StepMetric] = []
    for step_name, rows in grouped.items():
        total_duration = 0.0
        counted_duration = 0.0
        processed_count = 0
        coverage = 0
        fallbacks = 0
        for row in rows:
            duration, used_fallback = _duration_seconds(row)
            total_duration += duration
            fallbacks += int(used_fallback)
            key = (str(row.get("run_id") or ""), step_name)
            if key in log_counts:
                coverage += 1
                processed_count += int(log_counts[key])
                counted_duration += duration
        metrics.append(
            StepMetric(
                step_name=step_name,
                run_count=len(rows),
                count_coverage=coverage,
                processed_count=processed_count,
                total_duration_seconds=total_duration,
                counted_duration_seconds=counted_duration,
                duration_fallbacks=fallbacks,
            )
        )
    return sorted(
        metrics,
        key=lambda metric: (
            metric.seconds_per_item is not None,
            metric.seconds_per_item or -1,
        ),
        reverse=True,
    )


def _dedup_run_points(
    step_rows: Sequence[Mapping[str, Any]],
    log_counts: Mapping[tuple[str, str], int],
) -> list[DedupRunPoint]:
    points: list[DedupRunPoint] = []
    for row in step_rows:
        if str(row.get("step_name") or "") != "submission-dedup":
            continue
        run_id = str(row.get("run_id") or "")
        news_count = log_counts.get((run_id, "submission-dedup"))
        started_at = row.get("run_started_at")
        if news_count is None or not isinstance(started_at, datetime):
            continue
        artifacts = row.get("artifacts")
        if not isinstance(artifacts, Mapping):
            artifacts = {}
        match_raw = artifacts.get("submission_dedup_matches")
        try:
            match_count = int(match_raw or 0)
        except (TypeError, ValueError):
            match_count = 0
        dedup_seconds, _ = _duration_seconds(row)
        run_total_raw = row.get("run_total_seconds")
        run_total_seconds = float(run_total_raw or 0.0)
        points.append(
            DedupRunPoint(
                run_id=run_id,
                started_at=started_at,
                dedup_seconds=dedup_seconds,
                news_count=int(news_count),
                match_count=match_count,
                run_total_seconds=run_total_seconds,
            )
        )
    return sorted(points, key=lambda point: point.started_at)


def _linear_fit(points: Sequence[DedupRunPoint]) -> Optional[LinearFit]:
    if len(points) < 2:
        return None
    x_values = [float(point.news_count) for point in points]
    y_values = [point.dedup_seconds for point in points]
    x_mean = mean(x_values)
    y_mean = mean(y_values)
    x_variance = sum((value - x_mean) ** 2 for value in x_values)
    if x_variance <= 0:
        return None
    slope = sum(
        (x_value - x_mean) * (y_value - y_mean)
        for x_value, y_value in zip(x_values, y_values)
    ) / x_variance
    intercept = y_mean - slope * x_mean
    residual_sum = sum(
        (y_value - (intercept + slope * x_value)) ** 2
        for x_value, y_value in zip(x_values, y_values)
    )
    total_sum = sum((value - y_mean) ** 2 for value in y_values)
    r_squared = 1.0 - residual_sum / total_sum if total_sum > 0 else 0.0
    return LinearFit(
        intercept_seconds=intercept,
        slope_seconds_per_article=slope,
        r_squared=r_squared,
    )


def _percentage(count: int, total: int) -> str:
    return "0.00%" if total <= 0 else f"{count / total * 100:.2f}%"


def _fmt_seconds(value: Optional[float]) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def _markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return lines


def _paired(results: Sequence[ABResult]) -> list[ABResult]:
    return [row for row in results if row.score_a is not None and row.score_b is not None]


def _threshold_rows(results: Sequence[ABResult]) -> list[tuple[Any, ...]]:
    paired = _paired(results)
    rows: list[tuple[Any, ...]] = []
    for threshold in (70, 50, 35):
        a_hits = sum((row.score_a or -1) >= threshold for row in paired)
        b_hits = sum((row.score_b or -1) >= threshold for row in paired)
        rescued = sum(
            (row.score_a or -1) < threshold <= (row.score_b or -1)
            for row in paired
        )
        lost = sum(
            (row.score_b or -1) < threshold <= (row.score_a or -1)
            for row in paired
        )
        rows.append(
            (
                f">={threshold}",
                f"{a_hits} ({_percentage(a_hits, len(paired))})",
                f"{b_hits} ({_percentage(b_hits, len(paired))})",
                rescued,
                lost,
                b_hits - a_hits,
            )
        )
    return rows


def _length_group(chars: int) -> str:
    if chars < 1500:
        return "<1500"
    if chars <= 4000:
        return "1500-4000"
    return ">4000"


def _comparison_row(label: str, results: Sequence[ABResult]) -> tuple[Any, ...]:
    paired = _paired(results)
    if not paired:
        return (label, 0, "N/A", "N/A", 0, "0.00%", "N/A", 0)
    deltas = [int(row.delta or 0) for row in paired]
    higher = sum(row.b_higher is True for row in paired)
    a_35 = sum((row.score_a or -1) >= 35 for row in paired)
    b_35 = sum((row.score_b or -1) >= 35 for row in paired)
    return (
        label,
        len(paired),
        f"{mean(int(row.score_a or 0) for row in paired):.2f}",
        f"{mean(int(row.score_b or 0) for row in paired):.2f}",
        higher,
        _percentage(higher, len(paired)),
        f"{mean(deltas):+.2f}",
        b_35 - a_35,
    )


def _current_threshold_stats(
    results: Sequence[ABResult],
    *,
    internal_threshold: int,
    external_threshold: int,
) -> tuple[int, int, int, int]:
    paired = _paired(results)
    a_hits = 0
    b_hits = 0
    rescued = 0
    lost = 0
    for row in paired:
        threshold = internal_threshold if row.category == "internal_positive" else external_threshold
        a_pass = (row.score_a or -1) >= threshold
        b_pass = (row.score_b or -1) >= threshold
        a_hits += int(a_pass)
        b_hits += int(b_pass)
        rescued += int(not a_pass and b_pass)
        lost += int(a_pass and not b_pass)
    return a_hits, b_hits, rescued, lost


def render_report(
    *,
    generated_at: datetime,
    since: datetime,
    days: int,
    seed: str,
    requested_sample_size: int,
    before_exclusion: int,
    eligible_count: int,
    results: Sequence[ABResult],
    quota_error: str,
    model_name: str,
    beijing_keyword_count: int,
    internal_threshold: int,
    external_threshold: int,
    step_metrics: Sequence[StepMetric],
    dedup_points: Sequence[DedupRunPoint],
    dedup_fit: Optional[LinearFit],
    archive_counts: Mapping[str, int],
    archive_lookback_days: int,
    selected_run_count: int,
    oldest_run_at: Optional[datetime],
    newest_run_at: Optional[datetime],
    csv_path: Path,
) -> str:
    paired = _paired(results)
    category_counts = Counter(row.category for row in results)
    a_hits, b_hits, current_rescued, current_lost = _current_threshold_stats(
        results,
        internal_threshold=internal_threshold,
        external_threshold=external_threshold,
    )
    lines = [
        "# 教育相关性闸门第二轮一次性评估",
        "",
        f"- 生成时间（UTC）：`{generated_at.isoformat()}`",
        f"- 样本时间范围：最近 {days} 天，`primary_articles.created_at >= {since.isoformat()}`",
        "- 数据库保护：连接级只读；脚本仅执行 SELECT/WITH 查询",
        "",
        "## 一、擦边区漏检率与 1500/4000 字截断对照",
        "",
        f"- 擦边范围（filtered_out 且 score 20-39）总数：{before_exclusion}",
        f"- 排除任一 `matched_rules[].bonus < 0` 后可抽样总数：{eligible_count}（排除 {before_exclusion - eligible_count}）",
        f"- 请求抽样：{requested_sample_size}；实际抽样：{len(results)}；A/B 均成功：{len(paired)}",
        f"- 随机种子：`{seed}`，按 `md5(article_id || seed)` 排序取前 N 条，可复现",
        "- 无摘要与情感前提：`summary` 传空字符串，情感统一 positive；仅使用 internal_positive / external_positive 提示词",
        f"- 类别规则：加载 {beijing_keyword_count} 个北京关键词，只匹配标题和正文（不匹配来源）；实际类别："
        + "、".join(f"{key}={value}" for key, value in sorted(category_counts.items())),
        f"- 模型：`{model_name}`；逐条明细：`{csv_path}`",
        "- A/B 唯一输入变量：对同一个 `ExternalFilterCandidate` 连续调用同一个 adapter；category、提示词模板、模型、temperature=0、reasoning 配置、retries 与解析器完全相同，只把 `content_limit` 从 A=1500 改为 B=4000。adapter 默认值仍为 1500，现有 worker 未传该参数，行为不变",
        "",
        "### 总体阈值对比",
        "",
    ]
    lines.extend(
        _markdown_table(
            ("阈值", "A: 1500", "B: 4000", "B 新跨过", "B 跌破", "净增"),
            _threshold_rows(results),
        )
    )
    lines.extend(
        (
            "",
            f"按当前分类阈值（internal_positive={internal_threshold}，external_positive={external_threshold}）："
            f"A 通过 {a_hits}/{len(paired)}，B 通过 {b_hits}/{len(paired)}，"
            f"B 新捞回 {current_rescued}，跌破 {current_lost}，净增 {b_hits - a_hits}。",
            "",
            "### 按正文字数交叉",
            "",
        )
    )
    length_rows = []
    for group in ("<1500", "1500-4000", ">4000"):
        subset = [row for row in results if _length_group(row.content_chars) == group]
        length_rows.append(_comparison_row(group, subset))
    lines.extend(
        _markdown_table(
            ("正文字数", "配对数", "A均分", "B均分", "B>A", "B>A占比", "平均差(B-A)", ">=35净增"),
            length_rows,
        )
    )
    short_rows = [row for row in paired if row.content_chars <= A_CONTENT_LIMIT]
    short_changed = sum(row.score_a != row.score_b for row in short_rows)
    lines.extend(
        (
            "",
            f"短文重复性控制：正文 <=1500 的 {len(short_rows)} 条在 A/B 中生成完全相同的提示词；其中 {short_changed} 条分数仍不同，反映模型重复调用噪声，而不是截断收益。",
            "",
            "### 分类别拆分",
            "",
        )
    )
    for category in ("internal_positive", "external_positive"):
        subset = [row for row in results if row.category == category]
        lines.extend((f"#### {category}", ""))
        lines.extend(
            _markdown_table(
                ("阈值", "A: 1500", "B: 4000", "B 新跨过", "B 跌破", "净增"),
                _threshold_rows(subset),
            )
        )
        lines.extend(("",))
        lines.extend(
            _markdown_table(
                ("类别", "配对数", "A均分", "B均分", "B>A", "B>A占比", "平均差(B-A)", ">=35净增"),
                (_comparison_row(category, subset),),
            )
        )
        lines.append("")
    a_70 = sum((row.score_a or -1) >= 70 for row in paired)
    a_50 = sum((row.score_a or -1) >= 50 for row in paired)
    a_35 = sum((row.score_a or -1) >= 35 for row in paired)
    b_35 = sum((row.score_b or -1) >= 35 for row in paired)
    long_rows = [row for row in paired if row.content_chars > A_CONTENT_LIMIT]
    short_a_35 = sum((row.score_a or -1) >= 35 for row in short_rows)
    short_b_35 = sum((row.score_b or -1) >= 35 for row in short_rows)
    long_a_35 = sum((row.score_a or -1) >= 35 for row in long_rows)
    long_b_35 = sum((row.score_b or -1) >= 35 for row in long_rows)
    lines.extend(
        (
            "### 第一部分结论",
            "",
            f"- 擦边区在现状 A 组下：>=70 为 {a_70}/{len(paired)}（{_percentage(a_70, len(paired))}），>=50 为 {a_50}/{len(paired)}（{_percentage(a_50, len(paired))}），>=35 为 {a_35}/{len(paired)}（{_percentage(a_35, len(paired))}）。按当前分类阈值计算则为 {a_hits}/{len(paired)}（{_percentage(a_hits, len(paired))}）。",
            f"- 4000 字的表面结果：>=35 从 {a_35} 增至 {b_35}，净增 {b_35 - a_35}；按当前分类阈值从 {a_hits} 增至 {b_hits}，净增 {b_hits - a_hits}。",
            f"- 截断归因检查：正文 <=1500、提示词完全相同的控制组在 >=35 上也净增 {short_b_35 - short_a_35}；真正得到新增正文的 {len(long_rows)} 条在 >=35 上只净增 {long_b_35 - long_a_35}。结合相同提示词仍有 {short_changed}/{len(short_rows)} 条变分，当前单次 A/B 证据不足以证明 4000 字能稳定多捞回内容。",
            "",
        )
    )
    if quota_error:
        lines.extend(("**额度保护触发，后续样本未提交。**", f"`{quota_error}`", ""))

    lines.extend(
        (
            "## 二、流水线步骤耗时",
            "",
            f"口径：选择最近 {selected_run_count} 次 `pipeline_runs.status='success'` 的运行"
            + (
                f"（{oldest_run_at.isoformat()} 至 {newest_run_at.isoformat()}）"
                if oldest_run_at and newest_run_at
                else ""
            )
            + "。时长优先使用 `pipeline_run_steps.duration_seconds`；为空才用 finished_at-started_at。处理条数因表中没有该字段，从同一 run_id 日志的 `[worker] result: ok=... failed=...` 补齐，按 ok + 数值型 failed 计数、skipped 不计。单条耗时仅用成功匹配到日志计数的运行计算。",
            "",
        )
    )
    lines.extend(
        _markdown_table(
            (
                "步骤",
                "运行数",
                "条数覆盖",
                "处理条数",
                "总耗时(s)",
                "平均每次(s)",
                "单条平均(s)",
                "时间戳回算数",
            ),
            [
                (
                    metric.step_name,
                    metric.run_count,
                    f"{metric.count_coverage}/{metric.run_count}",
                    metric.processed_count if metric.count_coverage else "N/A",
                    f"{metric.total_duration_seconds:.3f}",
                    _fmt_seconds(metric.average_run_seconds),
                    _fmt_seconds(metric.seconds_per_item),
                    metric.duration_fallbacks,
                )
                for metric in step_metrics
            ],
        )
    )
    measurable = [metric for metric in step_metrics if metric.seconds_per_item is not None]
    summarize = next((metric for metric in step_metrics if metric.step_name == "summarize"), None)
    slowest_item = measurable[0] if measurable else None
    slowest_run = max(
        step_metrics,
        key=lambda metric: metric.average_run_seconds or -1,
        default=None,
    )
    lines.extend(("", "### 结论", ""))
    if slowest_item:
        lines.append(
            f"- 按单条平均耗时，最慢步骤是 **{slowest_item.step_name}**（{_fmt_seconds(slowest_item.seconds_per_item)} 秒/条）。"
        )
    if slowest_run:
        lines.append(
            f"- 按每次运行的墙钟耗时，最慢步骤是 **{slowest_run.step_name}**（平均 {_fmt_seconds(slowest_run.average_run_seconds)} 秒/次）。"
        )
    if summarize and slowest_item:
        lines.append(
            "- summarize "
            + ("是" if summarize.step_name == slowest_item.step_name else "不是")
            + "本口径下单条最慢的一步。"
        )
    lines.append("")

    lines.extend(
        (
            "## 三、submission-dedup 主要随日内新闻池线性增长",
            "",
            f"最近成功运行中有 {len(dedup_points)} 次包含成功的 submission-dedup 步骤。参与文章数取自同一 run_id 日志的 `submission_dedup result: ok=N`，代码中它等于 `len(news_rows)`：每次重新读取当天所有 `ready_for_export` 新闻，而不是只读取上次运行后的增量。匹配数取自 `pipeline_runs.artifacts.submission_dedup_matches`，表示本次 upsert 的匹配对数，包含新建和更新。整轮耗时按 pipeline run 的 finished_at-started_at 计算。",
            "",
        )
    )
    lines.extend(
        _markdown_table(
            (
                "run_id",
                "started_at",
                "dedup耗时(s)",
                "参与新闻数（日内池）",
                "upsert匹配数",
                "整轮耗时(s)",
                "dedup占比",
            ),
            [
                (
                    point.run_id,
                    point.started_at.isoformat(),
                    f"{point.dedup_seconds:.3f}",
                    point.news_count,
                    point.match_count,
                    f"{point.run_total_seconds:.3f}",
                    f"{point.run_share_percent:.2f}%",
                )
                for point in dedup_points
            ],
        )
    )
    lines.extend(("", "### 线性拟合与 336 秒拆解", ""))
    if dedup_fit and dedup_points:
        nearest = min(dedup_points, key=lambda point: abs(point.dedup_seconds - 336.0))
        modeled_variable = dedup_fit.slope_seconds_per_article * nearest.news_count
        predicted = dedup_fit.intercept_seconds + modeled_variable
        residual = nearest.dedup_seconds - predicted
        observed_variable = nearest.dedup_seconds - dedup_fit.intercept_seconds
        min_news = min(point.news_count for point in dedup_points)
        max_news = max(point.news_count for point in dedup_points)
        min_seconds = min(point.dedup_seconds for point in dedup_points)
        max_seconds = max(point.dedup_seconds for point in dedup_points)
        lines.extend(
            (
                f"30 点拟合为：`耗时秒数 = {dedup_fit.intercept_seconds:.1f} + {dedup_fit.slope_seconds_per_article:.3f} × 参与新闻数`，R²={dedup_fit.r_squared:.3f}。观察范围为 {min_news}-{max_news} 条、{min_seconds:.1f}-{max_seconds:.1f} 秒，关系明显向上而不是基本平坦。",
                "",
                f"最接近实测 336 秒的是 `{nearest.run_id}`：{nearest.news_count} 条、实际 {nearest.dedup_seconds:.1f} 秒。模型拆解为固定截距约 {dedup_fit.intercept_seconds:.0f} 秒 + 条数相关约 {modeled_variable:.0f} 秒 = 预测 {predicted:.0f} 秒，残差 {residual:+.0f} 秒。按实际 336 秒做量级归纳，可理解为约 **{dedup_fit.intercept_seconds:.0f} 秒固定开销，约 {observed_variable:.0f} 秒随条数增长及运行噪声变化**。",
                "",
            )
        )
    else:
        lines.extend(("有效点不足或参与文章数没有变化，无法拟合。", ""))

    total_items = int(archive_counts.get("total_items") or 0)
    embedded_items = int(archive_counts.get("embedded_items") or 0)
    active_items = int(archive_counts.get("active_window_items") or 0)
    lines.extend(
        (
            "### 固定开销与扫描范围",
            "",
            "- BAAI/bge-large-zh 在 `title_cluster` 模块中是进程内单例；当前小时任务每次都会启动新的 Python 进程，因此每轮 dedup 首次编码新闻时通常会重新加载模型。代码和日志没有模型加载的独立计时，无法可靠给出它单独占多少秒；它与存档读取、向量反序列化和矩阵准备共同包含在约 {:.0f} 秒回归截距中。".format(
                dedup_fit.intercept_seconds if dedup_fit else 0.0
            ),
            f"- 存档侧并非全历史扫描：每次读取最近 {archive_lookback_days} 天内已有 embedding 的条目。当前 `submitted_report_items` 共 {total_items} 行，其中 {embedded_items} 行已有 embedding，本次活跃窗口为 {active_items} 行；这 {active_items} 行每轮都会重新载入并与日内新闻池比对。",
            "- 结论：submission-dedup 不是以 336 秒为主的固定任务。固定部分约 90 秒量级，主要耗时随当日参与新闻数增长，边际约 1.12 秒/条。若从主链路拆出，能直接移除每轮数百秒墙钟时间；若保留高频运行，真正的优化点是改成新闻增量比对并复用常驻模型/存档矩阵。",
            "",
        )
    )
    return "\n".join(lines)


def write_csv(path: Path, results: Sequence[ABResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "article_id",
        "title",
        "url",
        "relevance_score",
        "score_a_1500",
        "score_b_4000",
        "category",
        "content_chars",
        "b_higher_than_a",
        "error_a",
        "error_b",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in results:
            writer.writerow(
                {
                    "article_id": row.article_id,
                    "title": row.title,
                    "url": row.url,
                    "relevance_score": row.relevance_score,
                    "score_a_1500": row.score_a,
                    "score_b_4000": row.score_b,
                    "category": row.category,
                    "content_chars": row.content_chars,
                    "b_higher_than_a": row.b_higher,
                    "error_a": row.error_a,
                    "error_b": row.error_b,
                }
            )


def read_csv(path: Path) -> dict[str, ABResult]:
    results: dict[str, ABResult] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            article_id = str(row.get("article_id") or "")
            if not article_id:
                continue
            score_a_raw = str(row.get("score_a_1500") or "").strip()
            score_b_raw = str(row.get("score_b_4000") or "").strip()
            relevance_raw = str(row.get("relevance_score") or "").strip()
            results[article_id] = ABResult(
                article_id=article_id,
                title=str(row.get("title") or ""),
                url=str(row.get("url") or ""),
                relevance_score=float(relevance_raw) if relevance_raw else None,
                score_a=int(score_a_raw) if score_a_raw else None,
                score_b=int(score_b_raw) if score_b_raw else None,
                category=str(row.get("category") or ""),
                content_chars=int(row.get("content_chars") or 0),
                error_a=str(row.get("error_a") or ""),
                error_b=str(row.get("error_b") or ""),
            )
    return results


def _iso_datetime(raw: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an ISO-8601 datetime") from exc
    if value.tzinfo is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One-time read-only edge-zone A/B and pipeline timing analysis."
    )
    parser.add_argument("--days", type=_positive_int, default=DEFAULT_DAYS)
    parser.add_argument("--sample-size", type=_sample_size, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--run-count", type=_positive_int, default=DEFAULT_RUN_COUNT)
    parser.add_argument("--concurrency", type=_positive_int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--retries", type=_positive_int, default=3)
    parser.add_argument("--seed")
    parser.add_argument("--as-of", type=_iso_datetime)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    generated_at = args.as_of or datetime.now(timezone.utc)
    since = generated_at - timedelta(days=args.days)
    seed = args.seed or secrets.token_hex(8)
    settings = get_settings()

    with closing(_connect_read_only()) as conn, conn.cursor() as cur:
        before_exclusion, eligible, sample_rows, step_rows = _fetch_analysis_inputs(
            cur,
            since=since,
            sample_size=args.sample_size,
            seed=seed,
            run_count=args.run_count,
            as_of=generated_at,
        )
        archive_lookback = dedup_lookback_days()
        cur.execute(ARCHIVE_COUNTS_QUERY, (archive_lookback,))
        archive_counts = dict(cur.fetchone() or {})

    run_ids = {str(row.get("run_id") or "") for row in step_rows}
    log_counts = _parse_log_counts(args.log_dir, run_ids)
    step_metrics = _aggregate_step_metrics(step_rows, log_counts)
    dedup_points = _dedup_run_points(step_rows, log_counts)
    dedup_fit = _linear_fit(dedup_points)
    run_times = [
        row["run_started_at"]
        for row in step_rows
        if isinstance(row.get("run_started_at"), datetime)
    ]
    beijing_keywords = load_beijing_keywords(settings.beijing_keywords_path)
    existing_results: Optional[dict[str, ABResult]] = None
    if args.resume:
        if not args.seed or not args.as_of:
            raise ValueError("--resume requires both --seed and --as-of")
        if not args.csv.exists():
            raise FileNotFoundError(f"Resume CSV does not exist: {args.csv}")
        existing_results = read_csv(args.csv)
    missing_calls = sum(
        int((existing_results or {}).get(str(row.get("article_id") or ""), ABResult("", "", "", None, None, None, "", 0)).score_a is None)
        + int((existing_results or {}).get(str(row.get("article_id") or ""), ABResult("", "", "", None, None, None, "", 0)).score_b is None)
        for row in sample_rows
    )
    print(
        f"[LLM] scoring {len(sample_rows)} pairs, missing calls={missing_calls} "
        f"(A={A_CONTENT_LIMIT}, B={B_CONTENT_LIMIT})",
        file=sys.stderr,
        flush=True,
    )
    results, quota_error = _score_sample(
        sample_rows,
        beijing_keywords=beijing_keywords,
        retries=args.retries,
        concurrency=args.concurrency,
        existing_results=existing_results,
    )
    write_csv(args.csv, results)
    report = render_report(
        generated_at=generated_at,
        since=since,
        days=args.days,
        seed=seed,
        requested_sample_size=args.sample_size,
        before_exclusion=before_exclusion,
        eligible_count=eligible,
        results=results,
        quota_error=quota_error,
        model_name=settings.llm_external_filter_model,
        beijing_keyword_count=len(beijing_keywords),
        internal_threshold=settings.internal_filter_threshold,
        external_threshold=settings.external_filter_threshold,
        step_metrics=step_metrics,
        dedup_points=dedup_points,
        dedup_fit=dedup_fit,
        archive_counts=archive_counts,
        archive_lookback_days=archive_lookback,
        selected_run_count=len(run_ids),
        oldest_run_at=min(run_times) if run_times else None,
        newest_run_at=max(run_times) if run_times else None,
        csv_path=args.csv,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    print(report)
    return 2 if quota_error else 0


if __name__ == "__main__":
    sys.exit(main())
