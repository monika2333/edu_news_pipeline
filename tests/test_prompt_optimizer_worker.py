from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from src.adapters.db_postgres_prompt_revisions import PromptRevisionConflictError
from src.adapters.llm_prompt_optimizer import (
    DECISION_CHANGE,
    DECISION_NO_CHANGE,
    PromptOptimizerDecision,
    PromptOptimizerResponseError,
)
from src.domain.prompt import PromptTemplate
from src.domain.prompt_edit import PromptEdit
from src.workers import prompt_optimizer as worker

NOW = datetime(2026, 10, 30, 0, 0, tzinfo=timezone.utc)


def _prompt(*, created_days_ago: int = 3) -> PromptTemplate:
    return PromptTemplate(
        key="external_positive",
        version="v3",
        content="BASE PROMPT",
        source="database",
        revision=3,
        revision_id=10,
        updated_at=NOW - timedelta(days=created_days_ago),
    )


def _article_row(article_id: str) -> dict[str, Any]:
    return {
        "article_id": article_id,
        "title": f"标题-{article_id}",
        "source": "测试来源",
        "llm_summary": "这是摘要",
        "content_markdown": "这是正文",
        "sentiment_label": "positive",
        "is_beijing_related": False,
    }


def _feedback_row(
    article_id: str,
    *,
    days_ago: int = 1,
    feedback_type: str = "too_low",
    score: float = 50,
    stored_score: Optional[float] = 40,
) -> dict[str, Any]:
    return {
        "article_id": article_id,
        "feedback_type": feedback_type,
        "original_score": score,
        "reason": "人工原因",
        "event_at": NOW - timedelta(days=days_ago),
        "prompt_key": "external_positive",
        "title": f"标题-{article_id}",
        "source": "测试来源",
        "summary": "这是摘要",
        "stored_score": stored_score,
    }


def _submission_row(
    article_id: str,
    *,
    days_ago: int = 1,
    stored_score: Optional[float] = 40,
) -> dict[str, Any]:
    return {
        "article_id": article_id,
        "event_at": NOW - timedelta(days=days_ago),
        "report_types": ["zongbao", "wanbao"],
        "title": f"标题-{article_id}",
        "source": "测试来源",
        "summary": "这是摘要",
        "stored_score": stored_score,
    }


class _FakeNewsSummaries:
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows

    def fetch_prompt_test_article(self, article_id: str) -> Optional[dict[str, Any]]:
        return self.rows.get(article_id)


class _FakeOptimization:
    def __init__(
        self,
        *,
        feedback: Optional[list[dict[str, Any]]] = None,
        submissions: Optional[list[dict[str, Any]]] = None,
        conflict: bool = False,
        collect_error_keys: Optional[set[str]] = None,
    ) -> None:
        self.feedback = feedback or []
        self.submissions = submissions or []
        self.conflict = conflict
        self.collect_error_keys = collect_error_keys or set()
        self.collect_calls: list[str] = []
        self.runs: list[dict[str, Any]] = []
        self.saved: list[dict[str, Any]] = []

    def collect_cases(self, prompt_key: str, *, since: datetime, limit: int):
        self.collect_calls.append(prompt_key)
        if prompt_key in self.collect_error_keys:
            raise RuntimeError(f"collect failed: {prompt_key}")
        return list(self.feedback), list(self.submissions)

    def record_run(self, **kwargs: Any) -> dict[str, Any]:
        run = {"id": 100 + len(self.runs), **kwargs}
        self.runs.append(run)
        return run

    def record_run_with_revision(self, **kwargs: Any) -> dict[str, Any]:
        if self.conflict:
            raise PromptRevisionConflictError("提示词已在别处被修改（当前版本：v9）")
        saved = {"id": 77, **kwargs}
        self.saved.append(saved)
        run = {
            "id": 200,
            "prompt_key": kwargs["prompt_key"],
            "outcome": "revision_created",
            "started_at": kwargs["started_at"],
            "finished_at": kwargs["finished_at"],
            "window_start": kwargs["window_start"],
            "baseline_revision_id": kwargs.get("baseline_revision_id"),
            "new_revision_id": 88,
            "model": kwargs["model"],
            "cases": kwargs["cases"],
            "attempts": kwargs["attempts"],
            "error": None,
        }
        self.runs.append(run)
        return {
            "run": run,
            "revision": {
                "id": 88,
                "revision": 4,
                "content": kwargs["content"],
                "note": kwargs["note"],
                "created_by_user_id": None,
            },
        }


class _Adapter:
    def __init__(self, rows, optimization: _FakeOptimization) -> None:
        self.news_summaries = _FakeNewsSummaries(rows)
        self.prompt_optimization = optimization


def _patch_prompt(
    monkeypatch: pytest.MonkeyPatch,
    prompt: Optional[PromptTemplate] = None,
) -> None:
    resolved = prompt or _prompt()
    monkeypatch.setattr(
        worker,
        "load_active_prompt",
        lambda key, adapter: SimpleNamespace(**{**resolved.__dict__, "key": key}),
    )
    monkeypatch.setattr(
        worker,
        "get_llm_step_config",
        lambda step: SimpleNamespace(model="optimizer-model"),
    )


def _no_change_optimizer(*args: Any, **kwargs: Any) -> PromptOptimizerDecision:
    return PromptOptimizerDecision(
        decision=DECISION_NO_CHANGE,
        edits=(),
        note="证据不足",
        raw_output='{"decision":"no_change"}',
        model="optimizer-model",
    )


def _change_optimizer(content: str) -> PromptOptimizerDecision:
    return PromptOptimizerDecision(
        decision=DECISION_CHANGE,
        edits=(PromptEdit(old="BASE", new=content),),
        note="补充一条高校风险规则",
        raw_output='{"decision":"change"}',
        model="optimizer-model",
    )


