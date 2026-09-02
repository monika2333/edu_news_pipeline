from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np

import pytest

from src.workers import submission_archive_processing


def test_launch_submission_report_processing_uses_child_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeProcess:
        pid = 4321

    def fake_popen(
        command: list[str],
        **kwargs: Any,
    ) -> FakeProcess:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(
        submission_archive_processing.subprocess,
        "Popen",
        fake_popen,
    )

    pid = submission_archive_processing.launch_submission_report_processing(
        "report-id"
    )

    assert pid == 4321
    assert captured["command"][-2:] == [
        "src.workers.submission_archive_processing",
        "report-id",
    ]
    assert captured["kwargs"]["cwd"] == (
        submission_archive_processing._REPO_ROOT
    )


def test_process_submission_report_links_then_checks_prior_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {
        "matched": 3,
        "pending": 3,
        "unmatched": 4,
    }
    monkeypatch.setattr(
        submission_archive_processing,
        "process_report_links",
        lambda report_id: expected,
    )
    prior_calls: list[str] = []
    monkeypatch.setattr(
        submission_archive_processing,
        "process_report_prior_matches",
        lambda report_id: prior_calls.append(report_id),
    )

    result = submission_archive_processing.process_submission_report(
        "report-id"
    )

    assert result == expected
    assert prior_calls == ["report-id"]
    assert "embedded" not in result


def test_title_hash_match_does_not_require_vectors() -> None:
    matches, counts = submission_archive_processing._build_prior_item_matches(
        [
            {
                "id": "feedback-item",
                "article_id": None,
                "norm_title_hash": "same-hash",
                "embedding": None,
                "embedding_model": None,
            }
        ],
        [
            {
                "id": "prior-item",
                "article_id": None,
                "norm_title_hash": "same-hash",
                "embedding": None,
                "embedding_model": None,
            }
        ],
        threshold=0.90,
    )

    assert matches == [
        {
            "item_id": "feedback-item",
            "prior_item_id": "prior-item",
            "similarity": 1.0,
            "match_method": "title_hash",
        }
    ]
    assert counts == {"article": 0, "title_hash": 1, "vector": 0}


def test_article_match_has_priority_and_all_prior_hits_are_recorded() -> None:
    item = {
        "id": "feedback-item",
        "article_id": "article-1",
        "norm_title_hash": "same-hash",
        "embedding": None,
        "embedding_model": None,
    }
    candidates = [
        {
            "id": f"prior-{index}",
            "article_id": "article-1",
            "norm_title_hash": "same-hash",
            "embedding": None,
            "embedding_model": None,
        }
        for index in range(2)
    ]

    matches, counts = submission_archive_processing._build_prior_item_matches(
        [item],
        candidates,
        threshold=0.90,
    )

    assert len(matches) == 2
    assert {match["match_method"] for match in matches} == {"article"}
    assert counts["article"] == 2


def test_vector_match_uses_cosine_threshold() -> None:
    from src.workers.submission_dedup import _pack_embedding

    matches, counts = submission_archive_processing._build_prior_item_matches(
        [
            {
                "id": "feedback-item",
                "article_id": None,
                "norm_title_hash": "feedback-hash",
                "embedding": _pack_embedding(np.asarray([3.0, 0.0])),
                "embedding_model": "BAAI/bge-large-zh",
            }
        ],
        [
            {
                "id": "prior-item",
                "article_id": None,
                "norm_title_hash": "prior-hash",
                "embedding": _pack_embedding(np.asarray([4.0, 0.0])),
                "embedding_model": "BAAI/bge-large-zh",
            }
        ],
        threshold=0.99,
    )

    assert matches[0]["match_method"] == "vector"
    assert matches[0]["similarity"] == pytest.approx(1.0)
    assert counts["vector"] == 1


