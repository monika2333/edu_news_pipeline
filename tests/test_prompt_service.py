from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

import pytest

from src.adapters.db_postgres_prompt_revisions import PromptRevisionConflictError
from src.console import prompt_service
from src.console.auth_service import ConsoleUser
from src.domain.prompt import PROMPT_KEYS

ADMIN = ConsoleUser(
    method="test",
    user_id="admin-id",
    username="admin",
    display_name="测试管理员",
    role="admin",
)
DUTY = ConsoleUser(
    method="test",
    user_id="duty-id",
    username="duty",
    display_name="值班编辑",
    role="duty_editor",
)


def _row(key: str, *, revision: int = 2, content: Optional[str] = None) -> dict[str, Any]:
    return {
        "id": 10 + revision,
        "prompt_key": key,
        "revision": revision,
        "content": content or f"{key} database body",
        "note": None,
        "created_at": "2026-10-21T08:00:00Z",
        "created_by_user_id": "user-1",
        "created_by_display_name": "编辑甲",
    }


class _FakePromptRevisions:
    def __init__(self, rows: Optional[dict[str, dict[str, Any]]] = None) -> None:
        self.latest = rows if rows is not None else {
            key: _row(key) for key in PROMPT_KEYS
        }
        self.history: dict[str, list[dict[str, Any]]] = {
            key: [dict(row)] for key, row in self.latest.items()
        }
        self.append_calls: list[dict[str, Any]] = []

    def fetch_latest_by_key(self) -> dict[str, dict[str, Any]]:
        return {key: dict(row) for key, row in self.latest.items()}

    def fetch_revisions(self, prompt_key: str, *, limit: int) -> list[dict[str, Any]]:
        rows = sorted(
            self.history.get(prompt_key, []),
            key=lambda item: int(item["revision"]),
            reverse=True,
        )
        return rows[:limit]

    def fetch_revision(self, prompt_key: str, revision_id: int) -> Optional[dict[str, Any]]:
        for row in self.history.get(prompt_key, []):
            if int(row["id"]) == revision_id:
                return dict(row)
        return None

    def append_revision_as_user(self, **kwargs: Any) -> dict[str, Any]:
        self.append_calls.append(dict(kwargs))
        prompt_key = str(kwargs["prompt_key"])
        rows = self.history.setdefault(prompt_key, [])
        baseline = kwargs.get("baseline")
        if not rows and baseline:
            rows.append(
                {
                    "id": 100,
                    "prompt_key": prompt_key,
                    "revision": int(baseline["revision"]),
                    "content": baseline["content"],
                    "note": "初始文件版本",
                    "created_at": "2026-10-20T08:00:00Z",
                    "created_by_user_id": None,
                    "created_by_display_name": None,
                }
            )
        next_revision = max((int(item["revision"]) for item in rows), default=0) + 1
        row = {
            "id": 200 + next_revision,
            "prompt_key": prompt_key,
            "revision": next_revision,
            "content": kwargs["content"],
            "note": kwargs.get("note"),
            "created_at": "2026-10-21T09:00:00Z",
            "created_by_user_id": kwargs["actor_user_id"],
            "created_by_display_name": "测试管理员",
        }
        rows.append(row)
        self.latest[prompt_key] = dict(row)
        return dict(row)


def _adapter(rows: Optional[dict[str, dict[str, Any]]] = None):
    return SimpleNamespace(prompt_revisions=_FakePromptRevisions(rows))


def _patch_adapter(monkeypatch: pytest.MonkeyPatch, adapter: Any) -> None:
    monkeypatch.setattr(prompt_service, "get_adapter", lambda: adapter)


def test_get_prompts_payload_serializes_all_prompts() -> None:
    payload = prompt_service.get_prompts_payload(_adapter())

    assert [item["key"] for item in payload["items"]] == list(PROMPT_KEYS)
    first = payload["items"][0]
    assert first["label"] == "京外正面重要性"
    assert first["source"] == "database"
    assert first["version"] == "v2"
    assert first["revision_id"] == 12
    assert first["char_count"] == len(first["content"])
    assert first["updated_by"]["display_name"] == "编辑甲"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("   ", "不能为空"),
        ("<prompt>内容</prompt>", "不要包含标签"),
        ("x" * 50_001, "不能超过"),
    ],
    ids=["blank", "tag", "too-long"],
)
def test_save_prompt_rejects_invalid_content(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
    message: str,
) -> None:
    adapter = _adapter()
    _patch_adapter(monkeypatch, adapter)

    with pytest.raises(ValueError, match=message):
        prompt_service.save_prompt(
            "external_positive",
            content=content,
            expected_revision_id=12,
            note=None,
            actor=ADMIN,
        )