def test_precheck_skips_replay_and_optimizer(monkeypatch) -> None:
    _patch_prompt(monkeypatch)
    optimization = _FakeOptimization(
        submissions=[_submission_row("s1", stored_score=80)],
    )
    adapter = _Adapter({"s1": _article_row("s1")}, optimization)
    replay_calls: list[str] = []
    optimizer_calls: list[str] = []

    def replay(row, key, content):
        replay_calls.append(content)
        return 80

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=replay,
        optimizer=lambda *args: optimizer_calls.append("x") or _no_change_optimizer(),
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "no_correction"
    assert replay_calls == []
    assert optimizer_calls == []
    assert item["model_calls"] == 0
    assert len(optimization.runs) == 1
    assert optimization.runs[0]["outcome"] == "no_correction"


def test_current_replay_splits_target_reference_and_ignored(monkeypatch) -> None:
    _patch_prompt(monkeypatch)
    feedback = [
        _feedback_row("in-window", days_ago=1),
        _feedback_row("r-outside", days_ago=10),
        _feedback_row("ignored", days_ago=10),
    ]
    optimization = _FakeOptimization(feedback=feedback)
    rows = {item["article_id"]: _article_row(item["article_id"]) for item in feedback}
    adapter = _Adapter(rows, optimization)
    current_scores = {"in-window": 30, "r-outside": 70, "ignored": 30}
    optimizer_calls: list[Any] = []

    def optimizer(*args):
        optimizer_calls.append(args)
        return _no_change_optimizer()

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=lambda row, key, content: current_scores[row["article_id"]],
        optimizer=optimizer,
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "model_declined"
    assert item["case_count"] == 3
    assert item["t_count"] == 1
    assert item["r_count"] == 1
    # 3 次首打 + 1 次进 T 前确认（只有窗口内的 in-window）+ 1 次优化模型。
    assert item["model_calls"] == 5
    assert item["confirm_replay_count"] == 1
    cases = optimization.runs[0]["cases"]
    by_id = {case["article_id"]: case for case in cases}
    assert by_id["in-window"]["bucket"] == "T"
    assert by_id["r-outside"]["bucket"] == "R"
    assert by_id["ignored"]["bucket"] == "ignored"
    assert len(optimizer_calls) == 1
    assert optimization.saved == []


def test_concurrent_replay_matches_serial_order_and_actually_overlaps(
    monkeypatch,
) -> None:
    _patch_prompt(monkeypatch)
    feedback = [_feedback_row(f"c{index}") for index in range(4)]
    delays = {"c0": 0.08, "c1": 0.06, "c2": 0.04, "c3": 0.02}

    def run_once(concurrency: int):
        optimization = _FakeOptimization(feedback=feedback)
        rows = {
            item["article_id"]: _article_row(item["article_id"])
            for item in feedback
        }
        adapter = _Adapter(rows, optimization)
        lock = threading.Lock()
        state = {"active": 0, "current": 0, "candidate": 0}

        def replay(row, key, content):
            article_id = row["article_id"]
            phase = "candidate" if "CAND" in content else "current"
            with lock:
                state["active"] += 1
                state[phase] = max(state[phase], state["active"])
            try:
                time.sleep(delays[article_id])
                if phase == "candidate":
                    return 80
                return 20 if article_id in {"c0", "c2"} else 80
            finally:
                with lock:
                    state["active"] -= 1

        result = worker.run(
            ["external_positive"],
            concurrency=concurrency,
            adapter=adapter,
            replay=replay,
            optimizer=lambda *args: _change_optimizer("CAND"),
            now=NOW,
        )
        return result, optimization, state

    serial_result, serial_opt, serial_peaks = run_once(1)
    parallel_result, parallel_opt, parallel_peaks = run_once(4)

    serial_item = serial_result["items"][0]
    parallel_item = parallel_result["items"][0]
    assert serial_peaks["current"] == 1
    assert serial_peaks["candidate"] == 1
    assert parallel_peaks["current"] > 1
    assert parallel_peaks["candidate"] > 1

    serial_cases = serial_opt.runs[0]["cases"]
    parallel_cases = parallel_opt.runs[0]["cases"]
    assert serial_cases == parallel_cases
    assert [
        (case["article_id"], case["bucket"])
        for case in serial_cases
        if case["bucket"] in {"T", "R"}
    ] == [
        (case["article_id"], case["bucket"])
        for case in parallel_cases
        if case["bucket"] in {"T", "R"}
    ]
    assert serial_opt.runs[0]["attempts"] == parallel_opt.runs[0]["attempts"]
    assert serial_opt.saved[0]["content"] == parallel_opt.saved[0]["content"]
    for key in (
        "outcome",
        "case_count",
        "t_count",
        "r_count",
        "fixed_count",
        "broken_count",
        "check_passed",
        "model_calls",
    ):
        assert serial_item[key] == parallel_item[key]
    assert serial_item["outcome"] == "revision_created"


def test_current_replay_errors_are_ignored_not_counted_as_target(monkeypatch) -> None:
    _patch_prompt(monkeypatch)
    feedback = [
        _feedback_row("real"),
        _feedback_row("flaky"),
        _feedback_row("unparsed"),
    ]
    optimization = _FakeOptimization(feedback=feedback)
    rows = {item["article_id"]: _article_row(item["article_id"]) for item in feedback}
    adapter = _Adapter(rows, optimization)
    optimizer_cases: list[list[dict[str, Any]]] = []
    candidate_calls: list[str] = []

    def replay(row, key, content):
        article_id = row["article_id"]
        if content == "BASE PROMPT":
            if article_id == "flaky":
                raise RuntimeError("upstream 502")
            if article_id == "unparsed":
                return None
            return 45
        candidate_calls.append(article_id)
        return 45 if article_id == "real" else 70

    def optimizer(key, content, cases, references, failure_feedback):
        optimizer_cases.append(cases)
        return _change_optimizer("CAND")

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=replay,
        optimizer=optimizer,
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "check_failed"
    assert item["t_count"] == 1
    assert item["fixed_count"] == 0
    assert item["skipped_count"] == 2
    assert item["replay_error_count"] == 2
    assert item["new_revision_id"] is None
    assert optimization.saved == []
    assert all(len(cases) == 1 for cases in optimizer_cases)
    assert candidate_calls == ["real", "real"]

    cases = {case["article_id"]: case for case in optimization.runs[-1]["cases"]}
    assert cases["real"]["bucket"] == "T"
    assert cases["flaky"]["bucket"] == "ignored"
    assert "502" in cases["flaky"]["skip_reason"]
    assert cases["unparsed"]["bucket"] == "ignored"
    assert "未解析" in cases["unparsed"]["skip_reason"]


