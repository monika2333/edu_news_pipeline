from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Optional

import pytest

from src.workers import crawl_sources


EMPTY_STATS: crawl_sources.CrawlStats = {
    "consumed": 0,
    "ok": 0,
    "failed": 0,
    "skipped": 0,
}
STRATEGY_FIELDS = (
    "details_in_list",
    "load_existing_ids",
    "skip_existing_ids",
    "count_prepare_errors",
    "count_feed_errors",
    "continue_after_feed_error",
    "missing_ids_fallback",
    "detail_delay",
    "delay_after_failure",
    "delay_after_last",
)
DEFAULT_STRATEGY = {
    "details_in_list": False,
    "load_existing_ids": True,
    "skip_existing_ids": False,
    "count_prepare_errors": False,
    "count_feed_errors": True,
    "continue_after_feed_error": False,
    "missing_ids_fallback": "raise",
    "detail_delay": 0.0,
    "delay_after_failure": False,
    "delay_after_last": False,
}
LINKED_SOURCES = {
    "beijinghao": (
        "beijinghao_list_items",
        "beijinghao_make_article_id",
        "beijinghao_feed_item_to_row",
        "beijinghao_fetch_detail",
        "beijinghao_build_detail_update",
    ),
    "btime": (
        "btime_list_items",
        "btime_make_article_id",
        "btime_feed_item_to_row",
        "btime_fetch_detail",
        "btime_build_detail_update",
    ),
    "chinadaily": (
        "cd_list_items",
        "cd_make_article_id",
        "cd_feed_item_to_row",
        "cd_fetch_detail",
        "cd_build_detail_update",
    ),
    "chinanews": (
        "cn_list_items",
        "cn_make_article_id",
        "cn_feed_item_to_row",
        "cn_fetch_detail",
        "cn_build_detail_update",
    ),
    "chinanews_xj": (
        "cn_xj_list_items",
        "cn_xj_make_article_id",
        "cn_xj_feed_item_to_row",
        "cn_xj_fetch_detail",
        "cn_xj_build_detail_update",
    ),
    "jyb": (
        "jyb_list_items",
        "jyb_make_article_id",
        "jyb_feed_item_to_row",
        "jyb_fetch_detail",
        "jyb_build_detail_update",
    ),
}


@contextmanager
def _worker_session(*_args: Any, **_kwargs: Any) -> Iterator[None]:
    yield


@pytest.fixture
def run_adapter(monkeypatch: pytest.MonkeyPatch) -> object:
    adapter = object()
    monkeypatch.setattr(
        crawl_sources,
        "get_settings",
        lambda: SimpleNamespace(process_limit=None, keywords_path=None),
    )
    monkeypatch.setattr(crawl_sources, "get_adapter", lambda: adapter)
    monkeypatch.setattr(crawl_sources, "worker_session", _worker_session)
    monkeypatch.setenv("TOUTIAO_AUTHORS_PATH", "toutiao-authors.txt")
    monkeypatch.setenv("TOUTIAO_SHOW_BROWSER", "yes")
    monkeypatch.setenv("TOUTIAO_FETCH_TIMEOUT", "21")
    monkeypatch.setenv("TOUTIAO_LANG", "zh-test")
    monkeypatch.setenv("TENCENT_DETAIL_DELAY", "0.75")
    monkeypatch.setenv("GMW_BASE_URL", "https://gmw.test/list")
    monkeypatch.setenv("GMW_TIMEOUT", "12.5")
    monkeypatch.setenv("GMW_EXISTING_CONSECUTIVE_STOP", "6")
    monkeypatch.setenv("QIANLONG_BASE_URL", "https://qianlong.test/list")
    monkeypatch.setenv("QIANLONG_TIMEOUT", "13.5")
    monkeypatch.setenv("QIANLONG_DELAY", "0.35")
    monkeypatch.setenv("QIANLONG_PAGES", "8")
    monkeypatch.setenv("QIANLONG_MAX_PAGES", "9")
    monkeypatch.setenv("QIANLONG_EXISTING_CONSECUTIVE_STOP", "7")
    monkeypatch.setenv("BJRB_TIMEOUT", "14.5")
    monkeypatch.setenv("BJRB_DELAY", "0.45")
    return adapter


