from __future__ import annotations

import ast
from pathlib import Path


WORKERS_ROOT = Path(__file__).parents[1] / "src" / "workers"
DIRECT_EXECUTOR_ALLOWED = {"feishu_archive_bot.py"}


def _dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _direct_thread_pool_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    imported_names: set[str] = set()
    concurrent_futures_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "concurrent.futures":
            imported_names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "ThreadPoolExecutor"
            )
        elif isinstance(node, ast.Import):
            concurrent_futures_aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "concurrent.futures"
            )

    violations: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in imported_names:
            violations.append(node.lineno)
        elif _dotted_name(node.func) in {
            f"{alias}.ThreadPoolExecutor"
            for alias in concurrent_futures_aliases
        }:
            violations.append(node.lineno)
    return violations


def test_f10_pipeline_workers_do_not_construct_plain_thread_pool_executors() -> None:
    violations: dict[str, list[int]] = {}
    for path in WORKERS_ROOT.rglob("*.py"):
        if path.name in DIRECT_EXECUTOR_ALLOWED:
            continue
        lines = _direct_thread_pool_calls(path)
        if lines:
            violations[path.relative_to(WORKERS_ROOT).as_posix()] = lines

    assert violations == {}
