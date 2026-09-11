from __future__ import annotations

import threading
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import run_pipeline_once as runner
from src.adapters import db_postgres_core, llm_summary
from src.business_config import get_model_for_step
from src.config import get_settings
from src.workers import summarize


class _Process:
    def __init__(self) -> None:
        self.starts: list[dict[str, Any]] = []
        self.finishes: list[dict[str, Any]] = []

    def record_pipeline_run_start(self, **kwargs: Any) -> None:
        self.starts.append(kwargs)

    def record_pipeline_run_step(self, **_kwargs: Any) -> None:
        return None

    def finalize_pipeline_run(self, **kwargs: Any) -> None:
        self.finishes.append(kwargs)


def _adapter(rows: list[dict[str, Any]]) -> Any:
    app_config = SimpleNamespace(
        fetch_settings=lambda: rows,
        fetch_enabled_accounts=lambda: [],
    )
    return SimpleNamespace(app_config=app_config, process=_Process())


def _rows(model: str = "model-a") -> list[dict[str, Any]]:
    return [
        {
            "section": "llm_models",
            "value": {
                "default": model,
                "steps": {
                    step: {"model": None, "reasoning": step != "summary"}
                    for step in (
                        "summary",
                        "source",
                        "sentiment",
                        "scoring",
                        "external_filter",
                        "beijing_gate",
                        "duplicate_review",
                    )
                },
            },
            "version": 1,
        },
        {"section": "crawl_sources", "value": ["toutiao"], "version": 2},
    ]


def test_m2_pipeline_freezes_business_config_for_the_whole_run(monkeypatch) -> None:
    rows = _rows()
    adapter = _adapter(rows)
    observed: list[str] = []

    def probe() -> dict[str, Any]:
        observed.append(get_model_for_step("summary"))
        rows[0] = _rows("model-b")[0]
        observed.append(get_model_for_step("summary"))
        return {}

    monkeypatch.setitem(runner.STEP_REGISTRY, "probe-config", probe)
    monkeypatch.setattr(runner, "warn_legacy_config", lambda: [])

    result = runner.run_pipeline_once(
        ["probe-config"],
        adapter=adapter,
    )

    assert result.status == "success"
    assert observed == ["model-a", "model-a"]


def test_f1_f2_f5_summary_worker_uses_one_run_snapshot_without_leaking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _rows()
    settings_reads = 0
    requested_models: list[str] = []
    worker_started = threading.Event()
    database_changed = threading.Event()

    class NewsSummaries:
        completed: list[tuple[str, str]] = []

        def fetch_pending(
            self,
            _limit: int,
            *,
            max_attempts: int,
        ) -> list[dict[str, Any]]:
            assert max_attempts == 3
            return [
                {
                    "article_id": "article-1",
                    "title": "测试新闻",
                    "content_markdown": "测试正文",
                    "summary_fail_count": 0,
                }
            ]

        def mark_attempt(self, _article_id: str) -> bool:
            return True

        def complete_generation(self, article_id: str, summary_text: str) -> None:
            self.completed.append((article_id, summary_text))

        def mark_failed(self, _article_id: str, *, message: str) -> None:
            raise AssertionError(message)

    def fetch_settings() -> list[dict[str, Any]]:
        nonlocal settings_reads
        settings_reads += 1
        return rows

    adapter = _adapter(rows)
    adapter.app_config.fetch_settings = fetch_settings
    adapter.news_summaries = NewsSummaries()
    real_summarise = llm_summary.summarise
    settings = replace(get_settings(), llm_api_key="test-key")

    def summarise_after_database_change(article: dict[str, Any]) -> dict[str, Any]:
        worker_started.set()
        assert database_changed.wait(timeout=5)
        return real_summarise(article)

    def change_database_during_worker() -> None:
        assert worker_started.wait(timeout=5)
        rows[0] = _rows("model-b")[0]
        database_changed.set()

    updater = threading.Thread(target=change_database_during_worker)
    monkeypatch.setattr(runner, "warn_legacy_config", lambda: [])
    monkeypatch.setattr(
        runner,
        "run_summarize",
        lambda: summarize.run(limit=1, concurrency=1),
    )
    monkeypatch.setattr(summarize, "get_adapter", lambda: adapter)
    monkeypatch.setattr(summarize, "summarise", summarise_after_database_change)
    monkeypatch.setattr(llm_summary, "get_settings", lambda: settings)
    monkeypatch.setattr(db_postgres_core, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        llm_summary,
        "post_chat_completion",
        lambda _url, **kwargs: (
            requested_models.append(kwargs["payload"]["model"])
            or {"choices": [{"message": {"content": "冻结摘要"}}]}
        ),
    )

    updater.start()
    result = runner.run_pipeline_once(["summarize"], adapter=adapter)
    updater.join(timeout=5)

    assert not updater.is_alive()
    assert result.status == "success"
    assert requested_models == ["model-a"]
    assert settings_reads == 1
    assert adapter.news_summaries.completed == [("article-1", "冻结摘要")]

    assert get_model_for_step("summary") == "model-b"
    assert settings_reads == 2


def test_m9_m18_source_override_is_used_and_written_to_snapshot(monkeypatch) -> None:
    rows = _rows()
    adapter = _adapter(rows)
    observed_sources: list[tuple[str, ...]] = []
    monkeypatch.setattr(runner, "warn_legacy_config", lambda: [])
    monkeypatch.setattr(
        runner,
        "run_crawl",
        lambda *, sources: observed_sources.append(tuple(sources)) or [],
    )

    result = runner.run_pipeline_once(
        ["crawl"],
        adapter=adapter,
        sources=["tencent", "toutiao"],
    )

    assert result.status == "success"
    assert observed_sources == [("tencent", "toutiao")]
    snapshot = adapter.process.starts[0]["config_snapshot"]
    assert snapshot["llm_models"]["summary"] == {
        "model": "model-a",
        "reasoning": False,
    }
    assert snapshot["crawl_sources"] == ["tencent", "toutiao"]
    assert snapshot["versions"] == {"llm_models": 1, "crawl_sources": 2}


def test_m20_source_failures_are_preserved_in_run_artifacts(monkeypatch) -> None:
    adapter = _adapter(_rows())
    monkeypatch.setattr(runner, "warn_legacy_config", lambda: [])
    monkeypatch.setattr(
        runner,
        "run_crawl",
        lambda *, sources: ["toutiao"],
    )

    result = runner.run_pipeline_once(["crawl"], adapter=adapter)

    assert result.status == "success"
    assert result.artifacts == {"crawl_failed_sources": "toutiao"}
    assert adapter.process.finishes[0]["artifacts"] == {
        "crawl_failed_sources": "toutiao"
    }
