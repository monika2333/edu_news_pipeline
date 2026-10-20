from __future__ import annotations

from pathlib import Path

import pytest

from scripts.db.normalize_schema import (
    SchemaNormalizationError,
    normalize_schema_file,
    normalize_schema_text,
)

SCHEMA_PATH = Path(__file__).parents[1] / "database" / "schema.sql"

# pg_dump 18 的真实输出形态：首尾带 \restrict 令牌、public schema 注释块、
# schema_migrations.version 是库内的无长度 varchar。
RAW_DUMP = (
    "\\restrict TOKENVALUE\n"
    "\n"
    "-- Dumped from database version 18.0\n"
    "-- Dumped by pg_dump version 18.0\n"
    "\n"
    "SET statement_timeout = 0;\n"
    "SET row_security = off;\n"
    "\n"
    "--\n"
    "-- Name: public; Type: SCHEMA; Schema: -; Owner: -\n"
    "--\n"
    "\n"
    "-- *not* creating schema, since initdb creates it\n"
    "\n"
    "\n"
    "--\n"
    "-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -\n"
    "--\n"
    "\n"
    "CREATE TABLE public.schema_migrations (\n"
    "    version character varying NOT NULL\n"
    ");\n"
    "\n"
    "\n"
    "--\n"
    "-- PostgreSQL database dump complete\n"
    "--\n"
    "\n"
    "\\unrestrict TOKENVALUE\n"
    "\n"
    "\n"
    "--\n"
    "-- Dbmate schema migrations\n"
    "--\n"
    "\n"
    "INSERT INTO public.schema_migrations (version) VALUES\n"
    "    ('20261020150000');\n"
)

NORMALIZED_DUMP = (
    "-- Dumped from database version 18.0\n"
    "-- Dumped by pg_dump version 18.0\n"
    "\n"
    "SET statement_timeout = 0;\n"
    "SET row_security = off;\n"
    "\n"
    "--\n"
    "-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -\n"
    "--\n"
    "\n"
    "CREATE TABLE public.schema_migrations (\n"
    "    version character varying(128) NOT NULL\n"
    ");\n"
    "\n"
    "\n"
    "--\n"
    "-- PostgreSQL database dump complete\n"
    "--\n"
    "\n"
    "\n"
    "--\n"
    "-- Dbmate schema migrations\n"
    "--\n"
    "\n"
    "INSERT INTO public.schema_migrations (version) VALUES\n"
    "    ('20261020150000');\n"
)


def test_normalize_produces_the_committed_shape() -> None:
    assert normalize_schema_text(RAW_DUMP) == NORMALIZED_DUMP


def test_normalize_strips_pg_dump_guard_lines() -> None:
    """令牌每次 dump 都变，留着会让每次刷新都有假 diff。"""
    normalized = normalize_schema_text(RAW_DUMP)

    assert "\\restrict" not in normalized
    assert "\\unrestrict" not in normalized
    assert normalized.startswith("-- Dumped from database version")


def test_normalize_drops_public_schema_comment_block() -> None:
    normalized = normalize_schema_text(RAW_DUMP)

    assert "-- Name: public; Type: SCHEMA;" not in normalized
    assert "since initdb creates it" not in normalized


def test_normalize_pins_schema_migrations_version_length() -> None:
    """无长度 varchar 会让 test_migration_replay 的镜像 DDL 比对失败。"""
    normalized = normalize_schema_text(RAW_DUMP)

    assert "    version character varying(128) NOT NULL\n" in normalized


def test_normalize_is_idempotent() -> None:
    once = normalize_schema_text(RAW_DUMP)

    assert normalize_schema_text(once) == once


def test_normalize_converts_crlf_to_lf() -> None:
    normalized = normalize_schema_text(RAW_DUMP.replace("\n", "\r\n"))

    assert "\r" not in normalized
    assert normalized == NORMALIZED_DUMP


def test_normalize_rejects_unrecognized_dump_shape() -> None:
    """pg_dump 输出形态变了要立刻失败，而不是写回一个半成品。"""
    without_migrations_table = RAW_DUMP.replace(
        "CREATE TABLE public.schema_migrations (\n"
        "    version character varying NOT NULL\n"
        ");\n",
        "",
    )

    with pytest.raises(SchemaNormalizationError, match="normalize_schema.py"):
        normalize_schema_text(without_migrations_table)


def test_normalize_file_writes_utf8_lf_without_bom(tmp_path: Path) -> None:
    target = tmp_path / "schema.sql"
    target.write_bytes(RAW_DUMP.encode("utf-8"))

    assert normalize_schema_file(target) is True
    written = target.read_bytes()
    assert written == NORMALIZED_DUMP.encode("utf-8")
    assert not written.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in written

    assert normalize_schema_file(target) is False


def test_committed_schema_is_normalized() -> None:
    """守卫：database/schema.sql 必须已是规整形态。

    这样直接跑 `dbmate up` 后原样提交 dump 会在 CI 里立刻暴露，
    而不是等到 test_migration_replay 用 varchar(128) 比对时才红。
    """
    raw = SCHEMA_PATH.read_bytes()
    text = raw.decode("utf-8")

    assert normalize_schema_text(text) == text
    assert raw.count(b"\\restrict") == 0
    assert b"\r" not in raw
    assert not raw.startswith(b"\xef\xbb\xbf")