def _patch_linked_callbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    for source, callback_names in LINKED_SOURCES.items():
        list_name, make_id_name, feed_name, fetch_name, build_name = callback_names

        def list_items(*, marker: str = source, **_kwargs: Any) -> list[str]:
            return [marker]

        def make_article_id(_url: str, *, marker: str = source) -> str:
            return f"{marker}:id"

        def feed_item_to_row(
            _item: Any,
            article_id: str,
            *,
            fetched_at: datetime,
            marker: str = source,
        ) -> dict[str, Any]:
            return {"callback": marker, "article_id": article_id, "fetched_at": fetched_at}

        def fetch_detail(_url: str, *, marker: str = source) -> dict[str, str]:
            return {"callback": marker}

        def build_detail_update(
            _item: Any,
            article_id: str,
            payload: dict[str, str],
            *,
            detail_fetched_at: datetime,
            marker: str = source,
        ) -> dict[str, Any]:
            return {
                "callback": marker,
                "article_id": article_id,
                "detail_fetched_at": detail_fetched_at,
                "payload_callback": payload["callback"],
            }

        monkeypatch.setattr(crawl_sources, list_name, list_items)
        monkeypatch.setattr(crawl_sources, make_id_name, make_article_id)
        monkeypatch.setattr(crawl_sources, feed_name, feed_item_to_row)
        monkeypatch.setattr(crawl_sources, fetch_name, fetch_detail)
        monkeypatch.setattr(crawl_sources, build_name, build_detail_update)


