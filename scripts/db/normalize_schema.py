"""把 dbmate / pg_dump 产出的 schema.sql 规整成仓库里提交的形态。

为什么需要：`dbmate up` 在有待执行迁移时会自动回写 `database/schema.sql`，而 pg_dump 18
的输出里混着三类会污染提交的内容：

1. 文件首尾的 `\\restrict` / `\\unrestrict` 行，令牌每次 dump 都重新随机生成；
2. `public` schema 的注释块（`-- *not* creating schema, since initdb creates it`），
   只是 dump 环境的形态，不是结构变更；
3. `schema_migrations.version` 写成**本机库现状**的 `character varying`（无长度）。
   仓库约定是 `varchar(128)`，`tests/test_migration_replay.py` 的镜像 DDL 也按 128 建表，
   整份提交会让那个用例的列签名比对直接变红。

本模块只做文本规整，不碰数据库；`scripts/db/refresh-schema.ps1` 负责 dump 与编排。
单独使用：

    python -m scripts.db.normalize_schema database/schema.sql
    python -m scripts.db.normalize_schema --check database/schema.sql

规整后仍不符合预期形态时抛 `SchemaNormalizationError`（多半是 pg_dump 版本变了，
需要同步更新本文件的规则），调用方应当在写回前先失败。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

__all__ = [
    "SchemaNormalizationError",
    "normalize_schema_file",
    "normalize_schema_text",
]

SCHEMA_MIGRATIONS_VERSION_LENGTH = 128

_GUARD_LINE = re.compile(r"^\\(?:un)?restrict [^\n]*\n", re.MULTILINE)

_PUBLIC_SCHEMA_BLOCK = re.compile(
    r"--\n"
    r"-- Name: public; Type: SCHEMA;[^\n]*\n"
    r"--\n"
    r"\n*"
    r"-- \*not\* creating schema, since initdb creates it\n"
    r"\n*"
)

_SCHEMA_MIGRATIONS_TABLE = re.compile(
    r"(CREATE TABLE public\.schema_migrations \(\n"
    r"    version character varying)(?:\(\d+\))?"
    r"( NOT NULL\n\);)"
)

_EXCESS_BLANK_LINES = re.compile(r"\n{4,}")

_EXPECTED_SCHEMA_MIGRATIONS_TABLE = (
    "CREATE TABLE public.schema_migrations (\n"
    "    version character varying(128) NOT NULL\n"
    ");"
)


class SchemaNormalizationError(RuntimeError):
    """dump 形态与预期不符：pg_dump 输出或库结构变了，需要更新规整规则。"""


def normalize_schema_text(text: str) -> str:
    """返回规整后的 schema.sql 文本（LF、UTF-8、无随机令牌）。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _GUARD_LINE.sub("", text)
    text = _PUBLIC_SCHEMA_BLOCK.sub("", text)
    text = _SCHEMA_MIGRATIONS_TABLE.sub(
        rf"\1({SCHEMA_MIGRATIONS_VERSION_LENGTH})\2", text
    )
    text = _EXCESS_BLANK_LINES.sub("\n\n\n", text)
    text = text.lstrip("\n").rstrip("\n") + "\n"
    _validate(text)
    return text


def normalize_schema_file(path: Path) -> bool:
    """就地规整文件；返回是否发生了改动。改写前已通过校验，失败时文件保持原样。"""
    original = path.read_bytes()
    normalized = normalize_schema_text(original.decode("utf-8")).encode("utf-8")
    if normalized == original:
        return False
    path.write_bytes(normalized)
    return True


def _validate(text: str) -> None:
    problems: list[str] = []
    if _GUARD_LINE.search(text):
        problems.append("残留 \\restrict / \\unrestrict 行")
    if "-- Name: public; Type: SCHEMA;" in text:
        problems.append("残留 public schema 注释块")
    if _EXPECTED_SCHEMA_MIGRATIONS_TABLE not in text:
        problems.append(
            "schema_migrations.version 未能固定为 "
            f"varchar({SCHEMA_MIGRATIONS_VERSION_LENGTH})"
        )
    if not text.startswith("-- Dumped from database version"):
        problems.append("文件开头不是 pg_dump 头注释")
    if not text.endswith(");\n"):
        problems.append("文件结尾不是迁移版本 INSERT 的结尾")
    if problems:
        raise SchemaNormalizationError(
            "schema.sql 规整后仍不符合预期形态（pg_dump 输出可能变了，"
            "请更新 scripts/db/normalize_schema.py 的规则）：" + "；".join(problems)
        )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize a dbmate/pg_dump schema.sql file into the committed shape "
            "(strip pg_dump guards, public schema block, pin varchar(128))"
        )
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="database/schema.sql",
        help="Path to the schema file (default: database/schema.sql)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only report whether the file is already normalized; never write",
    )
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.is_file():
        print(f"[normalize] missing file: {path}", file=sys.stderr)
        return 2

    if args.check:
        current = path.read_bytes().decode("utf-8")
        try:
            normalized = normalize_schema_text(current)
        except SchemaNormalizationError as error:
            print(f"[normalize] {error}", file=sys.stderr)
            return 1
        if normalized != current:
            print(f"[normalize] {path} is not normalized", file=sys.stderr)
            return 1
        print(f"[normalize] {path} is normalized")
        return 0

    try:
        changed = normalize_schema_file(path)
    except SchemaNormalizationError as error:
        print(f"[normalize] {error}", file=sys.stderr)
        return 1
    print(f"[normalize] {path}: {'updated' if changed else 'already normalized'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
