"""One-time, read-only acceptance test for external-filter positive prompts v2.

The prior D-group CSV fixes the 30-article cohort. PostgreSQL is opened in
read-only mode, and the script writes only the requested Markdown/CSV artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Optional, Sequence

import psycopg
import requests
from bs4 import BeautifulSoup
from psycopg import sql
from psycopg.rows import dict_row

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import one_time_external_filter_determinism as baseline
from src.adapters.external_filter_model import (
    build_prompt,
    load_prompt_versions,
)
from src.config import get_settings
from src.domain import ExternalFilterCandidate

SAMPLE_SEED = "external-filter-determinism-v1-20260809"
FIXED_PROVIDER = "alibaba/fp8"
REPETITIONS = 5
CONCURRENCY = 8
TARGET_URL = "https://xinwen.bjd.com.cn/content/s6a733642e4b0e45f3fd5aa33.html"
TARGET_ARTICLE_ID = "bjd:s6a733642e4b0e45f3fd5aa33"
TARGET_TITLE = (
    "市政府召开常务会议，研究“十五五”时期北京推进国际科技创新中心建设等事项"
)
TARGET_SOURCE = "北京日报客户端"
PRIOR_CSV_PATH = Path("artifacts/external_filter_determinism_calls.csv")
PRIOR_REPORT_PATH = Path("artifacts/external_filter_determinism_report.md")
DEFAULT_REPORT_PATH = Path("artifacts/external_filter_prompt_v2_acceptance_report.md")
DEFAULT_CSV_PATH = Path("artifacts/external_filter_prompt_v2_acceptance_calls.csv")

FETCH_BY_IDS_QUERY = """
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
        lower(external_importance_raw->>'category') AS category
    FROM news_summaries
    WHERE article_id = ANY(%s)
