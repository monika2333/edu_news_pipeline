from __future__ import annotations

from concurrent.futures import as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

from src.adapters.db_postgres_core import get_adapter
from src.adapters.db_postgres_prompt_optimization import (
    OUTCOME_CHECK_FAILED,
    OUTCOME_ERROR,
    OUTCOME_MODEL_DECLINED,
    OUTCOME_NO_CORRECTION,
    OUTCOME_REVISION_CREATED,
)
from src.adapters.db_postgres_prompt_revisions import PromptRevisionConflictError
from src.adapters.external_filter_model import (
    call_external_filter_model,
    parse_external_filter_score,
)
from src.adapters.llm_prompt_optimizer import (
    DECISION_NO_CHANGE,
    PromptOptimizerDecision,
    PromptOptimizerResponseError,
    call_prompt_optimizer,
)
from src.adapters.prompt_store import load_active_prompt
from src.business_config import get_llm_step_config
from src.config import get_settings
from src.domain.external_filter import external_filter_candidate_from_row
from src.domain.prompt import SCORING_PROMPT_KEYS, PromptTemplate
from src.domain.prompt_edit import (
    CASE_BUCKET_IGNORED,
    CASE_BUCKET_R,
    CASE_BUCKET_T,
    CASE_ORIGIN_FEEDBACK,
    CASE_ORIGIN_SUBMISSION,
    DIRECTION_UP,
    FIX_STATE_BROKEN,
    FIX_STATE_FIXED,
    OPTIMIZATION_LOOKBACK_DAYS,
    OPTIMIZATION_MAX_ATTEMPTS,
    OPTIMIZATION_MAX_CASES,
    SUBMITTED_SCORE_FLOOR,
    CheckResult,
    OptimizationCase,
    PromptEditError,
    apply_prompt_edits,
    case_broken_probe,
    case_fixed_probe,
    case_satisfied,
    evaluate_candidate_check,
    feedback_criterion,
    resolve_case_state,
)
from src.workers import (
    ContextPropagatingThreadPoolExecutor,
    log_error,
    log_info,
)

WORKER_NAME = "prompt-optimizer"
OUTCOME_PREVIEW = "preview"
# 进入 T 之前的二次确认发现两次结论不一致时的忽略原因（写入 skip_reason）。
CURRENT_FLUCTUATION_SKIP_REASON = "当前版本两次结论不一致（分数波动）"
DEFAULT_PROMPT_KEYS = tuple(sorted(SCORING_PROMPT_KEYS))
ReplayFn = Callable[[Mapping[str, Any], str, str], Optional[int]]
OptimizerFn = Callable[
    [
        str,
        str,
        Sequence[Mapping[str, Any]],
        Sequence[Mapping[str, Any]],
        Optional[str],
    ],
    PromptOptimizerDecision,
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return fallback
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return fallback


def _default_replay(
    row: Mapping[str, Any],
    prompt_key: str,
    content: str,
) -> Optional[int]:
    settings = get_settings()
    raw_output = call_external_filter_model(
        external_filter_candidate_from_row(row),
        category=prompt_key,
        retries=max(1, settings.external_filter_max_retries),
        prompt_template=content,
    )
    return parse_external_filter_score(raw_output)


def _default_optimizer(
    prompt_key: str,
    content: str,
    cases: Sequence[Mapping[str, Any]],
    reference_cases: Sequence[Mapping[str, Any]],
    failure_feedback: Optional[str],
) -> PromptOptimizerDecision:
    return call_prompt_optimizer(
        prompt_key=prompt_key,
        current_content=content,
        cases=cases,
        reference_cases=reference_cases,
        failure_feedback=failure_feedback,
    )


@dataclass(frozen=True)
class _ReplayResult:
    score: Optional[int]
    error: Optional[str] = None


def _replay_cases(
    cases: Sequence[OptimizationCase],
    *,
    rows: Mapping[str, Mapping[str, Any]],
    prompt_key: str,
    content: str,
    replay: ReplayFn,
    concurrency: int,
) -> dict[str, _ReplayResult]:
    """Replay cases concurrently, keyed by article id.

    Completion order is intentionally irrelevant: callers apply the returned
    mapping by iterating their original case list. That keeps scores, buckets,
    skip reasons and run-record case order identical to serial execution.
    """

    if not cases:
        return {}
    results: dict[str, _ReplayResult] = {}
    workers = max(1, int(concurrency or 1))
    with ContextPropagatingThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(replay, rows[case.article_id], prompt_key, content): case
            for case in cases
        }
        for future in as_completed(future_map):
            case = future_map[future]
            try:
                score = future.result()
            except Exception as exc:
                results[case.article_id] = _ReplayResult(
                    score=None,
                    error=str(exc) or exc.__class__.__name__,
                )
            else:
                results[case.article_id] = _ReplayResult(score=score)
    return results


