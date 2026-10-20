from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pytest

from src.business_config import ScoreKeywordBonus, business_config_context
from src.domain.models import PrimaryArticleForScoring
from src.workers import score
from tests.conftest import make_endpoint_config


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


def _frozen_context(bonuses: tuple[ScoreKeywordBonus, ...]):
    config = make_endpoint_config(score_keyword_bonuses=bonuses)
    return business_config_context(config)


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
                "score_promotion_threshold": 60,
            },
        )(),
    )
    monkeypatch.setattr(score, "_score_item", lambda _: 50)

    with _frozen_context((ScoreKeywordBonus(keyword="target keyword", bonus=25),)):
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
                "score_promotion_threshold": 60,
            },
        )(),
    )
    monkeypatch.setattr(score, "_score_item", lambda _: 60)

    with _frozen_context(
        (ScoreKeywordBonus(keyword="Beijing Municipal Party Committee", bonus=100),)
    ):
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

    with _frozen_context((ScoreKeywordBonus(keyword="negative rule", bonus=-100),)):
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

    successes, failures, excluded, bonus_skipped = score._process_scores_multi_worker(
        [item],
        2,
        rules,
        [],
        threshold,
    )

    assert calls == ["boundary"]
    assert failures == []
    assert excluded == 0
    assert bonus_skipped == 0
    assert successes[0][2] == expected_bonus
    assert successes[0][4].get("llm_skipped") is None


# ---------------------------------------------------------------------------
# 评分排除名单（黑名单）：排除优先于加分与 bonus skip


def _exclusion_item(
    article_id: str,
    *,
    title: str = "",
    content: str = "普通正文",
    keywords: Optional[List[str]] = None,
) -> PrimaryArticleForScoring:
    return PrimaryArticleForScoring(
        article_id=article_id,
        content=content,
        title=title,
        source=None,
        publish_time=None,
        publish_time_iso=None,
        url=None,
        keywords=list(keywords or []),
    )


def _run_with_fake_model(
    monkeypatch: pytest.MonkeyPatch,
    items: List[PrimaryArticleForScoring],
    *,
    bonuses: Tuple[ScoreKeywordBonus, ...] = (),
    exclusions: Tuple[str, ...] = (),
    threshold: int = 60,
    concurrency: int = 1,
    raw_score: int = 50,
) -> Tuple[FakeAdapter, List[str], List[str]]:
    fake_adapter = FakeAdapter(fetched=items, updates=[], promotions=[])
    calls: List[str] = []
    log_messages: List[str] = []
    monkeypatch.setattr(score, "get_adapter", lambda: fake_adapter)
    monkeypatch.setattr(
        score,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "default_concurrency": concurrency,
                "score_promotion_threshold": threshold,
            },
        )(),
    )

    def fake_score(item: PrimaryArticleForScoring) -> int:
        calls.append(item.article_id)
        return raw_score

    monkeypatch.setattr(score, "_score_item", fake_score)
    monkeypatch.setattr(score, "log_info", lambda _worker, message: log_messages.append(message))
    config = make_endpoint_config(
        score_keyword_bonuses=bonuses,
        score_exclusion_keywords=exclusions,
    )
    with business_config_context(config):
        score.run(limit=len(items), concurrency=concurrency)
    return fake_adapter, calls, log_messages


def _update_for(adapter: FakeAdapter, article_id: str) -> Dict[str, Any]:
    return next(
        update for update in adapter.updates if update["article_id"] == article_id
    )


def test_keyword_exclusion_hit_skips_model_and_filters_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _exclusion_item("excluded-1", content="正文提到考试改革")
    fake_adapter, calls, _logs = _run_with_fake_model(
        monkeypatch,
        [item],
        exclusions=("考试",),
    )

    assert calls == []
    update = _update_for(fake_adapter, "excluded-1")
    assert update["status"] == "filtered_out"
    assert update["score"] is None
    assert update["raw_relevance_score"] is None
    assert update["keyword_bonus_score"] == 0
    assert update["score_details"]["matched_rules"] == []
    assert update["score_details"]["final_score"] is None
    assert update["score_details"]["raw_relevance_score"] is None
    assert update["score_details"]["llm_skipped"] is True
    assert update["score_details"]["skip_reason"] == "keyword_excluded"
    assert update["score_details"]["excluded_rules"] == [
        {"rule_id": "exclude:考试", "label": "考试"}
    ]
    assert fake_adapter.promotions == []