def test_save_prompt_rejects_unchanged_content(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter()
    _patch_adapter(monkeypatch, adapter)

    with pytest.raises(ValueError, match="未发生变化"):
        prompt_service.save_prompt(
            "external_positive",
            content="external_positive database body",
            expected_revision_id=12,
            note=None,
            actor=ADMIN,
        )


def test_save_prompt_requires_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter()
    _patch_adapter(monkeypatch, adapter)

    with pytest.raises(PermissionError):
        prompt_service.save_prompt(
            "external_positive",
            content="new body",
            expected_revision_id=12,
            note=None,
            actor=DUTY,
        )


def test_save_prompt_rejects_stale_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter()
    _patch_adapter(monkeypatch, adapter)

    with pytest.raises(PromptRevisionConflictError):
        prompt_service.save_prompt(
            "external_positive",
            content="new body",
            expected_revision_id=11,
            note=None,
            actor=ADMIN,
        )


def test_save_prompt_appends_and_returns_new_item(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter()
    _patch_adapter(monkeypatch, adapter)

    item = prompt_service.save_prompt(
        "external_positive",
        content="  new body\r\nline  ",
        expected_revision_id=12,
        note="  收紧  ",
        actor=ADMIN,
    )

    call = adapter.prompt_revisions.append_calls[0]
    assert call["content"] == "new body\nline"
    assert call["note"] == "收紧"
    assert call["expected_revision_id"] == 12
    assert call["actor_user_id"] == "admin-id"
    assert call["baseline"] is None
    assert item["version"] == "v3"
    assert item["content"] == "new body\nline"
    assert item["source"] == "database"
    assert item["updated_by"]["display_name"] == "测试管理员"


def test_first_save_from_file_source_passes_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {key: _row(key) for key in PROMPT_KEYS if key != "beijing_gate"}
    adapter = _adapter(rows)
    _patch_adapter(monkeypatch, adapter)

    item = prompt_service.save_prompt(
        "beijing_gate",
        content="custom gate prompt",
        expected_revision_id=None,
        note=None,
        actor=ADMIN,
    )

    call = adapter.prompt_revisions.append_calls[0]
    assert call["baseline"]["revision"] == 1
    assert "北京" in call["baseline"]["content"]
    assert item["version"] == "v2"
    assert item["source"] == "database"


def test_list_revisions_returns_metadata_and_has_more(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter()
    history = adapter.prompt_revisions.history["external_positive"]
    history.insert(
        0,
        {
            "id": 99,
            "prompt_key": "external_positive",
            "revision": 1,
            "content": "older body",
            "note": "旧版本",
            "created_at": "2026-10-01T08:00:00Z",
            "created_by_user_id": None,
            "created_by_display_name": None,
        },
    )

    payload = prompt_service.list_prompt_revisions(
        "external_positive",
        limit=1,
        adapter=adapter,
    )

    assert payload["has_more"] is True
    assert len(payload["items"]) == 1
    first = payload["items"][0]
    assert first["version"] == "v2"
    assert first["created_by"]["display_name"] == "编辑甲"
    assert first["char_count"] == len("external_positive database body")


def test_get_revision_returns_content_and_missing_raises() -> None:
    adapter = _adapter()
    revision_id = int(adapter.prompt_revisions.latest["external_positive"]["id"])

    payload = prompt_service.get_prompt_revision(
        "external_positive",
        revision_id,
        adapter=adapter,
    )

    assert payload["content"] == "external_positive database body"
    assert payload["version"] == "v2"
    with pytest.raises(KeyError):
        prompt_service.get_prompt_revision("external_positive", 999, adapter=adapter)


def test_unknown_prompt_key_raises_key_error() -> None:
    with pytest.raises(KeyError):
        prompt_service.list_prompt_revisions("unknown", adapter=_adapter())
