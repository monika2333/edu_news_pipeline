from __future__ import annotations

import contextlib
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from src.adapters import (
    db_postgres_audit as audit,
    db_postgres_export as export,
    db_postgres_ingest as ingest,
    db_postgres_manual_reviews as manual_reviews,
    db_postgres_news_summaries as news_summaries,
    db_postgres_process as process,
    db_postgres_score_feedback as score_feedback,
    db_postgres_shift_reviews as shift_reviews,
    db_postgres_shifts as shifts,
    db_postgres_users as users,
)
from src.adapters.db_postgres_shared import MISSING as _MISSING
from src.adapters.db_postgres_shared import article_hash, iso_datetime, json_safe, to_iso
from src.config import get_settings
from src.domain import BeijingGateCandidate, ExportCandidate, ExternalFilterCandidate, PrimaryArticleForScoring

_CONNECTION: Optional[psycopg.Connection] = None
_ADAPTER: Optional["PostgresAdapter"] = None
_BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _future_shift_error_message(
    action: str,
    user: Mapping[str, Any],
    shifts: Sequence[Mapping[str, Any]],
) -> str:
    shift_dates: list[str] = []
    for shift in shifts:
        ends_at = shift["ends_at"].astimezone(_BUSINESS_TIMEZONE)
        label = f"{ends_at.month}月{ends_at.day}日"
        if label not in shift_dates:
            shift_dates.append(label)
    display_name = str(
        user.get("display_name")
        or user.get("username")
        or "该用户"
    ).strip()
    return (
        f"无法{action}“{display_name}”：仍负责以下未来班次："
        f"{'、'.join(shift_dates)}。请先改派或取消这些班次。"
    )


def _get_connection() -> psycopg.Connection:
    global _CONNECTION
    settings = get_settings()
    if _CONNECTION is None or _CONNECTION.closed:
        _CONNECTION = psycopg.connect(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            dbname=settings.db_name,
            autocommit=True,
        )
        schema = settings.db_schema or "public"
        with _CONNECTION.cursor() as cur:
            cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
    return _CONNECTION


