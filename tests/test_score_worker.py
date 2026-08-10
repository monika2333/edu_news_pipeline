from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pytest

from src.domain.models import PrimaryArticleForScoring
from src.workers import score


@dataclass
class FakeAdapter:
    fetched: List[PrimaryArticleForScoring]
    updates: List[Dict[str, Any]]
    promotions: List[Dict[str, Any]]
    news_summaries: FakeNewsSummariesNamespace = field(init=False)
    process: FakeProcessNamespace = field(init=False)

    def __post_init__(self) -> None:
        self.news_summaries = FakeNewsSummariesNamespace(self)
        self.process = FakeProcessNamespace(self)

class FakeProcessNamespace:
    def __init__(self, adapter: FakeAdapter) -> None:
        self._adapter = adapter

    def fetch_primary_for_scoring(self, limit: int):
        return self._adapter.fetched[:limit]

    def update_primary_scores(self, updates):
        self._adapter.updates.extend(updates)


class FakeNewsSummariesNamespace:
    def __init__(self, adapter: FakeAdapter) -> None:
        self._adapter = adapter

    def upsert_from_primary(self, payloads):
        self._adapter.promotions.extend(payloads)


def test_keyword_bonus_applied(monkeypatch):
    item = PrimaryArticleForScoring(
        article_id="test-article",
        content="This content mentions the target keyword",
        title="",
        source=None,
        publish_time=None,
        publish_time_iso=None,
        url=None,
        keywords=[]
    )
    fake_adapter = FakeAdapter(fetched=[item], updates=[], promotions=[])
    monkeypatch.setattr(score, "get_adapter", lambda: fake_adapter)
    monkeypatch.setattr(
        score,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "default_concurrency": 1,
                "score_keyword_bonus_rules": {"target keyword": 25},
                "score_promotion_threshold": 60,
            },
        )(),
    )
    monkeypatch.setattr(score, "_score_item", lambda _: 50)

    score.run(limit=1, concurrency=1)

    assert fake_adapter.updates, "expected update payload"
    update = fake_adapter.updates[0]
    assert update["raw_relevance_score"] == 50
    assert update["keyword_bonus_score"] == 25
    assert update["score"] == 75
    assert update["status"] == "scored"
    assert update["score_details"]["matched_rules"]
    assert fake_adapter.promotions, "final score meeting threshold should be promoted"
    promotion = fake_adapter.promotions[0]
    assert promotion["raw_relevance_score"] == 50
    assert promotion["keyword_bonus_score"] == 25
    assert promotion["score"] == 75
    assert promotion["status"] == "pending"


def test_promotion_uses_final_threshold(monkeypatch):
    item = PrimaryArticleForScoring(
        article_id="promote-me",
        content="Beijing Municipal Party Committee content",
        title="",
        source=None,
        publish_time=None,
        publish_time_iso=None,
        url=None,
        keywords=[]
    )
    fake_adapter = FakeAdapter(fetched=[item], updates=[], promotions=[])
    monkeypatch.setattr(score, "get_adapter", lambda: fake_adapter)
    monkeypatch.setattr(
        score,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "default_concurrency": 1,
                "score_keyword_bonus_rules": {"Beijing Municipal Party Committee": 100},
                "score_promotion_threshold": 60,
            },
        )(),
    )
    monkeypatch.setattr(score, "_score_item", lambda _: 60)

    score.run(limit=1, concurrency=1)

    assert fake_adapter.promotions, "item meeting final threshold should be promoted"
    promotion = fake_adapter.promotions[0]
    assert promotion["raw_relevance_score"] == 60
    assert promotion["keyword_bonus_score"] == 100
    assert promotion["score"] == 160
    assert promotion["status"] == "pending"


def _scoring_item(article_id: str, content: str) -> PrimaryArticleForScoring:
    return PrimaryArticleForScoring(
        article_id=article_id,
        content=content,
        title="",
        source=None,
        publish_time=None,
        publish_time_iso=None,
        url=None,
        keywords=[],
    )


def test_keyword_precheck_skips_only_impossible_rows_before_thread_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skipped_item = _scoring_item("skip-me", "negative rule")
    scored_item = _scoring_item("score-me", "ordinary content")
    fake_adapter = FakeAdapter(
        fetched=[skipped_item, scored_item],
        updates=[],
        promotions=[],
    )
    calls: List[str] = []
    bonus_calls: List[str] = []
    log_messages: List[str] = []
    monkeypatch.setattr(score, "get_adapter", lambda: fake_adapter)
    monkeypatch.setattr(
        score,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "default_concurrency": 2,
                "score_keyword_bonus_rules": {"negative rule": -100},
                "score_promotion_threshold": 60,
            },
        )(),
    )

    def fake_score(item: PrimaryArticleForScoring) -> int:
        calls.append(item.article_id)
        return 80

    original_calculate = score._calculate_keyword_bonus

    def recording_calculate(
        item: PrimaryArticleForScoring,
        rules: Dict[str, int],
    ) -> Tuple[int, List[Dict[str, Any]]]:
        bonus_calls.append(item.article_id)
        return original_calculate(item, rules)

    monkeypatch.setattr(score, "_score_item", fake_score)
    monkeypatch.setattr(score, "_calculate_keyword_bonus", recording_calculate)
    monkeypatch.setattr(
        score,
        "log_info",
        lambda worker, message: log_messages.append(message),
    )

    score.run(limit=2, concurrency=2)

    assert calls == ["score-me"]
    assert sorted(bonus_calls) == ["score-me", "skip-me"]
    skipped_update = next(
        update for update in fake_adapter.updates
        if update["article_id"] == "skip-me"
    )
    assert skipped_update["status"] == "filtered_out"
    assert skipped_update["raw_relevance_score"] is None
    assert skipped_update["keyword_bonus_score"] == -100
    assert skipped_update["score"] == -100
    assert skipped_update["score_details"] == {
        "raw_relevance_score": None,
        "keyword_bonus_score": -100,
        "final_score": -100,
        "matched_rules": [
            {
                "rule_id": "keyword:negative rule",
                "label": "negative rule",
                "bonus": -100,
            }
        ],
        "llm_skipped": True,
        "skip_reason": "keyword_bonus_below_threshold",
    }
    assert any("Skipped 1 LLM calls" in message for message in log_messages)


@pytest.mark.parametrize(
    ("rules", "content", "threshold", "expected_bonus"),
    [
        (
            {"negative": -100, "positive-a": 100, "positive-b": 100},
            "negative positive-a positive-b",
            60,
            100,
        ),
        ({}, "ordinary content", 101, 0),
        (
            {"negative": -100, "positive": 50},
            "negative positive",
            50,
            -50,
        ),
    ],
)
def test_keyword_precheck_boundary_cases_still_call_model(
    monkeypatch: pytest.MonkeyPatch,
    rules: Dict[str, int],
    content: str,
    threshold: int,
    expected_bonus: int,
) -> None:
    item = _scoring_item("boundary", content)
    calls: List[str] = []

    def fake_score(candidate: PrimaryArticleForScoring) -> int:
        calls.append(candidate.article_id)
        return 50

    monkeypatch.setattr(score, "_score_item", fake_score)

    successes, failures, skipped = score._process_scores_multi_worker(
        [item],
        2,
        rules,
        threshold,
    )

    assert calls == ["boundary"]
    assert failures == []
    assert skipped == 0
    assert successes[0][2] == expected_bonus
    assert successes[0][4].get("llm_skipped") is None
