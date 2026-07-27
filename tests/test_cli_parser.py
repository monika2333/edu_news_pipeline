import pytest

from src.cli.main import build_parser


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
    ]:
        assert keyword in help_text


def test_refresh_manual_clusters_defaults_to_zongbao() -> None:
    args = build_parser().parse_args(["refresh-manual-clusters"])

    assert args.command == "refresh-manual-clusters"
    assert args.report_type == "zongbao"


def test_refresh_manual_clusters_accepts_wanbao() -> None:
    args = build_parser().parse_args(
        ["refresh-manual-clusters", "--report-type", "wanbao"]
    )

    assert args.report_type == "wanbao"
