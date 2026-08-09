"""One-time, read-only analysis of the relevance-score gate.

This script never writes to PostgreSQL.  It enables PostgreSQL's connection-level
read-only mode before issuing a small, explicit set of SELECT queries.  The only
files it writes are the requested Markdown report (when ``--report`` is given)
and the sampled-article CSV.
"""

from __future__ import annotations

import argparse
import csv
import secrets
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
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

DEFAULT_DAYS = 14
DEFAULT_SAMPLE_SIZE = 200
MAX_SAMPLE_SIZE = 1000
DEFAULT_CONCURRENCY = 3
DEFAULT_CSV_PATH = Path("artifacts/one_time_score_gate_leak_sample.csv")

STATUS_COUNTS_QUERY = """
    SELECT status, COUNT(*) AS count
    FROM primary_articles
    WHERE created_at >= %s
    GROUP BY status
    ORDER BY status
"""

FILTERED_SCORE_QUERY = """
    SELECT score, COUNT(*) AS count
    FROM primary_articles
    WHERE created_at >= %s
      AND status = 'filtered_out'
    GROUP BY score
    ORDER BY score NULLS FIRST
"""

EXTERNAL_DISTRIBUTION_QUERY = """
    WITH scored AS (
        SELECT
            external_importance_score AS score,
            status,
            CASE lower(COALESCE(external_importance_raw->>'category', ''))
                WHEN 'internal_positive' THEN 'internal_positive'
                WHEN 'internal_negative' THEN 'internal_negative'
                WHEN 'external_positive' THEN 'external_positive'
                WHEN 'external_negative' THEN 'external_negative'
                WHEN 'internal' THEN 'internal_positive'
                WHEN 'external' THEN 'external_positive'
                ELSE CASE
                    WHEN is_beijing_related IS TRUE
                        THEN 'internal_'
                    ELSE 'external_'
                END || CASE
                    WHEN lower(COALESCE(sentiment_label, '')) = 'negative'
                        THEN 'negative'
                    ELSE 'positive'
                END
            END AS category
        FROM news_summaries
        WHERE created_at >= %s
          AND external_importance_score IS NOT NULL
    )
    SELECT category, status, score, COUNT(*) AS count
    FROM scored
    GROUP BY category, status, score
    ORDER BY category, score, status
"""

LEAK_SAMPLE_QUERY = """
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
      AND status = 'filtered_out'
    ORDER BY md5(article_id || %s)
    LIMIT %s
"""

READ_QUERIES = (
    STATUS_COUNTS_QUERY,
    FILTERED_SCORE_QUERY,
    EXTERNAL_DISTRIBUTION_QUERY,
    LEAK_SAMPLE_QUERY,
)

CATEGORIES = (
    "internal_positive",
    "internal_negative",
    "external_positive",
    "external_negative",
)
STANDARD_SCORE_BANDS = tuple(
    f"{lower}-{100 if lower == 90 else lower + 9}"
    for lower in range(0, 100, 10)
)


@dataclass(slots=True)
class LeakSampleResult:
    article_id: str
    title: str
    url: str
    relevance_score: Optional[float]
    rubric_score: Optional[int]
    category: str
    error: str = ""


@dataclass(slots=True)
class AssessmentData:
    primary_statuses: Counter[str]
    other_statuses: Counter[str]
    filtered_histogram: Counter[str]
    external_distribution: dict[str, dict[str, Counter[str]]]
    sampled_count: int
    sample_results: list[LeakSampleResult]
    quota_halt_error: str = ""


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def _sample_size(raw: str) -> int:
    value = _positive_int(raw)
    if value > MAX_SAMPLE_SIZE:
        raise argparse.ArgumentTypeError(
            f"must not exceed the safety cap of {MAX_SAMPLE_SIZE}"
        )
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


