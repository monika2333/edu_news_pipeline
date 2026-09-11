from __future__ import annotations

from types import SimpleNamespace
from typing import Callable

import pytest

from src.cli import main as cli_main
from src.cli.main import build_parser
from src.console import manual_filter_service, settings_service


@pytest.mark.parametrize(
    "command",
    [
        "crawl",
        "summarize",
        "enrich-summary",
        "geo-classify",
        "score",
        "export",
        "refresh-manual-clusters",
        "clear-review-buckets",
        "feishu-archive-bot",
        "submission-feedback-dedup",
    ],
)
def test_cli_supports_expected_subcommands(command: str) -> None:
    parser = build_parser()
    choices = parser._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
    assert command in choices, f"missing subcommand: {command}"


def test_cli_help_available() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    assert "Edu news pipeline" in help_text
    for keyword in [
        "crawl",
        "summarize",
        "enrich-summary",
        "geo-classify",
        "score",
        "export",
        "refresh-manual-clusters",
        "clear-review-buckets",
        "feishu-archive-bot",
        "submission-feedback-dedup",
    ]:
        assert keyword in help_text


def test_export_min_score_defaults_to_promotion_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_main,
        "get_settings",
        lambda: SimpleNamespace(score_promotion_threshold=30),
    )

    args = build_parser().parse_args(["export"])

    assert args.min_score == 30


def test_feedback_dedup_cli_supports_single_report_or_all() -> None:
    single = build_parser().parse_args(
        ["submission-feedback-dedup", "--report-id", "report-1"]
    )
    all_reports = build_parser().parse_args(
        ["submission-feedback-dedup", "--all"]
    )

    assert single.report_id == "report-1"
    assert single.all_reports is False
    assert all_reports.report_id is None
    assert all_reports.all_reports is True


def test_export_min_score_explicit_value_overrides_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_main,
        "get_settings",
        lambda: SimpleNamespace(score_promotion_threshold=30),
    )

    args = build_parser().parse_args(["export", "--min-score", "45"])

    assert args.min_score == 45


def test_refresh_manual_clusters_rejects_report_type() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["refresh-manual-clusters", "--report-type", "zongbao"]
        )


@pytest.mark.parametrize(
    ("refreshed", "expected_code"),
    [(True, 0), (False, 2)],
)
def test_refresh_manual_clusters_returns_distinct_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    refreshed: bool,
    expected_code: int,
) -> None:
    watchdog_seconds: list[int] = []
    monkeypatch.setattr(
        cli_main,
        "_watchdog",
        lambda seconds: watchdog_seconds.append(seconds),
    )
    monkeypatch.setattr(
        manual_filter_service,
        "trigger_clustering",
        lambda **kwargs: {"refreshed": refreshed},
    )

    result = cli_main._refresh_manual_clusters()

    assert result == expected_code
    assert watchdog_seconds == [600]


def test_watchdog_exits_with_timeout_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[object] = []

    class ImmediateThread:
        def __init__(
            self,
            *,
            target: Callable[[], None],
            daemon: bool,
        ) -> None:
            self._target = target
            events.append(("daemon", daemon))

        def start(self) -> None:
            self._target()

    def fake_exit(code: int) -> None:
        events.append(("exit", code))
        raise RuntimeError("process exit")

    monkeypatch.setattr(cli_main.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        cli_main.time,
        "sleep",
        lambda seconds: events.append(("sleep", seconds)),
    )
    monkeypatch.setattr(cli_main.os, "_exit", fake_exit)

    with pytest.raises(RuntimeError, match="process exit"):
        cli_main._watchdog(600)

    assert events == [
        ("daemon", True),
        ("sleep", 600),
        ("exit", 3),
    ]
    assert (
        "TIMEOUT: refresh-manual-clusters exceeded limit"
        in capsys.readouterr().err
    )


def test_main_propagates_refresh_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_main,
        "_refresh_manual_clusters",
        lambda: 2,
    )

    assert cli_main.main(["refresh-manual-clusters"]) == 2