@pytest.mark.parametrize(
    ("dispatch_key", "source", "display_name", "strategy_overrides"),
    [
        ("beijinghao", "beijinghao", "Beijinghao", {"count_feed_errors": False}),
        ("bjrb", "bjrb", "Beijing Daily", {"load_existing_ids": False, "count_prepare_errors": True, "missing_ids_fallback": "all", "detail_delay": 0.45}),
        ("btime", "btime", "Btime", {"count_feed_errors": False}),
        ("chinadaily", "chinadaily", "China Daily", {"count_feed_errors": False}),
        ("chinanews", "chinanews", "ChinaNews", {"count_feed_errors": False}),
        ("chinanews_xj", "chinanews_xj", "ChinaNews Xinjiang", {"count_feed_errors": False}),
        ("gmw", "gmw", "Guangming Daily", {"details_in_list": True}),
        ("jyb", "jyb", "JYB", {"count_feed_errors": False}),
        ("ldwb", "ldwb", "Laodong Wubao", {"details_in_list": True, "skip_existing_ids": True, "continue_after_feed_error": True}),
        ("qianlong", "qianlong", "Qianlong", {"details_in_list": True}),
        ("tencent", "tencent", "Tencent", {"count_prepare_errors": True, "missing_ids_fallback": "none", "detail_delay": 0.75, "delay_after_failure": True, "delay_after_last": True}),
        ("toutiao", "toutiao", "Toutiao", {"count_prepare_errors": True}),
        ("beijingdaily", "bjrb", "Beijing Daily", {"load_existing_ids": False, "count_prepare_errors": True, "missing_ids_fallback": "all", "detail_delay": 0.45}),
        ("laodongwubao", "ldwb", "Laodong Wubao", {"details_in_list": True, "skip_existing_ids": True, "continue_after_feed_error": True}),
        ("qq", "tencent", "Tencent", {"count_prepare_errors": True, "missing_ids_fallback": "none", "detail_delay": 0.75, "delay_after_failure": True, "delay_after_last": True}),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_every_dispatch_key_preserves_source_flow_strategy_and_callbacks(
    monkeypatch: pytest.MonkeyPatch,
    run_adapter: object,
    dispatch_key: str,
    source: str,
    display_name: str,
    strategy_overrides: dict[str, Any],
) -> None:
    del run_adapter
    calls: list[crawl_sources.SourceFlow] = []
    _patch_linked_callbacks(monkeypatch)

    def record_flow(**kwargs: Any) -> crawl_sources.CrawlStats:
        calls.append(kwargs["flow"])
        return EMPTY_STATS.copy()

    monkeypatch.setattr(crawl_sources, "_run_source_flow", record_flow)

    crawl_sources.run(limit=7, sources=[dispatch_key], pages=3)

    assert len(calls) == 1, f"{dispatch_key} should dispatch exactly one SourceFlow"
    flow = calls[0]
    assert flow.source == source, f"{dispatch_key} dispatched source {flow.source!r}"
    assert flow.display_name == display_name, f"{dispatch_key} display name changed"
    expected_strategy = DEFAULT_STRATEGY | strategy_overrides
    actual_strategy = {field: getattr(flow, field) for field in STRATEGY_FIELDS}
    assert actual_strategy == expected_strategy, f"{dispatch_key} strategy changed"

    if source in LINKED_SOURCES:
        item = SimpleNamespace(url=f"https://{source}.test/item")
        fetched_at = datetime(2026, 9, 10, tzinfo=timezone.utc)
        assert flow.list_items(2, {"existing"}) == [source]
        article_id, feed_row = flow.prepare_feed(item, fetched_at)
        assert article_id == f"{source}:id"
        assert feed_row["callback"] == source
        detail_row = flow.fetch_detail(item, article_id, fetched_at)
        assert detail_row["callback"] == source
        assert detail_row["payload_callback"] == source


@pytest.mark.parametrize(
    ("source", "runner_name", "extra_kwargs", "linked_source"),
    [
        ("beijinghao", "_run_registered_linked_page_flow", {"pages": 3}, "beijinghao"),
        ("bjrb", "_run_bjrb_flow", {"timeout_value": 14.5, "delay_value": 0.45}, None),
        ("btime", "_run_registered_linked_page_flow", {"pages": 3}, "btime"),
        ("chinadaily", "_run_registered_linked_page_flow", {"pages": 3}, "chinadaily"),
        ("chinanews", "_run_registered_linked_page_flow", {"pages": 3}, "chinanews"),
        ("chinanews_xj", "_run_registered_linked_page_flow", {"pages": 3}, "chinanews_xj"),
        ("gmw", "_run_gmw_flow", {"base_url": "https://gmw.test/list", "timeout_value": 12.5}, None),
        ("jyb", "_run_registered_linked_page_flow", {"pages": 3}, "jyb"),
        ("ldwb", "_run_ldwb_flow", {}, None),
        ("qianlong", "_run_qianlong_flow", {"base_urls": ("https://qianlong.test/list",), "timeout_value": 13.5, "delay_value": 0.35, "pages_hint": 3, "consecutive_stop": 7}, None),
        ("tencent", "_run_tencent_flow", {"pages": 3}, None),
        ("toutiao", "_run_toutiao_flow", {"authors_path": Path("toutiao-authors.txt"), "show_browser": True, "timeout_value": 21, "lang": "zh-test"}, None),
    ],
)
def test_registry_passes_each_runner_its_current_arguments(
    monkeypatch: pytest.MonkeyPatch,
    run_adapter: object,
    source: str,
    runner_name: str,
    extra_kwargs: dict[str, Any],
    linked_source: Optional[str],
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    expected_kwargs = dict(extra_kwargs)
    if source == "toutiao":
        relative_authors_path = Path("toutiao-authors.txt")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TOUTIAO_AUTHORS_PATH", str(relative_authors_path))
        expected_kwargs["authors_path"] = tmp_path / relative_authors_path

    def record_runner(**kwargs: Any) -> crawl_sources.CrawlStats:
        calls.append(kwargs)
        return EMPTY_STATS.copy()

    monkeypatch.setattr(crawl_sources, runner_name, record_runner)

    crawl_sources.run(limit=7, sources=[source], pages=3)

    if linked_source is not None:
        config = calls[0].pop("config")
        assert config.source == linked_source, f"{source} linked-page config changed"
    assert calls == [
        {
            "adapter": run_adapter,
            "keywords": [],
            "remaining_limit": 7,
            **expected_kwargs,
        }
    ], f"{source} runner arguments changed"


def test_toutiao_absolute_authors_path_passes_through_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    run_adapter: object,
    tmp_path: Path,
) -> None:
    working_directory = tmp_path / "working-directory"
    authors_directory = tmp_path / "authors-directory"
    working_directory.mkdir()
    authors_directory.mkdir()
    absolute_authors_path = authors_directory / "authors.txt"
    monkeypatch.chdir(working_directory)
    monkeypatch.setenv("TOUTIAO_AUTHORS_PATH", str(absolute_authors_path))
    calls: list[dict[str, Any]] = []

    def record_runner(**kwargs: Any) -> crawl_sources.CrawlStats:
        calls.append(kwargs)
        return EMPTY_STATS.copy()

    monkeypatch.setattr(crawl_sources, "_run_toutiao_flow", record_runner)

    crawl_sources.run(limit=7, sources=["toutiao"], pages=3)

    assert working_directory != absolute_authors_path.parent
    assert absolute_authors_path.is_absolute()
    assert calls == [
        {
            "adapter": run_adapter,
            "keywords": [],
            "remaining_limit": 7,
            "authors_path": absolute_authors_path,
            "show_browser": True,
            "timeout_value": 21,
            "lang": "zh-test",
        }
    ]


def test_tencent_authors_path_does_not_fall_back_to_legacy_location(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    legacy_path = tmp_path / "newsqq_crawl" / "qq_author.txt"
    legacy_path.parent.mkdir()
    legacy_path.write_text("legacy-author", encoding="utf-8")
    monkeypatch.delenv("TENCENT_AUTHORS_PATH", raising=False)
    monkeypatch.setattr(crawl_sources, "_repo_root", lambda: tmp_path)

    assert crawl_sources._resolve_tencent_authors_path() == tmp_path / "config" / "qq_author.txt"


@pytest.mark.parametrize(
    ("qianlong_pages", "qianlong_max_pages", "expected_pages"),
    [("8", "9", 8), (None, "9", 9)],
)
def test_qianlong_pages_prefers_primary_env_then_falls_back_to_legacy_env(
    monkeypatch: pytest.MonkeyPatch,
    run_adapter: object,
    qianlong_pages: Optional[str],
    qianlong_max_pages: str,
    expected_pages: int,
) -> None:
    del run_adapter
    calls: list[dict[str, Any]] = []
    if qianlong_pages is None:
        monkeypatch.delenv("QIANLONG_PAGES", raising=False)
    else:
        monkeypatch.setenv("QIANLONG_PAGES", qianlong_pages)
    monkeypatch.setenv("QIANLONG_MAX_PAGES", qianlong_max_pages)
    monkeypatch.setattr(
        crawl_sources,
        "_run_qianlong_flow",
        lambda **kwargs: calls.append(kwargs) or EMPTY_STATS.copy(),
    )

    crawl_sources.run(limit=7, sources=["qianlong"], pages=None)

    assert len(calls) == 1
    assert calls[0]["pages_hint"] == expected_pages


def test_invalid_bjrb_timeout_falls_back_to_current_default(
    monkeypatch: pytest.MonkeyPatch,
    run_adapter: object,
) -> None:
    del run_adapter
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("BJRB_TIMEOUT", "abc")
    monkeypatch.setattr(
        crawl_sources,
        "_run_bjrb_flow",
        lambda **kwargs: calls.append(kwargs) or EMPTY_STATS.copy(),
    )

    crawl_sources.run(limit=7, sources=["bjrb"])

    assert len(calls) == 1
    assert calls[0]["timeout_value"] == 20.0