def test_empty_target_set_does_not_call_optimizer(monkeypatch) -> None:
    _patch_prompt(monkeypatch)
    optimization = _FakeOptimization(feedback=[_feedback_row("fixed")])
    adapter = _Adapter({"fixed": _article_row("fixed")}, optimization)
    optimizer_calls: list[Any] = []

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=lambda row, key, content: 80,
        optimizer=lambda *args: optimizer_calls.append(args),
        now=NOW,
    )

    assert result["items"][0]["outcome"] == "no_correction"
    assert result["items"][0]["t_count"] == 0
    assert optimizer_calls == []
    assert result["items"][0]["model_calls"] == 1


def test_failed_check_retries_once_with_failure_feedback_then_saves(monkeypatch) -> None:
    _patch_prompt(monkeypatch)
    feedback = [_feedback_row("t1"), _feedback_row("t2")]
    optimization = _FakeOptimization(feedback=feedback)
    rows = {item["article_id"]: _article_row(item["article_id"]) for item in feedback}
    rows["r1"] = _article_row("r1")
    # 参照案例是当前版本已达标、由存档来源进入 R 的例子。
    optimization.submissions = [_submission_row("r1", stored_score=80)]
    adapter = _Adapter(rows, optimization)
    optimizer_feedback: list[Optional[str]] = []

    def replay(row, key, content):
        article_id = row["article_id"]
        if content == "BASE PROMPT":
            return 20 if article_id != "r1" else 80
        if content.startswith("CAND1"):
            return 20 if article_id != "r1" else 80
        return 80

    def optimizer(key, content, cases, references, failure_feedback):
        optimizer_feedback.append(failure_feedback)
        return _change_optimizer("CAND1" if len(optimizer_feedback) == 1 else "CAND2")

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=replay,
        optimizer=optimizer,
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "revision_created"
    assert item["new_version"] == "v4"
    # 3 首打 + 2 进 T 前确认 + 优化×2 + 候选×2 轮（3+3）+ 第 2 轮修好确认×2。
    assert item["model_calls"] == 15
    assert item["confirm_replay_count"] == 4
    assert item["attempts"] == 2
    assert optimizer_feedback[0] is None
    assert optimizer_feedback[1] is not None
    assert "改坏" in optimizer_feedback[1]
    assert "仍未修好" in optimizer_feedback[1]
    assert optimization.saved[0]["expected_revision_id"] == 10
    assert optimization.saved[0]["content"] == "CAND2 PROMPT"
    assert optimization.saved[0]["note"] == "补充一条高校风险规则"
    assert optimization.saved[0]["baseline"] is None


def test_reference_damage_above_ten_percent_fails_even_below_fixed_count(
    monkeypatch,
) -> None:
    _patch_prompt(monkeypatch)
    feedback = [_feedback_row(f"t{index}") for index in range(3)]
    submissions = [
        _submission_row(f"r{index}", stored_score=80) for index in range(5)
    ]
    optimization = _FakeOptimization(feedback=feedback, submissions=submissions)
    rows = {
        item["article_id"]: _article_row(item["article_id"])
        for item in feedback + submissions
    }
    adapter = _Adapter(rows, optimization)

    def replay(row, key, content):
        article_id = row["article_id"]
        if content == "BASE PROMPT":
            return 20 if article_id.startswith("t") else 80
        if article_id in {"r0", "r1"}:
            return 20
        return 80

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=replay,
        optimizer=lambda *args: _change_optimizer("CAND"),
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "check_failed"
    assert item["fixed_count"] == 3
    assert item["broken_count"] == 2
    assert item["r_count"] == 5
    assert item["attempts"] == 2
    assert item["new_revision_id"] is None
    assert optimization.saved == []
    last_check = optimization.runs[-1]["attempts"][-1]["check"]
    assert last_check["passed"] is False
    assert any("允许最多 1 条" in failure for failure in last_check["failures"])
    assert not any("不少于" in failure for failure in last_check["failures"])


def test_two_failed_checks_stop_after_one_retry_without_version(monkeypatch) -> None:
    _patch_prompt(monkeypatch)
    feedback = [_feedback_row("t1")]
    optimization = _FakeOptimization(
        feedback=feedback,
        submissions=[_submission_row("r1", stored_score=80)],
    )
    rows = {"t1": _article_row("t1"), "r1": _article_row("r1")}
    adapter = _Adapter(rows, optimization)
    optimizer_calls: list[Any] = []

    def optimizer(*args):
        optimizer_calls.append(args)
        return _change_optimizer("CAND")

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=lambda row, key, content: 20 if row["article_id"] == "t1" else 80,
        optimizer=optimizer,
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "check_failed"
    assert item["attempts"] == 2
    assert len(optimizer_calls) == 2
    assert item["new_revision_id"] is None
    assert optimization.saved == []
    assert optimization.runs[-1]["outcome"] == "check_failed"
    assert "未通过检查" not in item["error"]
    assert "只修好" in item["error"]


