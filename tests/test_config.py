from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

import src.config as config


LLM_ENV_KEYS = (
    "LLM_API_BASE_URL",
    "LLM_API_KEY",
    "LLM_ALLOWED_HOSTS",
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
    "LLM_BUDGET",
    "LLM_BEIJING_GATE_BUDGET",
    "LLM_SCORING_BUDGET",
    "LLM_SUMMARY_BUDGET",
    "LLM_EXTERNAL_FILTER_BUDGET",
    "LLM_SENTIMENT_BUDGET",
    "LLM_SOURCE_BUDGET",
    "LLM_DUPLICATE_REVIEW_BUDGET",
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
    "EXTERNAL_FILTER_POSITIVE_THRESHOLD",
    "EXTERNAL_FILTER_THRESHOLD",
    "EXTERNAL_FILTER_NEGATIVE_THRESHOLD",
    "INTERNAL_FILTER_POSITIVE_THRESHOLD",
    "INTERNAL_FILTER_THRESHOLD",
    "INTERNAL_FILTER_NEGATIVE_THRESHOLD",
    "EXTERNAL_FILTER_PROMPT_PATH",
    "EXTERNAL_NEGATIVE_FILTER_PROMPT_PATH",
    "INTERNAL_FILTER_PROMPT_PATH",
    "INTERNAL_NEGATIVE_FILTER_PROMPT_PATH",
    "BEIJING_GATE_PROMPT_PATH",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_RECEIVE_ID",
    "FEISHU_RECEIVE_ID_TYPE",
    "FEISHU_ARCHIVE_ALLOWED_OPEN_IDS",
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
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_API_HTTP_REFERER", "https://console.example.test")
    monkeypatch.setenv("LLM_API_TITLE", "Edu News Pipeline")
    monkeypatch.setenv("LLM_SCORING_TIMEOUT", "11")
    monkeypatch.setenv("LLM_SUMMARY_TIMEOUT", "22")
    monkeypatch.setenv("LLM_EXTERNAL_FILTER_TIMEOUT", "33")
    monkeypatch.setenv("LLM_BEIJING_GATE_TIMEOUT", "44")
    monkeypatch.setenv("LLM_BUDGET", "180")
    monkeypatch.setenv("LLM_BEIJING_GATE_BUDGET", "181")
    monkeypatch.setenv("LLM_SCORING_BUDGET", "182")
    monkeypatch.setenv("LLM_SUMMARY_BUDGET", "183")
    monkeypatch.setenv("LLM_EXTERNAL_FILTER_BUDGET", "184")
    monkeypatch.setenv("LLM_SENTIMENT_BUDGET", "185")
    monkeypatch.setenv("LLM_SOURCE_BUDGET", "186")
    monkeypatch.setenv("LLM_DUPLICATE_REVIEW_BUDGET", "187")
    monkeypatch.setenv("LLM_QUOTA_ALERT_ENABLED", "false")
    monkeypatch.setenv("LLM_QUOTA_ALERT_COOLDOWN_SECONDS", "99")
    monkeypatch.setenv("LLM_QUOTA_ALERT_STATE_PATH", "logs/test_quota_state.json")
    monkeypatch.setenv("LLM_ALLOWED_HOSTS", "openrouter.ai, Evil.example ,,api.deepseek.com")

    settings = config.get_settings()

    assert not hasattr(settings, "llm_api_base_url")
    assert settings.llm_api_key == "test-key"
    assert settings.llm_allowed_hosts == ("openrouter.ai", "evil.example", "api.deepseek.com")
    assert settings.llm_api_http_referer == "https://console.example.test"
    assert settings.llm_api_title == "Edu News Pipeline"
    assert not hasattr(settings, "llm_summary_model")
    assert not hasattr(settings, "llm_scoring_model")
    assert not hasattr(settings, "llm_reasoning_enabled")
    assert not hasattr(settings, "llm_summary_reasoning_enabled")
    assert not hasattr(settings, "llm_source_reasoning_enabled")
    assert not hasattr(settings, "llm_sentiment_reasoning_enabled")
    assert settings.llm_scoring_timeout == 11
    assert settings.llm_summary_timeout == 22
    assert settings.llm_external_filter_timeout == 33
    assert settings.llm_beijing_gate_timeout == 44
    assert settings.llm_global_budget == 180
    assert settings.llm_beijing_gate_budget == 181
    assert settings.llm_scoring_budget == 182
    assert settings.llm_summary_budget == 183
    assert settings.llm_external_filter_budget == 184
    assert settings.llm_sentiment_budget == 185
    assert settings.llm_source_budget == 186
    assert settings.llm_duplicate_review_budget == 187
    assert settings.llm_quota_alert_enabled is False
    assert settings.llm_quota_alert_cooldown_seconds == 99
    assert settings.llm_quota_alert_state_path.name == "test_quota_state.json"


def test_settings_prefers_positive_filter_threshold_names(
    clean_settings_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXTERNAL_FILTER_POSITIVE_THRESHOLD", "70")
    monkeypatch.setenv("EXTERNAL_FILTER_NEGATIVE_THRESHOLD", "5")
    monkeypatch.setenv("INTERNAL_FILTER_POSITIVE_THRESHOLD", "20")
    monkeypatch.setenv("INTERNAL_FILTER_NEGATIVE_THRESHOLD", "10")

    settings = config.get_settings()

    assert settings.external_filter_threshold == 70
    assert settings.external_filter_negative_threshold == 5
    assert settings.internal_filter_threshold == 20
    assert settings.internal_filter_negative_threshold == 10


def test_settings_ignores_legacy_positive_filter_threshold_names(
    clean_settings_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXTERNAL_FILTER_THRESHOLD", "65")
    monkeypatch.setenv("INTERNAL_FILTER_THRESHOLD", "55")

    settings = config.get_settings()

    assert settings.external_filter_threshold == 20
    assert settings.internal_filter_threshold == 20


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

    assert not hasattr(settings, "llm_api_base_url")
    assert settings.llm_api_key is None
    assert settings.llm_allowed_hosts == config.DEFAULT_LLM_ALLOWED_HOSTS
    assert not hasattr(settings, "llm_summary_model")
    assert not hasattr(settings, "llm_scoring_model")
    assert not hasattr(settings, "llm_reasoning_enabled")
    assert settings.llm_reasoning_effort is None
    assert settings.llm_reasoning_exclude is True
    assert not hasattr(settings, "llm_summary_reasoning_enabled")
    assert not hasattr(settings, "llm_source_reasoning_enabled")
    assert not hasattr(settings, "llm_sentiment_reasoning_enabled")
    assert settings.llm_scoring_timeout == 90
    assert settings.llm_summary_timeout == 90
    assert settings.llm_external_filter_timeout == 90
    assert settings.llm_beijing_gate_timeout == 90
    assert settings.llm_global_budget == 180
    assert settings.llm_beijing_gate_budget == 180
    assert settings.llm_scoring_budget == 180
    assert settings.llm_summary_budget == 180
    assert settings.llm_external_filter_budget == 180
    assert settings.llm_sentiment_budget == 180
    assert settings.llm_source_budget == 180
    assert settings.llm_duplicate_review_budget == 180
    assert settings.llm_quota_alert_enabled is True
    assert settings.llm_quota_alert_cooldown_seconds == 21600
    assert settings.llm_quota_alert_state_path.name == "llm_quota_alert_state.json"
    assert settings.default_concurrency == 50
    assert settings.summary_concurrency == 50


def test_settings_uses_versioned_prompt_defaults(clean_settings_env: None) -> None:
    settings = config.get_settings()

    prompt_paths = {
        settings.external_filter_prompt_path,
        settings.external_negative_filter_prompt_path,
        settings.internal_filter_prompt_path,
        settings.internal_negative_filter_prompt_path,
        settings.beijing_gate_prompt_path,
    }

    assert {path.parent.name for path in prompt_paths} == {"prompts"}
    assert all(path.parent.parent.name == "config" for path in prompt_paths)
    assert all(path.is_file() for path in prompt_paths)


def test_settings_resolves_prompt_path_overrides(
    clean_settings_env: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    custom_prompt = tmp_path / "custom_prompt.md"
    monkeypatch.setenv("BEIJING_GATE_PROMPT_PATH", str(custom_prompt))
    monkeypatch.setenv("EXTERNAL_FILTER_PROMPT_PATH", "custom/external.md")

    settings = config.get_settings()

    assert settings.beijing_gate_prompt_path == custom_prompt.resolve()
    assert settings.external_filter_prompt_path == (
        config._REPO_ROOT / "custom" / "external.md"
    ).resolve()


def test_settings_reads_feishu_archive_allowlist(
    clean_settings_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEISHU_ARCHIVE_ALLOWED_OPEN_IDS", "ou_a, ou_b,ou_a")

    settings = config.get_settings()

    assert settings.feishu_archive_allowed_open_ids == ("ou_a", "ou_b")


def test_settings_defaults_feishu_archive_allowlist_to_open_id_recipient(
    clean_settings_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEISHU_RECEIVE_ID", "ou_owner")
    monkeypatch.setenv("FEISHU_RECEIVE_ID_TYPE", "open_id")

    settings = config.get_settings()

    assert settings.feishu_archive_allowed_open_ids == ("ou_owner",)


@pytest.fixture
def env_file_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 env 文件指向临时文件并复位加载状态，供 reload 语义测试使用。"""
    env_file = tmp_path / "sandbox.env"
    monkeypatch.setattr(config, "_ENV_FILES", (env_file,))
    monkeypatch.setattr(config, "_ENV_LOADED", False)
    monkeypatch.setattr(config, "_ENV_FILE_KEYS", set())
    config.get_settings.cache_clear()
    yield env_file
    config.get_settings.cache_clear()


def test_reload_environment_picks_up_added_and_changed_values(
    env_file_sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENV_RELOAD_PROBE_KEY", raising=False)
    monkeypatch.delenv("ENV_RELOAD_ADDED_KEY", raising=False)
    env_file_sandbox.write_text("ENV_RELOAD_PROBE_KEY=old-value\n", encoding="utf-8")

    config.load_environment()
    assert os.getenv("ENV_RELOAD_PROBE_KEY") == "old-value"

    env_file_sandbox.write_text(
        "ENV_RELOAD_PROBE_KEY=new-value\nENV_RELOAD_ADDED_KEY=added\n",
        encoding="utf-8",
    )
    loaded = config.reload_environment()

    # 文件来源的变量按重载后的文件内容生效：改过的更新、新加的进入环境
    assert loaded == 2
    assert os.getenv("ENV_RELOAD_PROBE_KEY") == "new-value"
    assert os.getenv("ENV_RELOAD_ADDED_KEY") == "added"


def test_reload_environment_drops_removed_entries_and_keeps_exported_values(
    env_file_sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENV_RELOAD_FILE_ONLY", raising=False)
    monkeypatch.delenv("ENV_RELOAD_BOTH", raising=False)
    env_file_sandbox.write_text(
        "ENV_RELOAD_FILE_ONLY=from-file\nENV_RELOAD_BOTH=from-file\n",
        encoding="utf-8",
    )
    # 进程启动前显式导出的变量：初载和重载都必须让它赢过文件值
    monkeypatch.setenv("ENV_RELOAD_BOTH", "from-shell")

    config.load_environment()
    assert os.getenv("ENV_RELOAD_BOTH") == "from-shell"

    env_file_sandbox.write_text("ENV_RELOAD_BOTH=from-file-v2\n", encoding="utf-8")
    config.reload_environment()

    # 文件里删掉的变量随重载消失；显式导出的值不被文件覆盖
    assert os.getenv("ENV_RELOAD_FILE_ONLY") is None
    assert os.getenv("ENV_RELOAD_BOTH") == "from-shell"


def test_reload_environment_invalidates_settings_cache(env_file_sandbox: Path) -> None:
    env_file_sandbox.write_text("", encoding="utf-8")
    config.load_environment()

    first = config.get_settings()
    config.reload_environment()
    second = config.get_settings()

    # 缓存不失效的话，重载对一切经 get_settings() 的读取都不生效
    assert first is not second