def test_prior_matching_refetches_article_id_after_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Namespace:
        def __init__(self) -> None:
            self.fetch_count = 0
            self.persisted: list[dict[str, Any]] = []

        def fetch_report(self, report_id: str) -> dict[str, Any]:
            assert report_id == "report-1"
            self.fetch_count += 1
            return {
                "id": report_id,
                "report_type": "feedback",
                "report_date": date(2026, 9, 2),
                "items": [
                    {
                        "id": "feedback-item",
                        "article_id": (
                            None if self.fetch_count == 1 else "article-1"
                        ),
                    }
                ],
            }

        def fetch_item_match_inputs(
            self,
            item_ids: list[str],
        ) -> list[dict[str, Any]]:
            assert self.fetch_count == 2
            assert item_ids == ["feedback-item"]
            return [
                {
                    "id": "feedback-item",
                    "article_id": "article-1",
                    "norm_title_hash": "feedback-hash",
                    "embedding": None,
                    "embedding_model": None,
                }
            ]

        def fetch_prior_submission_candidates(self, **kwargs: Any) -> list[dict[str, Any]]:
            assert kwargs == {
                "report_date": date(2026, 9, 2),
                "lookback_days": 7,
            }
            return [
                {
                    "id": "prior-item",
                    "article_id": "article-1",
                    "norm_title_hash": "prior-hash",
                    "embedding": None,
                    "embedding_model": None,
                }
            ]

        def replace_item_duplicate_matches(self, **kwargs: Any) -> int:
            self.persisted = list(kwargs["matches"])
            return len(self.persisted)

    namespace = Namespace()
    adapter = type("Adapter", (), {"submission_archive": namespace})()
    monkeypatch.setattr(
        submission_archive_processing,
        "get_adapter",
        lambda: adapter,
    )
    monkeypatch.setattr(
        submission_archive_processing,
        "backfill_archive_embeddings",
        lambda: 0,
    )

    result = submission_archive_processing.process_report_prior_matches(
        "report-1"
    )

    assert namespace.fetch_count == 2
    assert namespace.persisted[0]["match_method"] == "article"
    assert result["article"] == 1


def test_prior_match_recompute_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Namespace:
        def __init__(self) -> None:
            self.rows: list[dict[str, Any]] = []

        def fetch_report(self, report_id: str) -> dict[str, Any]:
            return {
                "id": report_id,
                "report_type": "feedback",
                "report_date": date(2026, 9, 2),
                "items": [{"id": "feedback-item"}],
            }

        def fetch_item_match_inputs(self, _item_ids: list[str]) -> list[dict[str, Any]]:
            return [
                {
                    "id": "feedback-item",
                    "article_id": None,
                    "norm_title_hash": "same-hash",
                    "embedding": None,
                    "embedding_model": None,
                }
            ]

        def fetch_prior_submission_candidates(self, **_kwargs: Any) -> list[dict[str, Any]]:
            return [
                {
                    "id": "prior-item",
                    "article_id": None,
                    "norm_title_hash": "same-hash",
                    "embedding": None,
                    "embedding_model": None,
                }
            ]

        def replace_item_duplicate_matches(self, **kwargs: Any) -> int:
            self.rows = [dict(match) for match in kwargs["matches"]]
            return len(self.rows)

    namespace = Namespace()
    adapter = type("Adapter", (), {"submission_archive": namespace})()
    monkeypatch.setattr(submission_archive_processing, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        submission_archive_processing,
        "backfill_archive_embeddings",
        lambda: 0,
    )

    first = submission_archive_processing.process_report_prior_matches("report-1")
    first_rows = list(namespace.rows)
    second = submission_archive_processing.process_report_prior_matches("report-1")

    assert first == second
    assert namespace.rows == first_rows


def test_non_feedback_report_skips_prior_matching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Namespace:
        def fetch_report(self, report_id: str) -> dict[str, Any]:
            return {"id": report_id, "report_type": "zongbao", "items": []}

    adapter = type("Adapter", (), {"submission_archive": Namespace()})()
    monkeypatch.setattr(submission_archive_processing, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        submission_archive_processing,
        "backfill_archive_embeddings",
        lambda: pytest.fail("non-feedback must not backfill here"),
    )

    result = submission_archive_processing.process_report_prior_matches(
        "report-1"
    )

    assert result["matches"] == 0
