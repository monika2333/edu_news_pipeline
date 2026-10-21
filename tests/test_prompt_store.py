from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from src.adapters import prompt_store
from src.domain.prompt import PROMPT_KEYS


class _FakePromptRevisions:
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows
        self.fetches = 0

    def fetch_latest_by_key(self) -> dict[str, dict[str, Any]]:
        self.fetches += 1
        return self.rows


class _FakeAdapter:
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.prompt_revisions = _FakePromptRevisions(rows)


def _prompt_files(root) -> dict[str, Any]:
    paths = {}
    for key in PROMPT_KEYS:
        path = root / f"{key}.md"
        path.write_text(f"# {key}\n\n<prompt>\n{key} body\n</prompt>\n", encoding="utf-8")
        paths[key] = path
    return {
        "external_filter_prompt_path": paths["external_positive"],
        "external_negative_filter_prompt_path": paths["external_negative"],
        "internal_filter_prompt_path": paths["internal_positive"],
        "internal_negative_filter_prompt_path": paths["internal_negative"],
        "beijing_gate_prompt_path": paths["beijing_gate"],
    }


def test_read_prompt_template_extracts_only_the_prompt_body(tmp_path) -> None:
    path = tmp_path / "prompt.md"
    path.write_text("# 标题\n\n<prompt>\n有效内容\n</prompt>\n\n## 说明", encoding="utf-8")

    assert prompt_store.read_prompt_template(path) == "有效内容"


def test_read_prompt_template_falls_back_to_the_whole_file(tmp_path) -> None:
    path = tmp_path / "prompt.md"
    path.write_text("  没有标签的内容  ", encoding="utf-8")

    assert prompt_store.read_prompt_template(path) == "没有标签的内容"


def test_read_prompt_versions_skips_malformed_and_unknown_entries(tmp_path) -> None:
    path = tmp_path / "VERSIONS"
    path.write_text(
        "# comment\n"
        "external_positive: v2\n"
        "external_negative: v1\n"
        "beijing_gate: v9\n"
        "unknown_key: v3\n"
        "internal_positive: nope\n"
        "internal_negative\n",
        encoding="utf-8",
    )

    assert prompt_store.read_prompt_versions(path) == {
        "external_positive": 2,
        "external_negative": 1,
        "beijing_gate": 9,
    }


def test_read_prompt_versions_returns_empty_when_file_is_missing(tmp_path) -> None:
    assert prompt_store.read_prompt_versions(tmp_path / "missing") == {}


def test_load_active_prompts_prefers_database_and_falls_back_to_files(
    tmp_path,
    monkeypatch,
) -> None:
    settings = SimpleNamespace(**_prompt_files(tmp_path))
    monkeypatch.setattr(
        prompt_store,
        "read_prompt_versions",
        lambda: {"external_positive": 2, "external_negative": 1},
    )
    adapter = _FakeAdapter(
        {
            "external_positive": {
                "id": 7,
                "revision": 4,
                "content": "database body",
                "created_at": "2026-10-21T08:00:00Z",
                "created_by_user_id": "user-1",
                "created_by_display_name": "编辑甲",
            }
        }
    )

    prompts = prompt_store.load_active_prompts(adapter, settings=settings)

    database_prompt = prompts["external_positive"]
    assert database_prompt.source == "database"
    assert database_prompt.version == "v4"
    assert database_prompt.revision == 4
    assert database_prompt.revision_id == 7
    assert database_prompt.content == "database body"
    assert database_prompt.updated_by_display_name == "编辑甲"

    file_prompt = prompts["external_negative"]
    assert file_prompt.source == "file"
    assert file_prompt.version == "v1"
    assert file_prompt.content == "external_negative body"
    assert file_prompt.revision_id is None


def test_load_file_prompt_uses_settings_paths_and_defaults_beijing_gate_to_v1(
    tmp_path,
) -> None:
    settings = SimpleNamespace(**_prompt_files(tmp_path))

    prompt = prompt_store.load_file_prompt(
        prompt_store.PROMPT_DEFINITIONS[-1],
        settings=settings,
        versions={},
    )

    assert prompt.key == "beijing_gate"
    assert prompt.source == "file"
    assert prompt.version == "v1"
    assert prompt.content == "beijing_gate body"