@pytest.mark.parametrize("scenario", ["no_correction", "declined", "failed", "error"])
def test_non_dry_run_records_each_non_version_outcome(
    monkeypatch,
    scenario: str,
) -> None:
    _patch_prompt(monkeypatch)
    feedback = [] if scenario == "no_correction" else [_feedback_row("t1")]
    optimization = _FakeOptimization(feedback=feedback)
    adapter = _Adapter({"t1": _article_row("t1")}, optimization)

    def replay(row, key, content):
        return 30 if content == "BASE PROMPT" else 30

    def optimizer(*args):
        if scenario == "error":
            raise RuntimeError("network timeout")
        if scenario == "declined":
            return _no_change_optimizer()
        return _change_optimizer("CAND")

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=replay,
        optimizer=optimizer,
        now=NOW,
    )

    expected = {
        "no_correction": "no_correction",
        "declined": "model_declined",
        "failed": "check_failed",
        "error": "error",
    }[scenario]
    assert result["items"][0]["outcome"] == expected
    assert len(optimization.runs) == 1
    assert optimization.runs[0]["outcome"] == expected
    assert optimization.saved == []


def test_invalid_model_output_is_retried_with_reason(monkeypatch) -> None:
    _patch_prompt(monkeypatch)
    feedback = [_feedback_row("t1")]
    optimization = _FakeOptimization(feedback=feedback)
    adapter = _Adapter({"t1": _article_row("t1")}, optimization)
    feedbacks: list[Optional[str]] = []

    def optimizer(key, content, cases, references, failure_feedback):
        feedbacks.append(failure_feedback)
        if len(feedbacks) == 1:
            raise PromptOptimizerResponseError("优化模型没有返回 JSON 对象")
        return _change_optimizer("CAND")

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=lambda row, key, content: 20 if content == "BASE PROMPT" else 80,
        optimizer=optimizer,
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "revision_created"
    assert item["attempts"] == 2
    assert feedbacks[0] is None
    assert feedbacks[1] is not None
    assert "输出不合格" in feedbacks[1]
    assert "JSON" in feedbacks[1]
    assert optimization.runs[-1]["attempts"][0]["decision"] == "invalid_output"


def test_invalid_model_output_twice_ends_as_check_failed_without_version(
    monkeypatch,
) -> None:
    _patch_prompt(monkeypatch)
    optimization = _FakeOptimization(feedback=[_feedback_row("t1")])
    adapter = _Adapter({"t1": _article_row("t1")}, optimization)

    def optimizer(*args):
        raise PromptOptimizerResponseError("edits 必须是数组")

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=lambda row, key, content: 20,
        optimizer=optimizer,
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "check_failed"
    assert item["attempts"] == 2
    assert item["new_revision_id"] is None
    assert optimization.saved == []
    assert "连续不合格" in item["error"]


def test_transport_error_is_not_retried(monkeypatch) -> None:
    _patch_prompt(monkeypatch)
    optimization = _FakeOptimization(feedback=[_feedback_row("t1")])
    adapter = _Adapter({"t1": _article_row("t1")}, optimization)
    calls: list[Any] = []

    def optimizer(*args):
        calls.append(args)
        raise TimeoutError("request timed out")

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=lambda row, key, content: 20,
        optimizer=optimizer,
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "error"
    assert len(calls) == 1
    assert item["attempts"] == 1
    assert "优化模型调用失败" in item["error"]


def test_manual_save_during_run_records_error_and_does_not_overwrite(monkeypatch) -> None:
    _patch_prompt(monkeypatch)
    feedback = [_feedback_row("t1")]
    optimization = _FakeOptimization(feedback=feedback, conflict=True)
    adapter = _Adapter({"t1": _article_row("t1")}, optimization)

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=lambda row, key, content: 20 if content == "BASE PROMPT" else 80,
        optimizer=lambda *args: _change_optimizer("CAND"),
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "error"
    assert "手动保存" in item["error"]
    assert optimization.saved == []
    assert optimization.runs[-1]["outcome"] == "error"
    assert result["had_error"] is True


def test_persist_run_is_noop_in_dry_run() -> None:
    optimization = _FakeOptimization()
    adapter = _Adapter({}, optimization)
    run_id = worker._persist_run(
        adapter,
        prompt_key="external_positive",
        outcome="no_correction",
        started_at=NOW,
        finished_at=NOW,
        window_start=NOW - timedelta(days=30),
        baseline_revision_id=None,
        new_revision_id=None,
        model="optimizer-model",
        cases=[],
        attempts=[],
        error=None,
        dry_run=True,
    )

    assert run_id is None
    assert optimization.runs == []


