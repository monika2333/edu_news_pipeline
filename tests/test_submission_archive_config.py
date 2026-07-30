from __future__ import annotations

import pytest

from src.domain import submission_archive_config


def test_submission_thresholds_allow_only_documented_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUBMISSION_DEDUP_LOOKBACK_DAYS", "20")
    monkeypatch.setenv("SUBMISSION_LINK_AUTO_THRESHOLD", "0.9")
    monkeypatch.setenv("SUBMISSION_LINK_REVIEW_THRESHOLD", "0.6")
    monkeypatch.setenv("SUBMISSION_DEDUP_RECALL_THRESHOLD", "0.75")

    assert submission_archive_config.dedup_lookback_days() == 20
    assert submission_archive_config.link_auto_threshold() == 0.9
    assert submission_archive_config.link_review_threshold() == 0.6
    assert submission_archive_config.dedup_recall_threshold() == 0.75
    assert submission_archive_config.EMBED_MODEL == "BAAI/bge-large-zh"


def test_invalid_submission_env_values_fall_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUBMISSION_DEDUP_LOOKBACK_DAYS", "0")
    monkeypatch.setenv("SUBMISSION_LINK_AUTO_THRESHOLD", "not-a-score")
    monkeypatch.setenv("SUBMISSION_LINK_REVIEW_THRESHOLD", "2")
    monkeypatch.setenv("SUBMISSION_DEDUP_RECALL_THRESHOLD", "-1")

    assert submission_archive_config.dedup_lookback_days() == 15
    assert submission_archive_config.link_auto_threshold() == 0.85
    assert submission_archive_config.link_review_threshold() == 0.55
    assert submission_archive_config.dedup_recall_threshold() == 0.90
