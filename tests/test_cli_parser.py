from __future__ import annotations

from types import SimpleNamespace
from typing import Callable

import pytest

from src.cli import main as cli_main
from src.cli.main import build_parser
from src.console import manual_filter_service


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
        "feishu-archive-bot",
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
        "feishu-archive-bot",
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