def test_dry_run_previews_cases_without_calling_models(monkeypatch) -> None:
    _patch_prompt(monkeypatch)
    feedback = [
        _feedback_row("t-low", days_ago=1, feedback_type="too_low"),
        _feedback_row(
            "t-high",
            days_ago=1,
            feedback_type="too_high",
            score=80,
        ),
    ]
    submissions = [
        _submission_row("s-in", days_ago=1, stored_score=40),
        _submission_row("s-old", days_ago=10, stored_score=40),
    ]
    optimization = _FakeOptimization(feedback=feedback, submissions=submissions)
    no_body = _article_row("s-in")
    no_body["content_markdown"] = "   "
    rows = {
        "t-low": _article_row("t-low"),
        "s-in": no_body,
        "s-old": _article_row("s-old"),
    }
    adapter = _Adapter(rows, optimization)
    replay_calls: list[str] = []
    optimizer_calls: list[str] = []

    def replay(row, key, content):
        replay_calls.append(row["article_id"])
        return 80

    def optimizer(*args):
        optimizer_calls.append("called")
        return _change_optimizer("CAND")

    result = worker.run(
        ["external_positive"],
        dry_run=True,
        adapter=adapter,
        replay=replay,
        optimizer=optimizer,
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "preview"
    assert item["prompt_version"] == "v3"
    assert item["window_start"] == (NOW - timedelta(days=3)).isoformat()
    assert item["case_count"] == 4
    assert item["preview_feedback_low_count"] == 1
    assert item["preview_feedback_high_count"] == 1
    assert item["preview_submission_count"] == 2
    assert item["preview_in_window_count"] == 3
    assert item["preview_precheck_will_finish"] is False
    assert item["preview_estimated_skipped_count"] == 2
    assert item["preview_estimated_replay_count"] == 2
    assert item["preview_min_model_calls"] == 3
    assert item["model_calls"] == 0
    assert item["optimizer_calls"] == 0
    assert replay_calls == []
    assert optimizer_calls == []
    assert optimization.runs == []
    assert optimization.saved == []


def test_dry_run_precheck_stops_without_reading_articles_or_models(
    monkeypatch,
) -> None:
    _patch_prompt(monkeypatch)
    optimization = _FakeOptimization(
        submissions=[_submission_row("s1", stored_score=80)],
    )
    adapter = _Adapter({}, optimization)
    replay_calls: list[str] = []
    optimizer_calls: list[str] = []

    result = worker.run(
        ["external_positive"],
        dry_run=True,
        adapter=adapter,
        replay=lambda row, key, content: replay_calls.append("replay") or 80,
        optimizer=lambda *args: optimizer_calls.append("optimizer"),
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "preview"
    assert item["preview_precheck_will_finish"] is True
    assert item["preview_estimated_skipped_count"] == 0
    assert item["preview_estimated_replay_count"] == 0
    assert item["preview_min_model_calls"] == 0
    assert replay_calls == []
    assert optimizer_calls == []
    assert optimization.runs == []
    assert optimization.saved == []


def test_dry_run_prompt_error_does_not_block_the_next_prompt(monkeypatch) -> None:
    _patch_prompt(monkeypatch)
    optimization = _FakeOptimization(collect_error_keys={"external_positive"})
    adapter = _Adapter({}, optimization)

    result = worker.run(
        ["external_positive", "external_negative"],
        dry_run=True,
        adapter=adapter,
        replay=lambda row, key, content: 80,
        optimizer=lambda *args: _no_change_optimizer(),
        now=NOW,
    )

    assert [item["outcome"] for item in result["items"]] == [
        "error",
        "preview",
    ]
    assert result["had_error"] is True
    assert optimization.runs == []
    assert optimization.saved == []


def test_one_prompt_error_does_not_block_the_next_prompt(monkeypatch) -> None:
    _patch_prompt(monkeypatch)
    optimization = _FakeOptimization(collect_error_keys={"external_positive"})
    adapter = _Adapter({}, optimization)

    result = worker.run(
        ["external_positive", "external_negative"],
        adapter=adapter,
        replay=lambda row, key, content: 80,
        optimizer=lambda *args: _no_change_optimizer(),
        now=NOW,
    )

    assert [item["outcome"] for item in result["items"]] == [
        "error",
        "no_correction",
    ]
    assert result["had_error"] is True
    assert "collect failed" in result["items"][0]["error"]


def test_submission_uses_60_floor_not_a_lower_threshold(monkeypatch) -> None:
    _patch_prompt(monkeypatch)
    optimization = _FakeOptimization(
        submissions=[_submission_row("s1", stored_score=55)],
    )
    adapter = _Adapter({"s1": _article_row("s1")}, optimization)

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=lambda row, key, content: 55,
        optimizer=lambda *args: _no_change_optimizer(),
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "model_declined"
    assert item["t_count"] == 1
    case = optimization.runs[0]["cases"][0]
    assert case["threshold"] == 60
    assert case["bucket"] == "T"


def test_file_fallback_window_is_thirty_days(monkeypatch) -> None:
    _patch_prompt(
        monkeypatch,
        PromptTemplate(
            key="external_positive",
            version="v1",
            content="BASE PROMPT",
            source="file",
            revision=1,
            revision_id=None,
            updated_at=None,
        ),
    )
    optimization = _FakeOptimization(
        feedback=[_feedback_row("old", days_ago=25)],
    )
    adapter = _Adapter({"old": _article_row("old")}, optimization)

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=lambda row, key, content: 30,
        optimizer=lambda *args: _no_change_optimizer(),
        now=NOW,
    )

    item = result["items"][0]
    assert item["t_count"] == 1
    assert item["outcome"] == "model_declined"


def test_feedback_on_same_article_wins_over_archive(monkeypatch) -> None:
    _patch_prompt(monkeypatch)
    feedback = [
        _feedback_row("same", feedback_type="too_high", score=80, stored_score=40)
    ]
    optimization = _FakeOptimization(
        feedback=feedback,
        submissions=[_submission_row("same", stored_score=40)],
    )
    adapter = _Adapter({"same": _article_row("same")}, optimization)

    worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=lambda row, key, content: 90,
        optimizer=lambda *args: _no_change_optimizer(),
        now=NOW,
    )

    cases = optimization.runs[0]["cases"]
    assert len(cases) == 1
    assert cases[0]["origin"] == "feedback"
    assert cases[0]["direction"] == "down"
    assert cases[0]["threshold"] == 70


def test_default_replay_never_uses_the_write_adapter(monkeypatch) -> None:
    monkeypatch.setattr(
        worker,
        "call_external_filter_model",
        lambda candidate, **kwargs: "85",
    )

    def forbidden_adapter():
        raise AssertionError("replay must not request the write adapter")

    monkeypatch.setattr(worker, "get_adapter", forbidden_adapter)

    score = worker._default_replay(
        _article_row("a1"),
        "external_positive",
        "PROMPT",
    )

    assert score == 85


def test_window_uses_current_version_created_at(monkeypatch) -> None:
    _patch_prompt(monkeypatch, _prompt(created_days_ago=20))
    feedback = [_feedback_row("old", days_ago=25), _feedback_row("recent", days_ago=1)]
    optimization = _FakeOptimization(feedback=feedback)
    rows = {item["article_id"]: _article_row(item["article_id"]) for item in feedback}
    adapter = _Adapter(rows, optimization)

    worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=lambda row, key, content: 30,
        optimizer=lambda *args: _no_change_optimizer(),
        now=NOW,
    )

    by_id = {
        case["article_id"]: case for case in optimization.runs[0]["cases"]
    }
    assert by_id["old"]["bucket"] == "ignored"
    assert by_id["recent"]["bucket"] == "T"


