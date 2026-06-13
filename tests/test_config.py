from __future__ import annotations

from collections.abc import Iterator

import pytest

import src.config as config


LLM_ENV_KEYS = (
    "LLM_API_BASE_URL",
    "LLM_API_KEY",
    "LLM_API_HTTP_REFERER",
    "LLM_API_TITLE",
    "LLM_MODEL",
    "LLM_SUMMARY_MODEL",
    "LLM_SOURCE_MODEL",
    "LLM_SCORING_MODEL",
    "LLM_SENTIMENT_MODEL",
    "LLM_EXTERNAL_FILTER_MODEL",
    "LLM_BEIJING_GATE_MODEL",
    "LLM_SUMMARY_REASONING_ENABLED",
    "LLM_SOURCE_REASONING_ENABLED",
    "LLM_SENTIMENT_REASONING_ENABLED",
    "LLM_REASONING_ENABLED",
    "LLM_REASONING_EFFORT",
    "LLM_REASONING_EXCLUDE",
    "LLM_SCORING_TIMEOUT",
    "LLM_SUMMARY_TIMEOUT",
    "LLM_EXTERNAL_FILTER_TIMEOUT",
    "LLM_BEIJING_GATE_TIMEOUT",
    "LLM_QUOTA_ALERT_ENABLED",
    "LLM_QUOTA_ALERT_COOLDOWN_SECONDS",
    "LLM_QUOTA_ALERT_STATE_PATH",
    "LLM_BASE_URL",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_API_KEY",
    "SILICONFLOW_BASE_URL",
    "SILICONFLOW_API_KEY",
    "SUMMARY_LLM_API_KEY",
    "SUMMARY_LLM_BASE_URL",
    "SCORE_MODEL_NAME",
    "SUMMARIZE_MODEL_NAME",
    "SOURCE_MODEL_NAME",
    "SENTIMENT_MODEL_NAME",
    "EXTERNAL_FILTER_MODEL_NAME",
    "BEIJING_GATE_MODEL_NAME",
    "MODEL_NAME",
)


@pytest.fixture
def clean_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in LLM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "_ENV_LOADED", True)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_settings_reads_canonical_llm_variables(clean_settings_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_API_HTTP_REFERER", "https://console.example.test")
    monkeypatch.setenv("LLM_API_TITLE", "Edu News Pipeline")
    monkeypatch.setenv("LLM_MODEL", "model-default")
    monkeypatch.setenv("LLM_SUMMARY_MODEL", "model-summary")
    monkeypatch.setenv("LLM_SOURCE_MODEL", "model-source")
    monkeypatch.setenv("LLM_SCORING_MODEL", "model-scoring")
    monkeypatch.setenv("LLM_SENTIMENT_MODEL", "model-sentiment")
    monkeypatch.setenv("LLM_EXTERNAL_FILTER_MODEL", "model-external-filter")
    monkeypatch.setenv("LLM_BEIJING_GATE_MODEL", "model-beijing-gate")
    monkeypatch.setenv("LLM_SUMMARY_REASONING_ENABLED", "true")
    monkeypatch.setenv("LLM_SOURCE_REASONING_ENABLED", "false")
    monkeypatch.setenv("LLM_SENTIMENT_REASONING_ENABLED", "false")
    monkeypatch.setenv("LLM_SCORING_TIMEOUT", "11")
    monkeypatch.setenv("LLM_SUMMARY_TIMEOUT", "22")
    monkeypatch.setenv("LLM_EXTERNAL_FILTER_TIMEOUT", "33")
    monkeypatch.setenv("LLM_BEIJING_GATE_TIMEOUT", "44")
    monkeypatch.setenv("LLM_QUOTA_ALERT_ENABLED", "false")
    monkeypatch.setenv("LLM_QUOTA_ALERT_COOLDOWN_SECONDS", "99")
    monkeypatch.setenv("LLM_QUOTA_ALERT_STATE_PATH", "logs/test_quota_state.json")

    settings = config.get_settings()

    assert settings.llm_api_base_url == "https://llm.example.test/v1"
    assert settings.llm_api_key == "test-key"
    assert settings.llm_api_http_referer == "https://console.example.test"
    assert settings.llm_api_title == "Edu News Pipeline"
    assert settings.llm_summary_model == "model-summary"
    assert settings.llm_source_model == "model-source"
    assert settings.llm_scoring_model == "model-scoring"
    assert settings.llm_sentiment_model == "model-sentiment"
    assert settings.llm_external_filter_model == "model-external-filter"
    assert settings.llm_beijing_gate_model == "model-beijing-gate"
    assert settings.llm_summary_reasoning_enabled is True
    assert settings.llm_source_reasoning_enabled is False
    assert settings.llm_sentiment_reasoning_enabled is False
    assert settings.llm_scoring_timeout == 11
    assert settings.llm_summary_timeout == 22
    assert settings.llm_external_filter_timeout == 33
    assert settings.llm_beijing_gate_timeout == 44
    assert settings.llm_quota_alert_enabled is False
    assert settings.llm_quota_alert_cooldown_seconds == 99
    assert settings.llm_quota_alert_state_path.name == "test_quota_state.json"


def test_settings_uses_llm_model_for_all_task_models(
    clean_settings_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL", "model-shared")

    settings = config.get_settings()

    assert settings.llm_summary_model == "model-shared"
    assert settings.llm_source_model == "model-shared"
    assert settings.llm_scoring_model == "model-shared"
    assert settings.llm_sentiment_model == "model-shared"
    assert settings.llm_external_filter_model == "model-shared"
    assert settings.llm_beijing_gate_model == "model-shared"


def test_settings_ignores_removed_llm_variable_names(
    clean_settings_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://old-openrouter.example.test/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "old-key")
    monkeypatch.setenv("SUMMARY_LLM_API_KEY", "old-summary-key")
    monkeypatch.setenv("SCORE_MODEL_NAME", "old-score-model")
    monkeypatch.setenv("SUMMARIZE_MODEL_NAME", "old-summary-model")
    monkeypatch.setenv("EXTERNAL_FILTER_MODEL_NAME", "old-filter-model")
    monkeypatch.setenv("BEIJING_GATE_MODEL_NAME", "old-beijing-model")

    settings = config.get_settings()

    assert settings.llm_api_base_url == "https://openrouter.ai/api/v1"
    assert settings.llm_api_key is None
    assert settings.llm_scoring_model == "deepseek/deepseek-v4-flash"
    assert settings.llm_summary_model == "deepseek/deepseek-v4-flash"
    assert settings.llm_external_filter_model == settings.llm_scoring_model
    assert settings.llm_beijing_gate_model == settings.llm_scoring_model
    assert settings.llm_reasoning_enabled is True
    assert settings.llm_reasoning_effort is None
    assert settings.llm_reasoning_exclude is True
    assert settings.llm_summary_reasoning_enabled is False
    assert settings.llm_source_reasoning_enabled is True
    assert settings.llm_sentiment_reasoning_enabled is True
    assert settings.llm_scoring_timeout == 90
    assert settings.llm_summary_timeout == 90
    assert settings.llm_external_filter_timeout == 90
    assert settings.llm_beijing_gate_timeout == 90
    assert settings.llm_quota_alert_enabled is True
    assert settings.llm_quota_alert_cooldown_seconds == 21600
    assert settings.llm_quota_alert_state_path.name == "llm_quota_alert_state.json"
    assert settings.default_concurrency == 50
    assert settings.summary_concurrency == 50