def _case_payload(case: OptimizationCase) -> dict[str, Any]:
    return {
        "article_id": case.article_id,
        "title": case.title,
        "source": case.source,
        "origin": case.origin,
        "origin_label": case.origin_label,
        "direction": case.direction,
        "direction_label": case.direction_label,
        "criterion_label": case.criterion_label,
        "threshold": case.threshold,
        "original_score": case.original_score,
        "stored_score": case.stored_score,
        "event_at": case.event_at.isoformat(),
        "reason": case.reason,
        "feedback_type": case.feedback_type,
        "report_types": list(case.report_types),
        "bucket": case.bucket,
        "current_score": case.current_score,
        "candidate_score": case.candidate_score,
        "current_satisfied": case.current_satisfied,
        "candidate_satisfied": case.candidate_satisfied,
        "current_confirm_score": case.current_confirm_score,
        "candidate_confirm_score": case.candidate_confirm_score,
        "fix_state": case.fix_state,
        "skip_reason": case.skip_reason,
        "replay_error": case.replay_error,
    }


def _optimizer_case_payload(case: OptimizationCase) -> dict[str, Any]:
    return {
        "title": case.title,
        "source": case.source,
        "summary": case.summary,
        "content_excerpt": case.content_excerpt,
        "current_score": case.current_score,
        "stored_score": case.stored_score,
        "direction": case.direction,
        "direction_label": case.direction_label,
        "criterion_label": case.criterion_label,
        "reason": case.reason,
        "origin_label": case.origin_label,
    }


def _collect_cases(
    adapter: Any,
    prompt_key: str,
    *,
    since: datetime,
    limit: int = OPTIMIZATION_MAX_CASES,
) -> list[OptimizationCase]:
    feedback_rows, submission_rows = adapter.prompt_optimization.collect_cases(
        prompt_key,
        since=since,
        limit=limit,
    )
    feedback_cases: list[OptimizationCase] = []
    feedback_article_ids: set[str] = set()
    for row in feedback_rows:
        article_id = str(row.get("article_id") or "")
        if not article_id:
            continue
        direction, threshold = feedback_criterion(
            str(row.get("feedback_type") or ""),
            float(row.get("original_score") or 0.0),
        )
        feedback_article_ids.add(article_id)
        feedback_cases.append(
            OptimizationCase(
                article_id=article_id,
                title=str(row.get("title") or "（无标题）"),
                source=row.get("source"),
                origin=CASE_ORIGIN_FEEDBACK,
                direction=direction,
                threshold=threshold,
                original_score=float(row.get("original_score") or 0.0),
                prompt_key=prompt_key,
                event_at=_as_datetime(row.get("event_at"), since),
                reason=row.get("reason"),
                feedback_type=str(row.get("feedback_type") or ""),
                stored_score=(
                    float(row["stored_score"])
                    if row.get("stored_score") is not None
                    else None
                ),
                summary=str(row.get("summary") or ""),
            )
        )

    submission_cases: list[OptimizationCase] = []
    for row in submission_rows:
        article_id = str(row.get("article_id") or "")
        if not article_id or article_id in feedback_article_ids:
            # 同一篇文章同时有标注和存档时，以标注为准。
            continue
        stored_score = (
            float(row["stored_score"])
            if row.get("stored_score") is not None
            else None
        )
        report_types = tuple(
            sorted(str(item) for item in (row.get("report_types") or ()))
        )
        submission_cases.append(
            OptimizationCase(
                article_id=article_id,
                title=str(row.get("title") or "（无标题）"),
                source=row.get("source"),
                origin=CASE_ORIGIN_SUBMISSION,
                direction=DIRECTION_UP,
                threshold=SUBMITTED_SCORE_FLOOR,
                original_score=stored_score,
                prompt_key=prompt_key,
                event_at=_as_datetime(row.get("event_at"), since),
                report_types=report_types,
                stored_score=stored_score,
                summary=str(row.get("summary") or ""),
            )
        )

    merged = feedback_cases + submission_cases
    merged.sort(key=lambda item: item.event_at, reverse=True)
    return merged[:limit]


def _precheck_skips_replay(
    cases: Sequence[OptimizationCase],
    window_start: datetime,
) -> bool:
    window_cases = [case for case in cases if case.event_at >= window_start]
    if any(case.origin == CASE_ORIGIN_FEEDBACK for case in window_cases):
        return False
    return all(
        case.stored_score is not None
        and case.stored_score >= SUBMITTED_SCORE_FLOOR
        for case in window_cases
        if case.origin == CASE_ORIGIN_SUBMISSION
    )


def _failure_feedback(
    check: CheckResult,
    target_cases: Sequence[OptimizationCase],
    reference_cases: Sequence[OptimizationCase],
) -> str:
    lines = ["自动检查未通过："]
    lines.extend(f"- {item}" for item in check.failures)
    not_fixed = [
        case.title for case in target_cases if case.fix_state != FIX_STATE_FIXED
    ]
    broken = [
        case.title
        for case in reference_cases
        if case.fix_state == FIX_STATE_BROKEN
    ]
    if not_fixed:
        lines.append("仍未修好：" + "、".join(not_fixed[:8]))
    if broken:
        lines.append("被改坏：" + "、".join(broken[:8]))
    return "\n".join(lines)


