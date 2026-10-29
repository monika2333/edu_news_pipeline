from __future__ import annotations

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
    assert item["model_calls"] == 4
    cases = optimization.runs[0]["cases"]
    by_id = {case["article_id"]: case for case in cases}
    assert by_id["in-window"]["bucket"] == "T"
    assert by_id["r-outside"]["bucket"] == "R"
    assert by_id["ignored"]["bucket"] == "ignored"
    assert len(optimizer_calls) == 1
    assert optimization.saved == []


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
    assert item["model_calls"] == 11
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
def test_dry_run_never_writes_a_run_record_for_non_version_outcomes(
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
        dry_run=True,
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
    assert optimization.runs == []
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


def test_dry_run_checks_but_writes_nothing(monkeypatch) -> None:
    _patch_prompt(monkeypatch)
    feedback = [_feedback_row("t1")]
    optimization = _FakeOptimization(feedback=feedback)
    adapter = _Adapter({"t1": _article_row("t1")}, optimization)

    result = worker.run(
        ["external_positive"],
        dry_run=True,
        adapter=adapter,
        replay=lambda row, key, content: 20 if content == "BASE PROMPT" else 80,
        optimizer=lambda *args: _change_optimizer("CAND"),
        now=NOW,
    )

    item = result["items"][0]
    assert item["outcome"] == "revision_created"
    assert item["dry_run"] is True
    assert item["new_revision_id"] is None
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
