from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest

from src.domain.models import PrimaryArticleForScoring
from src.workers import score


@dataclass
class FakeAdapter:
    fetched: List[PrimaryArticleForScoring]
    updates: List[Dict[str, Any]]
    promotions: List[Dict[str, Any]]

    def fetch_primary_articles_for_scoring(self, limit: int):
        return self.fetched[:limit]

    def update_primary_article_scores(self, updates):
        self.updates.extend(updates)

    def upsert_news_summaries_from_primary(self, payloads):
        self.promotions.extend(payloads)


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