def _summary(
    *,
    prompt_key: str,
    outcome: str,
    started_at: datetime,
    finished_at: datetime,
    window_start: datetime,
    baseline_revision_id: Optional[int],
    new_revision_id: Optional[int],
    new_version: Optional[str],
    model: str,
    cases: Sequence[OptimizationCase],
    target_cases: Sequence[OptimizationCase],
    reference_cases: Sequence[OptimizationCase],
    skipped_count: int,
    model_calls: int,
    check: Optional[CheckResult],
    attempts: Sequence[Mapping[str, Any]],
    error: Optional[str],
    note: Optional[str],
    dry_run: bool,
    run_id: Optional[int] = None,
    confirm_replay_count: int = 0,
) -> dict[str, Any]:
    return {
        "prompt_key": prompt_key,
        "outcome": outcome,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "window_start": window_start.isoformat(),
        "baseline_revision_id": baseline_revision_id,
        "new_revision_id": new_revision_id,
        "new_version": new_version,
        "model": model,
        "case_count": len(cases),
        "t_count": len(target_cases),
        "r_count": len(reference_cases),
        "skipped_count": skipped_count,
        "replay_error_count": sum(
            1
            for case in cases
            if str(case.skip_reason or "").startswith("当前版本回放")
        ),
        "fluctuation_ignored_count": sum(
            1
            for case in cases
            if case.skip_reason == CURRENT_FLUCTUATION_SKIP_REASON
        ),
        "fixed_count": check.fixed_count if check else 0,
        "broken_count": check.broken_count if check else 0,
        "weighted_fixed_score": (
            check.weighted_fixed_score if check else 0
        ),
        "fluctuation_count": check.fluctuation_count if check else 0,
        "check_passed": bool(check and check.passed),
        "check_status": (
            "passed" if check and check.passed
            else "failed" if check
            else None
        ),
        "model_calls": model_calls,
        "confirm_replay_count": confirm_replay_count,
        "optimizer_calls": len(attempts),
        "attempts": len(attempts),
        "error": error,
        "note": note,
        "dry_run": dry_run,
        "run_id": run_id,
    }


def _persist_run(
    adapter: Any,
    *,
    prompt_key: str,
    outcome: str,
    started_at: datetime,
    finished_at: datetime,
    window_start: datetime,
    baseline_revision_id: Optional[int],
    new_revision_id: Optional[int],
    model: str,
    cases: Sequence[OptimizationCase],
    attempts: Sequence[Mapping[str, Any]],
    error: Optional[str],
    dry_run: bool,
) -> Optional[int]:
    if dry_run:
        return None
    row = adapter.prompt_optimization.record_run(
        prompt_key=prompt_key,
        outcome=outcome,
        started_at=started_at,
        finished_at=finished_at,
        window_start=window_start,
        baseline_revision_id=baseline_revision_id,
        new_revision_id=new_revision_id,
        model=model,
        cases=[_case_payload(case) for case in cases],
        attempts=attempts,
        error=error,
    )
    return int(row["id"])


def _finish_without_revision(
    *,
    adapter: Any,
    prompt_key: str,
    outcome: str,
    started_at: datetime,
    window_start: datetime,
    baseline_revision_id: Optional[int],
    model: str,
    cases: Sequence[OptimizationCase],
    target_cases: Sequence[OptimizationCase],
    reference_cases: Sequence[OptimizationCase],
    skipped_count: int,
    model_calls: int,
    check: Optional[CheckResult],
    attempts: Sequence[Mapping[str, Any]],
    error: Optional[str],
    note: Optional[str],
    dry_run: bool,
    confirm_replay_count: int = 0,
) -> dict[str, Any]:
    finished_at = _utcnow()
    run_id = _persist_run(
        adapter,
        prompt_key=prompt_key,
        outcome=outcome,
        started_at=started_at,
        finished_at=finished_at,
        window_start=window_start,
        baseline_revision_id=baseline_revision_id,
        new_revision_id=None,
        model=model,
        cases=cases,
        attempts=attempts,
        error=error,
        dry_run=dry_run,
    )
    return _summary(
        prompt_key=prompt_key,
        outcome=outcome,
        started_at=started_at,
        finished_at=finished_at,
        window_start=window_start,
        baseline_revision_id=baseline_revision_id,
        new_revision_id=None,
        new_version=None,
        model=model,
        cases=cases,
        target_cases=target_cases,
        reference_cases=reference_cases,
        skipped_count=skipped_count,
        model_calls=model_calls,
        check=check,
        attempts=attempts,
        error=error,
        note=note,
        dry_run=dry_run,
        run_id=run_id,
        confirm_replay_count=confirm_replay_count,
    )


