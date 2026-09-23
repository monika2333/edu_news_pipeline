"""Settings phase 2: four wordlist sections in app_settings.

Covers the section validators, the load/freeze path, the run snapshot, and
the four worker consumption points. Mutation coverage:
- M1  crawl gate reads frozen education_keywords
- M2  education_keywords rejects empty-after-normalization
- M3  beijing_keywords rejects empty-after-normalization
- M4  score has no hidden default fallback and reads frozen config
- M5  snapshot records full content (not just versions) of all four sections
- M6  workers read the frozen context, never reload the database
- M7  geo_classify routes by configured Beijing keywords
- M8  enrich_summary normalizes by configured source aliases
- M11 validator rejects duplicate bonus keywords
- M12 bonus section is an ordered list, order preserved end to end
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest

from src import business_config
from src.business_config import (
    BusinessConfigError,
    ScoreKeywordBonus,
    business_config_context,
    get_business_config,
    load_business_config,
)
from src.domain import SourceAliasRules, normalize_source_name
from src.workers import crawl_sources, enrich_summary, geo_classify, score
from tests.conftest import make_endpoint_config

# ---------------------------------------------------------------------------
# shared fixtures


def _endpoint_items_value() -> dict[str, Any]:
    return {
        "default": "openrouter",
        "items": [
            {
                "key": "openrouter",
                "label": "OpenRouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_env": "LLM_API_KEY",
                "api_style": "openrouter",
                "temperature_override": None,
            }
        ],
    }


def _settings_rows() -> list[dict[str, Any]]:
    return [
        {
            "section": "llm_models",
            "value": {
                "default": "default-model",
                "steps": {
                    step: {"model": None, "reasoning": False}
                    for step in (
                        "summary",
                        "source",
                        "sentiment",
                        "scoring",
                        "external_filter",
                        "beijing_gate",
                        "duplicate_review",
                    )
                },
            },
            "version": 3,
        },
        {
            "section": "llm_endpoints",
            "value": _endpoint_items_value(),
            "version": 2,
        },
        {"section": "crawl_sources", "value": ["toutiao"], "version": 5},
        {
            "section": "score_keyword_bonuses",
            "value": [
                {"keyword": "教育工委", "bonus": 100},
                {"keyword": "高考", "bonus": 10},
            ],
            "version": 7,
        },
        {"section": "education_keywords", "value": ["教育", "学校"], "version": 1},
        {"section": "beijing_keywords", "value": ["北京", "海淀"], "version": 4},
        {
            "section": "source_aliases",
            "value": {"suffixes": ["客户端"], "aliases": {"北京号": "北京日报"}},
            "version": 2,
        },
        {
            "section": "review_sort_keywords",
            "value": {"市教委": ["市教委"], "中小学": ["小学"], "高校": ["大学"]},
            "version": 1,
        },
    ]


def _fake_adapter(rows: list[dict[str, Any]]) -> Any:
    app_config = SimpleNamespace(
        fetch_settings=lambda: rows,
        fetch_enabled_accounts=lambda: [],
    )
    return SimpleNamespace(app_config=app_config)


# ---------------------------------------------------------------------------
# section validators


class TestScoreKeywordBonusesValidator:
    def test_ordered_list_is_normalized_and_order_preserved(self) -> None:
        value = [
            {"keyword": " 高考 ", "bonus": 10},
            {"keyword": "教育工委", "bonus": 100},
        ]

        normalized = business_config.validate_score_keyword_bonuses(value)

        # M12: stored as an ordered list, keyword order survives normalization
        assert normalized == [
            {"keyword": "高考", "bonus": 10},
            {"keyword": "教育工委", "bonus": 100},
        ]

    def test_empty_list_is_allowed_and_means_no_bonus(self) -> None:
        assert business_config.validate_score_keyword_bonuses([]) == []

    def test_object_form_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="有序数组"):
            business_config.validate_score_keyword_bonuses({"高考": 10})

    @pytest.mark.parametrize(
        ("bonus", "message"),
        [
            ("10", "必须是整数"),
            (10.5, "必须是整数"),
            (True, "布尔值不算"),
            (101, "-100 到 100"),
            (-101, "-100 到 100"),
        ],
    )
    def test_invalid_bonus_values_are_rejected(self, bonus: Any, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            business_config.validate_score_keyword_bonuses(
                [{"keyword": "高考", "bonus": bonus}]
            )

    def test_duplicate_keyword_is_rejected(self) -> None:
        # M11: {"a": 1, " a ": 2} strips to the same keyword twice
        with pytest.raises(ValueError, match="关键词重复"):
            business_config.validate_score_keyword_bonuses(
                [
                    {"keyword": "高考", "bonus": 10},
                    {"keyword": " 高考 ", "bonus": 20},
                ]
            )

    def test_blank_or_missing_keyword_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="keyword 不能为空"):
            business_config.validate_score_keyword_bonuses(
                [{"keyword": "   ", "bonus": 1}]
            )
        with pytest.raises(ValueError, match="keyword 不能为空"):
            business_config.validate_score_keyword_bonuses([{"bonus": 1}])

    def test_unknown_fields_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="未知字段"):
            business_config.validate_score_keyword_bonuses(
                [{"keyword": "高考", "bonus": 1, "weight": 2}]
            )


class TestKeywordListValidators:
    @pytest.mark.parametrize(
        "validator",
        [business_config.validate_education_keywords, business_config.validate_beijing_keywords],
    )
    def test_strip_drop_empty_and_silent_dedupe(self, validator) -> None:
        assert validator([" 教育 ", "教育", "", "  ", "学校"]) == ["教育", "学校"]

    @pytest.mark.parametrize(
        ("validator", "label"),
        [
            (business_config.validate_education_keywords, "education_keywords"),
            (business_config.validate_beijing_keywords, "beijing_keywords"),
        ],
    )
    def test_empty_after_normalization_is_rejected(self, validator, label) -> None:
        # M2/M3: an empty list would silently admit everything (crawl gate)
        # or classify everything as outside Beijing (local routing).
        for value in ([], ["   ", ""], None, "教育", ["教育", 3]):
            with pytest.raises(ValueError):
                validator(value)


class TestSourceAliasesValidator:
    def test_valid_structure_passes(self) -> None:
        normalized = business_config.validate_source_aliases(
            {"suffixes": ["客户端"], "aliases": {"北京号": "北京日报"}}
        )

        assert normalized == {
            "suffixes": ["客户端"],
            "aliases": {"北京号": "北京日报"},
        }

    def test_both_empty_is_allowed_and_means_no_normalization(self) -> None:
        assert business_config.validate_source_aliases(
            {"suffixes": [], "aliases": {}}
        ) == {"suffixes": [], "aliases": {}}

    def test_invalid_entries_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="suffixes"):
            business_config.validate_source_aliases(
                {"suffixes": ["客户端", ""], "aliases": {}}
            )
        with pytest.raises(ValueError, match="aliases"):
            business_config.validate_source_aliases(
                {"suffixes": [], "aliases": {"北京号": ""}}
            )
        with pytest.raises(ValueError, match="未知字段"):
            business_config.validate_source_aliases(
                {"suffixes": [], "aliases": {}, "extra": 1}
            )

    def test_resolve_returns_domain_rules(self) -> None:
        rules = business_config.resolve_source_aliases(
            {"suffixes": ["客户端"], "aliases": {"北京号": "北京日报"}}
        )

        assert isinstance(rules, SourceAliasRules)
        assert rules.suffixes == ("客户端",)
        assert rules.aliases == {"北京号": "北京日报"}


def test_source_aliases_suffix_order_is_preserved_end_to_end() -> None:
    """T4: suffixes 的顺序有意义，校验与加载都不得排序。

    顺序特意取成非字典序：sorted 会变成 ["APP", "客户端"]。
    """

    value = {"suffixes": ["客户端", "APP"], "aliases": {}}
    assert sorted(value["suffixes"]) == ["APP", "客户端"]

    assert (
        business_config.validate_source_aliases(value)["suffixes"]
        == ["客户端", "APP"]
    )

    rows = _settings_rows()
    next(row for row in rows if row["section"] == "source_aliases")["value"] = value
    loaded = load_business_config(_fake_adapter(rows))
    assert loaded.source_aliases.suffixes == ("客户端", "APP")


def test_suffix_order_decides_which_suffix_is_stripped() -> None:
    """「北京晚报客户端」同时以「晚报客户端」和「客户端」结尾：
    只剥离排在前面的「晚报客户端」。若被排序成字典序（「客户端」在前），
    同一个名字会被剥成「北京晚报」。"""

    value = {"suffixes": ["晚报客户端", "客户端"], "aliases": {}}
    assert sorted(value["suffixes"]) == ["客户端", "晚报客户端"]

    rules = business_config.resolve_source_aliases(value)
    assert rules.suffixes == ("晚报客户端", "客户端")
    assert normalize_source_name("北京晚报客户端", rules) == "北京"

    rows = _settings_rows()
    next(row for row in rows if row["section"] == "source_aliases")["value"] = value
    loaded = load_business_config(_fake_adapter(rows))
    assert normalize_source_name("北京晚报客户端", loaded.source_aliases) == "北京"


def test_validate_section_dispatches_new_sections() -> None:
    assert business_config.validate_section(
        "score_keyword_bonuses", [{"keyword": "高考", "bonus": 10}]
    ) == [{"keyword": "高考", "bonus": 10}]
    assert business_config.validate_section("education_keywords", ["教育"]) == ["教育"]
    assert business_config.validate_section("beijing_keywords", ["北京"]) == ["北京"]
    assert business_config.validate_section(
        "source_aliases", {"suffixes": [], "aliases": {}}
    ) == {"suffixes": [], "aliases": {}}


# ---------------------------------------------------------------------------
# load_business_config and snapshot


@pytest.mark.parametrize(
    "section",
    [
        "score_keyword_bonuses",
        "education_keywords",
        "beijing_keywords",
        "source_aliases",
    ],
)
def test_load_requires_each_new_section(section: str) -> None:
    rows = [row for row in _settings_rows() if row["section"] != section]

    with pytest.raises(BusinessConfigError, match=section):
        load_business_config(_fake_adapter(rows))


@pytest.mark.parametrize(
    ("section", "invalid_value"),
    [
        # 加分词典：分值不是整数
        ("score_keyword_bonuses", [{"keyword": "高考", "bonus": "10"}]),
        # 教育关键词：空列表等于抓取全部放行，加载路径必须拒绝（T3）
        ("education_keywords", []),
        # 京内关键词：空列表等于全部判京外，加载路径必须拒绝（T3）
        ("beijing_keywords", []),
        # 来源别名：别名值为空字符串
        ("source_aliases", {"suffixes": ["客户端"], "aliases": {"北京号": ""}}),
    ],
)
def test_load_rejects_invalid_stored_wordlists(
    section: str,
    invalid_value: Any,
) -> None:
    rows = _settings_rows()
    next(row for row in rows if row["section"] == section)["value"] = invalid_value

    with pytest.raises(BusinessConfigError, match="数据库业务配置无效"):
        load_business_config(_fake_adapter(rows))


def test_load_resolves_wordlists_and_versions() -> None:
    loaded = load_business_config(_fake_adapter(_settings_rows()))

    assert loaded.score_keyword_bonuses == (
        ScoreKeywordBonus(keyword="教育工委", bonus=100),
        ScoreKeywordBonus(keyword="高考", bonus=10),
    )
    # M12 的读取半边：bonus 规则必须保持配置顺序，不允许读取时打乱
    assert list(loaded.score_bonus_rules().items()) == [
        ("教育工委", 100),
        ("高考", 10),
    ]
    assert loaded.education_keywords == ("教育", "学校")
    assert loaded.beijing_keywords == ("北京", "海淀")
    assert loaded.source_aliases == SourceAliasRules(
        suffixes=("客户端",),
        aliases={"北京号": "北京日报"},
    )
    assert loaded.versions == {
        "llm_endpoints": 2,
        "llm_models": 3,
        "crawl_sources": 5,
        "score_keyword_bonuses": 7,
        "education_keywords": 1,
        "beijing_keywords": 4,
        "source_aliases": 2,
        "review_sort_keywords": 1,
    }


def test_snapshot_records_full_wordlist_content_and_versions() -> None:
    # M5: the snapshot is the only config history; it must carry the complete
    # content of every wordlist section, not just its version number.
    loaded = load_business_config(_fake_adapter(_settings_rows()))

    snapshot = loaded.snapshot()

    assert snapshot["score_keyword_bonuses"] == [
        {"keyword": "教育工委", "bonus": 100},
        {"keyword": "高考", "bonus": 10},
    ]
    assert snapshot["education_keywords"] == ["教育", "学校"]
    assert snapshot["beijing_keywords"] == ["北京", "海淀"]
    assert snapshot["source_aliases"] == {
        "suffixes": ["客户端"],
        "aliases": {"北京号": "北京日报"},
    }
    assert snapshot["review_sort_keywords"] == {
        "市教委": ["市教委"],
        "中小学": ["小学"],
        "高校": ["大学"],
    }
    assert set(snapshot["versions"]) == set(business_config.SETTING_SECTIONS)


def test_snapshot_order_survives_replace_with_crawl_sources() -> None:
    loaded = load_business_config(_fake_adapter(_settings_rows()))

    snapshot = loaded.with_crawl_sources(["gmw"]).snapshot()

    assert snapshot["score_keyword_bonuses"][0]["keyword"] == "教育工委"


# ---------------------------------------------------------------------------
# M4: score worker


def _scoring_item(article_id: str, content: str):
    from src.domain.models import PrimaryArticleForScoring

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


class _ScoreAdapter:
    def __init__(self, rows) -> None:
        self.fetched = rows
        self.updates: list[dict[str, Any]] = []
        self.promotions: list[dict[str, Any]] = []
        self.process = SimpleNamespace(
            fetch_primary_for_scoring=lambda limit: self.fetched[:limit],
            update_primary_scores=self.updates.extend,
        )
        self.news_summaries = SimpleNamespace(upsert_from_primary=self.promotions.extend)


def test_score_applies_bonus_only_from_frozen_config(monkeypatch) -> None:
    # M4: the feature keyword exists only in the frozen config; the legacy
    # in-code defaults must not leak back in.
    adapter = _ScoreAdapter([_scoring_item("a-1", "正文提到特征词麻辣教联体")])
    monkeypatch.setattr(score, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        score,
        "get_settings",
        lambda: SimpleNamespace(default_concurrency=1, score_promotion_threshold=60),
    )
    monkeypatch.setattr(score, "_score_item", lambda _item: 50)

    config = make_endpoint_config(
        score_keyword_bonuses=(ScoreKeywordBonus(keyword="麻辣教联体", bonus=25),)
    )
    with business_config_context(config):
        score.run(limit=1, concurrency=1)

    update = adapter.updates[0]
    assert update["keyword_bonus_score"] == 25
    assert update["score"] == 75
    assert update["status"] == "scored"
    assert update["score_details"]["matched_rules"] == [
        {"rule_id": "keyword:麻辣教联体", "label": "麻辣教联体", "bonus": 25}
    ]


def test_score_empty_bonus_list_means_no_bonus_without_fallback(monkeypatch) -> None:
    # M4 (fallback half): content matching the old in-code defaults must not
    # earn any bonus when the configured list is empty.
    adapter = _ScoreAdapter([_scoring_item("a-2", "北京市委教育工委发布通知")])
    monkeypatch.setattr(score, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        score,
        "get_settings",
        lambda: SimpleNamespace(default_concurrency=1, score_promotion_threshold=60),
    )
    monkeypatch.setattr(score, "_score_item", lambda _item: 50)

    with business_config_context(make_endpoint_config()):
        score.run(limit=1, concurrency=1)

    update = adapter.updates[0]
    assert update["keyword_bonus_score"] == 0
    assert update["score_details"]["matched_rules"] == []
    assert update["score_details"].get("llm_skipped") is None
    assert update["score"] == 50


# ---------------------------------------------------------------------------
# M1: crawl education-keyword gate


def test_crawl_gate_uses_frozen_education_keywords(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    persisted: list[list[dict[str, Any]]] = []

    class _StubRegistration:
        runner_name = "_stub_flow"
        aliases: tuple[str, ...] = ()
        linked_page = None

        def run(self, context: crawl_sources.SourceRunContext) -> crawl_sources.CrawlStats:
            captured["keywords"] = tuple(context.keywords)
            return {"consumed": 0, "ok": 0, "failed": 0, "skipped": 0}

    adapter = SimpleNamespace(
        ingest=SimpleNamespace(
            get_seen_raw_tokens=lambda: set(),
            upsert_filtered=lambda rows: persisted.append(list(rows)) or len(rows),
        ),
    )
    monkeypatch.setattr(
        crawl_sources,
        "_get_source_registration",
        lambda _source: _StubRegistration(),
    )
    monkeypatch.setattr(crawl_sources, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        crawl_sources,
        "get_settings",
        lambda: SimpleNamespace(process_limit=None),
    )
    monkeypatch.setattr(
        crawl_sources,
        "worker_session",
        lambda *args, **kwargs: contextlib.nullcontext(),
    )

    config = make_endpoint_config(education_keywords=("麻辣教联体",))
    with business_config_context(config):
        crawl_sources.run(limit=10, sources=["gmw"])

    # M1: the gate keywords come from the frozen config, not from a file or
    # an empty list.
    assert captured["keywords"] == ("麻辣教联体",)

    matching_row = {
        "article_id": "hit",
        "content_markdown": "正文提到麻辣教联体改革",
        "title": None,
        "source": None,
        "publish_time": None,
        "publish_time_iso": None,
        "url": None,
    }
    missing_row = {**matching_row, "article_id": "miss", "content_markdown": "无关正文"}
    crawl_sources._queue_filtered_rows(
        adapter,
        [matching_row, missing_row],
        source="gmw",
        keywords=captured["keywords"],
    )

    assert [row["article_id"] for row in persisted[0]] == ["hit"]


# ---------------------------------------------------------------------------
# M7: geo_classify local routing


def test_geo_classify_routes_by_configured_beijing_keywords(monkeypatch) -> None:
    routed: list[tuple[str, Any, str]] = []

    class _NewsSummaries:
        def fetch_pending_routes(self, limit: int) -> list[dict[str, Any]]:
            return [
                {
                    "article_id": "g-1",
                    "title": "配置独有词教学楼启用",
                    "content_markdown": "正文",
                    "llm_summary": "摘要",
                    "sentiment_label": "positive",
                }
            ][:limit]

        def complete_routing(self, article_id, *, beijing_related, status) -> None:
            routed.append((article_id, beijing_related, status))

    adapter = SimpleNamespace(
        news_summaries=_NewsSummaries(),
        process=SimpleNamespace(
            fetch_beijing_gate_candidates=lambda limit, *, max_failures: []
        ),
    )
    monkeypatch.setattr(geo_classify, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        geo_classify,
        "get_settings",
        lambda: SimpleNamespace(
            process_limit=None,
            default_concurrency=1,
            external_filter_batch_size=10,
            beijing_gate_max_retries=1,
        ),
    )
    monkeypatch.setattr(
        geo_classify, "call_beijing_gate", lambda candidate, retries: None
    )

    # M7: the keyword exists only in the frozen config.
    config = make_endpoint_config(beijing_keywords=("配置独有词",))
    with business_config_context(config):
        geo_classify.run(limit=5, concurrency=1)

    assert routed == [("g-1", True, "pending_beijing_gate")]


# ---------------------------------------------------------------------------
# M8: enrich_summary alias normalization


def test_enrich_summary_normalizes_by_configured_aliases(monkeypatch) -> None:
    completed: list[tuple[str, str, Any, Any]] = []

    class _NewsSummaries:
        def fetch_pending_enrichments(self, limit: int) -> list[dict[str, Any]]:
            return [
                {
                    "article_id": "e-1",
                    "title": "标题",
                    "content_markdown": "正文",
                    "llm_summary": "摘要",
                }
            ][:limit]

        def complete_enrichment(self, article_id, *, label, confidence, llm_source):
            completed.append((article_id, label, confidence, llm_source))

    adapter = SimpleNamespace(news_summaries=_NewsSummaries())
    monkeypatch.setattr(enrich_summary, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        enrich_summary,
        "get_settings",
        lambda: SimpleNamespace(
            process_limit=None, summary_concurrency=1, default_concurrency=1
        ),
    )
    monkeypatch.setattr(
        enrich_summary,
        "classify_sentiment",
        lambda summary_text: {"label": "positive", "confidence": 0.9},
    )
    monkeypatch.setattr(
        enrich_summary,
        "detect_source",
        lambda article: {"llm_source": "别名独有源"},
    )

    # M8: the alias exists only in the frozen config.
    config = make_endpoint_config(
        source_aliases=SourceAliasRules(aliases={"别名独有源": "归一后的源"})
    )
    with business_config_context(config):
        enrich_summary.run(limit=1, concurrency=1)

    assert completed == [("e-1", "positive", 0.9, "归一后的源")]


# ---------------------------------------------------------------------------
# M6: frozen context wins over a mid-run database change


def test_geo_classify_keeps_frozen_keywords_when_database_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_rows = _settings_rows()
    changed_rows = [
        {
            **row,
            "value": ("库里新词",)
            if row["section"] == "beijing_keywords"
            else row["value"],
        }
        for row in _settings_rows()
    ]

    fetch_calls = 0
    routed: list[tuple[str, Any, str]] = []

    class _SharedAppConfig:
        def fetch_settings(self):
            nonlocal fetch_calls
            fetch_calls += 1
            # 数据库配置在冻结之后"被改掉"：京内词换成了库里新词
            return changed_rows

        def fetch_enabled_accounts(self):
            return []

    class _NewsSummaries:
        def fetch_pending_routes(self, limit: int) -> list[dict[str, Any]]:
            return [
                {
                    "article_id": "f-1",
                    "title": "海淀教学楼启用",
                    "content_markdown": "正文",
                    "llm_summary": "摘要",
                    "sentiment_label": "positive",
                }
            ][:limit]

        def complete_routing(self, article_id, *, beijing_related, status) -> None:
            routed.append((article_id, beijing_related, status))

    shared_adapter = SimpleNamespace(
        app_config=_SharedAppConfig(),
        news_summaries=_NewsSummaries(),
        process=SimpleNamespace(
            fetch_beijing_gate_candidates=lambda limit, *, max_failures: []
        ),
    )
    monkeypatch.setattr(geo_classify, "get_adapter", lambda: shared_adapter)
    monkeypatch.setattr(
        "src.adapters.db_postgres_core.get_adapter", lambda: shared_adapter
    )
    monkeypatch.setattr(
        geo_classify,
        "get_settings",
        lambda: SimpleNamespace(
            process_limit=None,
            default_concurrency=1,
            external_filter_batch_size=10,
            beijing_gate_max_retries=1,
        ),
    )
    monkeypatch.setattr(
        geo_classify, "call_beijing_gate", lambda candidate, retries: None
    )

    frozen_config = load_business_config(_fake_adapter(frozen_rows))
    assert "海淀" in frozen_config.beijing_keywords
    with business_config_context(frozen_config):
        geo_classify.run(limit=5, concurrency=1)

    # 冻结配置中的词仍然生效；worker 没有重新读取数据库
    assert routed == [("f-1", True, "pending_beijing_gate")]
    assert fetch_calls == 0


# ---------------------------------------------------------------------------
# legacy warnings for the phase-2 sources


def test_phase2_legacy_env_keys_and_files_warn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr("src.config.load_environment", lambda: None)
    monkeypatch.setenv("SCORE_KEYWORD_BONUSES", '{"高考": 10}')
    monkeypatch.setenv("KEYWORDS_PATH", "ignored.txt")
    for relative in (
        "config/education_keywords.txt",
        "config/beijing_keywords.txt",
        "config/source_aliases.json",
        "config/score_keyword_bonuses.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder", encoding="utf-8")

    messages = business_config.warn_legacy_config(root=tmp_path)

    for needle in (
        "SCORE_KEYWORD_BONUSES",
        "KEYWORDS_PATH",
        "config/education_keywords.txt",
        "config/beijing_keywords.txt",
        "config/source_aliases.json",
        "config/score_keyword_bonuses.json",
    ):
        assert any(needle in message and "不再生效" in message for message in messages), needle
