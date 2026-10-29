from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Optional

import psycopg

if TYPE_CHECKING:
    from src.adapters.db_postgres_core import PostgresAdapter


class PromptRevisionConflictError(RuntimeError):
    """Raised when a prompt was changed after the client read it."""


class PromptRevisionsNamespace:
    """Append-only prompt revision storage."""

    def __init__(self, adapter: PostgresAdapter) -> None:
        self._adapter = adapter

    def fetch_latest_by_key(self) -> dict[str, dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_latest_prompt_revisions(cur)

    def fetch_revisions(
        self,
        prompt_key: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_prompt_revisions(cur, prompt_key, limit=limit)

    def fetch_revision(
        self,
        prompt_key: str,
        revision_id: int,
    ) -> Optional[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_prompt_revision(cur, prompt_key, revision_id)

    def append_revision_as_user(
        self,
        *,
        prompt_key: str,
        content: str,
        note: Optional[str],
        expected_revision_id: Optional[int],
        baseline: Optional[Mapping[str, Any]],
        actor_user_id: Optional[str],
    ) -> dict[str, Any]:
        with self._adapter.transaction() as cur:
            return append_prompt_revision(
                cur,
                prompt_key=prompt_key,
                content=content,
                note=note,
                expected_revision_id=expected_revision_id,
                baseline=baseline,
                actor_user_id=actor_user_id,
            )


def fetch_latest_prompt_revisions(cur: psycopg.Cursor) -> dict[str, dict[str, Any]]:
    cur.execute(
        """
        SELECT DISTINCT ON (p.prompt_key)
               p.*, u.display_name AS created_by_display_name
        FROM prompt_revisions p
        LEFT JOIN console_users u ON u.id = p.created_by_user_id
        ORDER BY p.prompt_key, p.revision DESC
        """
    )
    return {str(row["prompt_key"]): dict(row) for row in cur.fetchall()}


def fetch_prompt_revisions(
    cur: psycopg.Cursor,
    prompt_key: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT p.*, u.display_name AS created_by_display_name
        FROM prompt_revisions p
        LEFT JOIN console_users u ON u.id = p.created_by_user_id
        WHERE p.prompt_key = %s
        ORDER BY p.revision DESC
        LIMIT %s
        """,
        (prompt_key, limit),
    )
    return [dict(row) for row in cur.fetchall()]


def fetch_prompt_revision(
    cur: psycopg.Cursor,
    prompt_key: str,
    revision_id: int,
) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        SELECT p.*, u.display_name AS created_by_display_name
        FROM prompt_revisions p
        LEFT JOIN console_users u ON u.id = p.created_by_user_id
        WHERE p.prompt_key = %s AND p.id = %s
        """,
        (prompt_key, revision_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def append_prompt_revision(
    cur: psycopg.Cursor,
    *,
    prompt_key: str,
    content: str,
    note: Optional[str],
    expected_revision_id: Optional[int],
    baseline: Optional[Mapping[str, Any]],
    actor_user_id: Optional[str],
) -> dict[str, Any]:
    """Append one revision, optionally seeding the pre-console file version first.

    The advisory lock serializes writers per prompt key. ``expected_revision_id`` is
    the id the editor loaded; ``None`` means "no database revision yet". A stale
    token raises ``PromptRevisionConflictError`` instead of silently overwriting.

    ``actor_user_id=None`` is the system identity used by the nightly optimizer.
    The run record table, not ``prompt_revisions``, distinguishes those versions
    from imported file baselines.
    """

    # 同一提示词的并发保存串行化；不同提示词互不阻塞。
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%s))",
        (f"prompt_revisions:{prompt_key}",),
    )
    cur.execute(
        """
        SELECT id, revision
        FROM prompt_revisions
        WHERE prompt_key = %s
        ORDER BY revision DESC
        LIMIT 1
        """,
        (prompt_key,),
    )
    latest = cur.fetchone()
    latest_id = int(latest["id"]) if latest else None
    if expected_revision_id != latest_id:
        current_version = (
            f"v{int(latest['revision'])}" if latest else "文件默认"
        )
        raise PromptRevisionConflictError(
            f"提示词已在别处被修改（当前版本：{current_version}）"
        )

    next_revision = 1
    if latest is not None:
        next_revision = int(latest["revision"]) + 1
    elif baseline is not None:
        baseline_revision = int(baseline.get("revision") or 0)
        baseline_content = str(baseline.get("content") or "")
        if baseline_revision > 0 and baseline_content.strip():
            # 首次保存文件来源的提示词时，把当前文件版本记为历史起点，
            # 这样「回看」能看到编辑前的原文，恢复也回到这一版。
            cur.execute(
                """
                INSERT INTO prompt_revisions (
                    prompt_key, revision, content, note, created_by_user_id
                )
                VALUES (%s, %s, %s, %s, NULL)
                ON CONFLICT (prompt_key, revision) DO NOTHING
                """,
                (
                    prompt_key,
                    baseline_revision,
                    baseline_content,
                    "初始文件版本",
                ),
            )
            next_revision = baseline_revision + 1

    cur.execute(
        """
        INSERT INTO prompt_revisions (
            prompt_key, revision, content, note, created_by_user_id
        )
        VALUES (%s, %s, %s, %s, %s)
        RETURNING *
        """,
        (prompt_key, next_revision, content, note, actor_user_id),
    )
    row = dict(cur.fetchone())
    if actor_user_id is None:
        row["created_by_display_name"] = None
    else:
        cur.execute(
            "SELECT display_name FROM console_users WHERE id = %s",
            (actor_user_id,),
        )
        user = cur.fetchone()
        row["created_by_display_name"] = user["display_name"] if user else None
    return row


__all__ = [
    "PromptRevisionConflictError",
    "PromptRevisionsNamespace",
    "append_prompt_revision",
    "fetch_latest_prompt_revisions",
    "fetch_prompt_revision",
    "fetch_prompt_revisions",
]