def _scripted_replay(
    script: dict[str, dict[str, list[Optional[int]]]],
    calls: dict[tuple[str, str], int],
):
    """按 (文章, 阶段) 的调用序号回放脚本分数，并记录每次调用。"""

    def replay(row, key, content):
        article_id = row["article_id"]
        phase = "cand" if "CAND" in content else "cur"
        counter = calls.get((article_id, phase), 0)
        calls[(article_id, phase)] = counter + 1
        scores = script[article_id][phase]
        assert counter < len(scores), f"{article_id}/{phase} 调用次数超出脚本"
        return scores[counter]

    return replay


def test_enter_t_confirmation_ignores_current_flapping_cases(monkeypatch) -> None:
    """第一次不达标、确认重打达标 → 波动忽略，不进 T 也不进 R。"""
    _patch_prompt(monkeypatch)
    feedback = [
        _feedback_row("flap"),
        _feedback_row("stable"),
    ]
    optimization = _FakeOptimization(feedback=feedback)
    rows = {item["article_id"]: _article_row(item["article_id"]) for item in feedback}
    adapter = _Adapter(rows, optimization)
    script = {
        "flap": {"cur": [58, 62], "cand": []},
        "stable": {"cur": [55, 55], "cand": []},
    }
    optimizer_cases: list[list[dict[str, Any]]] = []

    def optimizer(key, content, cases, references, failure_feedback):
        optimizer_cases.append(cases)
        return _no_change_optimizer()

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=_scripted_replay(script, {}),
        optimizer=optimizer,
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "model_declined"
    assert item["t_count"] == 1
    assert item["r_count"] == 0
    assert item["fluctuation_ignored_count"] == 1
    cases = {case["article_id"]: case for case in optimization.runs[0]["cases"]}
    assert cases["flap"]["bucket"] == "ignored"
    assert cases["flap"]["skip_reason"] == "当前版本两次结论不一致（分数波动）"
    assert cases["flap"]["current_confirm_score"] == 62
    assert cases["stable"]["bucket"] == "T"
    # 波动案例不发给优化模型。
    assert [case["title"] for case in optimizer_cases[0]] == ["标题-stable"]


def test_enter_t_confirmation_error_or_unparsed_falls_back_to_ignore(
    monkeypatch,
) -> None:
    _patch_prompt(monkeypatch)
    feedback = [_feedback_row("boom"), _feedback_row("blank"), _feedback_row("ok")]
    optimization = _FakeOptimization(feedback=feedback)
    rows = {item["article_id"]: _article_row(item["article_id"]) for item in feedback}
    adapter = _Adapter(rows, optimization)
    calls: dict[tuple[str, str], int] = {}

    def replay(row, key, content):
        article_id = row["article_id"]
        phase = "cand" if "CAND" in content else "cur"
        counter = calls.get((article_id, phase), 0)
        calls[(article_id, phase)] = counter + 1
        if article_id == "boom" and counter == 1:
            raise RuntimeError("upstream 502")
        if article_id == "blank" and counter == 1:
            return None
        if article_id == "boom":
            return 58
        if article_id == "blank":
            return 58
        return 50 if article_id == "ok" else 58

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=replay,
        optimizer=_no_change_optimizer,
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "model_declined"
    assert item["t_count"] == 1
    assert item["skipped_count"] == 2
    cases = {case["article_id"]: case for case in optimization.runs[0]["cases"]}
    assert cases["boom"]["bucket"] == "ignored"
    assert "当前版本回放失败" in cases["boom"]["skip_reason"]
    assert cases["blank"]["bucket"] == "ignored"
    assert "未解析" in cases["blank"]["skip_reason"]
    assert cases["ok"]["bucket"] == "T"


def test_first_pass_case_enters_r_without_confirmation(monkeypatch) -> None:
    """第一次就达标的案例直接进 R，不发起进 T 前的确认回放。"""
    _patch_prompt(monkeypatch)
    feedback = [_feedback_row("pass-first"), _feedback_row("t1")]
    optimization = _FakeOptimization(feedback=feedback)
    rows = {item["article_id"]: _article_row(item["article_id"]) for item in feedback}
    adapter = _Adapter(rows, optimization)
    script = {
        "pass-first": {"cur": [80], "cand": [80]},
        "t1": {"cur": [50, 50], "cand": [70, 70]},
    }
    calls: dict[tuple[str, str], int] = {}

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=_scripted_replay(script, calls),
        optimizer=lambda *args: _change_optimizer("CAND"),
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "revision_created"
    assert calls == {
        ("pass-first", "cur"): 1,
        ("t1", "cur"): 2,
        ("pass-first", "cand"): 1,
        ("t1", "cand"): 2,
    }
    # 确认回放共两次：t1 进 T 前一次 + t1 修好前一次。
    assert item["confirm_replay_count"] == 2
    assert item["model_calls"] == 7


def test_enter_t_baseline_is_closer_score_so_small_gain_is_not_fixed(
    monkeypatch,
) -> None:
    """起点取更接近达标的确认分（62 而非首打 55）：涨 4 分不算修好、也不发起确认。"""
    _patch_prompt(monkeypatch)
    feedback = [_feedback_row("t1", score=55)]  # 阈值 65
    optimization = _FakeOptimization(feedback=feedback)
    adapter = _Adapter({"t1": _article_row("t1")}, optimization)
    script = {"t1": {"cur": [55, 62], "cand": [66, 66]}}
    calls: dict[tuple[str, str], int] = {}

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=_scripted_replay(script, calls),
        optimizer=lambda *args: _change_optimizer("CAND"),
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "check_failed"
    assert item["fixed_count"] == 0
    # 只有进 T 前的一次确认；两次尝试的候选首打都不满足涨幅，没有候选确认。
    assert calls[("t1", "cur")] == 2
    assert calls[("t1", "cand")] == 2
    assert item["confirm_replay_count"] == 1
    case = optimization.runs[0]["cases"][0]
    assert case["bucket"] == "T"
    assert case["current_confirm_score"] == 62
    assert case["candidate_confirm_score"] is None
    assert case["fix_state"] == "unchanged"