class PostgresAdapter:
    """High-level helpers for interacting with the local PostgreSQL database."""

    def __init__(self, connection: Optional[psycopg.Connection] = None) -> None:
        self._settings = get_settings()
        self._schema = self._settings.db_schema or "public"
        self._conn = connection or _get_connection()

    def _conn_cursor(self):
        if self._conn.closed:
            self._conn = _get_connection()
        return self._conn.cursor(row_factory=dict_row)

    @contextlib.contextmanager
    def transaction(self):
        if self._conn.closed:
            self._conn = _get_connection()
        prev_autocommit = self._conn.autocommit
        if prev_autocommit:
            self._conn.autocommit = False
        cur = self._conn.cursor(row_factory=dict_row)
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()
            self._conn.autocommit = prev_autocommit

    @contextlib.contextmanager
    def _cursor(self):
        cur = self._conn_cursor()
        try:
            yield cur
            if not self._conn.autocommit:
                self._conn.commit()
        except Exception:
            if not self._conn.autocommit:
                self._conn.rollback()
            raise
        finally:
            cur.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _article_hash(article_id: Optional[str], original_url: Optional[str], title: Optional[str]) -> str:
        return article_hash(article_id, original_url, title)

    @staticmethod
    def _to_iso(publish_time: Optional[int]) -> Optional[str]:
        return to_iso(publish_time)

    @staticmethod
    def _iso_datetime(value: Any) -> Optional[str]:
        return iso_datetime(value)

    @staticmethod
    def _json_safe(value: Any) -> Any:
        return json_safe(value)

    # ------------------------------------------------------------------
    # Console users + sessions
    # ------------------------------------------------------------------
    def create_console_user(
        self,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        role: str,
        preferred_weekday: Optional[int] = None,
        actor_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self.transaction() as cur:
            created = users.create_console_user(
                cur,
                username=username,
                display_name=display_name,
                password_hash=password_hash,
                role=role,
                preferred_weekday=preferred_weekday,
            )
            audit.insert_review_event(
                cur,
                actor_user_id=actor_user_id,
                action="user.create",
                target_type="console_user",
                target_id=str(created["id"]),
                before_data=None,
                after_data=created,
            )
            return created

    def fetch_console_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            return users.fetch_console_user_by_username(cur, username)

    def fetch_console_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            return users.fetch_console_user_by_id(cur, user_id)

    def fetch_console_users(self) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return users.fetch_console_users(cur)

    def update_console_user(
        self,
        *,
        user_id: str,
        actor_user_id: str,
        display_name: Optional[str] = None,
        set_display_name: bool = False,
        role: Optional[str] = None,
        set_role: bool = False,
        preferred_weekday: Optional[int] = None,
        set_preferred_weekday: bool = False,
        is_active: Optional[bool] = None,
        set_is_active: bool = False,
    ) -> Optional[Dict[str, Any]]:
        with self.transaction() as cur:
            before = users.fetch_console_user_for_update(cur, user_id)
            if not before:
                return None
            removes_active_admin = (
                before["role"] == "admin"
                and bool(before["is_active"])
                and (
                    (set_role and role != "admin")
                    or (set_is_active and is_active is False)
                )
            )
            if removes_active_admin:
                active_admin_ids = users.lock_active_admin_ids(cur)
                if len(active_admin_ids) <= 1:
                    raise ValueError("系统至少需要保留一个启用中的管理员账号")
            disables_editor = (
                before["role"] == "duty_editor"
                and bool(before["is_active"])
                and (
                    (set_is_active and is_active is False)
                    or (set_role and role != "duty_editor")
                )
            )
            if disables_editor:
                future_shifts = users.fetch_future_shifts_for_user(cur, user_id)
                if future_shifts:
                    raise ValueError(
                        _future_shift_error_message("停用", before, future_shifts)
                    )
            after = users.update_console_user(
                cur,
                user_id=user_id,
                display_name=display_name,
                set_display_name=set_display_name,
                role=role,
                set_role=set_role,
                preferred_weekday=preferred_weekday,
                set_preferred_weekday=set_preferred_weekday,
                is_active=is_active,
                set_is_active=set_is_active,
            )
            if after:
                if set_is_active and is_active is False:
                    users.revoke_console_user_sessions(cur, user_id=user_id)
                audit.insert_review_event(
                    cur,
                    actor_user_id=actor_user_id,
                    action="user.update",
                    target_type="console_user",
                    target_id=user_id,
                    before_data=before,
                    after_data=after,
                )
            return after

    def delete_console_user(
        self,
        *,
        user_id: str,
        actor_user_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self.transaction() as cur:
            before = users.fetch_console_user_for_update(cur, user_id)
            if not before:
                return None
            if before["role"] == "admin" and bool(before["is_active"]):
                active_admin_ids = users.lock_active_admin_ids(cur)
                if len(active_admin_ids) <= 1:
                    raise ValueError("系统至少需要保留一个启用中的管理员账号")
            future_shifts = users.fetch_future_shifts_for_user(cur, user_id)
            if future_shifts:
                raise ValueError(
                    _future_shift_error_message("删除", before, future_shifts)
                )
            users.delete_duty_schedules_for_user(cur, user_id=user_id)
            after = users.soft_delete_console_user(cur, user_id=user_id)
            if after:
                users.revoke_console_user_sessions(cur, user_id=user_id)
                audit.insert_review_event(
                    cur,
                    actor_user_id=actor_user_id,
                    action="user.delete",
                    target_type="console_user",
                    target_id=user_id,
                    before_data=before,
                    after_data=after,
                )
            return after

    def reset_console_user_password(
        self,
        *,
        user_id: str,
        actor_user_id: str,
        password_hash: str,
    ) -> bool:
        with self.transaction() as cur:
            before = users.fetch_console_user_for_update(cur, user_id)
            if not before:
                return False
            updated = users.update_console_user_password(
                cur,
                user_id=user_id,
                password_hash=password_hash,
            )
            if not updated:
                return False
            revoked = users.revoke_console_user_sessions(cur, user_id=user_id)
            audit.insert_review_event(
                cur,
                actor_user_id=actor_user_id,
                action="user.reset_password",
                target_type="console_user",
                target_id=user_id,
                before_data={"password_changed_at": before["password_changed_at"]},
                after_data={"sessions_revoked": revoked},
            )
            return True

    def create_console_session(
        self,
        *,
        user_id: str,
        token_hash: str,
        csrf_token_hash: str,
        expires_at: datetime,
    ) -> Dict[str, Any]:
        with self.transaction() as cur:
            session = users.create_console_session(
                cur,
                user_id=user_id,
                token_hash=token_hash,
                csrf_token_hash=csrf_token_hash,
                expires_at=expires_at,
            )
            users.record_console_user_login(cur, user_id)
            audit.insert_review_event(
                cur,
                actor_user_id=user_id,
                action="auth.login.success",
                target_type="console_user",
                target_id=user_id,
                before_data=None,
                after_data={
                    "session_id": session["id"],
                    "expires_at": expires_at,
                },
            )
            return session

    def record_console_auth_event(
        self,
        *,
        action: str,
        target_id: Optional[str],
        actor_user_id: Optional[str],
        after_data: Mapping[str, Any],
        request_id: Optional[str] = None,
    ) -> int:
        with self.transaction() as cur:
            return audit.insert_review_event(
                cur,
                actor_user_id=actor_user_id,
                action=action,
                target_type="console_auth",
                target_id=target_id,
                before_data=None,
                after_data=after_data,
                request_id=request_id,
            )

    def fetch_console_session_by_token_hash(
        self,
        token_hash: str,
    ) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            return users.fetch_console_session_by_token_hash(cur, token_hash)

    def touch_console_session(self, session_id: str) -> None:
        with self._cursor() as cur:
            users.touch_console_session(cur, session_id)

    def revoke_console_session_by_token_hash(
        self,
        token_hash: str,
        *,
        actor_user_id: Optional[str] = None,
    ) -> bool:
        with self.transaction() as cur:
            revoked = users.revoke_console_session_by_token_hash(cur, token_hash)
            if revoked:
                audit.insert_review_event(
                    cur,
                    actor_user_id=actor_user_id,
                    action="auth.logout",
                    target_type="console_user",
                    target_id=actor_user_id,
                    before_data=None,
                    after_data={"revoked": True},
                )
            return revoked

    def change_console_user_password(
        self,
        *,
        user_id: str,
        password_hash: str,
        current_session_id: Optional[str],
    ) -> bool:
        with self.transaction() as cur:
            updated = users.update_console_user_password(
                cur,
                user_id=user_id,
                password_hash=password_hash,
            )
            if updated:
                revoked = users.revoke_console_user_sessions(
                    cur,
                    user_id=user_id,
                    except_session_id=current_session_id,
                )
                audit.insert_review_event(
                    cur,
                    actor_user_id=user_id,
                    action="auth.password.change",
                    target_type="console_user",
                    target_id=user_id,
                    before_data=None,
                    after_data={"other_sessions_revoked": revoked},
                )
            return updated

    def delete_expired_console_sessions(self) -> int:
        with self._cursor() as cur:
            return users.delete_expired_console_sessions(cur)

    # ------------------------------------------------------------------
    # Duty schedules + shifts
    # ------------------------------------------------------------------
    def fetch_active_duty_editors(self) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return shifts.fetch_active_duty_editors(cur)

    def fetch_duty_schedule(self) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return shifts.fetch_duty_schedule(cur)

    def upsert_duty_schedule(
        self,
        assignments: Mapping[int, str],
        *,
        actor_user_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        with self.transaction() as cur:
            before = shifts.fetch_duty_schedule(cur)
            after = shifts.upsert_duty_schedule(cur, assignments)
            audit.insert_review_event(
                cur,
                actor_user_id=actor_user_id,
                action="schedule.update",
                target_type="duty_schedule",
                target_id="weekly",
                before_data={"assignments": before},
                after_data={"assignments": after},
            )
            return after

    def insert_duty_shifts(
        self,
        rows: Sequence[Mapping[str, Any]],
    ) -> int:
        with self._cursor() as cur:
            return shifts.insert_duty_shifts(cur, rows)

    def create_duty_shift(
        self,
        *,
        user_id: str,
        starts_at: datetime,
        ends_at: datetime,
        notes: Optional[str],
        actor_user_id: Optional[str],
    ) -> Dict[str, Any]:
        with self.transaction() as cur:
            created = shifts.create_duty_shift(
                cur,
                user_id=user_id,
                starts_at=starts_at,
                ends_at=ends_at,
                notes=notes,
                created_by_user_id=actor_user_id,
            )
            audit.insert_review_event(
                cur,
                actor_user_id=actor_user_id,
                action="shift.create",
                target_type="duty_shift",
                target_id=str(created["id"]),
                before_data=None,
                after_data=created,
            )
            return created

    def fetch_duty_shift(self, shift_id: str) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            return shifts.fetch_duty_shift(cur, shift_id)

    def fetch_duty_shifts(
        self,
        *,
        user_id: Optional[str] = None,
        starts_before: Optional[datetime] = None,
        ends_after: Optional[datetime] = None,
        include_cancelled: bool = True,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return shifts.fetch_duty_shifts(
                cur,
                user_id=user_id,
                starts_before=starts_before,
                ends_after=ends_after,
                include_cancelled=include_cancelled,
                limit=limit,
            )

    def fetch_overlapping_duty_shift(
        self,
        *,
        starts_at: datetime,
        ends_at: datetime,
        exclude_shift_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            return shifts.fetch_overlapping_duty_shift(
                cur,
                starts_at=starts_at,
                ends_at=ends_at,
                exclude_shift_id=exclude_shift_id,
            )

    def update_duty_shift(
        self,
        *,
        shift_id: str,
        actor_user_id: Optional[str],
        user_id: Optional[str] = None,
        set_user_id: bool = False,
        notes: Optional[str] = None,
        set_notes: bool = False,
        cancelled: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        with self.transaction() as cur:
            before = shifts.fetch_duty_shift(cur, shift_id)
            if not before:
                return None
            after = shifts.update_duty_shift(
                cur,
                shift_id=shift_id,
                user_id=user_id,
                set_user_id=set_user_id,
                notes=notes,
                set_notes=set_notes,
                cancelled=cancelled,
            )
            if after:
                audit.insert_review_event(
                    cur,
                    actor_user_id=actor_user_id,
                    action="shift.update",
                    target_type="duty_shift",
                    target_id=shift_id,
                    before_data=before,
                    after_data=after,
                )
            return after

    def fetch_shift_coverage_end(self) -> Optional[datetime]:
        with self._cursor() as cur:
            return shifts.fetch_shift_coverage_end(cur)

    # ------------------------------------------------------------------
    # Duty reviews
    # ------------------------------------------------------------------
    def fetch_shift_review_items(
        self,
        *,
        shift_id: str,
        decision: Optional[str],
        report_type: Optional[str],
        limit: int,
        offset: int,
        mismatch_only: bool = False,
        include_admin_state: bool = False,
        admin_discarded_only: bool = False,
        exclude_admin_discarded: bool = False,
        exclude_finalized: bool = False,
    ) -> Tuple[List[Dict[str, Any]], int]:
        with self._cursor() as cur:
            return shift_reviews.fetch_shift_review_items(
                cur,
                shift_id=shift_id,
                decision=decision,
                report_type=report_type,
                limit=limit,
                offset=offset,
                mismatch_only=mismatch_only,
                include_admin_state=include_admin_state,
                admin_discarded_only=admin_discarded_only,
                exclude_admin_discarded=exclude_admin_discarded,
                exclude_finalized=exclude_finalized,
            )

    def fetch_shift_clusters(
        self,
        *,
        shift_id: str,
        report_type: str,
    ) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return shift_reviews.fetch_shift_clusters(
                cur,
                shift_id=shift_id,
                report_type=report_type,
            )

    def save_shift_review(
        self,
        *,
        shift_id: str,
        article_id: str,
        actor_user_id: str,
        expected_version: Optional[int],
        patch: Mapping[str, Any],
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self.transaction() as cur:
            if not shift_reviews.shift_contains_article(
                cur,
                shift_id=shift_id,
                article_id=article_id,
            ):
                raise ValueError("Article does not belong to this active shift")
            before, after = shift_reviews.upsert_shift_review(
                cur,
                shift_id=shift_id,
                article_id=article_id,
                actor_user_id=actor_user_id,
                expected_version=expected_version,
                patch=patch,
            )
            audit.insert_review_event(
                cur,
                actor_user_id=actor_user_id,
                action="shift_review.update",
                target_type="shift_review",
                target_id=f"{shift_id}:{article_id}",
                before_data=before,
                after_data=after,
                request_id=request_id,
            )
            return after

    def save_shift_reviews(
        self,
        *,
        shift_id: str,
        actor_user_id: str,
        updates: Sequence[Mapping[str, Any]],
        action: str,
        request_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self.transaction() as cur:
            before_items: List[Optional[Dict[str, Any]]] = []
            after_items: List[Dict[str, Any]] = []
            for update in updates:
                article_id = str(update["article_id"])
                if not shift_reviews.shift_contains_article(
                    cur,
                    shift_id=shift_id,
                    article_id=article_id,
                ):
                    raise ValueError("Article does not belong to this active shift")
                before, after = shift_reviews.upsert_shift_review(
                    cur,
                    shift_id=shift_id,
                    article_id=article_id,
                    actor_user_id=actor_user_id,
                    expected_version=update.get("expected_version"),
                    patch=update.get("patch") or {},
                )
                before_items.append(before)
                after_items.append(after)
            if after_items:
                audit.insert_review_event(
                    cur,
                    actor_user_id=actor_user_id,
                    action=action,
                    target_type="shift_review_batch",
                    target_id=shift_id,
                    before_data={"items": before_items},
                    after_data={"items": after_items},
                    request_id=request_id,
                )
            return after_items

    def set_shift_review_admin_discarded(
        self,
        *,
        shift_id: str,
        article_id: str,
        actor_user_id: str,
        discarded: bool,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.set_shift_reviews_admin_discarded(
            shift_id=shift_id,
            article_ids=[article_id],
            actor_user_id=actor_user_id,
            discarded=discarded,
            request_id=request_id,
        )[0]

    def set_shift_reviews_admin_discarded(
        self,
        *,
        shift_id: str,
        article_ids: Sequence[str],
        actor_user_id: str,
        discarded: bool,
        request_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self.transaction() as cur:
            saved: List[Dict[str, Any]] = []
            for article_id in article_ids:
                before, after = shift_reviews.set_admin_discarded(
                    cur,
                    shift_id=shift_id,
                    article_id=article_id,
                    actor_user_id=actor_user_id,
                    discarded=discarded,
                )
                audit.insert_review_event(
                    cur,
                    actor_user_id=actor_user_id,
                    action=(
                        "duty_summary.discard"
                        if discarded
                        else "duty_summary.restore"
                    ),
                    target_type="shift_review",
                    target_id=f"{shift_id}:{article_id}",
                    before_data=before,
                    after_data=after,
                    request_id=request_id,
                )
                saved.append(after)
            return saved

    def finalize_shift_review_batch(
        self,
        *,
        shift_id: str,
        report_type: str,
        actor_user_id: str,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self.transaction() as cur:
            batch = shift_reviews.finalize_shift_review_batch(
                cur,
                shift_id=shift_id,
                report_type=report_type,
                actor_user_id=actor_user_id,
            )
            audit.insert_review_event(
                cur,
                actor_user_id=actor_user_id,
                action="shift_review.finalize",
                target_type="shift_review_finalization_batch",
                target_id=str(batch["id"]),
                before_data=None,
                after_data=batch,
                request_id=request_id,
            )
            return batch

    def fetch_shift_finalized_items(
        self,
        *,
        shift_id: str,
        report_type: str,
    ) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return shift_reviews.fetch_shift_finalized_items(
                cur,
                shift_id=shift_id,
                report_type=report_type,
            )

    def restore_shift_review_finalization(
        self,
        *,
        shift_id: str,
        batch_id: str,
        actor_user_id: str,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self.transaction() as cur:
            result = shift_reviews.restore_shift_review_finalization(
                cur,
                shift_id=shift_id,
                batch_id=batch_id,
                actor_user_id=actor_user_id,
            )
            audit.insert_review_event(
                cur,
                actor_user_id=actor_user_id,
                action="shift_review.restore_finalization",
                target_type="shift_review_finalization_batch",
                target_id=batch_id,
                before_data={"batch_id": batch_id},
                after_data=result,
                request_id=request_id,
            )
            return result

    def update_shift_review_order(
        self,
        *,
        shift_id: str,
        actor_user_id: str,
        selected_order: Sequence[str],
        backup_order: Sequence[str],
        request_id: Optional[str] = None,
    ) -> int:
        with self.transaction() as cur:
            updated = shift_reviews.update_shift_review_order(
                cur,
                shift_id=shift_id,
                actor_user_id=actor_user_id,
                selected_order=selected_order,
                backup_order=backup_order,
            )
            audit.insert_review_event(
                cur,
                actor_user_id=actor_user_id,
                action="shift_review.reorder",
                target_type="duty_shift",
                target_id=shift_id,
                before_data=None,
                after_data={
                    "selected_order": list(selected_order),
                    "backup_order": list(backup_order),
                },
                request_id=request_id,
            )
            return updated

    def fetch_shift_stats(self, shift_id: str) -> Dict[str, Any]:
        with self._cursor() as cur:
            return shift_reviews.fetch_shift_stats(cur, shift_id)

    def fetch_admin_shift_summaries(
        self,
        *,
        limit: int = 60,
    ) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return shift_reviews.fetch_admin_shift_summaries(cur, limit=limit)

    def preview_shift_reviews_for_manual(
        self,
        *,
        shift_id: str,
        article_ids: Sequence[str],
    ) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return manual_reviews.preview_shift_reviews_for_manual(
                cur,
                shift_id=shift_id,
                article_ids=article_ids,
            )

    def import_shift_reviews_into_manual(
        self,
        *,
        shift_id: str,
        article_ids: Sequence[str],
        target_status: str,
        report_type: str,
        actor_username: str,
        actor_user_id: str,
        conflict_resolutions: Sequence[Mapping[str, Any]],
        request_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self.transaction() as cur:
            before = manual_reviews.fetch_manual_review_rows(
                cur,
                article_ids,
                for_update=True,
            )
            resolution_by_id = {
                str(item["article_id"]): item
                for item in conflict_resolutions
                if item.get("article_id")
            }
            imported = manual_reviews.import_shift_reviews_into_manual(
                cur,
                shift_id=shift_id,
                article_ids=article_ids,
                target_status=target_status,
                report_type=report_type,
                actor_username=actor_username,
                actor_user_id=actor_user_id,
                existing_reviews=before,
                conflict_resolutions=resolution_by_id,
            )
            audit.insert_review_event(
                cur,
                actor_user_id=actor_user_id,
                action="duty_summary.import",
                target_type="duty_shift",
                target_id=shift_id,
                before_data={"manual_reviews": before},
                after_data={
                    "manual_reviews": imported,
                    "target_status": target_status,
                    "report_type": report_type,
                },
                request_id=request_id,
            )
            return imported

    def fetch_review_events(
        self,
        *,
        limit: int,
        offset: int,
        actor_user_id: Optional[str] = None,
        target_type: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        with self._cursor() as cur:
            return audit.fetch_review_events(
                cur,
                limit=limit,
                offset=offset,
                actor_user_id=actor_user_id,
                target_type=target_type,
            )

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------
    def upsert_toutiao_articles(self, rows: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return ingest.upsert_toutiao_articles(cur, rows)

    def upsert_raw_feed_rows(self, rows: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return ingest.upsert_raw_feed_rows(cur, rows)

    def update_raw_article_details(self, rows: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return ingest.update_raw_article_details(cur, rows)

    def get_raw_articles_missing_content(self, article_ids: Sequence[str]) -> Set[str]:
        with self._cursor() as cur:
            return ingest.get_raw_articles_missing_content(cur, article_ids)

    def fetch_raw_articles_missing_content(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return ingest.fetch_raw_articles_missing_content(cur, limit)

    def upsert_filtered_articles(self, rows: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return ingest.upsert_filtered_articles(cur, rows)

    def fetch_filtered_articles_for_hashing(self, limit: int) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return ingest.fetch_filtered_articles_for_hashing(cur, limit)

    def fetch_filtered_articles_by_hashes(self, hashes: Sequence[str]) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return ingest.fetch_filtered_articles_by_hashes(cur, hashes)

    def update_filtered_article_features(self, updates: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return ingest.update_filtered_article_features(cur, updates)

    def fetch_filtered_articles_by_band(self, band_index: int, band_value: int, limit: int) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return ingest.fetch_filtered_articles_by_band(cur, band_index, band_value, limit)

    def update_filtered_primary_ids(self, updates: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return ingest.update_filtered_primary_ids(cur, updates)

    def upsert_primary_articles(self, rows: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return ingest.upsert_primary_articles(cur, rows)

    def get_existing_raw_article_ids(self) -> Set[str]:
        with self._cursor() as cur:
            return ingest.get_existing_raw_article_ids(cur)

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------
    def insert_pending_summary(
        self,
        article: Mapping[str, Any],
        *,
        keywords: Optional[Sequence[str]] = None,
        fetched_at: Optional[str] = None,
    ) -> None:
        with self._cursor() as cur:
            news_summaries.insert_pending_summary(cur, article, keywords=keywords, fetched_at=fetched_at)

    def fetch_pending_summaries(
        self,
        limit: Optional[int] = None,
        *,
        max_attempts: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return news_summaries.fetch_pending_summaries(cur, limit, max_attempts=max_attempts)

    def mark_summary_attempt(self, article_id: str) -> bool:
        with self._cursor() as cur:
            return news_summaries.mark_summary_attempt(cur, article_id)

    def complete_summary_generation(self, article_id: str, summary_text: str) -> None:
        with self._cursor() as cur:
            news_summaries.complete_summary_generation(cur, article_id, summary_text)

    def fetch_pending_summary_enrichments(
        self,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return news_summaries.fetch_pending_summary_enrichments(cur, limit)

    def complete_summary_enrichment(
        self,
        article_id: str,
        *,
        label: str,
        confidence: Optional[float],
        llm_source: Optional[str],
    ) -> None:
        with self._cursor() as cur:
            news_summaries.complete_summary_enrichment(
                cur,
                article_id,
                label=label,
                confidence=confidence,
                llm_source=llm_source,
            )

    def fetch_pending_summary_routes(
        self,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return news_summaries.fetch_pending_summary_routes(cur, limit)

    def complete_summary_routing(
        self,
        article_id: str,
        *,
        beijing_related: Optional[bool],
        status: str,
    ) -> None:
        with self._cursor() as cur:
            news_summaries.complete_summary_routing(
                cur,
                article_id,
                beijing_related=beijing_related,
                status=status,
            )

    def complete_summary(
        self,
        article_id: str,
        summary_text: str,
        *,
        llm_source: Optional[str] = None,
        keywords: Optional[Sequence[str]] = None,
        beijing_related: Optional[bool] = None,
        sentiment_label: Optional[str] = None,
        sentiment_confidence: Optional[float] = None,
        status: str = "ready_for_export",
        external_importance_status: Any = _MISSING,
        external_importance_score: Any = _MISSING,
        external_importance_checked_at: Any = _MISSING,
        external_importance_raw: Any = _MISSING,
        external_filter_attempted_at: Any = _MISSING,
        external_filter_fail_count: Any = _MISSING,
        is_beijing_related_llm: Any = _MISSING,
        beijing_gate_checked_at: Any = _MISSING,
        beijing_gate_raw: Any = _MISSING,
        beijing_gate_attempted_at: Any = _MISSING,
        beijing_gate_fail_count: Any = _MISSING,
    ) -> None:
        with self._cursor() as cur:
            news_summaries.complete_summary(
                cur,
                article_id,
                summary_text,
                llm_source=llm_source,
                keywords=keywords,
                beijing_related=beijing_related,
                sentiment_label=sentiment_label,
                sentiment_confidence=sentiment_confidence,
                status=status,
                external_importance_status=external_importance_status,
                external_importance_score=external_importance_score,
                external_importance_checked_at=external_importance_checked_at,
                external_importance_raw=external_importance_raw,
                external_filter_attempted_at=external_filter_attempted_at,
                external_filter_fail_count=external_filter_fail_count,
                is_beijing_related_llm=is_beijing_related_llm,
                beijing_gate_checked_at=beijing_gate_checked_at,
                beijing_gate_raw=beijing_gate_raw,
                beijing_gate_attempted_at=beijing_gate_attempted_at,
                beijing_gate_fail_count=beijing_gate_fail_count,
            )

    def mark_summary_failed(self, article_id: str, *, message: Optional[str] = None) -> None:
        with self._cursor() as cur:
            news_summaries.mark_summary_failed(cur, article_id, message=message)

    def search_news_summaries(
        self,
        *,
        query: Optional[str] = None,
        sources: Optional[Sequence[str]] = None,
        sentiments: Optional[Sequence[str]] = None,
        statuses: Optional[Sequence[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        with self._cursor() as cur:
            return news_summaries.search_news_summaries(
                cur,
                query=query,
                sources=sources,
                sentiments=sentiments,
                statuses=statuses,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
                offset=offset,
            )

    def fetch_news_summary_content(self, article_id: str) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            return news_summaries.fetch_news_summary_content(cur, article_id)

    def fetch_raw_articles_for_summary(
        self,
        *,
        after_fetched_at: Optional[str],
        limit: Optional[int],
    ) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return news_summaries.fetch_raw_articles_for_summary(
                cur,
                after_fetched_at=after_fetched_at,
                limit=limit,
            )

    def get_existing_news_summary_ids(self, article_ids: Sequence[str]) -> Set[str]:
        with self._cursor() as cur:
            return news_summaries.get_existing_news_summary_ids(cur, article_ids)

    def upsert_news_summary(
        self,
        article: Dict[str, Any],
        summary: str,
        *,
        keywords: Optional[Sequence[str]] = None,
    ) -> None:
        with self._cursor() as cur:
            news_summaries.upsert_news_summary(cur, article, summary, keywords=keywords)

    def update_summary_score(self, article_id: str, score: Optional[float]) -> None:
        with self._cursor() as cur:
            news_summaries.update_summary_score(cur, article_id, score)

    # ------------------------------------------------------------------
    # Backward-compat wrappers (to be removed after refactor)
    # ------------------------------------------------------------------
    def upsert_toutiao_feed_rows(self, rows: Sequence[Mapping[str, Any]]) -> int:
        return self.upsert_raw_feed_rows(rows)

    def update_toutiao_article_details(self, rows: Sequence[Mapping[str, Any]]) -> int:
        return self.update_raw_article_details(rows)

    def get_toutiao_articles_missing_content(self, article_ids: Sequence[str]) -> Set[str]:
        return self.get_raw_articles_missing_content(article_ids)

    def fetch_toutiao_articles_missing_content(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.fetch_raw_articles_missing_content(limit)

    def get_existing_toutiao_article_ids(self) -> Set[str]:
        return self.get_existing_raw_article_ids()

    def fetch_toutiao_articles_for_summary(
        self,
        *,
        after_fetched_at: Optional[str],
        limit: Optional[int],
    ) -> List[Dict[str, Any]]:
        return self.fetch_raw_articles_for_summary(after_fetched_at=after_fetched_at, limit=limit)

    def upsert_news_summaries_from_primary(self, rows: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return news_summaries.upsert_news_summaries_from_primary(cur, rows)

    # ------------------------------------------------------------------
    # Process + Scoring
    # ------------------------------------------------------------------
    def fetch_primary_articles_for_scoring(self, limit: int) -> List[PrimaryArticleForScoring]:
        with self._cursor() as cur:
            return process.fetch_primary_articles_for_scoring(cur, limit)

    def update_primary_article_scores(self, updates: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return process.update_primary_article_scores(cur, updates)

    def fetch_beijing_gate_candidates(
        self,
        limit: int,
        *,
        max_failures: Optional[int] = None,
    ) -> List[BeijingGateCandidate]:
        with self._cursor() as cur:
            return process.fetch_beijing_gate_candidates(cur, limit, max_failures=max_failures)

    def fetch_external_filter_candidates(
        self,
        limit: int,
        *,
        max_failures: Optional[int] = None,
    ) -> List[ExternalFilterCandidate]:
        with self._cursor() as cur:
            return process.fetch_external_filter_candidates(cur, limit, max_failures=max_failures)

    def complete_beijing_gate(
        self,
        article_id: str,
        *,
        status: str,
        is_beijing_related: Optional[bool],
        is_beijing_related_llm: Optional[bool],
        raw_output: Optional[Mapping[str, Any]],
        external_importance_status: Optional[str] = None,
        reset_external_filter: bool = False,
        sentiment_label: Optional[str] = None,
        candidate_category: Optional[str] = None,
    ) -> None:
        with self._cursor() as cur:
            process.complete_beijing_gate(
                cur,
                article_id,
                status=status,
                is_beijing_related=is_beijing_related,
                is_beijing_related_llm=is_beijing_related_llm,
                raw_output=raw_output,
                external_importance_status=external_importance_status,
                reset_external_filter=reset_external_filter,
                sentiment_label=sentiment_label,
                candidate_category=candidate_category,
            )

    def mark_beijing_gate_failure(
        self,
        article_id: str,
        *,
        fail_count: int,
        error: str,
        raw_output: Optional[Mapping[str, Any]] = None,
        final_status: Optional[str] = None,
        external_importance_status: Optional[str] = None,
    ) -> None:
        with self._cursor() as cur:
            process.mark_beijing_gate_failure(
                cur,
                article_id,
                fail_count=fail_count,
                error=error,
                raw_output=raw_output,
                final_status=final_status,
                external_importance_status=external_importance_status,
            )

    def complete_external_filter(
        self,
        article_id: str,
        *,
        passed: bool,
        score: int,
        raw_output: str,
        category: Optional[str] = None,
        prompt_key: str,
        prompt_version: str,
    ) -> None:
        with self.transaction() as cur:
            timestamp = process.complete_external_filter(
                cur,
                article_id,
                passed=passed,
                score=score,
                raw_output=raw_output,
                category=category,
                prompt_key=prompt_key,
                prompt_version=prompt_version,
            )
            if passed:
                manual_reviews.enqueue_manual_review(cur, article_id, status="pending")
            else:
                manual_reviews.update_manual_review_statuses(
                    cur,
                    [
                        {
                            "article_id": article_id,
                            "status": "discarded",
                            "decided_at": timestamp,
                        }
                    ],
                )

    def upsert_score_feedback(
        self,
        article_id: str,
        *,
        feedback_type: str,
        notes: Optional[str],
    ) -> Dict[str, Any]:
        with self.transaction() as cur:
            return score_feedback.upsert_score_feedback(
                cur,
                article_id,
                feedback_type=feedback_type,
                notes=notes,
            )

    def clear_score_feedback(self, article_id: str) -> bool:
        with self.transaction() as cur:
            return score_feedback.clear_score_feedback(cur, article_id)

    def mark_external_filter_failure(
        self,
        article_id: str,
        *,
        fail_count: int,
        final_failure: bool,
        error: str,
    ) -> None:
        with self._cursor() as cur:
            process.mark_external_filter_failure(
                cur,
                article_id,
                fail_count=fail_count,
                final_failure=final_failure,
                error=error,
            )

    def fetch_external_backfill_candidates(self, limit: int, since_date: Optional[date] = None) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return process.fetch_external_backfill_candidates(cur, limit, since_date=since_date)

    def reset_external_filter_pending(self, article_ids: Sequence[str]) -> int:
        with self._cursor() as cur:
            return process.reset_external_filter_pending(cur, article_ids)

    def fetch_beijing_tag_candidates(self, limit: int) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return process.fetch_beijing_tag_candidates(cur, limit)

    def update_beijing_related_bulk(self, updates: Sequence[Tuple[str, bool]]) -> int:
        with self._cursor() as cur:
            return process.update_beijing_related_bulk(cur, updates)

    # ------------------------------------------------------------------
    # Manual reviews
    # ------------------------------------------------------------------
    def _normalize_report_type_value(self, report_type: Optional[str]) -> Optional[str]:
        return manual_reviews.normalize_report_type_value(report_type)

    @staticmethod
    def _report_type_expr(alias: str = "") -> str:
        return manual_reviews.report_type_expr(alias)

    def enqueue_manual_review(
        self,
        article_id: str,
        *,
        status: str = "pending",
        report_type: Optional[str] = None,
        rank: Optional[float] = None,
        summary: Optional[str] = None,
        notes: Optional[str] = None,
        score: Optional[float] = None,
        decided_by: Optional[str] = None,
        decided_at: Optional[datetime] = None,
    ) -> None:
        with self._cursor() as cur:
            manual_reviews.enqueue_manual_review(
                cur,
                article_id,
                status=status,
                report_type=report_type,
                rank=rank,
                summary=summary,
                notes=notes,
                score=score,
                decided_by=decided_by,
                decided_at=decided_at,
            )

    def fetch_manual_reviews(
        self,
        *,
        status: str,
        limit: int,
        offset: int,
        only_ready: bool = False,
        region: Optional[str] = None,
        sentiment: Optional[str] = None,
        report_type: Optional[str] = None,
        order_by_decided_at: bool = False,
    ) -> Tuple[List[Dict[str, Any]], int]:
        with self._cursor() as cur:
            return manual_reviews.fetch_manual_reviews(
                cur,
                status=status,
                limit=limit,
                offset=offset,
                only_ready=only_ready,
                region=region,
                sentiment=sentiment,
                report_type=report_type,
                order_by_decided_at=order_by_decided_at,
            )

    def update_manual_review_order_and_categories(
        self,
        review_updates: Sequence[Mapping[str, Any]],
        category_updates: Sequence[Mapping[str, Any]],
        *,
        report_type: Optional[str] = None,
    ) -> Tuple[int, int]:
        with self.transaction() as cur:
            updated_reviews = manual_reviews.update_manual_review_statuses(
                cur,
                review_updates,
                report_type=report_type,
            )
            updated_categories = news_summaries.update_summary_categories(cur, category_updates)
        return updated_reviews, updated_categories

    def update_manual_review_order_as_user(
        self,
        review_updates: Sequence[Mapping[str, Any]],
        category_updates: Sequence[Mapping[str, Any]],
        *,
        actor_username: str,
        actor_user_id: Optional[str],
        report_type: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Tuple[int, int]:
        with self.transaction() as cur:
            before, after = manual_reviews.update_manual_review_order_as_user(
                cur,
                review_updates,
                actor_username=actor_username,
                actor_user_id=actor_user_id,
                report_type=report_type,
            )
            updated_categories = news_summaries.update_summary_categories(
                cur,
                category_updates,
            )
            if after:
                audit.insert_review_event(
                    cur,
                    actor_user_id=actor_user_id,
                    action="manual_review.reorder",
                    target_type="manual_review_batch",
                    target_id=report_type,
                    before_data={"items": before},
                    after_data={
                        "items": after,
                        "category_updates": list(category_updates),
                    },
                    request_id=request_id,
                )
            return len(after), updated_categories

    def fetch_manual_pending_for_cluster(
        self,
        *,
        region: Optional[str] = None,
        sentiment: Optional[str] = None,
        fetch_limit: int = 5000,
        report_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return manual_reviews.fetch_manual_pending_for_cluster(
                cur,
                region=region,
                sentiment=sentiment,
                fetch_limit=fetch_limit,
                report_type=report_type,
            )

    def search_manual_candidates(
        self,
        *,
        query: Optional[str] = None,
        published_before: Optional[date] = None,
        limit: int = 30,
        offset: int = 0,
        region: Optional[str] = None,
        sentiment: Optional[str] = None,
        report_type: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        with self._cursor() as cur:
            return manual_reviews.search_manual_candidates(
                cur,
                query=query,
                published_before=published_before,
                limit=limit,
                offset=offset,
                region=region,
                sentiment=sentiment,
                report_type=report_type,
            )

    def count_manual_candidates_before_date(
        self,
        *,
        region: str,
        sentiment: str,
        query: Optional[str] = None,
        published_before: Optional[date] = None,
        report_type: Optional[str] = None,
    ) -> int:
        with self._cursor() as cur:
            return manual_reviews.count_manual_candidates_before_date(
                cur,
                region=region,
                sentiment=sentiment,
                query=query,
                published_before=published_before,
                report_type=report_type,
            )

    def discard_manual_candidates_before_date(
        self,
        *,
        region: str,
        sentiment: str,
        query: Optional[str] = None,
        published_before: Optional[date] = None,
        actor: Optional[str] = None,
        decided_at: Optional[datetime] = None,
        report_type: Optional[str] = None,
    ) -> int:
        with self._cursor() as cur:
            return manual_reviews.discard_manual_candidates_before_date(
                cur,
                region=region,
                sentiment=sentiment,
                query=query,
                published_before=published_before,
                actor=actor,
                decided_at=decided_at,
                report_type=report_type,
            )

    def delete_manual_clusters(self, *, report_type: Optional[str] = None) -> int:
        with self._cursor() as cur:
            return manual_reviews.delete_manual_clusters(cur, report_type=report_type)

    def insert_manual_clusters(
        self,
        clusters: Sequence[Mapping[str, Any]],
        *,
        report_type: Optional[str] = None,
    ) -> int:
        with self._cursor() as cur:
            return manual_reviews.insert_manual_clusters(cur, clusters, report_type=report_type)

    def replace_manual_clusters(
        self,
        clusters: Sequence[Mapping[str, Any]],
        *,
        report_type: Optional[str] = None,
    ) -> int:
        with self.transaction() as cur:
            manual_reviews.delete_manual_clusters(cur, report_type=report_type)
            return manual_reviews.insert_manual_clusters(cur, clusters, report_type=report_type)

    def fetch_manual_clusters(
        self,
        *,
        bucket_key: Optional[str] = None,
        report_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return manual_reviews.fetch_manual_clusters(cur, bucket_key=bucket_key, report_type=report_type)

    def try_advisory_lock(self, lock_id: int) -> bool:
        with self._cursor() as cur:
            return manual_reviews.try_advisory_lock(cur, lock_id)

    def release_advisory_lock(self, lock_id: int) -> None:
        with self._cursor() as cur:
            manual_reviews.release_advisory_lock(cur, lock_id)

    def manual_review_status_counts(self, *, report_type: Optional[str] = None) -> Dict[str, int]:
        with self._cursor() as cur:
            return manual_reviews.manual_review_status_counts(cur, report_type=report_type)

    def manual_review_pending_count(self, *, report_type: Optional[str] = None) -> int:
        with self._cursor() as cur:
            return manual_reviews.manual_review_pending_count(cur, report_type=report_type)

    def manual_review_max_rank(self, status: str, *, report_type: Optional[str] = None) -> float:
        with self._cursor() as cur:
            return manual_reviews.manual_review_max_rank(cur, status, report_type=report_type)

    def update_manual_review_statuses(
        self,
        updates: Sequence[Mapping[str, Any]],
        *,
        report_type: Optional[str] = None,
    ) -> int:
        with self._cursor() as cur:
            return manual_reviews.update_manual_review_statuses(cur, updates, report_type=report_type)

    def update_manual_review_statuses_as_user(
        self,
        updates: Sequence[Mapping[str, Any]],
        *,
        actor_username: str,
        actor_user_id: Optional[str],
        expected_versions: Mapping[str, int],
        require_versions: bool,
        action: str,
        report_type: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self.transaction() as cur:
            effective_updates = (
                manual_reviews.allocate_manual_review_decision_ranks(
                    cur,
                    updates,
                    report_type=report_type,
                )
                if action == "manual_review.decide"
                else [dict(item) for item in updates]
            )
            before, after = (
                manual_reviews.update_manual_review_statuses_with_versions(
                    cur,
                    effective_updates,
                    actor_username=actor_username,
                    actor_user_id=actor_user_id,
                    expected_versions=expected_versions,
                    require_versions=require_versions,
                    report_type=report_type,
                )
            )
            if after:
                audit.insert_review_event(
                    cur,
                    actor_user_id=actor_user_id,
                    action=action,
                    target_type="manual_review_batch",
                    target_id=report_type,
                    before_data={"items": before},
                    after_data={"items": after},
                    request_id=request_id,
                )
            return after

    def reset_manual_reviews_to_pending(
        self,
        article_ids: Sequence[str],
        *,
        actor: Optional[str] = None,
        decided_at: Optional[datetime] = None,
        report_type: Optional[str] = None,
    ) -> int:
        with self._cursor() as cur:
            return manual_reviews.reset_manual_reviews_to_pending(
                cur,
                article_ids,
                actor=actor,
                decided_at=decided_at,
                report_type=report_type,
            )

    def update_manual_review_summaries(
        self,
        edits: Mapping[str, Mapping[str, Any]],
        *,
        actor: Optional[str] = None,
        decided_at: Optional[datetime] = None,
        report_type: Optional[str] = None,
    ) -> int:
        with self._cursor() as cur:
            return manual_reviews.update_manual_review_summaries(
                cur,
                edits,
                actor=actor,
                decided_at=decided_at,
                report_type=report_type,
            )

    def discard_manual_candidates_before_date_as_user(
        self,
        *,
        region: str,
        sentiment: str,
        query: Optional[str],
        published_before: Optional[date],
        report_type: str,
        actor_username: str,
        actor_user_id: Optional[str],
        request_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self.transaction() as cur:
            targets = (
                manual_reviews.fetch_manual_candidates_before_date_for_update(
                    cur,
                    region=region,
                    sentiment=sentiment,
                    query=query,
                    published_before=published_before,
                    report_type=report_type,
                )
            )
            updates = [
                {
                    "article_id": str(row["article_id"]),
                    "status": "discarded",
                    "rank": None,
                    "report_type": report_type,
                }
                for row in targets
            ]
            expected_versions = {
                str(row["article_id"]): int(row["version"])
                for row in targets
            }
            before, after = (
                manual_reviews.update_manual_review_statuses_with_versions(
                    cur,
                    updates,
                    actor_username=actor_username,
                    actor_user_id=actor_user_id,
                    expected_versions=expected_versions,
                    require_versions=True,
                    report_type=report_type,
                )
            )
            if after:
                audit.insert_review_event(
                    cur,
                    actor_user_id=actor_user_id,
                    action="manual_review.bulk_discard",
                    target_type="manual_review_batch",
                    target_id=report_type,
                    before_data={"items": before},
                    after_data={"items": after},
                    request_id=request_id,
                )
            return after

    def update_manual_review_summaries_as_user(
        self,
        edits: Mapping[str, Mapping[str, Any]],
        *,
        actor_username: str,
        actor_user_id: Optional[str],
        expected_versions: Mapping[str, int],
        require_versions: bool,
        report_type: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self.transaction() as cur:
            before, after = (
                manual_reviews.update_manual_review_summaries_with_versions(
                    cur,
                    edits,
                    actor_username=actor_username,
                    actor_user_id=actor_user_id,
                    expected_versions=expected_versions,
                    require_versions=require_versions,
                    report_type=report_type,
                )
            )
            if after:
                audit.insert_review_event(
                    cur,
                    actor_user_id=actor_user_id,
                    action="manual_review.edit",
                    target_type="manual_review_batch",
                    target_id=report_type,
                    before_data={"items": before},
                    after_data={"items": after},
                    request_id=request_id,
                )
            return after

    def fetch_manual_selected_for_export(self, *, report_type: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return manual_reviews.fetch_manual_selected_for_export(cur, report_type=report_type)

    # ------------------------------------------------------------------
    # Export + batches
    # ------------------------------------------------------------------
    def fetch_export_candidates(self, min_score: float) -> List[ExportCandidate]:
        with self._cursor() as cur:
            return export.fetch_export_candidates(cur, min_score)

    def _get_batch_by_tag(self, report_tag: str) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            return export.get_batch_by_tag(cur, report_tag)

    def _parse_report_tag(self, report_tag: str) -> Tuple[date, str]:
        return export.parse_report_tag(report_tag)

    def _create_batch(self, report_tag: str) -> Dict[str, Any]:
        with self._cursor() as cur:
            return export.create_batch(cur, report_tag)

    def get_export_history(self, report_tag: str) -> Tuple[Set[str], Optional[str]]:
        with self._cursor() as cur:
            return export.get_export_history(cur, report_tag)

    def get_all_exported_article_ids(self) -> Set[str]:
        with self._cursor() as cur:
            return export.get_all_exported_article_ids(cur)

    def record_export(
        self,
        report_tag: str,
        exported: Sequence[Tuple[ExportCandidate, str]],
        *,
        output_path: str,
    ) -> None:
        with self._cursor() as cur:
            export.record_export(cur, report_tag, exported, output_path=output_path)

    def fetch_latest_brief_batch(self) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            return export.fetch_latest_brief_batch(cur)

    def fetch_brief_items_by_batch(self, batch_id: str) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return export.fetch_brief_items_by_batch(cur, batch_id)

    def fetch_brief_item_count(self, batch_id: str) -> int:
        with self._cursor() as cur:
            return export.fetch_brief_item_count(cur, batch_id)

    # ------------------------------------------------------------------
    # Pipeline run metadata
    # ------------------------------------------------------------------
    def record_pipeline_run_start(
        self,
        *,
        run_id: str,
        started_at: datetime,
        plan: Sequence[str],
        trigger_source: Optional[str] = None,
    ) -> None:
        with self._cursor() as cur:
            process.record_pipeline_run_start(
                cur,
                run_id=run_id,
                started_at=started_at,
                plan=plan,
                trigger_source=trigger_source,
            )

    def record_pipeline_run_step(
        self,
        *,
        run_id: str,
        order_index: int,
        step_name: str,
        status: str,
        started_at: datetime,
        finished_at: datetime,
        duration_seconds: Optional[float],
        error: Optional[str],
    ) -> None:
        with self._cursor() as cur:
            process.record_pipeline_run_step(
                cur,
                run_id=run_id,
                order_index=order_index,
                step_name=step_name,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=duration_seconds,
                error=error,
            )

    def finalize_pipeline_run(
        self,
        *,
        run_id: str,
        status: str,
        finished_at: datetime,
        steps_completed: int,
        artifacts: Optional[Mapping[str, str]] = None,
        error_summary: Optional[str] = None,
    ) -> None:
        with self._cursor() as cur:
            process.finalize_pipeline_run(
                cur,
                run_id=run_id,
                status=status,
                finished_at=finished_at,
                steps_completed=steps_completed,
                artifacts=artifacts,
                error_summary=error_summary,
            )

    def fetch_pipeline_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return process.fetch_pipeline_runs(cur, limit=limit)

    def fetch_pipeline_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            return process.fetch_pipeline_run(cur, run_id)

    def fetch_pipeline_run_steps(self, run_id: str) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return process.fetch_pipeline_run_steps(cur, run_id)


def get_adapter() -> PostgresAdapter:
    global _ADAPTER
    if _ADAPTER is None:
        _ADAPTER = PostgresAdapter()
    return _ADAPTER


__all__ = ["PostgresAdapter", "get_adapter"]