def _preview_result(
    *,
    prompt: PromptTemplate,
    prompt_key: str,
    started_at: datetime,
    window_start: datetime,
    baseline_revision_id: Optional[int],
    model: str,
    cases: Sequence[OptimizationCase],
    precheck_will_finish: bool,
    skipped_count: int,
    estimated_replay_count: int,
) -> dict[str, Any]:
    feedback_low = sum(
        1
        for case in cases
        if case.origin == CASE_ORIGIN_FEEDBACK and case.direction == DIRECTION_UP
    )
    feedback_high = sum(
        1
        for case in cases
        if case.origin == CASE_ORIGIN_FEEDBACK and case.direction != DIRECTION_UP
    )
    submissions = sum(
        1 for case in cases if case.origin == CASE_ORIGIN_SUBMISSION
    )
    in_window = sum(1 for case in cases if case.event_at >= window_start)
    summary = _summary(
        prompt_key=prompt_key,
        outcome=OUTCOME_PREVIEW,
        started_at=started_at,
        finished_at=_utcnow(),
        window_start=window_start,
        baseline_revision_id=baseline_revision_id,
        new_revision_id=None,
        new_version=None,
        model=model,
        cases=cases,
        target_cases=[],
        reference_cases=[],
        skipped_count=skipped_count,
        model_calls=0,
        check=None,
        attempts=[],
        error=None,
        note=None,
        dry_run=True,
        run_id=None,
    )
    summary.update(
        {
            "prompt_version": prompt.version,
            "preview_feedback_low_count": feedback_low,
            "preview_feedback_high_count": feedback_high,
            "preview_submission_count": submissions,
            "preview_in_window_count": in_window,
            "preview_precheck_will_finish": precheck_will_finish,
            "preview_estimated_skipped_count": skipped_count,
            "preview_estimated_replay_count": estimated_replay_count,
            "preview_min_model_calls": (
                0
                if precheck_will_finish
                else estimated_replay_count + 1
            ),
        }
    )
    return summary


def _save_revision(
    *,
    adapter: Any,
    prompt_key: str,
    candidate_content: str,
    prompt: PromptTemplate,
    note: Optional[str],
    started_at: datetime,
    window_start: datetime,
    baseline_revision_id: Optional[int],
    model: str,
    cases: Sequence[OptimizationCase],
    target_cases: Sequence[OptimizationCase],
    reference_cases: Sequence[OptimizationCase],
    skipped_count: int,
    model_calls: int,
    check: CheckResult,
    attempts: Sequence[Mapping[str, Any]],
    dry_run: bool,
    confirm_replay_count: int = 0,
) -> dict[str, Any]:
    if dry_run:
        finished_at = _utcnow()
        return _summary(
            prompt_key=prompt_key,
            outcome=OUTCOME_REVISION_CREATED,
            started_at=started_at,
            finished_at=finished_at,
            window_start=window_start,
            baseline_revision_id=baseline_revision_id,
            new_revision_id=None,
            new_version=None,
            model=model,
            cases=cases,
            target_cases=target_cases,
            reference_cases=reference_cases,
            skipped_count=skipped_count,
            model_calls=model_calls,
            check=check,
            attempts=attempts,
            error=None,
            note=note,
            dry_run=True,
            run_id=None,
            confirm_replay_count=confirm_replay_count,
        )

    baseline = None
    if prompt.source == "file" and prompt.content.strip():
        baseline = {
            "revision": prompt.revision or 1,
            "content": prompt.content,
        }
    try:
        saved = adapter.prompt_optimization.record_run_with_revision(
            prompt_key=prompt_key,
            content=candidate_content,
            note=note,
            expected_revision_id=prompt.revision_id,
            baseline=baseline,
            baseline_revision_id=baseline_revision_id,
            started_at=started_at,
            finished_at=_utcnow(),
            window_start=window_start,
            model=model,
            cases=[_case_payload(case) for case in cases],
            attempts=attempts,
        )
    except PromptRevisionConflictError as exc:
        return _finish_without_revision(
            adapter=adapter,
            prompt_key=prompt_key,
            outcome=OUTCOME_ERROR,
            started_at=started_at,
            window_start=window_start,
            baseline_revision_id=baseline_revision_id,
            model=model,
            cases=cases,
            target_cases=target_cases,
            reference_cases=reference_cases,
            skipped_count=skipped_count,
            model_calls=model_calls,
            check=check,
            attempts=attempts,
            error=f"运行期间提示词被手动保存，已放弃本次结果：{exc}",
            note=note,
            dry_run=False,
            confirm_replay_count=confirm_replay_count,
        )
    except Exception as exc:
        return _finish_without_revision(
            adapter=adapter,
            prompt_key=prompt_key,
            outcome=OUTCOME_ERROR,
            started_at=started_at,
            window_start=window_start,
            baseline_revision_id=baseline_revision_id,
            model=model,
            cases=cases,
            target_cases=target_cases,
            reference_cases=reference_cases,
            skipped_count=skipped_count,
            model_calls=model_calls,
            check=check,
            attempts=attempts,
            error=f"写入新版本失败：{exc}",
            note=note,
            dry_run=False,
            confirm_replay_count=confirm_replay_count,
        )

    revision = saved["revision"]
    run = saved["run"]
    finished_at = _as_datetime(run.get("finished_at"), _utcnow())
    return _summary(
        prompt_key=prompt_key,
        outcome=OUTCOME_REVISION_CREATED,
        started_at=started_at,
        finished_at=finished_at,
        window_start=window_start,
        baseline_revision_id=(
            int(run["baseline_revision_id"])
            if run.get("baseline_revision_id") is not None
            else baseline_revision_id
        ),
        new_revision_id=int(revision["id"]),
        new_version=f"v{int(revision['revision'])}",
        model=model,
        cases=cases,
        target_cases=target_cases,
        reference_cases=reference_cases,
        skipped_count=skipped_count,
        model_calls=model_calls,
        check=check,
        attempts=attempts,
        error=None,
        note=note,
        dry_run=False,
        run_id=int(run["id"]),
        confirm_replay_count=confirm_replay_count,
    )


