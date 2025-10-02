import pytest

from src.cli.main import build_parser


@pytest.mark.parametrize("command", ["crawl", "summarize", "score", "export"])
def test_cli_supports_expected_subcommands(command: str) -> None:
    parser = build_parser()
    choices = parser._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
    assert command in choices, f"missing subcommand: {command}"


def test_cli_help_available() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    assert "Edu news pipeline" in help_text
    for keyword in ["crawl", "summarize", "score", "export"]:
        assert keyword in help_text
