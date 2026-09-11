from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from scripts import run_pipeline_once as runner
from src.business_config import get_model_for_step


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
    assert result.artifacts == {"crawl_failed_sources": ["toutiao"]}
    assert adapter.process.finishes[0]["artifacts"] == {
        "crawl_failed_sources": ["toutiao"]
    }