def _record_unexpected_error(
    *,
    adapter: Any,
    prompt_key: str,
    started_at: datetime,
    error: Exception,
    dry_run: bool,
) -> dict[str, Any]:
    """Best-effort error row; a database failure must not block other prompts."""

    window_start = started_at - timedelta(days=OPTIMIZATION_LOOKBACK_DAYS)
    try:
        model = get_llm_step_config("prompt_optimizer").model
    except Exception:
        model = ""
    try:
        return _finish_without_revision(
            adapter=adapter,
            prompt_key=prompt_key,
            outcome=OUTCOME_ERROR,
            started_at=started_at,
            window_start=window_start,
            baseline_revision_id=None,
            model=model,
            cases=[],
            target_cases=[],
            reference_cases=[],
            skipped_count=0,
            model_calls=0,
            check=None,
            attempts=[],
            error=str(error) or error.__class__.__name__,
            note=None,
            dry_run=dry_run,
        )
    except Exception as record_error:
        log_error(WORKER_NAME, f"{prompt_key} run-record", record_error)
        finished_at = _utcnow()
        return _summary(
            prompt_key=prompt_key,
            outcome=OUTCOME_ERROR,
            started_at=started_at,
            finished_at=finished_at,
            window_start=window_start,
            baseline_revision_id=None,
            new_revision_id=None,
            new_version=None,
            model=model,
            cases=[],
            target_cases=[],
            reference_cases=[],
            skipped_count=0,
            model_calls=0,
            check=None,
            attempts=[],
            error=str(error) or error.__class__.__name__,
            note=None,
            dry_run=dry_run,
        )


