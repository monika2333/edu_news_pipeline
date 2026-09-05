from __future__ import annotations

from pathlib import Path
import re

import pytest

from src.domain import submission_archive_config


def test_documented_submission_defaults_match_code_constants() -> None:
    reference = (
        Path(__file__).resolve().parent.parent / "docs" / "env_reference.md"
    ).read_text(encoding="utf-8")
    expected = {
        "SUBMISSION_DEDUP_LOOKBACK_DAYS": (
            submission_archive_config.DEFAULT_DEDUP_LOOKBACK_DAYS
        ),
        "SUBMISSION_LINK_AUTO_THRESHOLD": (
            submission_archive_config.DEFAULT_LINK_AUTO_THRESHOLD
        ),
        "SUBMISSION_LINK_REVIEW_THRESHOLD": (
            submission_archive_config.DEFAULT_LINK_REVIEW_THRESHOLD
        ),
        "SUBMISSION_DEDUP_RECALL_THRESHOLD": (
            submission_archive_config.DEFAULT_DEDUP_RECALL_THRESHOLD
        ),
        "SUBMISSION_FEEDBACK_LOOKBACK_DAYS": (
            submission_archive_config.DEFAULT_FEEDBACK_LOOKBACK_DAYS
        ),
        "SUBMISSION_FEEDBACK_MATCH_THRESHOLD": (
            submission_archive_config.DEFAULT_FEEDBACK_MATCH_THRESHOLD
        ),
    }

    for name, value in expected.items():
        match = re.search(rf"^{name}=([^\s]+)$", reference, flags=re.MULTILINE)
        assert match is not None
        assert float(match.group(1)) == float(value)


def test_submission_thresholds_allow_only_documented_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUBMISSION_DEDUP_LOOKBACK_DAYS", "20")
    monkeypatch.setenv("SUBMISSION_LINK_AUTO_THRESHOLD", "0.9")
    monkeypatch.setenv("SUBMISSION_LINK_REVIEW_THRESHOLD", "0.6")
    monkeypatch.setenv("SUBMISSION_DEDUP_RECALL_THRESHOLD", "0.75")
    monkeypatch.setenv("SUBMISSION_FEEDBACK_LOOKBACK_DAYS", "9")
    monkeypatch.setenv("SUBMISSION_FEEDBACK_MATCH_THRESHOLD", "0.93")

    assert submission_archive_config.dedup_lookback_days() == 20
    assert submission_archive_config.link_auto_threshold() == 0.9
    assert submission_archive_config.link_review_threshold() == 0.6
    assert submission_archive_config.dedup_recall_threshold() == 0.75
    assert submission_archive_config.feedback_lookback_days() == 9
    assert submission_archive_config.feedback_match_threshold() == 0.93
    assert submission_archive_config.EMBED_MODEL == "BAAI/bge-large-zh"


def test_invalid_submission_env_values_fall_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUBMISSION_DEDUP_LOOKBACK_DAYS", "0")
    monkeypatch.setenv("SUBMISSION_LINK_AUTO_THRESHOLD", "not-a-score")
    monkeypatch.setenv("SUBMISSION_LINK_REVIEW_THRESHOLD", "2")
    monkeypatch.setenv("SUBMISSION_DEDUP_RECALL_THRESHOLD", "-1")
    monkeypatch.setenv("SUBMISSION_FEEDBACK_LOOKBACK_DAYS", "0")
    monkeypatch.setenv("SUBMISSION_FEEDBACK_MATCH_THRESHOLD", "1.1")

    assert submission_archive_config.dedup_lookback_days() == 15
    assert submission_archive_config.link_auto_threshold() == 0.65
    assert submission_archive_config.link_review_threshold() == 0.55
    assert submission_archive_config.dedup_recall_threshold() == 0.90
    assert submission_archive_config.feedback_lookback_days() == 7
    assert submission_archive_config.feedback_match_threshold() == 0.90