def _import_preview(
    *,
    daily_only_sources: list[str] | None = None,
    has_parse_errors: bool = False,
) -> dict[str, object]:
    return {
        "sections": {
            "llm_models": {"default": "model-a", "steps": {}},
            "crawl_sources": daily_only_sources or ["toutiao"],
        },
        "account_summary": {},
        "accounts": [],
        "daily_only_sources": daily_only_sources or [],
        "has_parse_errors": has_parse_errors,
    }


@pytest.mark.parametrize(
    ("preview", "error"),
    [
        (_import_preview(daily_only_sources=["bjrb"]), "仅每日任务来源"),
        (_import_preview(has_parse_errors=True), "无法解析"),
    ],
    ids=["daily-only-source", "parse-error"],
)
def test_f6_import_settings_apply_uses_service_rejection(
    monkeypatch: pytest.MonkeyPatch,
    preview: dict[str, object],
    error: str,
) -> None:
    calls: list[bool] = []
    writes: list[dict[str, object]] = []
    original_import = settings_service.import_legacy_config
    monkeypatch.setattr(
        settings_service,
        "preview_legacy_import",
        lambda **_kwargs: preview,
    )
    monkeypatch.setattr(
        settings_service,
        "get_adapter",
        lambda: SimpleNamespace(
            import_app_config=lambda **kwargs: writes.append(kwargs)
        ),
    )

    def tracked_import(*, apply: bool) -> dict[str, object]:
        calls.append(apply)
        return original_import(apply=apply)

    monkeypatch.setattr(settings_service, "import_legacy_config", tracked_import)

    with pytest.raises(ValueError, match=error):
        cli_main.main(["import-settings", "--apply", "--json"])

    assert calls == [False, True]
    assert writes == []


def test_f6_import_settings_apply_writes_through_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview = _import_preview()
    calls: list[bool] = []
    writes: list[dict[str, object]] = []
    original_import = settings_service.import_legacy_config
    monkeypatch.setattr(
        settings_service,
        "preview_legacy_import",
        lambda **_kwargs: preview,
    )
    monkeypatch.setattr(
        settings_service,
        "get_adapter",
        lambda: SimpleNamespace(
            import_app_config=lambda **kwargs: writes.append(kwargs)
        ),
    )

    def tracked_import(*, apply: bool) -> dict[str, object]:
        calls.append(apply)
        return original_import(apply=apply)

    monkeypatch.setattr(settings_service, "import_legacy_config", tracked_import)

    assert cli_main.main(["import-settings", "--apply", "--json"]) == 0
    assert calls == [False, True]
    assert writes == [
        {
            "sections": preview["sections"],
            "accounts": preview["accounts"],
        }
    ]


def test_clear_review_buckets_cli_uses_scheduled_actor_and_prints_counts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from src.console import manual_filter_admin_service

    calls: list[dict[str, object]] = []
    wrong_calls: list[dict[str, object]] = []

    def fake_clear_review_buckets(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "total": 0,
            "buckets": {
                "zongbao": {"selected": 0, "backup": 0},
                "wanbao": {"selected": 0, "backup": 0},
            },
        }

    monkeypatch.setattr(
        manual_filter_admin_service,
        "clear_all_review_buckets",
        fake_clear_review_buckets,
    )
    monkeypatch.setattr(
        manual_filter_admin_service,
        "clear_review_buckets",
        lambda **kwargs: wrong_calls.append(kwargs) or fake_clear_review_buckets(),
    )

    result = cli_main.main(["clear-review-buckets"])

    assert result == 0
    assert wrong_calls == []
    assert calls == [
        {
            "actor_username": "system:scheduled_clear",
            "trigger": "scheduled",
        }
    ]
    output = capsys.readouterr().out
    assert "[clear-review-buckets]" in output
    assert "cleared=0" in output
    assert "zongbao(selected=0, backup=0)" in output
    assert "wanbao(selected=0, backup=0)" in output