def _optimize_prompt(
    *,
    adapter: Any,
    prompt_key: str,
    started_at: datetime,
    dry_run: bool,
    replay: ReplayFn,
    optimizer: OptimizerFn,
    concurrency: int,
) -> dict[str, Any]:
    prompt = load_active_prompt(prompt_key, adapter)
    if not prompt.content.strip():
        raise ValueError(f"提示词 {prompt_key} 内容为空，无法优化")
    model = get_llm_step_config("prompt_optimizer").model
    lookback_start = started_at - timedelta(days=OPTIMIZATION_LOOKBACK_DAYS)
    window_start = max(
        lookback_start,
        _as_datetime(prompt.updated_at, lookback_start),
    )
    baseline_revision_id = prompt.revision_id

    cases = _collect_cases(
        adapter,
        prompt_key,
        since=lookback_start,
        limit=OPTIMIZATION_MAX_CASES,
    )
    precheck_will_finish = _precheck_skips_replay(cases, window_start)
    if precheck_will_finish:
        if dry_run:
            return _preview_result(
                prompt=prompt,
                prompt_key=prompt_key,
                started_at=started_at,
                window_start=window_start,
                baseline_revision_id=baseline_revision_id,
                model=model,
                cases=cases,
                precheck_will_finish=True,
                skipped_count=0,
                estimated_replay_count=0,
            )
        return _finish_without_revision(
            adapter=adapter,
            prompt_key=prompt_key,
            outcome=OUTCOME_NO_CORRECTION,
            started_at=started_at,
            window_start=window_start,
            baseline_revision_id=baseline_revision_id,
            model=model,
            cases=cases,
            target_cases=[],
            reference_cases=[],
            skipped_count=0,
            model_calls=0,
            check=None,
            attempts=[],
            error=None,
            note=None,
            dry_run=dry_run,
        )

    if dry_run:
        skipped_count = 0
        for case in cases:
            row = adapter.news_summaries.fetch_prompt_test_article(case.article_id)
            if row is None or not str(row.get("content_markdown") or "").strip():
                skipped_count += 1
        return _preview_result(
            prompt=prompt,
            prompt_key=prompt_key,
            started_at=started_at,
            window_start=window_start,
            baseline_revision_id=baseline_revision_id,
            model=model,
            cases=cases,
            precheck_will_finish=False,
            skipped_count=skipped_count,
            estimated_replay_count=len(cases) - skipped_count,
        )

    model_calls = 0
    confirm_replay_count = 0
    skipped_count = 0
    rows: dict[str, Mapping[str, Any]] = {}
    replayable_cases: list[OptimizationCase] = []
    for case in cases:
        row = adapter.news_summaries.fetch_prompt_test_article(case.article_id)
        if row is None:
            case.skip_reason = "文章不在 news_summaries"
            case.bucket = CASE_BUCKET_IGNORED
            skipped_count += 1
            continue
        if not str(row.get("content_markdown") or "").strip():
            case.skip_reason = "文章没有正文"
            case.bucket = CASE_BUCKET_IGNORED
            skipped_count += 1
            continue
        rows[case.article_id] = row
        case.summary = str(row.get("llm_summary") or case.summary)
        case.content_excerpt = str(row.get("content_markdown") or "")[:600]
        replayable_cases.append(case)

    current_results = _replay_cases(
        replayable_cases,
        rows=rows,
        prompt_key=prompt_key,
        content=prompt.content,
        replay=replay,
        concurrency=concurrency,
    )
    model_calls += len(replayable_cases)
    confirm_candidates: list[OptimizationCase] = []
    for case in replayable_cases:
        result = current_results[case.article_id]
        if result.error is not None:
            case.replay_error = result.error
            case.skip_reason = f"当前版本回放失败：{result.error}"
            case.bucket = CASE_BUCKET_IGNORED
            skipped_count += 1
            continue
        score = result.score
        if score is None:
            case.current_score = None
            case.replay_error = "模型未返回可解析的分数"
            case.skip_reason = "当前版本回放未解析出分数"
            case.bucket = CASE_BUCKET_IGNORED
            skipped_count += 1
            continue
        case.current_score = score
        case.current_satisfied = case_satisfied(
            case.direction,
            case.threshold,
            score,
        )
        if case.current_satisfied:
            case.bucket = CASE_BUCKET_R
        elif case.event_at >= window_start:
            # 窗口内首打不达标：进入 T 之前先用当前版本再打一次确认。
            confirm_candidates.append(case)
        else:
            case.bucket = CASE_BUCKET_IGNORED

    # 进入 T 前的二次确认：两次都不达标才进 T，一次达标一次不达标视为分数波动。
    current_confirm_results = _replay_cases(
        confirm_candidates,
        rows=rows,
        prompt_key=prompt_key,
        content=prompt.content,
        replay=replay,
        concurrency=concurrency,
    )
    model_calls += len(confirm_candidates)
    confirm_replay_count += len(confirm_candidates)
    for case in confirm_candidates:
        result = current_confirm_results[case.article_id]
        if result.error is not None:
            case.replay_error = result.error
            case.skip_reason = f"当前版本回放失败：{result.error}"
            case.bucket = CASE_BUCKET_IGNORED
            skipped_count += 1
            continue
        second = result.score
        if second is None:
            case.replay_error = "模型未返回可解析的分数"
            case.skip_reason = "当前版本回放未解析出分数"
            case.bucket = CASE_BUCKET_IGNORED
            skipped_count += 1
            continue
        case.current_confirm_score = second
        if case_satisfied(case.direction, case.threshold, second):
            case.skip_reason = CURRENT_FLUCTUATION_SKIP_REASON
            case.bucket = CASE_BUCKET_IGNORED
            continue
        case.bucket = CASE_BUCKET_T

    target_cases = [case for case in cases if case.bucket == CASE_BUCKET_T]
    reference_cases = [case for case in cases if case.bucket == CASE_BUCKET_R]
    if not target_cases:
        return _finish_without_revision(
            adapter=adapter,
            prompt_key=prompt_key,
            outcome=OUTCOME_NO_CORRECTION,
            started_at=started_at,
            window_start=window_start,
            baseline_revision_id=baseline_revision_id,
            model=model,
            cases=cases,
            target_cases=[],
            reference_cases=reference_cases,
            skipped_count=skipped_count,
            model_calls=model_calls,
            check=None,
            attempts=[],
            error=None,
            note=None,
            dry_run=dry_run,
            confirm_replay_count=confirm_replay_count,
        )

    attempts: list[dict[str, Any]] = []
    failure_feedback: Optional[str] = None
    last_check: Optional[CheckResult] = None
    for attempt_number in range(1, OPTIMIZATION_MAX_ATTEMPTS + 1):
        model_calls += 1
        try:
            decision = optimizer(
                prompt_key,
                prompt.content,
                [_optimizer_case_payload(case) for case in target_cases],
                [
                    _optimizer_case_payload(case)
                    for case in reference_cases[:10]
                ],
                failure_feedback,
            )
        except PromptOptimizerResponseError as exc:
            message = str(exc) or exc.__class__.__name__
            attempts.append(
                {
                    "attempt": attempt_number,
                    "decision": "invalid_output",
                    "note": None,
                    "edits": [],
                    "apply_error": None,
                    "check": None,
                    "error": message,
                }
            )
            failure_feedback = f"上一轮模型输出不合格：{message}"
            if attempt_number < OPTIMIZATION_MAX_ATTEMPTS:
                continue
            return _finish_without_revision(
                adapter=adapter,
                prompt_key=prompt_key,
                outcome=OUTCOME_CHECK_FAILED,
                started_at=started_at,
                window_start=window_start,
                baseline_revision_id=baseline_revision_id,
                model=model,
                cases=cases,
                target_cases=target_cases,
                reference_cases=reference_cases,
                skipped_count=skipped_count,
                model_calls=model_calls,
                check=None,
                attempts=attempts,
                error=f"优化模型输出连续不合格：{message}",
                note=None,
                dry_run=dry_run,
                confirm_replay_count=confirm_replay_count,
            )
        except Exception as exc:
            attempts.append(
                {
                    "attempt": attempt_number,
                    "decision": "error",
                    "note": None,
                    "edits": [],
                    "apply_error": None,
                    "check": None,
                    "error": str(exc) or exc.__class__.__name__,
                }
            )
            return _finish_without_revision(
                adapter=adapter,
                prompt_key=prompt_key,
                outcome=OUTCOME_ERROR,
                started_at=started_at,
                window_start=window_start,
                baseline_revision_id=baseline_revision_id,
                model=model,
                cases=cases,
                target_cases=target_cases,
                reference_cases=reference_cases,
                skipped_count=skipped_count,
                model_calls=model_calls,
                check=None,
                attempts=attempts,
                error=f"优化模型调用失败：{exc}",
                note=None,
                dry_run=dry_run,
                confirm_replay_count=confirm_replay_count,
            )

        attempt_payload: dict[str, Any] = {
            "attempt": attempt_number,
            "decision": decision.decision,
            "note": decision.note,
            "edits": [
                {"old": edit.old, "new": edit.new}
                for edit in decision.edits
            ],
            "apply_error": None,
            "check": None,
            "error": None,
        }
        if decision.decision == DECISION_NO_CHANGE:
            attempts.append(attempt_payload)
            return _finish_without_revision(
                adapter=adapter,
                prompt_key=prompt_key,
                outcome=OUTCOME_MODEL_DECLINED,
                started_at=started_at,
                window_start=window_start,
                baseline_revision_id=baseline_revision_id,
                model=model,
                cases=cases,
                target_cases=target_cases,
                reference_cases=reference_cases,
                skipped_count=skipped_count,
                model_calls=model_calls,
                check=None,
                attempts=attempts,
                error=None,
                note=decision.note,
                dry_run=dry_run,
                confirm_replay_count=confirm_replay_count,
            )

        try:
            candidate_content = apply_prompt_edits(
                prompt.content,
                decision.edits,
            )
        except PromptEditError as exc:
            attempt_payload["apply_error"] = str(exc)
            attempts.append(attempt_payload)
            failure_feedback = f"上一轮改动无法套用：{exc}"
            if attempt_number < OPTIMIZATION_MAX_ATTEMPTS:
                continue
            return _finish_without_revision(
                adapter=adapter,
                prompt_key=prompt_key,
                outcome=OUTCOME_CHECK_FAILED,
                started_at=started_at,
                window_start=window_start,
                baseline_revision_id=baseline_revision_id,
                model=model,
                cases=cases,
                target_cases=target_cases,
                reference_cases=reference_cases,
                skipped_count=skipped_count,
                model_calls=model_calls,
                check=None,
                attempts=attempts,
                error=str(exc),
                note=decision.note,
                dry_run=dry_run,
                confirm_replay_count=confirm_replay_count,
            )

        candidate_cases = [
            case
            for case in target_cases + reference_cases
            if case.article_id in rows
        ]
        candidate_scores: dict[str, Optional[int]] = {
            case.article_id: None
            for case in target_cases + reference_cases
            if case.article_id not in rows
        }
        # 上一轮尝试的确认分数与结论不能带进本轮。
        for case in target_cases + reference_cases:
            case.candidate_confirm_score = None
            case.fix_state = None
        candidate_results = _replay_cases(
            candidate_cases,
            rows=rows,
            prompt_key=prompt_key,
            content=candidate_content,
            replay=replay,
            concurrency=concurrency,
        )
        model_calls += len(candidate_cases)
        for case in target_cases + reference_cases:
            result = candidate_results.get(case.article_id)
            if result is None:
                continue
            if result.error is not None:
                case.replay_error = result.error
            score = result.score
            candidate_scores[case.article_id] = score
            case.candidate_score = score
            case.candidate_satisfied = case_satisfied(
                case.direction,
                case.threshold,
                score,
            )

        # 算修好 / 改坏前的二次确认：只有第一次回放同时满足「达标（或不达标）+
        # 变动过容差」的案例才补打一次候选版本，两次都满足才算数。
        confirm_cases = [
            case
            for case in target_cases + reference_cases
            if case.article_id in rows
            and case.candidate_score is not None
            and (
                (
                    case.bucket == CASE_BUCKET_T
                    and case_fixed_probe(case, case.candidate_score)
                )
                or (
                    case.bucket == CASE_BUCKET_R
                    and case_broken_probe(case, case.candidate_score)
                )
            )
        ]
        candidate_confirm_results = _replay_cases(
            confirm_cases,
            rows=rows,
            prompt_key=prompt_key,
            content=candidate_content,
            replay=replay,
            concurrency=concurrency,
        )
        model_calls += len(confirm_cases)
        confirm_replay_count += len(confirm_cases)
        candidate_confirm_scores: dict[str, Optional[int]] = {}
        for case in confirm_cases:
            result = candidate_confirm_results[case.article_id]
            if result.error is not None:
                case.replay_error = result.error
            second = result.score
            if second is None:
                # 确认回放未解析出分数：计入未解析，整次检查不通过。
                candidate_confirm_scores[case.article_id] = None
                continue
            case.candidate_confirm_score = second
            candidate_confirm_scores[case.article_id] = second
            case.fix_state = resolve_case_state(
                case,
                case.candidate_score,
                second,
                confirmed=True,
            )
        for case in target_cases + reference_cases:
            if (
                case.fix_state is None
                and case.article_id in rows
                and case.candidate_score is not None
            ):
                case.fix_state = resolve_case_state(
                    case,
                    case.candidate_score,
                    None,
                    confirmed=False,
                )

        check = evaluate_candidate_check(
            target_cases,
            reference_cases,
            candidate_scores,
            candidate_confirm_scores,
        )
        last_check = check
        attempt_payload["check"] = {
            "passed": check.passed,
            "fixed_count": check.fixed_count,
            "broken_count": check.broken_count,
            "weighted_fixed_score": check.weighted_fixed_score,
            "fluctuation_count": check.fluctuation_count,
            "t_total": check.t_total,
            "r_total": check.r_total,
            "unparseable_count": check.unparseable_count,
            "failures": list(check.failures),
        }
        attempts.append(attempt_payload)
        if check.passed:
            note = decision.note or (
                f"自动优化：修好 {check.fixed_count}/{check.t_total} 条，"
                f"改坏 {check.broken_count}/{check.r_total} 条"
            )
            return _save_revision(
                adapter=adapter,
                prompt_key=prompt_key,
                candidate_content=candidate_content,
                prompt=prompt,
                note=note[:200],
                started_at=started_at,
                window_start=window_start,
                baseline_revision_id=baseline_revision_id,
                model=model,
                cases=cases,
                target_cases=target_cases,
                reference_cases=reference_cases,
                skipped_count=skipped_count,
                model_calls=model_calls,
                check=check,
                attempts=attempts,
                dry_run=dry_run,
                confirm_replay_count=confirm_replay_count,
            )
        failure_feedback = _failure_feedback(
            check,
            target_cases,
            reference_cases,
        )

    return _finish_without_revision(
        adapter=adapter,
        prompt_key=prompt_key,
        outcome=OUTCOME_CHECK_FAILED,
        started_at=started_at,
        window_start=window_start,
        baseline_revision_id=baseline_revision_id,
        model=model,
        cases=cases,
        target_cases=target_cases,
        reference_cases=reference_cases,
        skipped_count=skipped_count,
        model_calls=model_calls,
        check=last_check,
        attempts=attempts,
        error="；".join(last_check.failures) if last_check else None,
        note=None,
        dry_run=dry_run,
        confirm_replay_count=confirm_replay_count,
    )