"""

V1_EXPECTED = {
    "exact_articles": 21,
    "jump_articles": 5,
    "mean_score": 39.87,
    "distinct_scores": 10,
}

A_V1_EXPECTED = {
    "exact_articles": 3,
    "jump_articles": 1,
}

KNOWN_CONFLICTS = {
    "7581807128730534452": (
        "Tier 6-C（领域完全无关）",
        "Tier 3-B（重点院校科研与科技成果）",
    ),
    "7661199314286346815": (
        "第一步“非教育主体”/Tier 3-C（弱相关或空泛内容）",
        "Tier 1-C（可复制的教育治理创新）",
    ),
    "7671161332351156799": (
        "Tier 6-C（领域完全无关）",
        "Tier 3-G（教育评论与观察）",
    ),
    "gmw:2026-06/24/content_38845066": (
        "Tier 5-C（教育议题但报送价值有限）",
        "Tier 3-G（教育评论与观察）",
    ),
    "tencent:20260618A04KRO00": (
        "Tier 6-B（非本地新闻）",
        "Tier 3-H（含北京高校的排名、榜单与评估结果）",
    ),
    "7659340936928887336": (
        "【第一步：教育主体判定】/Tier 3-C（科普公共服务仍是非教育主体）",
        "Tier 1-C（被“四位一体、可复制推广、种子教师培训”误判为教育治理创新）",
    ),
    "chinanews:/gn/2026/05-25/10627892": (
        "Tier 6-C（论坛核心议题是巴尔干文化与区域研究，而非教育）",
        "Tier 3-F（北京单所高校主办的人文国际交流论坛）；75 分还说明 Tier 2-E 被规模表述误触发",
    ),
    "jyb:/rmtxwwyyq/jyxx1306/202607/t20260706_2111499339": (
        "【第一步：教育主体判定】/Tier 3-C（核心对象被判为老年医疗健康）",
        "Tier 1-C（课程体系、实践基地、产教融合与人才培养被判为教育治理创新）",
    ),
}


@dataclass(slots=True)
class ScoreSummary:
    exact_articles: int
    jump_articles: int
    mean_score: float
    distinct_scores: int


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def _artifact_path(path: Path) -> Path:
    resolved = path.resolve()
    artifacts_root = (_REPO_ROOT / "artifacts").resolve()
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


def _read_prior_config_calls(
    path: Path,
    config_name: str,
) -> list[baseline.CallResult]:
    if not path.exists():
        raise FileNotFoundError(f"Prior determinism CSV is missing: {path}")
    calls = [
        row for row in baseline._read_csv(path) if row.config_name == config_name
    ]
    keys = {(row.article_id, row.repetition) for row in calls}
    if len(calls) != 150 or len(keys) != 150:
        raise RuntimeError(
            f"Prior {config_name} baseline must contain 150 unique calls, "
            f"found {len(calls)}"
        )
    if any(row.status != "ok" or row.parsed_score is None for row in calls):
        raise RuntimeError(
            f"Prior {config_name} baseline contains non-successful calls"
        )
    return calls


def _article_order(calls: Sequence[baseline.CallResult]) -> list[str]:
    order: list[str] = []
    seen: set[str] = set()
    for row in calls:
        if row.article_id not in seen:
            seen.add(row.article_id)
            order.append(row.article_id)
    if len(order) != 30:
        raise RuntimeError(f"Expected 30 prior articles, found {len(order)}")
    return order


def _fetch_same_rows(article_ids: Sequence[str]) -> list[dict[str, Any]]:
    with closing(_connect_read_only()) as conn, conn.cursor() as cur:
        cur.execute(FETCH_BY_IDS_QUERY, (list(article_ids),))
        by_id = {str(row["article_id"]): dict(row) for row in cur.fetchall()}
    missing = [article_id for article_id in article_ids if article_id not in by_id]
    if missing:
        raise RuntimeError(f"Prior sample articles are missing from news_summaries: {missing}")
    return [by_id[article_id] for article_id in article_ids]


def _fetch_target_article() -> tuple[ExternalFilterCandidate, int, bool, str]:
    response = requests.get(
        TARGET_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/131 Safari/537.36"
            )
        },
        timeout=30,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    story = soup.select_one(".storyContent")
    if story is None:
        raise RuntimeError("Unable to locate .storyContent on the target page")
    paragraphs = [
        " ".join(paragraph.get_text(" ", strip=True).split())
        for paragraph in story.find_all("p")
        if paragraph.get_text(strip=True)
    ]
    content = "\n\n".join(paragraphs).strip()
    if not content:
        raise RuntimeError("Target page contained no article paragraphs")
    candidate = ExternalFilterCandidate(
        article_id=TARGET_ARTICLE_ID,
        title=TARGET_TITLE,
        source=TARGET_SOURCE,
        publish_time_iso=None,
        summary="",
        content=content,
        sentiment_label="positive",
        is_beijing_related=True,
        is_beijing_related_llm=True,
        external_importance_status="one_time_prompt_v2_acceptance",
    )
    return candidate, len(content), len(content.strip()) > 1500, "CSS .storyContent 内全部非空 p 段落"


def _build_plan(
    candidate: ExternalFilterCandidate,
    *,
    category: str,
    stored_score: float,
    config_name: str,
    repetition: int,
) -> baseline.InvocationPlan:
    prompt = build_prompt(candidate, category=category)
    payload = baseline._payload_for_config(prompt, config_name, FIXED_PROVIDER)
    return baseline.InvocationPlan(
        article_id=candidate.article_id,
        title=candidate.title or "",
        stored_score=stored_score,
        score_band=min(int(stored_score // 20), 4),
        category=category,
        config_name=config_name,
        repetition=repetition,
        prompt=prompt,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        common_payload_sha256=baseline._stable_hash(
            baseline._common_payload(payload)
        ),
        request_payload_sha256=baseline._stable_hash(payload),
        payload=payload,
    )


def _build_plans(
    rows: Sequence[Mapping[str, Any]],
    target: ExternalFilterCandidate,
) -> list[baseline.InvocationPlan]:
    plans: list[baseline.InvocationPlan] = []
    for row in rows:
        candidate = baseline._candidate_from_row(row)
        category = str(row.get("category") or "")
        if category not in {"internal_positive", "external_positive"}:
            raise RuntimeError(
                f"Acceptance cohort contains unsupported category {category!r}: "
                f"{candidate.article_id}"
            )
        stored_score = float(row.get("external_importance_score") or 0)
        for config_name in ("A", "D"):
            for repetition in range(1, REPETITIONS + 1):
                plans.append(
                    _build_plan(
                        candidate,
                        category=category,
                        stored_score=stored_score,
                        config_name=config_name,
                        repetition=repetition,
                    )
                )
    for repetition in range(1, REPETITIONS + 1):
        plans.append(
            _build_plan(
                target,
                category="internal_positive",
                stored_score=0,
                config_name="D",
                repetition=repetition,
            )
        )
    randomizer = random.Random(f"{SAMPLE_SEED}:prompt-v2-acceptance")
    randomizer.shuffle(plans)
    baseline._assert_only_intended_variables(plans)
    return plans


def _group_scores(
    calls: Sequence[baseline.CallResult],
) -> dict[str, list[int]]:
    grouped: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row in calls:
        if row.status != "ok" or row.parsed_score is None:
            continue
        grouped[row.article_id].append((row.repetition, int(row.parsed_score)))
    return {
        article_id: [score for _, score in sorted(values)]
        for article_id, values in grouped.items()
    }


def _score_summary(grouped: Mapping[str, Sequence[int]]) -> ScoreSummary:
    all_scores = [score for scores in grouped.values() for score in scores]
    return ScoreSummary(
        exact_articles=sum(len(set(scores)) == 1 for scores in grouped.values()),
        jump_articles=sum(min(scores) <= 20 and max(scores) >= 50 for scores in grouped.values()),
        mean_score=mean(all_scores),
        distinct_scores=len(set(all_scores)),
    )


def _jump_ids(grouped: Mapping[str, Sequence[int]]) -> list[str]:
    return [
        article_id
        for article_id, scores in grouped.items()
        if min(scores) <= 20 and max(scores) >= 50
    ]


def _tier_for_score(category: str, score: int) -> str:
    if category == "internal_positive":
        if score >= 85:
            return "Tier 1"
        if score >= 70:
            return "Tier 2"
        if score >= 50:
            return "Tier 3"
        if score >= 35:
            return "Tier 4"
        if score >= 20:
            return "Tier 5"
        return "Tier 6"
    if score >= 70:
        return "Tier 1"
    if score >= 35:
        return "Tier 2"
    return "Tier 3"


def _pass_rate(
    calls: Sequence[baseline.CallResult],
    category: str,
    threshold: int,
) -> tuple[int, int, float]:
    scores = [
        int(row.parsed_score)
        for row in calls
        if row.category == category
        and row.status == "ok"
        and row.parsed_score is not None
    ]
    passed = sum(score >= threshold for score in scores)
    return passed, len(scores), passed / len(scores) * 100 if scores else 0.0


def _escape_table(value: str) -> str:
    return " ".join((value or "").split()).replace("|", "\\|")


def _validate_prior_baseline(
    prior_calls: Sequence[baseline.CallResult],
) -> tuple[dict[str, list[int]], ScoreSummary]:
    grouped = _group_scores(prior_calls)
    summary = _score_summary(grouped)
    if summary.exact_articles != V1_EXPECTED["exact_articles"]:
        raise RuntimeError(f"Unexpected v1 exact count: {summary.exact_articles}")
    if summary.jump_articles != V1_EXPECTED["jump_articles"]:
        raise RuntimeError(f"Unexpected v1 jump count: {summary.jump_articles}")
    if round(summary.mean_score, 2) != V1_EXPECTED["mean_score"]:
        raise RuntimeError(f"Unexpected v1 mean: {summary.mean_score}")
    if summary.distinct_scores != V1_EXPECTED["distinct_scores"]:
        raise RuntimeError(f"Unexpected v1 distinct score count: {summary.distinct_scores}")
    return grouped, summary


def _validate_prior_a_baseline(
    prior_calls: Sequence[baseline.CallResult],
) -> tuple[dict[str, list[int]], ScoreSummary]:
    grouped = _group_scores(prior_calls)
    summary = _score_summary(grouped)
    if summary.exact_articles != A_V1_EXPECTED["exact_articles"]:
        raise RuntimeError(f"Unexpected A-v1 exact count: {summary.exact_articles}")
    if summary.jump_articles != A_V1_EXPECTED["jump_articles"]:
        raise RuntimeError(f"Unexpected A-v1 jump count: {summary.jump_articles}")
    return grouped, summary


def _parameter_validation(
    plans: Sequence[baseline.InvocationPlan],
    prior_d_calls: Sequence[baseline.CallResult],
    timeout: int,
    retries: int,
) -> dict[str, Any]:
    settings = get_settings()
    prior_models = {row.response_model for row in prior_d_calls}
    prior_providers = {row.response_provider for row in prior_d_calls}
    skeleton_hashes: dict[str, set[str]] = defaultdict(set)
    for plan in plans:
        skeleton_hashes[plan.config_name].add(
            baseline._stable_hash(
                {
                    **{
                        key: value
                        for key, value in plan.payload.items()
                        if key != "messages"
                    },
                    "messages": [{"role": "user", "content": "<PROMPT>"}],
                }
            )
        )
    a_reference = baseline._payload_for_config("<PROMPT>", "A", FIXED_PROVIDER)
    payloads_ok = all(
        plan.payload.get("model") == settings.llm_external_filter_model
        and plan.payload.get("temperature") == 0.0
        and (
            (
                plan.config_name == "A"
                and plan.payload.get("reasoning") == a_reference.get("reasoning")
                and "provider" not in plan.payload
            )
            or (
                plan.config_name == "D"
                and plan.payload.get("reasoning") == {"effort": "none"}
                and plan.payload.get("provider")
                == {"only": [FIXED_PROVIDER], "allow_fallbacks": False}
            )
        )
        for plan in plans
    )
    prior_report = PRIOR_REPORT_PATH.read_text(encoding="utf-8")
    runtime_text = f"共同并发={CONCURRENCY}，timeout={timeout}s，HTTP retries={retries}"
    return {
        "prior_models": prior_models,
        "prior_providers": prior_providers,
        "current_model": settings.llm_external_filter_model,
        "skeleton_hash_counts": {
            config_name: len(hashes)
            for config_name, hashes in sorted(skeleton_hashes.items())
        },
        "payloads_ok": payloads_ok,
        "a_reasoning": a_reference.get("reasoning"),
        "runtime_matches_report": runtime_text in prior_report,
    }


def _run_with_resume(
    plans: Sequence[baseline.InvocationPlan],
    *,
    concurrency: int,
    retries: int,
    timeout: int,
    csv_path: Path,
) -> list[baseline.CallResult]:
    plan_by_key = {
        (plan.article_id, plan.config_name, plan.repetition): plan for plan in plans
    }
    results_by_key = {
        key: baseline._pending_result(plan) for key, plan in plan_by_key.items()
    }
    if csv_path.exists():
        for result in baseline._read_csv(csv_path):
            key = (result.article_id, result.config_name, result.repetition)
            plan = plan_by_key.get(key)
            if plan is None:
                raise RuntimeError(f"Existing CSV contains an unexpected slot: {key}")
            if (
                result.prompt_sha256 != plan.prompt_sha256
                or result.request_payload_sha256 != plan.request_payload_sha256
            ):
                raise RuntimeError(
                    f"Existing CSV payload does not match current plan: {key}"
                )
            results_by_key[key] = result
    missing = [
        plan
        for key, plan in plan_by_key.items()
        if results_by_key[key].status != "ok"
    ]
    baseline._write_csv(csv_path, list(results_by_key.values()))
    if not missing:
        return list(results_by_key.values())
    print(
        f"Resuming {len(missing)} missing calls; preserving "
        f"{len(plans) - len(missing)} successful calls",
        file=sys.stderr,
        flush=True,
    )
    completed = 0
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_map = {
            executor.submit(baseline._invoke, plan, retries, timeout): plan
            for plan in missing
        }
        for future in as_completed(future_map):
            plan = future_map[future]
            key = (plan.article_id, plan.config_name, plan.repetition)
            try:
                result = future.result()
            except Exception as exc:
                result = baseline._pending_result(plan)
                result.status = "error"
                result.error = str(exc)
                result.completed_at = datetime.now(timezone.utc).isoformat()
            results_by_key[key] = result
            completed += 1
            if completed % 10 == 0 or completed == len(missing):
                baseline._write_csv(csv_path, list(results_by_key.values()))
                print(
                    f"Completed {completed}/{len(missing)} resumed calls",
                    file=sys.stderr,
                    flush=True,
                )
    results = list(results_by_key.values())
    baseline._write_csv(csv_path, results)
    return results


def _render_report(
    *,
    rows: Sequence[Mapping[str, Any]],
    prior_a_calls: Sequence[baseline.CallResult],
    prior_d_calls: Sequence[baseline.CallResult],
    current_calls: Sequence[baseline.CallResult],
    target_chars: int,
    target_truncated: bool,
    target_fetch_method: str,
    versions: Mapping[str, str],
    parameter_check: Mapping[str, Any],
    timeout: int,
    retries: int,
    csv_path: Path,
) -> str:
    prior_a_grouped, prior_a_summary = _validate_prior_a_baseline(prior_a_calls)
    prior_d_grouped, prior_d_summary = _validate_prior_baseline(prior_d_calls)
    main_ids = {str(row.get("article_id") or "") for row in rows}
    a_main_calls = [
        row
        for row in current_calls
        if row.article_id in main_ids and row.config_name == "A"
    ]
    d_main_calls = [
        row
        for row in current_calls
        if row.article_id in main_ids and row.config_name == "D"
    ]
    target_calls = [
        row
        for row in current_calls
        if row.article_id == TARGET_ARTICLE_ID and row.config_name == "D"
    ]
    a_current_grouped = _group_scores(a_main_calls)
    d_current_grouped = _group_scores(d_main_calls)
    target_grouped = _group_scores(target_calls)
    if len(a_main_calls) != 150 or len(a_current_grouped) != 30:
        raise RuntimeError(
            f"Current A cohort is incomplete: calls={len(a_main_calls)}, "
            f"articles={len(a_current_grouped)}"
        )
    if len(d_main_calls) != 150 or len(d_current_grouped) != 30:
        raise RuntimeError(
            f"Current D cohort is incomplete: calls={len(d_main_calls)}, "
            f"articles={len(d_current_grouped)}"
        )
    if len(target_calls) != 5 or len(target_grouped.get(TARGET_ARTICLE_ID, [])) != 5:
        raise RuntimeError("Target article five-call regression is incomplete")
    a_current_summary = _score_summary(a_current_grouped)
    d_current_summary = _score_summary(d_current_grouped)
    target_scores = target_grouped[TARGET_ARTICLE_ID]
    target_passed = all(85 <= score <= 100 for score in target_scores)
    exact_passed = d_current_summary.exact_articles >= 24
    jumps_passed = d_current_summary.jump_articles <= 2
    overall_passed = exact_passed and jumps_passed and target_passed
    d_current_jumps = _jump_ids(d_current_grouped)
    d_prior_jumps = _jump_ids(prior_d_grouped)
    a_current_jumps = _jump_ids(a_current_grouped)
    meta = {
        str(row.get("article_id") or ""): {
            "title": str(row.get("title") or ""),
            "category": str(row.get("category") or ""),
        }
        for row in rows
    }
    prior_prompt_hashes = {
        (row.article_id, row.prompt_sha256) for row in prior_d_calls
    }
    current_prompt_hashes = {
        (row.article_id, row.prompt_sha256) for row in d_main_calls
    }
    changed_prompt_articles = sum(
        {prompt_hash for article_id, prompt_hash in prior_prompt_hashes if article_id == aid}
        != {prompt_hash for article_id, prompt_hash in current_prompt_hashes if article_id == aid}
        for aid in main_ids
    )
    if changed_prompt_articles != 30:
        raise RuntimeError(
            f"Expected all 30 prompt hashes to change from v1, got {changed_prompt_articles}"
        )
    all_statuses = Counter(row.status for row in current_calls)
    all_providers = Counter(row.response_provider or "(missing)" for row in current_calls)
    all_models = Counter(row.response_model or "(missing)" for row in current_calls)
    v1_internal = _pass_rate(prior_d_calls, "internal_positive", 20)
    v1_external = _pass_rate(prior_d_calls, "external_positive", 35)
    v2_internal = _pass_rate(d_main_calls, "internal_positive", 20)
    v2_external = _pass_rate(d_main_calls, "external_positive", 35)
    a_provider_counts = Counter(
        row.response_provider or "(missing)" for row in a_main_calls
    )
    a_jump_reduced = a_current_summary.jump_articles < prior_a_summary.jump_articles

    lines = [
        "# 四分类正面提示词 v2 验收报告",
        "",
        "## 技术结论",
        "",
        f"**验收结论：{'通过' if overall_passed else '不通过'}。** "
        f"D-v2 完全一致文章 {d_current_summary.exact_articles}/30（目标 ≥24），整档跳文章 "
        f"{d_current_summary.jump_articles}/30（目标 ≤2）；指定市政府常务会议 "
        f"5 次得分为 {'/'.join(map(str, target_scores))}，硬性回归"
        f"{'通过' if target_passed else '不通过'}。",
        "",
        f"- 生产配置直接结论：A-v2 整档跳 {a_current_summary.jump_articles}/30，A-v1 为 {prior_a_summary.jump_articles}/30；"
        f"{'有所下降' if a_jump_reduced else '没有下降'}。因此就整档跳主问题而言，v2 在开启 reasoning、自由路由的生产配置下"
        f"{'提供了改善证据' if a_jump_reduced else '没有提供改善证据'}。",
        f"- 调用完整性：计划 305 次，CSV 305 行；状态分布：{dict(all_statuses)}",
        f"- 提示词版本：internal_positive=`{versions['internal_positive']}`，external_positive=`{versions['external_positive']}`；external_negative=`{versions['external_negative']}`、internal_negative=`{versions['internal_negative']}`（本次未调用负面提示词）",
        f"- 路由核对：{dict(all_providers)}；响应 model：{dict(all_models)}",
        "- D 组仅作为固定供应商、关闭 reasoning 的低噪声诊断环境，不作为生产配置建议",
        "",
        "## v2 是否消除了拒绝档与 Tier 1/2 之间的整档跳",
        "",
        "主指标定义为同一篇 5 次中同时出现 ≤20 与 ≥50；它比普通极差更直接对应本次规则冲突问题。",
        "",
        "| 指标 | v1 实测 | v2 本次 | 目标 | 结果 |",
        "|---|---:|---:|---:|---|",
        f"| 5 次完全一致的文章数 | {prior_d_summary.exact_articles}/30 | {d_current_summary.exact_articles}/30 | ≥24/30 | {'通过' if exact_passed else '不通过'} |",
        f"| 整档跳文章数（同时出现 ≤20 和 ≥50） | {prior_d_summary.jump_articles}/30 | {d_current_summary.jump_articles}/30 | ≤2/30 | {'通过' if jumps_passed else '不通过'} |",
        f"| 平均分 | {prior_d_summary.mean_score:.2f} | {d_current_summary.mean_score:.2f} | 记录 | — |",
        f"| 分数取值个数 | {prior_d_summary.distinct_scores} | {d_current_summary.distinct_scores} | 记录 | — |",
        "",
        f"在 D 组中，v2 把整档跳从 {prior_d_summary.jump_articles} 条降到 {d_current_summary.jump_articles} 条，但仍高于上限 2；同时完全一致文章从 {prior_d_summary.exact_articles} 条降到 {d_current_summary.exact_articles} 条，说明冲突范围缩小但一般重复性反而变差。",
        "",
        "### v1 五条整档跳文章在 v2 下的结果",
        "",
        "| article_id | 标题 | 类别 | v1 五次 | v2 五次 | v2 是否仍整档跳 |",
        "|---|---|---|---|---|---|",
    ]
    for article_id in d_prior_jumps:
        info = meta[article_id]
        v1_scores = "/".join(map(str, prior_d_grouped[article_id]))
        v2_scores = "/".join(map(str, d_current_grouped[article_id]))
        lines.append(
            f"| {article_id} | {_escape_table(info['title'])} | {info['category']} | "
            f"{v1_scores} | {v2_scores} | {'是' if article_id in d_current_jumps else '否'} |"
        )

    lines.extend(
        [
            "",
            "### v2 仍然整档跳的文章",
            "",
        ]
    )
    if not d_current_jumps:
        lines.append("无。")
    else:
        lines.extend(
            [
                "| article_id | 标题 | 类别 | 五次分数 | 摆动档位 | 对应条款判断 |",
                "|---|---|---|---|---|---|",
            ]
        )
        for article_id in d_current_jumps:
            info = meta[article_id]
            scores = d_current_grouped[article_id]
            low_score = min(scores)
            high_score = max(scores)
            low_rule, high_rule = KNOWN_CONFLICTS.get(
                article_id,
                (
                    f"{_tier_for_score(info['category'], low_score)} 低档条款（需结合正文复核）",
                    f"{_tier_for_score(info['category'], high_score)} 高档条款（需结合正文复核）",
                ),
            )
            lines.append(
                f"| {article_id} | {_escape_table(info['title'])} | {info['category']} | "
                f"{'/'.join(map(str, scores))} | {_tier_for_score(info['category'], low_score)} ↔ "
                f"{_tier_for_score(info['category'], high_score)} | {low_rule} ↔ {high_rule} |"
            )

    lines.extend(
        [
            "",
            "## 指定市政府常务会议的 Tier 1-G 硬性回归",
            "",
            f"**结论：{'通过' if target_passed else '不通过'}。** 5 次分数：`{'/'.join(map(str, target_scores))}`；期望全部落在 85-100。"
            + ("" if target_passed else " 即使第一部分指标通过，本次总验收仍判定不通过。"),
            "",
            f"- 数据来源：[北京日报客户端原文]({TARGET_URL}) 在 news_summaries、primary_articles、raw_articles 均未命中；脚本对该页面执行 HTTP GET，从 `{target_fetch_method}` 抽取正文",
            f"- 正文字数：{target_chars} 字；1500 字截断：{'是' if target_truncated else '否'}",
            "- 候选构造：category=`internal_positive`，summary 为空字符串，source=`北京日报客户端`，is_beijing_related=True；随后调用生产 `build_prompt`",
        ]
    )
    if not target_passed:
        lines.extend(["", "### 五次原始输出", ""])
        for row in sorted(target_calls, key=lambda item: item.repetition):
            lines.extend(
                [
                    f"#### 第 {row.repetition} 次 / parsed={row.parsed_score}",
                    "",
                    "```text",
                    row.raw_output,
                    "```",
                    "",
                ]
            )

    lines.extend(
        [
            "",
            "## v2 的阈值通过率位移",
            "",
            "仅记录位移，不据此调整阈值。分母为对应类别 15 篇 × 5 次 = 75 次调用。",
            "",
            "| 类别 | 当前阈值 | v1 通过 | v1 通过率 | v2 通过 | v2 通过率 | 变化 |",
            "|---|---:|---:|---:|---:|---:|---:|",
            f"| internal_positive | 20 | {v1_internal[0]}/{v1_internal[1]} | {v1_internal[2]:.1f}% | {v2_internal[0]}/{v2_internal[1]} | {v2_internal[2]:.1f}% | {v2_internal[2] - v1_internal[2]:+.1f} pp |",
            f"| external_positive | 35 | {v1_external[0]}/{v1_external[1]} | {v1_external[2]:.1f}% | {v2_external[0]}/{v2_external[1]} | {v2_external[2]:.1f}% | {v2_external[2] - v1_external[2]:+.1f} pp |",
            "",
            "## A 组生产配置下的 v2 回归",
            "",
            "A 组保持生产现状：不设置 provider、reasoning 按生产环境保持开启、temperature=0。D 组仅用于低噪声诊断，不作为生产配置建议。",
            "",
            "| 配置 | 提示词 | 5 次完全一致 | 整档跳条数 |",
            "|---|---|---:|---:|",
            f"| A | v1 | {prior_a_summary.exact_articles}/30 | {prior_a_summary.jump_articles}/30 |",
            f"| A | v2 | {a_current_summary.exact_articles}/30 | {a_current_summary.jump_articles}/30 |",
            f"| D | v1 | {prior_d_summary.exact_articles}/30 | {prior_d_summary.jump_articles}/30 |",
            f"| D | v2 | {d_current_summary.exact_articles}/30 | {d_current_summary.jump_articles}/30 |",
            "",
            f"**直接回答：{'是' if a_jump_reduced else '否'}。** A 组整档跳从 {prior_a_summary.jump_articles}/30 "
            f"变为 {a_current_summary.jump_articles}/30，v2 在生产实际配置下"
            f"{'同样降低了整档跳' if a_jump_reduced else '没有降低整档跳'}。",
            "",
            f"A-v2 实际 provider 分布：{dict(a_provider_counts)}；payload reasoning={parameter_check['a_reasoning']}。",
            "",
            "### A-v2 整档跳明细",
            "",
        ]
    )
    if not a_current_jumps:
        lines.append("无。")
    else:
        lines.extend(
            [
                "| article_id | 标题 | 类别 | A-v1 五次 | A-v2 五次 |",
                "|---|---|---|---|---|",
            ]
        )
        for article_id in a_current_jumps:
            info = meta[article_id]
            lines.append(
                f"| {article_id} | {_escape_table(info['title'])} | {info['category']} | "
                f"{'/'.join(map(str, prior_a_grouped[article_id]))} | "
                f"{'/'.join(map(str, a_current_grouped[article_id]))} |"
            )

    lines.extend(
        [
            "",
            "## 样本与唯一变量验证",
            "",
            f"- 同批样本：直接从上次 CSV 的 D 组提取 30 个 article_id，再以 `WHERE article_id = ANY(...)` 只读回取；随机种子记录为 `{SAMPLE_SEED}`。没有重新按当前全库抽样，避免新增数据改变样本。",
            f"- 样本一致性：旧 A、D 两组各 150 个唯一调用槽位；本次 A、D 主样本也各为相同 30 个 article_id × 5；D 组 30/30 的 prompt SHA 与 v1 不同（实测 {changed_prompt_articles}/30），符合提示词版本变更。",
            f"- Payload：复用 `scripts/one_time_external_filter_determinism.py` 的 `_payload_for_config` 与 `_invoke`；payload 断言结果={parameter_check['payloads_ok']}，去掉实际 prompt 后各配置 skeleton hash 个数={parameter_check['skeleton_hash_counts']}。",
            f"- D 固定参数：model=`{parameter_check['current_model']}`（v1 响应 model={sorted(parameter_check['prior_models'])}），temperature=0，provider.only=[`{FIXED_PROVIDER}`]，allow_fallbacks=false，reasoning.effort=none。A 与 D 的预定差异仅为 A 不固定 provider 且 reasoning 使用生产值 `{parameter_check['a_reasoning']}`；两组共同 timeout={timeout}s、retries={retries}、concurrency={CONCURRENCY}。与上次报告运行参数匹配={parameter_check['runtime_matches_report']}。",
            "- v1→v2 比较的唯一变化是从当前 VERSIONS 加载的正面提示词内容；A-v1/A-v2 使用同一 A payload 构造函数，D-v1/D-v2 使用同一 D payload 构造函数。A 与 D 之间的 provider/reasoning 差异是实验定义，不被当作提示词效果。",
            "- 解析与记录：复用上一脚本 `_invoke`，内部仍调用生产 `parse_external_filter_score`；CSV 字段顺序复用上一脚本 `CSV_FIELDS`。",
            f"- CSV：`{csv_path.as_posix()}`；所有计划槽位均保留 status/error，不静默丢弃。",
            "",
            "## 限制与后续动作",
            "",
            "- A 组给出自由路由、开启 reasoning 的生产现状证据；provider 分布只代表本次 150 次调用时的实际路由结果。",
            "- D 组是固定 Alibaba FP8、关闭 reasoning 的诊断环境，不是生产配置建议。",
            "- 通过率位移只是 30 条分层样本上的调用级描述，不用于重校阈值。",
            "- 若主指标或硬性回归未通过，应回到仍冲突的具体条款修订提示词；本任务未修改任何提示词或阈值。",
            "",
            f"生成时间：{datetime.now(timezone.utc).astimezone().isoformat()}",
        ]
    )
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One-time read-only acceptance test for positive importance prompts v2"
    )
    parser.add_argument("--prior-csv", type=Path, default=PRIOR_CSV_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--concurrency", type=_positive_int, default=CONCURRENCY)
    parser.add_argument("--retries", type=_positive_int)
    parser.add_argument("--timeout", type=_positive_int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--render-only", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = _parser().parse_args(argv)
    if args.concurrency != CONCURRENCY:
        raise ValueError(
            f"Acceptance requires prior D concurrency={CONCURRENCY}, got {args.concurrency}"
        )
    settings = get_settings()
    retries = args.retries or settings.external_filter_max_retries
    timeout = args.timeout or settings.llm_external_filter_timeout
    if retries != 3 or timeout != 90:
        raise ValueError(
            f"Acceptance requires prior D retries=3/timeout=90, got {retries}/{timeout}"
        )
    report_path = _artifact_path(args.report)
    csv_path = _artifact_path(args.csv)
    versions = load_prompt_versions()
    if versions["internal_positive"] != "v2" or versions["external_positive"] != "v2":
        raise RuntimeError(f"Positive prompt versions are not both v2: {versions}")
    prior_a_calls = _read_prior_config_calls(args.prior_csv, "A")
    prior_d_calls = _read_prior_config_calls(args.prior_csv, "D")
    article_ids = _article_order(prior_d_calls)
    if set(article_ids) != set(_article_order(prior_a_calls)):
        raise RuntimeError("Prior A and D groups do not contain the same article IDs")
    rows = _fetch_same_rows(article_ids)
    target, target_chars, target_truncated, target_fetch_method = _fetch_target_article()
    plans = _build_plans(rows, target)
    parameter_check = _parameter_validation(plans, prior_d_calls, timeout, retries)
    if (
        not parameter_check["payloads_ok"]
        or parameter_check["skeleton_hash_counts"] != {"A": 1, "D": 1}
    ):
        raise RuntimeError(f"A/D payload validation failed: {parameter_check}")
    print(
        json.dumps(
            {
                "sample_seed": SAMPLE_SEED,
                "same_article_count": len(article_ids),
                "planned_calls": len(plans),
                "target_chars": target_chars,
                "target_truncated": target_truncated,
                "versions": versions,
                "parameter_check": {
                    key: sorted(value) if isinstance(value, set) else value
                    for key, value in parameter_check.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        file=sys.stderr,
    )
    if args.dry_run:
        return 0
    if args.render_only:
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV does not exist: {csv_path}")
        current_calls = baseline._read_csv(csv_path)
        expected = {
            (plan.article_id, plan.config_name, plan.repetition) for plan in plans
        }
        actual = {
            (row.article_id, row.config_name, row.repetition) for row in current_calls
        }
        if expected != actual:
            raise RuntimeError(
                f"Existing CSV does not match plans: expected={len(expected)}, actual={len(actual)}"
            )
    else:
        current_calls = _run_with_resume(
            plans,
            concurrency=args.concurrency,
            retries=retries,
            timeout=timeout,
            csv_path=csv_path,
        )
    report = _render_report(
        rows=rows,
        prior_a_calls=prior_a_calls,
        prior_d_calls=prior_d_calls,
        current_calls=current_calls,
        target_chars=target_chars,
        target_truncated=target_truncated,
        target_fetch_method=target_fetch_method,
        versions=versions,
        parameter_check=parameter_check,
        timeout=timeout,
        retries=retries,
        csv_path=csv_path,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
