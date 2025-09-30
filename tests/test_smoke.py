from importlib import import_module

from src.cli.main import build_parser


def test_cli_builds_with_expected_commands():
    parser = build_parser()
    subparsers = [action for action in parser._subparsers._group_actions if hasattr(action, "choices")]
    assert subparsers, "CLI should expose subcommands"
    choices = subparsers[0].choices
    for command in {"crawl", "summarize", "score", "export"}:
        assert command in choices, f"missing CLI command: {command}"


def test_workers_importable():
    for module in [
        "src.workers.crawl_toutiao",
        "src.workers.summarize",
        "src.workers.score",
        "src.workers.export_brief",
    ]:
        mod = import_module(module)
        assert hasattr(mod, "run"), f"worker {module} must expose run()"