def test_keyword_exclusion_beats_positive_bonus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _exclusion_item("excluded-2", content="加分词 与 排除词 同时出现")
    fake_adapter, calls, _logs = _run_with_fake_model(
        monkeypatch,
        [item],
        bonuses=(ScoreKeywordBonus(keyword="加分词", bonus=100),),
        exclusions=("排除词",),
    )

    assert calls == []
    update = _update_for(fake_adapter, "excluded-2")
    assert update["status"] == "filtered_out"
    assert update["keyword_bonus_score"] == 0
    assert update["score_details"]["matched_rules"] == []
    assert update["score_details"]["skip_reason"] == "keyword_excluded"
    assert fake_adapter.promotions == []


@pytest.mark.parametrize(
    ("title", "content", "keywords"),
    [
        ("考试改革方案发布", "无关正文", []),
        ("普通标题", "正文里提到考试改革", []),
        ("普通标题", "无关正文", ["考试"]),
    ],
    ids=["title", "content", "keywords"],
)
def test_keyword_exclusion_matches_all_three_text_sources(
    monkeypatch: pytest.MonkeyPatch,
    title: str,
    content: str,
    keywords: List[str],
) -> None:
    item = _exclusion_item(
        "excluded-source",
        title=title,
        content=content,
        keywords=keywords,
    )
    fake_adapter, calls, _logs = _run_with_fake_model(
        monkeypatch,
        [item],
        exclusions=("考试",),
    )

    assert calls == []
    update = _update_for(fake_adapter, "excluded-source")
    assert update["status"] == "filtered_out"
    assert update["score_details"]["skip_reason"] == "keyword_excluded"
    assert update["score_details"]["excluded_rules"] == [
        {"rule_id": "exclude:考试", "label": "考试"}
    ]


def test_empty_exclusion_list_keeps_normal_scoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _exclusion_item("normal-1", content="正文提到考试改革")
    fake_adapter, calls, _logs = _run_with_fake_model(
        monkeypatch,
        [item],
        bonuses=(ScoreKeywordBonus(keyword="考试", bonus=10),),
        exclusions=(),
        raw_score=60,
    )

    assert calls == ["normal-1"]
    update = _update_for(fake_adapter, "normal-1")
    assert update["status"] == "scored"
    assert update["score"] == 70
    assert update["keyword_bonus_score"] == 10
    assert update["score_details"].get("skip_reason") is None


def test_non_matching_exclusion_list_keeps_normal_scoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _exclusion_item("normal-2", content="与名单无关的正文")
    fake_adapter, calls, _logs = _run_with_fake_model(
        monkeypatch,
        [item],
        exclusions=("考试", "培训"),
        raw_score=80,
    )

    assert calls == ["normal-2"]
    update = _update_for(fake_adapter, "normal-2")
    assert update["status"] == "scored"
    assert update["score"] == 80
    assert "excluded_rules" not in update["score_details"]


def test_keyword_exclusion_wins_over_bonus_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _exclusion_item("excluded-low", content="考试 与 减分词 同时出现")
    fake_adapter, calls, _logs = _run_with_fake_model(
        monkeypatch,
        [item],
        bonuses=(ScoreKeywordBonus(keyword="减分词", bonus=-100),),
        exclusions=("考试",),
        threshold=60,
    )

    assert calls == []
    update = _update_for(fake_adapter, "excluded-low")
    assert update["status"] == "filtered_out"
    assert update["score"] is None
    assert update["score_details"]["skip_reason"] == "keyword_excluded"
    assert update["score_details"]["skip_reason"] != score.KEYWORD_SKIP_REASON


def test_skip_log_counts_exclusions_and_bonus_skips_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    excluded = _exclusion_item("excluded-log", content="正文提到考试")
    bonus_skipped = _exclusion_item("bonus-log", content="正文提到减分词")
    scored = _exclusion_item("scored-log", content="普通正文")
    _adapter, calls, logs = _run_with_fake_model(
        monkeypatch,
        [excluded, bonus_skipped, scored],
        bonuses=(ScoreKeywordBonus(keyword="减分词", bonus=-100),),
        exclusions=("考试",),
        threshold=60,
    )

    assert calls == ["scored-log"]
    skip_logs = [message for message in logs if message.startswith("Skipped ")]
    assert len(skip_logs) == 1
    assert "Skipped 2 LLM calls" in skip_logs[0]
    assert "1 keyword exclusions" in skip_logs[0]
    assert "1 bonus below threshold" in skip_logs[0]