def test_better_baseline_still_allows_real_fix(monkeypatch) -> None:
    _patch_prompt(monkeypatch)
    feedback = [_feedback_row("t1", score=55)]  # 阈值 65
    optimization = _FakeOptimization(feedback=feedback)
    adapter = _Adapter({"t1": _article_row("t1")}, optimization)
    script = {"t1": {"cur": [55, 62], "cand": [68, 68]}}

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=_scripted_replay(script, {}),
        optimizer=lambda *args: _change_optimizer("CAND"),
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "revision_created"
    assert item["fixed_count"] == 1
    assert item["confirm_replay_count"] == 2
    case = optimization.runs[0]["cases"][0]
    assert case["fix_state"] == "fixed"
    assert case["candidate_confirm_score"] == 68


def test_second_attempt_resets_previous_confirm_fields(monkeypatch) -> None:
    """第 1 轮确认为修好的案例，第 2 轮首打不满足时，运行记录反映第 2 轮。"""
    _patch_prompt(monkeypatch)
    # 三条 T：t-star 第 1 轮候选 70/70 修好；另两条始终 50 修不好，
    # 使第 1 轮 fixed=1 < ceil(3/2)=2 触发重试。
    feedback = [_feedback_row("t-star"), _feedback_row("f1"), _feedback_row("f2")]
    optimization = _FakeOptimization(feedback=feedback)
    rows = {item["article_id"]: _article_row(item["article_id"]) for item in feedback}
    adapter = _Adapter(rows, optimization)
    script = {
        "t-star": {"cur": [50, 50], "cand": [70, 70, 55]},
        "f1": {"cur": [50, 50], "cand": [50, 50]},
        "f2": {"cur": [50, 50], "cand": [50, 50]},
    }

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=_scripted_replay(script, {}),
        optimizer=lambda *args: _change_optimizer("CAND"),
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "check_failed"
    assert item["attempts"] == 2
    # 第 1 轮 t-star 确为修好（证明残留数据在第 1 轮确实产生过）。
    assert optimization.runs[0]["attempts"][0]["check"]["fixed_count"] == 1
    # 最终运行记录是第 2 轮的结果：首打 55 不达标 → 未变化，没有确认回放。
    cases = {case["article_id"]: case for case in optimization.runs[0]["cases"]}
    assert cases["t-star"]["fix_state"] == "unchanged"
    assert cases["t-star"]["candidate_score"] == 55
    assert cases["t-star"]["candidate_confirm_score"] is None


def test_fixed_requires_candidate_double_confirmation(monkeypatch) -> None:
    """修好要两次都满足：第一次涨 20、第二次掉回 52 → 记波动、不计修好。"""
    _patch_prompt(monkeypatch)
    feedback = [_feedback_row("t1")]
    optimization = _FakeOptimization(feedback=feedback)
    adapter = _Adapter({"t1": _article_row("t1")}, optimization)
    script = {"t1": {"cur": [50, 50], "cand": [70, 52, 70, 52]}}

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=_scripted_replay(script, {}),
        optimizer=lambda *args: _change_optimizer("CAND"),
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "check_failed"
    assert item["fixed_count"] == 0
    assert item["model_calls"] == 8
    assert item["confirm_replay_count"] == 3
    case = optimization.runs[0]["cases"][0]
    assert case["fix_state"] == "fluctuation"
    assert case["candidate_score"] == 70
    assert case["candidate_confirm_score"] == 52
    assert optimization.runs[0]["attempts"][0]["check"]["fluctuation_count"] == 1


def test_broken_requires_candidate_double_confirmation(monkeypatch) -> None:
    """改坏要两次都满足：第一次跌 30、确认回 80 达标 → 记波动、不算改坏。"""
    _patch_prompt(monkeypatch)
    feedback = [_feedback_row("t1")]
    optimization = _FakeOptimization(
        feedback=feedback,
        submissions=[_submission_row("r1", stored_score=80)],
    )
    adapter = _Adapter(
        {"t1": _article_row("t1"), "r1": _article_row("r1")},
        optimization,
    )
    script = {
        "t1": {"cur": [50, 50], "cand": [70, 70]},
        "r1": {"cur": [80], "cand": [50, 80]},
    }

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=_scripted_replay(script, {}),
        optimizer=lambda *args: _change_optimizer("CAND"),
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "revision_created"
    check = optimization.runs[0]["attempts"][0]["check"]
    assert check["broken_count"] == 0
    assert check["fluctuation_count"] == 1
    cases = {case["article_id"]: case for case in optimization.runs[0]["cases"]}
    assert cases["r1"]["fix_state"] == "fluctuation"
    assert cases["r1"]["candidate_confirm_score"] == 80
    assert cases["t1"]["fix_state"] == "fixed"


def test_confirmation_replays_only_triggered_for_boundary_cases(monkeypatch) -> None:
    """候选首打不满足条件的案例不发起确认（防止对所有 R 膨胀补打）。"""
    _patch_prompt(monkeypatch)
    feedback = [_feedback_row("t1")]
    optimization = _FakeOptimization(
        feedback=feedback,
        submissions=[_submission_row("r1", stored_score=80)],
    )
    adapter = _Adapter(
        {"t1": _article_row("t1"), "r1": _article_row("r1")},
        optimization,
    )
    # t1 候选 58 仍不达标（未变化）；r1 候选 80 仍达标（未受影响）。
    script = {
        "t1": {"cur": [50, 50], "cand": [58, 58]},
        "r1": {"cur": [80], "cand": [80, 80]},
    }
    calls: dict[tuple[str, str], int] = {}

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=_scripted_replay(script, calls),
        optimizer=lambda *args: _change_optimizer("CAND"),
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "check_failed"
    assert item["confirm_replay_count"] == 1
    assert item["model_calls"] == 9
    assert calls == {
        ("t1", "cur"): 2,
        ("r1", "cur"): 1,
        ("t1", "cand"): 2,
        ("r1", "cand"): 2,
    }
    cases = {case["article_id"]: case for case in optimization.runs[0]["cases"]}
    assert cases["t1"]["fix_state"] == "unchanged"
    assert cases["r1"]["fix_state"] == "unchanged"


