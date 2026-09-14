from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Iterator, Optional

from src.workers import crawl_sources


EMPTY_STATS: crawl_sources.CrawlStats = {
    "consumed": 0,
    "ok": 0,
    "failed": 0,
    "skipped": 0,
}


def _account(identifier: str) -> SimpleNamespace:
    return SimpleNamespace(
        normalized_identifier=identifier,
        profile_url=f"https://example.test/{identifier}",
        original_input=identifier,
    )


def test_toutiao_new_and_seen_accounts_receive_independent_first_run_limits(
    monkeypatch,
) -> None:
    captured: list[Any] = []

    def collect(entries, *_args: Any, **_kwargs: Any) -> list[Any]:
        captured.extend(entries)
        return []

    monkeypatch.setattr(crawl_sources, "_collect_feed", collect)

    crawl_sources._run_toutiao_flow(
        adapter=SimpleNamespace(
            ingest=SimpleNamespace(get_existing_raw_ids=lambda: set())
        ),
        accounts=[_account("new-token"), _account("seen-token")],
        show_browser=False,
        timeout_value=15,
        lang="zh-CN",
        keywords=[],
        remaining_limit=100,
        seen_tokens={"seen-token"},
        first_run_limit=10,
    )

    assert [(entry.token, entry.first_run_limit) for entry in captured] == [
        ("new-token", 10),
        ("seen-token", None),
    ]


def test_toutiao_new_account_gets_configured_first_run_limit(monkeypatch) -> None:
    captured: list[Any] = []
    monkeypatch.setattr(
        crawl_sources,
        "_collect_feed",
        lambda entries, *_args, **_kwargs: captured.extend(entries) or [],
    )

    crawl_sources._run_toutiao_flow(
        adapter=SimpleNamespace(
            ingest=SimpleNamespace(get_existing_raw_ids=lambda: set())
        ),
        accounts=[_account("new-token")],
        show_browser=False,
        timeout_value=15,
        lang="zh-CN",
        keywords=[],
        remaining_limit=100,
        seen_tokens=set(),
        first_run_limit=10,
    )

    assert captured[0].first_run_limit == 10


def test_reenabled_toutiao_account_with_seen_token_is_not_first_run(
    monkeypatch,
) -> None:
    captured: list[Any] = []
    monkeypatch.setattr(
        crawl_sources,
        "_collect_feed",
        lambda entries, *_args, **_kwargs: captured.extend(entries) or [],
    )

    crawl_sources._run_toutiao_flow(
        adapter=SimpleNamespace(
            ingest=SimpleNamespace(get_existing_raw_ids=lambda: set())
        ),
        accounts=[_account("seen-token")],
        show_browser=False,
        timeout_value=15,
        lang="zh-CN",
        keywords=[],
        remaining_limit=100,
        seen_tokens={"seen-token"},
        first_run_limit=10,
    )

    assert captured[0].first_run_limit is None


def test_tencent_new_account_entry_carries_first_run_limit(monkeypatch) -> None:
    captured: list[Any] = []

    def list_items(entries, **_kwargs: Any) -> list[Any]:
        captured.extend(entries)
        return []

    monkeypatch.setattr(crawl_sources, "tencent_list_feed_items", list_items)

    crawl_sources._run_tencent_flow(
        adapter=SimpleNamespace(
            ingest=SimpleNamespace(get_existing_raw_ids=lambda: set())
        ),
        keywords=[],
        remaining_limit=100,
        pages=7,
        accounts=[_account("new-author")],
        seen_tokens=set(),
        first_run_limit=10,
    )

    assert len(captured) == 1
    assert captured[0].author_id == "new-author"
    assert captured[0].first_run_limit == 10


class _CountingIngest:
    def __init__(self, tokens: set[str]) -> None:
        self.tokens = tokens
        self.seen_token_queries = 0

    def get_seen_raw_tokens(self) -> set[str]:
        self.seen_token_queries += 1
        return set(self.tokens)


@contextmanager
def _worker_session(*_args: Any, **_kwargs: Any) -> Iterator[None]:
    yield


def test_seen_token_set_is_loaded_once_per_run_for_both_account_sources(
    monkeypatch,
) -> None:
    ingest = _CountingIngest({"seen-token"})
    adapter = SimpleNamespace(ingest=ingest)
    account = _account("seen-token")
    observed: list[tuple[Optional[set[str]], int]] = []

    monkeypatch.setattr(
        crawl_sources,
        "get_settings",
        lambda: SimpleNamespace(process_limit=None, keywords_path=None),
    )
    monkeypatch.setattr(crawl_sources, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        crawl_sources,
        "get_business_config",
        lambda: SimpleNamespace(
            crawl_sources=("toutiao", "tencent"),
            accounts={"toutiao": (account,), "tencent": (account,)},
        ),
    )
    monkeypatch.setattr(crawl_sources, "worker_session", _worker_session)
    monkeypatch.setattr(
        crawl_sources,
        "_run_toutiao_flow",
        lambda **kwargs: observed.append(
            (kwargs["seen_tokens"], kwargs["first_run_limit"])
        )
        or EMPTY_STATS.copy(),
    )
    monkeypatch.setattr(
        crawl_sources,
        "_run_tencent_flow",
        lambda **kwargs: observed.append(
            (kwargs["seen_tokens"], kwargs["first_run_limit"])
        )
        or EMPTY_STATS.copy(),
    )

    crawl_sources.run(limit=100, sources=["toutiao", "tencent"])

    assert ingest.seen_token_queries == 1
    assert observed == [({"seen-token"}, 10), ({"seen-token"}, 10)]


def test_zero_configured_first_run_limit_is_clamped_to_one(monkeypatch) -> None:
    adapter = SimpleNamespace(ingest=_CountingIngest(set()))
    account = _account("new-token")
    observed_limits: list[int] = []

    monkeypatch.setenv("CRAWL_FIRST_RUN_LIMIT", "0")
    monkeypatch.setattr(
        crawl_sources,
        "get_settings",
        lambda: SimpleNamespace(process_limit=None, keywords_path=None),
    )
    monkeypatch.setattr(crawl_sources, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        crawl_sources,
        "get_business_config",
        lambda: SimpleNamespace(
            crawl_sources=("toutiao",),
            accounts={"toutiao": (account,)},
        ),
    )
    monkeypatch.setattr(crawl_sources, "worker_session", _worker_session)
    monkeypatch.setattr(
        crawl_sources,
        "_run_toutiao_flow",
        lambda **kwargs: observed_limits.append(kwargs["first_run_limit"])
        or EMPTY_STATS.copy(),
    )

    crawl_sources.run(limit=100, sources=["toutiao"])

    assert observed_limits == [1]


def test_zero_row_first_run_creates_no_marker_and_is_first_run_again(
    monkeypatch,
) -> None:
    captured_limits: list[Optional[int]] = []

    def collect(entries, *_args: Any, **_kwargs: Any) -> list[Any]:
        captured_limits.append(entries[0].first_run_limit)
        return []

    monkeypatch.setattr(crawl_sources, "_collect_feed", collect)
    kwargs = {
        "adapter": SimpleNamespace(
            ingest=SimpleNamespace(get_existing_raw_ids=lambda: set())
        ),
        "accounts": [_account("still-new")],
        "show_browser": False,
        "timeout_value": 15,
        "lang": "zh-CN",
        "keywords": [],
        "remaining_limit": 100,
        "seen_tokens": set(),
        "first_run_limit": 10,
    }

    crawl_sources._run_toutiao_flow(**kwargs)
    crawl_sources._run_toutiao_flow(**kwargs)

    assert captured_limits == [10, 10]