def _score_band(value: Any) -> str:
    if value is None:
        return "NULL"
    score = float(value)
    if score < 0:
        return "<0"
    if score > 100:
        return ">100"
    lower = min(int(score // 10) * 10, 90)
    upper = 100 if lower == 90 else lower + 9
    return f"{lower}-{upper}"


def _band_sort_key(label: str) -> tuple[int, int]:
    if label == "NULL":
        return (0, 0)
    if label == "<0":
        return (1, 0)
    if label == ">100":
        return (3, 0)
    return (2, int(label.split("-", 1)[0]))


def _display_bands(counts: Mapping[str, Any]) -> list[str]:
    leading = [label for label in ("NULL", "<0") if label in counts]
    trailing = sorted(
        (
            label
            for label in counts
            if label not in STANDARD_SCORE_BANDS and label not in {"NULL", "<0"}
        ),
        key=_band_sort_key,
    )
    return leading + list(STANDARD_SCORE_BANDS) + trailing


def _fetch_status_counts(cur: psycopg.Cursor, since: datetime) -> tuple[Counter[str], Counter[str]]:
    cur.execute(STATUS_COUNTS_QUERY, (since,))
    grouped: Counter[str] = Counter(
        {"scored": 0, "filtered_out": 0, "failed": 0, "other": 0}
    )
    other: Counter[str] = Counter()
    for row in cur.fetchall():
        status = str(row.get("status") or "(NULL)")
        count = int(row.get("count") or 0)
        if status in {"scored", "filtered_out", "failed"}:
            grouped[status] += count
        else:
            grouped["other"] += count
            other[status] += count
    return grouped, other


def _fetch_filtered_histogram(cur: psycopg.Cursor, since: datetime) -> Counter[str]:
    cur.execute(FILTERED_SCORE_QUERY, (since,))
    histogram: Counter[str] = Counter()
    for row in cur.fetchall():
        histogram[_score_band(row.get("score"))] += int(row.get("count") or 0)
    return histogram


def _fetch_external_distribution(
    cur: psycopg.Cursor,
    since: datetime,
) -> dict[str, dict[str, Counter[str]]]:
    cur.execute(EXTERNAL_DISTRIBUTION_QUERY, (since,))
    distribution: dict[str, dict[str, Counter[str]]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    for row in cur.fetchall():
        category = str(row.get("category") or "unknown")
        status = str(row.get("status") or "(NULL)")
        band = _score_band(row.get("score"))
        distribution[category][band][status] += int(row.get("count") or 0)
    return {
        category: {band: Counter(counts) for band, counts in bands.items()}
        for category, bands in distribution.items()
    }


def _fetch_leak_sample(
    cur: psycopg.Cursor,
    since: datetime,
    sample_size: int,
    seed: str,
) -> list[dict[str, Any]]:
    cur.execute(LEAK_SAMPLE_QUERY, (since, seed, sample_size))
    return [dict(row) for row in cur.fetchall()]


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


def _build_sample_candidate(
    row: Mapping[str, Any],
    beijing_keywords: set[str],
) -> ExternalFilterCandidate:
    title = str(row.get("title") or "")
    source = str(row.get("source") or "")
    content = str(row.get("content_markdown") or "")
    local_beijing_match = is_beijing_related(
        (title, source, content),
        beijing_keywords,
    )
    return ExternalFilterCandidate(
        article_id=str(row.get("article_id") or ""),
        title=title or None,
        source=source or None,
        publish_time_iso=(
            row["publish_time_iso"].isoformat()
            if isinstance(row.get("publish_time_iso"), datetime)
            else str(row.get("publish_time_iso") or "") or None
        ),
        summary="",
        content=content,
        sentiment_label="positive",
        is_beijing_related=local_beijing_match,
        is_beijing_related_llm=None,
        external_importance_status="one_time_assessment",
        keyword_matches=_keyword_matches(row.get("score_details")),
    )


def _as_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _score_sample_row(
    row: Mapping[str, Any],
    beijing_keywords: set[str],
    retries: int,
) -> LeakSampleResult:
    candidate = _build_sample_candidate(row, beijing_keywords)
    category = candidate.candidate_category
    raw_output = call_external_filter_model(
        candidate,
        category=category,
        retries=retries,
    )
    rubric_score = parse_external_filter_score(raw_output)
    if rubric_score is None:
        raise RuntimeError("Model did not return a numeric score")
    return LeakSampleResult(
        article_id=candidate.article_id,
        title=candidate.title or "",
        url=str(row.get("url") or ""),
        relevance_score=_as_optional_float(row.get("score")),
        rubric_score=rubric_score,
        category=category,
    )


def _error_result(row: Mapping[str, Any], category: str, error: Exception) -> LeakSampleResult:
    return LeakSampleResult(
        article_id=str(row.get("article_id") or ""),
        title=str(row.get("title") or ""),
        url=str(row.get("url") or ""),
        relevance_score=_as_optional_float(row.get("score")),
        rubric_score=None,
        category=category,
        error=str(error)[:500],
    )


def _score_sample(
    rows: Sequence[Mapping[str, Any]],
    *,
    beijing_keywords: set[str],
    retries: int,
    concurrency: int,
) -> tuple[list[LeakSampleResult], str]:
    # Submit at most one small batch at a time.  If the provider reports quota
    # exhaustion, later rows are not sent to the LLM.
    results: list[LeakSampleResult] = []
    quota_halt_error = ""
    for start in range(0, len(rows), concurrency):
        batch = rows[start : start + concurrency]
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [
                executor.submit(
                    _score_sample_row,
                    row,
                    beijing_keywords,
                    retries,
                )
                for row in batch
            ]
            for row, future in zip(batch, futures):
                candidate = _build_sample_candidate(row, beijing_keywords)
                try:
                    results.append(future.result())
                except LLMQuotaError as exc:
                    quota_halt_error = str(exc)
                    results.append(_error_result(row, candidate.candidate_category, exc))
                except Exception as exc:
                    results.append(_error_result(row, candidate.candidate_category, exc))
        if quota_halt_error:
            unsubmitted = rows[start + len(batch) :]
            for row in unsubmitted:
                candidate = _build_sample_candidate(row, beijing_keywords)
                results.append(
                    _error_result(
                        row,
                        candidate.candidate_category,
                        RuntimeError("Not submitted after LLM quota halt"),
                    )
                )
            print(
                f"[LLM] quota halt after batch ending at row {start + len(batch)}",
                file=sys.stderr,
                flush=True,
            )
            break
        print(
            f"[LLM] completed {min(start + len(batch), len(rows))}/{len(rows)} sampled rows",
            file=sys.stderr,
            flush=True,
        )
    return results, quota_halt_error


def _percentage(count: int, total: int) -> str:
    return "0.00%" if total <= 0 else f"{count / total * 100:.2f}%"


def _markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return lines


def render_report(
    data: AssessmentData,
    *,
    generated_at: datetime,
    since: datetime,
    days: int,
    requested_sample_size: int,
    seed: str,
    model_name: str,
    beijing_keyword_count: int,
    csv_path: Path,
) -> str:
    lines = [
        "# 教育相关性闸门一次性评估",
        "",
        f"- 生成时间（UTC）：`{generated_at.isoformat()}`",
        f"- 时间范围：最近 {days} 天，`created_at >= {since.isoformat()}`",
        "- 数据库保护：连接级只读；脚本仅执行 SELECT/WITH 查询",
        "",
        "## 一、闸门通过率",
        "",
        "口径：按 `primary_articles.created_at` 统计。`其他` 合并除 scored、filtered_out、failed 之外的状态。",
        "",
    ]
    status_total = sum(data.primary_statuses.values())
    status_labels = (
        ("scored", "scored"),
        ("filtered_out", "filtered_out"),
        ("failed", "failed"),
        ("other", "其他"),
    )
    lines.extend(
        _markdown_table(
            ("状态", "条数", "占比"),
            [
                (label, data.primary_statuses[key], _percentage(data.primary_statuses[key], status_total))
                for key, label in status_labels
            ],
        )
    )
    if data.other_statuses:
        details = "、".join(
            f"{status}={count}" for status, count in sorted(data.other_statuses.items())
        )
        lines.extend(("", f"其他状态明细：{details}。"))

    decided_total = data.primary_statuses["scored"] + data.primary_statuses["filtered_out"]
    lines.extend(
        (
            "",
            "闸门通过率（仅以 scored + filtered_out 为分母）："
            f"{data.primary_statuses['scored']} / {decided_total} = "
            f"{_percentage(data.primary_statuses['scored'], decided_total)}。",
        )
    )

    filtered_total = sum(data.filtered_histogram.values())
    lines.extend(
        (
            "",
            "### filtered_out 原相关性分数直方图",
            "",
            "这里的分数是 `primary_articles.score`（相关性原始分 + 关键词加分后的最终分）。",
            "",
        )
    )
    lines.extend(
        _markdown_table(
            ("分数档", "条数", "占 filtered_out"),
            [
                (
                    band,
                    data.filtered_histogram[band],
                    _percentage(data.filtered_histogram[band], filtered_total),
                )
                for band in _display_bands(data.filtered_histogram)
            ],
        )
    )
    if not data.filtered_histogram:
        lines.append("\n时间范围内没有 filtered_out 记录。")

    lines.extend(
        (
            "",
            "## 二、四分类重要性分数分布",
            "",
            "口径：按 `news_summaries.created_at` 统计 `external_importance_score IS NOT NULL` 的行。类别优先读取 `external_importance_raw.category`；旧数据缺失时由 `is_beijing_related` 与 `sentiment_label` 重建（空情感按 positive）。",
            "",
        )
    )
    categories = list(CATEGORIES) + sorted(
        category for category in data.external_distribution if category not in CATEGORIES
    )
    for category in categories:
        bands = data.external_distribution.get(category, {})
        lines.extend((f"### {category}", ""))
        table_rows: list[tuple[Any, ...]] = []
        for band in _display_bands(bands):
            counts = bands.get(band, Counter())
            ready = counts["ready_for_export"]
            filtered = counts["external_filtered"]
            total = sum(counts.values())
            table_rows.append((band, total, ready, filtered, total - ready - filtered))
        lines.extend(
            _markdown_table(
                ("分数档", "总数", "ready_for_export", "external_filtered", "其他状态"),
                table_rows,
            )
            if table_rows
            else ["（无数据）"]
        )
        lines.append("")

    successful = [item for item in data.sample_results if item.rubric_score is not None]
    failed = [item for item in data.sample_results if item.rubric_score is None]
    above_70 = sum((item.rubric_score or -1) >= 70 for item in successful)
    above_50 = sum((item.rubric_score or -1) >= 50 for item in successful)
    category_counts = Counter(item.category for item in data.sample_results)
    category_text = "、".join(
        f"{category}={count}" for category, count in sorted(category_counts.items())
    ) or "无"

    lines.extend(
        (
            "## 三、filtered_out 漏检抽样复评分",
            "",
            f"- 请求抽样规模：{requested_sample_size}；实际抽到：{data.sampled_count}；成功评分：{len(successful)}；失败/未评分：{len(failed)}",
            f"- 随机种子：`{seed}`（以 `md5(article_id || seed)` 排序后取前 N 条，可复现）",
            f"- 使用模型：`{model_name}`",
            "- 无摘要前提：候选对象的 `summary` 明确传空字符串，现有 adapter 会在提示词中写入“（无摘要）”；正文仍沿用现有 1500 字截断规则",
            f"- 类别简化规则：复用现有北京关键词本地匹配（本次加载 {beijing_keyword_count} 个词），对标题、来源、正文做匹配；命中走 internal，未命中走 external。因没有情感结果，统一按 positive，最终仅使用 internal_positive / external_positive rubric",
            f"- 实际类别数量：{category_text}",
            "- 调用方式：直接复用 `src/adapters/external_filter_model.py` 的提示词加载、请求和分数解析逻辑；没有复制或修改提示词",
            f"- 逐条 CSV：`{csv_path}`",
            "",
        )
    )
    lines.extend(
        _markdown_table(
            ("阈值", "条数", "占成功评分", "占实际抽样"),
            (
                ("rubric >= 70", above_70, _percentage(above_70, len(successful)), _percentage(above_70, data.sampled_count)),
                ("rubric >= 50", above_50, _percentage(above_50, len(successful)), _percentage(above_50, data.sampled_count)),
            ),
        )
    )
    if data.quota_halt_error:
        lines.extend(
            (
                "",
                "**额度保护已触发：** 收到 LLM quota 错误后停止提交后续批次。",
                f"错误：`{data.quota_halt_error}`",
            )
        )
    lines.extend(
        (
            "",
            "> 注意：第三部分缺少真实摘要、情感与地域模型结果，且 positive 类别是简化假设，因此只能作为近似的漏检下界/敏感性检查，不能替代完整流水线回放。",
            "",
        )
    )
    return "\n".join(lines)


def write_csv(path: Path, results: Sequence[LeakSampleResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "article_id",
                "title",
                "url",
                "relevance_score",
                "rubric_score",
                "category",
                "error",
            ),
        )
        writer.writeheader()
        for item in results:
            writer.writerow(
                {
                    "article_id": item.article_id,
                    "title": item.title,
                    "url": item.url,
                    "relevance_score": item.relevance_score,
                    "rubric_score": item.rubric_score,
                    "category": item.category,
                    "error": item.error,
                }
            )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "One-time, read-only assessment of removing the score relevance gate. "
            "The default run scores up to 200 sampled articles; HTTP retries may "
            "make more than one request per article."
        )
    )
    parser.add_argument("--days", type=_positive_int, default=DEFAULT_DAYS)
    parser.add_argument(
        "--sample-size",
        type=_sample_size,
        default=DEFAULT_SAMPLE_SIZE,
        help=f"LLM sample size (default: {DEFAULT_SAMPLE_SIZE}; hard cap: {MAX_SAMPLE_SIZE})",
    )
    parser.add_argument(
        "--concurrency",
        type=_positive_int,
        default=DEFAULT_CONCURRENCY,
        help=f"Concurrent LLM calls per quota-aware batch (default: {DEFAULT_CONCURRENCY})",
    )
    parser.add_argument("--retries", type=_positive_int, default=3)
    parser.add_argument(
        "--seed",
        help="Optional reproducible sampling seed; a random seed is generated by default",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help=f"Detail CSV path (default: {DEFAULT_CSV_PATH})",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Optional Markdown output path; report is always printed to stdout",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    generated_at = datetime.now(timezone.utc)
    since = generated_at - timedelta(days=args.days)
    seed = args.seed or secrets.token_hex(8)
    settings = get_settings()

    with closing(_connect_read_only()) as conn, conn.cursor() as cur:
        primary_statuses, other_statuses = _fetch_status_counts(cur, since)
        filtered_histogram = _fetch_filtered_histogram(cur, since)
        external_distribution = _fetch_external_distribution(cur, since)
        sampled_rows = _fetch_leak_sample(cur, since, args.sample_size, seed)

    beijing_keywords = load_beijing_keywords(settings.beijing_keywords_path)
    print(
        f"[LLM] scoring {len(sampled_rows)} sampled rows with concurrency={args.concurrency}",
        file=sys.stderr,
        flush=True,
    )
    sample_results, quota_halt_error = _score_sample(
        sampled_rows,
        beijing_keywords=beijing_keywords,
        retries=args.retries,
        concurrency=args.concurrency,
    )
    data = AssessmentData(
        primary_statuses=primary_statuses,
        other_statuses=other_statuses,
        filtered_histogram=filtered_histogram,
        external_distribution=external_distribution,
        sampled_count=len(sampled_rows),
        sample_results=sample_results,
        quota_halt_error=quota_halt_error,
    )
    write_csv(args.csv, sample_results)
    report = render_report(
        data,
        generated_at=generated_at,
        since=since,
        days=args.days,
        requested_sample_size=args.sample_size,
        seed=seed,
        model_name=settings.llm_external_filter_model,
        beijing_keyword_count=len(beijing_keywords),
        csv_path=args.csv,
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
    print(report)
    return 2 if quota_halt_error else 0


if __name__ == "__main__":
    sys.exit(main())
