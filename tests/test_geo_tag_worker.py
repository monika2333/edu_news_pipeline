from __future__ import annotations

from types import SimpleNamespace
from typing import List, Tuple

import pytest

from src.workers import geo_tag


class FakeAdapter:
    def __init__(self) -> None:
        self._fetch_calls = 0
        self.updates: List[List[Tuple[str, bool]]] = []

    def fetch_beijing_tag_candidates(self, limit: int):
        self._fetch_calls += 1
        if self._fetch_calls > 1:
            return []
        return [
            {
                "article_id": "a-1",
                "content_markdown": "北京举办教育论坛",
                "llm_summary": "",
                "llm_keywords": [],
            },
            {
                "article_id": "a-2",
                "content_markdown": "外省高校资讯",
                "llm_summary": "",
                "llm_keywords": ["延庆"],
            },
        ]

    def update_beijing_related_bulk(self, updates):
        self.updates.append(list(updates))
        return len(updates)


@pytest.fixture()
def fake_environment(monkeypatch):
    adapter = FakeAdapter()
    settings = SimpleNamespace(beijing_keywords_path=None)

    monkeypatch.setattr(geo_tag, "get_adapter", lambda: adapter)
    monkeypatch.setattr(geo_tag, "get_settings", lambda: settings)
    monkeypatch.setattr(geo_tag, "load_beijing_keywords", lambda path: {"北京", "延庆"})

    yield adapter


def test_geo_tag_run_updates_articles(fake_environment):
    adapter = fake_environment

    geo_tag.run(limit=None, batch_size=10)

    assert adapter.updates
    assert ("a-1", True) in adapter.updates[0]
    assert ("a-2", True) in adapter.updates[0]
