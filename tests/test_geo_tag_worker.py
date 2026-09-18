from __future__ import annotations

from typing import List, Tuple

import pytest

from src.business_config import business_config_context
from src.workers import geo_tag
from tests.conftest import make_endpoint_config


class FakeProcessNamespace:
    def __init__(self, adapter: FakeAdapter) -> None:
        self._adapter = adapter

    def fetch_beijing_tag_candidates(self, limit: int):
        self._adapter._fetch_calls += 1
        if self._adapter._fetch_calls > 1:
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
        self._adapter.updates.append(list(updates))
        return len(updates)


class FakeAdapter:
    def __init__(self) -> None:
        self._fetch_calls = 0
        self.updates: List[List[Tuple[str, bool]]] = []
        self.process = FakeProcessNamespace(self)


@pytest.fixture()
def fake_environment(monkeypatch):
    adapter = FakeAdapter()

    monkeypatch.setattr(geo_tag, "get_adapter", lambda: adapter)

    config = make_endpoint_config(beijing_keywords=("北京", "延庆"))
    with business_config_context(config):
        yield adapter


def test_geo_tag_run_updates_articles(fake_environment):
    adapter = fake_environment

    geo_tag.run(limit=None, batch_size=10)

    assert adapter.updates
    assert ("a-1", True) in adapter.updates[0]
    assert ("a-2", True) in adapter.updates[0]


def test_geo_tag_uses_frozen_keywords_from_business_config(monkeypatch):
    """geo-tag 只能读冻结上下文里的京内词；重新加载数据库配置属越界。"""

    adapter = FakeAdapter()
    monkeypatch.setattr(geo_tag, "get_adapter", lambda: adapter)

    def forbidden_load(*_args, **_kwargs):
        raise AssertionError("geo-tag 必须使用冻结配置，而不是重新加载 business config")

    monkeypatch.setattr(
        "src.business_config.load_business_config", forbidden_load
    )

    config = make_endpoint_config(beijing_keywords=("冰城哈尔滨",))
    with business_config_context(config):
        geo_tag.run(limit=None, batch_size=10)

    # 换成一个不会命中候选正文的词：全部判为京外，但流程照常写回
    assert adapter.updates
    assert all(
        is_related is False
        for batch in adapter.updates
        for _, is_related in batch
    )