def test_candidate_confirm_unparseable_fails_attempt_then_recovers(monkeypatch) -> None:
    """确认回放解析不出分数 → 计入未解析、该次检查不通过；下一轮重试可恢复。"""
    _patch_prompt(monkeypatch)
    feedback = [_feedback_row("t1")]
    optimization = _FakeOptimization(feedback=feedback)
    adapter = _Adapter({"t1": _article_row("t1")}, optimization)
    script = {"t1": {"cur": [50, 50], "cand": [70, None, 70, 70]}}

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=_scripted_replay(script, {}),
        optimizer=lambda *args: _change_optimizer("CAND"),
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "revision_created"
    attempts = optimization.runs[0]["attempts"]
    assert attempts[0]["check"]["passed"] is False
    assert attempts[0]["check"]["unparseable_count"] == 1
    assert any(
        "未能解析出分数" in failure for failure in attempts[0]["check"]["failures"]
    )
    assert attempts[1]["check"]["passed"] is True
    case = optimization.runs[0]["cases"][0]
    assert case["fix_state"] == "fixed"
    assert case["candidate_confirm_score"] == 70


def test_oct31_night_scenario_under_new_rules(monkeypatch) -> None:
    """复现 2026-10-31 internal_positive 场景：波动案例不进 T，降 3–4 分不算改坏。"""
    _patch_prompt(monkeypatch)
    feedback = [
        _feedback_row("shuzhi"),    # 数智育人：运行时 58，测量重打能过线
        _feedback_row("wenhua"),    # 首都高校环境文化季：同上
        _feedback_row("t-real"),
        _feedback_row("t-hard"),
    ]
    submissions = [
        _submission_row(f"r{index}", stored_score=62) for index in range(7)
    ]
    optimization = _FakeOptimization(feedback=feedback, submissions=submissions)
    rows = {
        item["article_id"]: _article_row(item["article_id"])
        for item in feedback + submissions
    }
    adapter = _Adapter(rows, optimization)
    script = {
        # 当前版本首打 58 不达标 → 确认重打 62/61 过线 → 波动忽略，不进 T。
        "shuzhi": {"cur": [58, 62], "cand": []},
        "wenhua": {"cur": [58, 61], "cand": []},
        # 真实待修正：t-real 两次 55 → 起点 55，候选 66 涨 11 → 确认 66 修好（计权 2）。
        "t-real": {"cur": [55, 55], "cand": [66, 66]},
        # t-hard 两次 50/52 → 起点 52，候选 58 仍不达标 → 未变化。
        "t-hard": {"cur": [50, 52], "cand": [58, 58]},
        # 参照：旧分 61–63，候选只降 3–4 分的三条（10-31 被判改坏的其中三条）。
        "r0": {"cur": [61], "cand": [58, 58]},
        "r1": {"cur": [62], "cand": [58, 58]},
        "r2": {"cur": [63], "cand": [59, 59]},
        # 真实改坏：降 9–11 分，两次确认都成立。
        "r3": {"cur": [61], "cand": [50, 52]},
        "r4": {"cur": [65], "cand": [80, 80]},
        "r5": {"cur": [66], "cand": [80, 80]},
        "r6": {"cur": [67], "cand": [80, 80]},
    }

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=_scripted_replay(script, {}),
        optimizer=lambda *args: _change_optimizer("CAND"),
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "revision_created"
    assert item["t_count"] == 2
    assert item["r_count"] == 7
    assert item["fluctuation_ignored_count"] == 2
    assert item["fixed_count"] == 1
    assert item["broken_count"] == 1
    assert item["weighted_fixed_score"] == 2
    assert item["confirm_replay_count"] == 6
    cases = {case["article_id"]: case for case in optimization.runs[0]["cases"]}
    # 测量中当前版本重打能过线的两条：确认后不进 T。
    for article_id in ("shuzhi", "wenhua"):
        assert cases[article_id]["bucket"] == "ignored"
        assert cases[article_id]["skip_reason"] == "当前版本两次结论不一致（分数波动）"
    # 降 3–4 分的三条不算改坏。
    for article_id in ("r0", "r1", "r2"):
        assert cases[article_id]["fix_state"] == "unchanged"
    assert cases["r3"]["fix_state"] == "broken"
    assert cases["t-real"]["fix_state"] == "fixed"
    assert cases["t-hard"]["fix_state"] == "unchanged"
    check = optimization.runs[0]["attempts"][0]["check"]
    assert check["weighted_fixed_score"] == 2
    assert check["fluctuation_count"] == 0


def test_run_summary_counts_outcomes_separately(
    monkeypatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """run() 结尾按结果分别计数，check_failed 不再算进成功。"""
    _patch_prompt(monkeypatch)
    feedback = [_feedback_row("t1")]
    optimization = _FakeOptimization(feedback=feedback)
    adapter = _Adapter({"t1": _article_row("t1")}, optimization)
    script = {"t1": {"cur": [50, 50], "cand": [58, 58]}}

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=_scripted_replay(script, {}),
        optimizer=lambda *args: _change_optimizer("CAND"),
        now=NOW,
    )

    assert result["items"][0]["outcome"] == "check_failed"
    captured = capsys.readouterr()
    assert (
        "result: revision_created=0 no_correction=0 model_declined=0 "
        "check_failed=1 error=0 preview=0" in captured.out
    )
    assert "ok=" not in captured.out
    assert result["had_error"] is False