def run(
    prompt_keys: Optional[Sequence[str]] = None,
    *,
    dry_run: bool = False,
    concurrency: Optional[int] = None,
    adapter: Any = None,
    replay: Optional[ReplayFn] = None,
    optimizer: Optional[OptimizerFn] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Run one nightly optimization pass over one or more scoring prompts."""

    settings = get_settings()
    workers = max(1, concurrency or settings.default_concurrency or 5)
    resolved_adapter = adapter or get_adapter()
    keys = list(prompt_keys) if prompt_keys else list(DEFAULT_PROMPT_KEYS)
    unknown = [key for key in keys if key not in SCORING_PROMPT_KEYS]
    if unknown:
        raise ValueError(f"不支持的提示词：{', '.join(unknown)}")
    replay_fn = replay or _default_replay
    optimizer_fn = optimizer or _default_optimizer
    started_at = now or _utcnow()
    items: list[dict[str, Any]] = []

    for prompt_key in keys:
        log_info(WORKER_NAME, f"开始处理 {prompt_key}")
        try:
            summary = _optimize_prompt(
                adapter=resolved_adapter,
                prompt_key=prompt_key,
                started_at=started_at,
                dry_run=dry_run,
                replay=replay_fn,
                optimizer=optimizer_fn,
                concurrency=workers,
            )
        except Exception as exc:
            log_error(WORKER_NAME, prompt_key, exc)
            summary = _record_unexpected_error(
                adapter=resolved_adapter,
                prompt_key=prompt_key,
                started_at=started_at,
                error=exc,
                dry_run=dry_run,
            )
        items.append(summary)
        log_info(
            WORKER_NAME,
            f"{prompt_key}: {summary['outcome']} "
            f"cases={summary['case_count']} T={summary['t_count']} "
            f"R={summary['r_count']} model_calls={summary['model_calls']}",
        )

    failed = sum(1 for item in items if item["outcome"] == OUTCOME_ERROR)
    outcome_counts: dict[str, int] = {}
    for item in items:
        outcome = str(item.get("outcome") or "")
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
    # 按结果分别计数，未通过检查不算成功（退出码规则不变：只有 error 非零）。
    log_info(
        WORKER_NAME,
        "result: "
        + " ".join(
            f"{outcome}={outcome_counts.get(outcome, 0)}"
            for outcome in (
                OUTCOME_REVISION_CREATED,
                OUTCOME_NO_CORRECTION,
                OUTCOME_MODEL_DECLINED,
                OUTCOME_CHECK_FAILED,
                OUTCOME_ERROR,
                OUTCOME_PREVIEW,
            )
        ),
    )
    return {
        "items": items,
        "had_error": failed > 0,
        "dry_run": dry_run,
        "started_at": started_at.isoformat(),
        "finished_at": _utcnow().isoformat(),
    }


__all__ = [
    "DEFAULT_PROMPT_KEYS",
    "OUTCOME_PREVIEW",
    "WORKER_NAME",
    "run",
]
