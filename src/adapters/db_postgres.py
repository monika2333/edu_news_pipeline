from __future__ import annotations

import contextlib
from datetime import datetime, timezone, date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Json

from src.config import get_settings
from src.domain import (
    ExportCandidate,
    SummaryForScoring,
)

_CONNECTION: Optional[psycopg.Connection] = None
_ADAPTER: Optional["PostgresAdapter"] = None


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
            cur.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
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
    def _cursor(self):
        cur = self._conn_cursor()
        try:
            yield cur
        finally:
            cur.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    # Toutiao articles (crawler storage)
    # ------------------------------------------------------------------
    def upsert_toutiao_articles(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        columns = [
            'token',
            'profile_url',
            'article_id',
            'title',
            'source',
            'publish_time',
            'publish_time_iso',
            'url',
            'summary',
            'comment_count',
            'digg_count',
            'content_markdown',
            'fetched_at',
        ]
        insert_sql = '''
            INSERT INTO toutiao_articles (token, profile_url, article_id, title, source,
                publish_time, publish_time_iso, url, summary, comment_count, digg_count,
                content_markdown, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (article_id) DO UPDATE
            SET token = EXCLUDED.token,
                profile_url = EXCLUDED.profile_url,
                title = EXCLUDED.title,
                source = EXCLUDED.source,
                publish_time = EXCLUDED.publish_time,
                publish_time_iso = EXCLUDED.publish_time_iso,
                url = EXCLUDED.url,
                summary = EXCLUDED.summary,
                comment_count = EXCLUDED.comment_count,
                digg_count = EXCLUDED.digg_count,
                content_markdown = EXCLUDED.content_markdown,
                fetched_at = EXCLUDED.fetched_at,
                updated_at = now()
        '''
        data = [tuple(row.get(col) for col in columns) for row in rows]
        with self._cursor() as cur:
            cur.executemany(insert_sql, data)
        return len(rows)


    def upsert_toutiao_feed_rows(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        columns = [
            'token',
            'profile_url',
            'article_id',
            'title',
            'source',
            'publish_time',
            'publish_time_iso',
            'url',
            'summary',
            'comment_count',
            'digg_count',
            'fetched_at',
        ]
        insert_sql = '''
            INSERT INTO toutiao_articles (token, profile_url, article_id, title, source,
                publish_time, publish_time_iso, url, summary, comment_count, digg_count,
                fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (article_id) DO UPDATE
            SET token = EXCLUDED.token,
                profile_url = EXCLUDED.profile_url,
                title = EXCLUDED.title,
                source = EXCLUDED.source,
                publish_time = EXCLUDED.publish_time,
                publish_time_iso = EXCLUDED.publish_time_iso,
                url = EXCLUDED.url,
                summary = EXCLUDED.summary,
                comment_count = EXCLUDED.comment_count,
                digg_count = EXCLUDED.digg_count,
                fetched_at = EXCLUDED.fetched_at,
                updated_at = now()
        '''
        data = [tuple(row.get(col) for col in columns) for row in rows]
        with self._cursor() as cur:
            cur.executemany(insert_sql, data)
        return len(rows)

    def update_toutiao_article_details(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        columns = [
            'token',
            'profile_url',
            'title',
            'source',
            'publish_time',
            'publish_time_iso',
            'url',
            'summary',
            'comment_count',
            'digg_count',
            'content_markdown',
            'detail_fetched_at',
        ]
        update_sql = '''
            UPDATE toutiao_articles
            SET token = %s,
                profile_url = %s,
                title = %s,
                source = %s,
                publish_time = %s,
                publish_time_iso = %s,
                url = %s,
                summary = %s,
                comment_count = %s,
                digg_count = %s,
                content_markdown = %s,
                detail_fetched_at = %s,
                updated_at = now()
            WHERE article_id = %s
        '''
        missing: List[str] = []
        with self._cursor() as cur:
            for row in rows:
                article_id = str(row.get('article_id') or '')
                if not article_id:
                    raise ValueError('Detail update requires article_id')
                values = [row.get(col) for col in columns]
                cur.execute(update_sql, values + [article_id])
                if cur.rowcount == 0:
                    missing.append(article_id)
        if missing:
            missing_values = ', '.join(sorted(missing))
            raise ValueError(f'Missing feed rows for detail update: {missing_values}')
        return len(rows)



    def get_toutiao_articles_with_detail(self, article_ids: Sequence[str]) -> Set[str]:
        unique_ids = list({str(item) for item in article_ids if item})
        if not unique_ids:
            return set()
        query = "SELECT article_id FROM toutiao_articles WHERE article_id = ANY(%s) AND detail_fetched_at IS NOT NULL"
        with self._cursor() as cur:
            cur.execute(query, (unique_ids,))
            rows = cur.fetchall()
        return {str(row['article_id']) for row in rows if row.get('article_id')}

    def get_existing_toutiao_article_ids(self) -> Set[str]:
        ids: Set[str] = set()
        with self._cursor() as cur:
            cur.execute("SELECT article_id FROM toutiao_articles")
            for row in cur.fetchall():
                article_id = row.get('article_id')
                if article_id:
                    ids.add(str(article_id))
        return ids

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    @staticmethod
    def _article_hash(article_id: Optional[str], original_url: Optional[str], title: Optional[str]) -> str:
        import hashlib

        basis = "-".join(filter(None, (article_id, original_url, title)))
        if not basis:
            basis = datetime.now(timezone.utc).isoformat()
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    @staticmethod
    def _to_iso(publish_time: Optional[int]) -> Optional[str]:
        if publish_time is None:
            return None
        try:
            return datetime.fromtimestamp(int(publish_time), tz=timezone.utc).isoformat()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------
    def fetch_toutiao_articles_for_summary(
        self,
        *,
        after_fetched_at: Optional[str],
        limit: Optional[int],
    ) -> List[Dict[str, Any]]:
        fetch_target = max(1, (limit or 50))
        base_query = [
            "SELECT article_id, title, source, publish_time, publish_time_iso, url, content_markdown, fetched_at, detail_fetched_at",
            "FROM toutiao_articles",
            "WHERE content_markdown IS NOT NULL AND LENGTH(TRIM(content_markdown)) > 0",
            "  AND detail_fetched_at IS NOT NULL",
        ]
        params: List[Any] = []
        if after_fetched_at:
            base_query.append("AND fetched_at >= %s")
            params.append(after_fetched_at)
        base_query.append("ORDER BY fetched_at ASC")
        base_query.append("LIMIT %s")
        params.append(fetch_target)
        query = " ".join(base_query)
        with self._cursor() as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            fetched = record.get('fetched_at')
            if isinstance(fetched, datetime):
                record['fetched_at'] = fetched.isoformat()
            publish_iso = record.get('publish_time_iso')
            if isinstance(publish_iso, datetime):
                record['publish_time_iso'] = publish_iso.isoformat()
            detail_fetched = record.get('detail_fetched_at')
            if isinstance(detail_fetched, datetime):
                record['detail_fetched_at'] = detail_fetched.isoformat()
            result.append(record)
        return result

    def get_existing_news_summary_ids(self, article_ids: Sequence[str]) -> Set[str]:
        unique_ids = list({str(item) for item in article_ids if item})
        if not unique_ids:
            return set()
        query = "SELECT article_id FROM news_summaries WHERE article_id = ANY(%s)"
        with self._cursor() as cur:
            cur.execute(query, (unique_ids,))
            rows = cur.fetchall()
        return {str(row["article_id"]) for row in rows if row.get("article_id")}

    def upsert_news_summary(
        self,
        article: Dict[str, Any],
        summary: str,
        *,
        keywords: Optional[Sequence[str]] = None,
    ) -> None:
        article_id = str(article.get("article_id") or "")
        if not article_id:
            raise ValueError("Postgres upsert requires article_id")
        content_value = article.get("content_markdown")
        if content_value is None:
            content_value = ""
        payload: Dict[str, Any] = {
            "article_id": article_id,
            "title": article.get("title"),
            "source": article.get("source"),
            "publish_time": article.get("publish_time"),
            "publish_time_iso": article.get("publish_time_iso"),
            "url": article.get("url"),
            "content_markdown": str(content_value),
            "llm_summary": summary,
            "summary_generated_at": datetime.now(timezone.utc).isoformat(),
        }
        llm_source_value = article.get("llm_source")
        if llm_source_value is not None:
            payload["llm_source"] = str(llm_source_value).strip()
        fetched_at = article.get("fetched_at")
        if fetched_at:
            payload["fetched_at"] = fetched_at
        if keywords:
            deduped = []
            for kw in keywords:
                if kw and kw not in deduped:
                    deduped.append(kw)
            if deduped:
                payload["llm_keywords"] = deduped
        columns = list(payload.keys())
        values = [payload[col] for col in columns]
        updates = [f"{col} = EXCLUDED.{col}" for col in columns if col != "article_id"]
        query = f"""
            INSERT INTO news_summaries ({', '.join(columns)})
            VALUES ({', '.join(['%s'] * len(columns))})
            ON CONFLICT (article_id) DO UPDATE SET {', '.join(updates)}
        """
        with self._cursor() as cur:
            try:
                cur.execute(query, values)
            except psycopg.DatabaseError as exc:
                message = str(exc)
                if "fetched_at" in message and "news_summaries" in message:
                    # Retry without fetched_at to mimic previous remote behaviour
                    filtered_columns = [c for c in columns if c != "fetched_at"]
                    filtered_values = [payload[c] for c in filtered_columns]
                    filtered_updates = [f"{col} = EXCLUDED.{col}" for col in filtered_columns if col != "article_id"]
                    retry_query = f"""
                        INSERT INTO news_summaries ({', '.join(filtered_columns)})
                        VALUES ({', '.join(['%s'] * len(filtered_columns))})
                        ON CONFLICT (article_id) DO UPDATE SET {', '.join(filtered_updates)}
                    """
                    cur.execute(retry_query, filtered_values)
                else:
                    raise

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    def fetch_summaries_for_scoring(self, limit: Optional[int] = None) -> List[SummaryForScoring]:
        query = [
            "SELECT article_id, content_markdown, llm_summary",
            "FROM news_summaries",
            "WHERE correlation IS NULL",
            "  AND llm_summary IS NOT NULL",
            "ORDER BY summary_generated_at ASC",
        ]
        params: List[Any] = []
        if limit and limit > 0:
            query.append("LIMIT %s")
            params.append(limit)
        full_query = " ".join(query)
        with self._cursor() as cur:
            cur.execute(full_query, tuple(params))
            rows = cur.fetchall()
        out: List[SummaryForScoring] = []
        for row in rows:
            summary_text = row.get("llm_summary")
            if not summary_text:
                continue
            article_id = row.get("article_id")
            if not article_id:
                continue
            out.append(
                SummaryForScoring(
                    article_id=str(article_id),
                    content=str(row.get("content_markdown") or ""),
                    summary=str(summary_text),
                )
            )
        return out

    def update_correlation(self, article_id: str, score: Optional[float]) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE news_summaries SET correlation = %s, updated_at = NOW() WHERE article_id = %s",
                (score, article_id),
            )

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------
    def fetch_export_candidates(self, min_score: float) -> List[ExportCandidate]:
        query = """
            SELECT
                article_id,
                title,
                llm_summary,
                content_markdown,
                correlation,
                url,
                source,
                publish_time_iso,
                publish_time,
                llm_source
            FROM news_summaries
            WHERE correlation IS NOT NULL AND correlation >= %s
            ORDER BY correlation DESC
        """
        with self._cursor() as cur:
            cur.execute(query, (min_score,))
            rows = cur.fetchall()
        out: List[ExportCandidate] = []
        for row in rows:
            article_id = str(row.get("article_id") or "")
            if not article_id:
                continue
            title = row.get("title")
            summary_text = row.get("llm_summary") or ""
            content = row.get("content_markdown") or ""
            correlation = float(row.get("correlation") or 0.0)
            url = row.get("url")
            published_at = row.get("publish_time_iso") or row.get("publish_time")
            if isinstance(published_at, datetime):
                published_at = published_at.isoformat()
            source_name = row.get("source")
            article_hash = self._article_hash(article_id, url, title)
            out.append(
                ExportCandidate(
                    filtered_article_id=article_id,
                    raw_article_id=article_id,
                    article_hash=article_hash,
                    title=title,
                    summary=str(summary_text),
                    content=str(content),
                    source=source_name,
                    llm_source=row.get("llm_source"),
                    relevance_score=correlation,
                    original_url=url,
                    published_at=published_at,
                )
            )
        return out

    def _get_batch_by_tag(self, report_tag: str) -> Optional[Dict[str, Any]]:
        query = """
            SELECT id, report_date, sequence_no, export_payload
            FROM brief_batches
            WHERE generated_by = %s
            LIMIT 1
        """
        with self._cursor() as cur:
            cur.execute(query, (report_tag,))
            row = cur.fetchone()
        return dict(row) if row else None

    def _parse_report_tag(self, report_tag: str) -> Tuple[date, str]:
        try:
            parts = report_tag.split("-")
            if len(parts) >= 3:
                y, m, d = parts[0:3]
                report_date = date(int(y), int(m), int(d))
                suffix = "-".join(parts[3:]) if len(parts) > 3 else ""
                return report_date, suffix
        except Exception:
            pass
        return datetime.now(timezone.utc).date(), report_tag

    def _create_batch(self, report_tag: str) -> Dict[str, Any]:
        report_date, suffix = self._parse_report_tag(report_tag)
        fetch_query = """
            SELECT sequence_no
            FROM brief_batches
            WHERE report_date = %s
            ORDER BY sequence_no DESC
            LIMIT 1
        """
        with self._cursor() as cur:
            cur.execute(fetch_query, (report_date.isoformat(),))
            row = cur.fetchone()
            next_seq = 1
            if row:
                try:
                    next_seq = int(row["sequence_no"]) + 1
                except Exception:
                    next_seq = 1
            payload = {
                "report_date": report_date.isoformat(),
                "sequence_no": next_seq,
                "generated_by": report_tag,
                "export_payload": Json({"report_tag": report_tag, "suffix": suffix}),
            }
            cur.execute(
                """
                INSERT INTO brief_batches (report_date, sequence_no, generated_by, export_payload)
                VALUES (%s, %s, %s, %s)
                RETURNING id, report_date, sequence_no, export_payload
                """,
                (
                    payload["report_date"],
                    payload["sequence_no"],
                    payload["generated_by"],
                    payload["export_payload"],
                ),
            )
            created = cur.fetchone()
            if not created:
                raise RuntimeError("Failed to create brief batch")
            return dict(created)

    def get_export_history(self, report_tag: str) -> Tuple[Set[str], Optional[str]]:
        batch = self._get_batch_by_tag(report_tag)
        if not batch:
            return set(), None
        batch_id = str(batch["id"])
        query = "SELECT article_id FROM brief_items WHERE brief_batch_id = %s"
        with self._cursor() as cur:
            cur.execute(query, (batch_id,))
            rows = cur.fetchall()
        ids = {str(row.get("article_id")) for row in rows if row.get("article_id")}
        return ids, batch_id

    def get_all_exported_article_ids(self) -> Set[str]:
        batch_size = 1000
        start = 0
        seen: Set[str] = set()
        while True:
            query = """
                SELECT article_id
                FROM brief_items
                ORDER BY id
                OFFSET %s LIMIT %s
            """
            with self._cursor() as cur:
                cur.execute(query, (start, batch_size))
                rows = cur.fetchall()
            if not rows:
                break
            for row in rows:
                article_id = row.get("article_id")
                if article_id:
                    seen.add(str(article_id))
            if len(rows) < batch_size:
                break
            start += batch_size
        return seen

    def record_export(
        self,
        report_tag: str,
        exported: Sequence[Tuple[ExportCandidate, str]],
        *,
        output_path: str,
    ) -> None:
        existing_ids, batch_id = self.get_export_history(report_tag)
        if batch_id is None:
            batch = self._create_batch(report_tag)
            batch_id = str(batch["id"])
        with self._cursor() as cur:
            cur.execute(
                "UPDATE brief_batches SET export_payload = %s, updated_at = NOW() WHERE id = %s",
                (Json({"report_tag": report_tag, "output_path": output_path}), batch_id),
            )
            order_index_start = 0
            if existing_ids:
                cur.execute(
                    """
                    SELECT order_index
                    FROM brief_items
                    WHERE brief_batch_id = %s
                    ORDER BY order_index DESC
                    LIMIT 1
                    """,
                    (batch_id,),
                )
                row = cur.fetchone()
                if row:
                    try:
                        order_index_start = int(row["order_index"]) + 1
                    except Exception:
                        order_index_start = 0
            insert_payload: List[Tuple[Any, ...]] = []
            for offset, (candidate, section) in enumerate(exported):
                article_id = candidate.filtered_article_id
                if article_id in existing_ids:
                    continue
                insert_payload.append(
                    (
                        batch_id,
                        article_id,
                        section,
                        order_index_start + offset,
                        candidate.summary,
                        Json(
                            {
                                "title": candidate.title,
                                "correlation": candidate.relevance_score,
                                "original_url": candidate.original_url,
                                "published_at": candidate.published_at,
                                "source": candidate.source,
                            }
                        ),
                    )
                )
            if insert_payload:
                cur.executemany(
                    """
                    INSERT INTO brief_items (
                        brief_batch_id,
                        article_id,
                        section,
                        order_index,
                        final_summary,
                        metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    insert_payload,
                )

    def fetch_latest_brief_batch(self) -> Optional[Dict[str, Any]]:
        query = """
            SELECT *
            FROM brief_batches
            ORDER BY report_date DESC, sequence_no DESC
            LIMIT 1
        """
        with self._cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()
        return dict(row) if row else None

    def fetch_brief_items_by_batch(self, batch_id: str) -> List[Dict[str, Any]]:
        query = """
            SELECT id, article_id, section, order_index, final_summary, metadata
            FROM brief_items
            WHERE brief_batch_id = %s
            ORDER BY order_index ASC
        """
        with self._cursor() as cur:
            cur.execute(query, (batch_id,))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def fetch_brief_item_count(self, batch_id: str) -> int:
        query = "SELECT COUNT(*) AS total FROM brief_items WHERE brief_batch_id = %s"
        with self._cursor() as cur:
            cur.execute(query, (batch_id,))
            row = cur.fetchone()
        return int(row["total"]) if row else 0

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
        payload = {
            "run_id": run_id,
            "status": "running",
            "trigger_source": trigger_source,
            "plan": plan,
            "started_at": started_at.isoformat(),
            "finished_at": None,
            "steps_completed": 0,
            "artifacts": None,
            "error_summary": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        columns = list(payload.keys())
        values = [Json(v) if isinstance(v, (list, dict)) else v for v in payload.values()]
        updates = [
            "status = EXCLUDED.status",
            "trigger_source = EXCLUDED.trigger_source",
            "plan = EXCLUDED.plan",
            "started_at = EXCLUDED.started_at",
            "finished_at = EXCLUDED.finished_at",
            "steps_completed = EXCLUDED.steps_completed",
            "artifacts = EXCLUDED.artifacts",
            "error_summary = EXCLUDED.error_summary",
            "updated_at = EXCLUDED.updated_at",
        ]
        query = f"""
            INSERT INTO pipeline_runs ({', '.join(columns)})
            VALUES ({', '.join(['%s'] * len(columns))})
            ON CONFLICT (run_id) DO UPDATE SET {', '.join(updates)}
        """
        with self._cursor() as cur:
            cur.execute(query, values)

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
            cur.execute(
                """
                INSERT INTO pipeline_run_steps (
                    run_id,
                    order_index,
                    step_name,
                    status,
                    started_at,
                    finished_at,
                    duration_seconds,
                    error
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    order_index,
                    step_name,
                    status,
                    started_at.isoformat(),
                    finished_at.isoformat(),
                    duration_seconds,
                    error,
                ),
            )
            cur.execute(
                """
                UPDATE pipeline_runs
                SET steps_completed = %s,
                    updated_at = NOW()
                WHERE run_id = %s
                """,
                (order_index, run_id),
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
            cur.execute(
                """
                UPDATE pipeline_runs
                SET status = %s,
                    finished_at = %s,
                    steps_completed = %s,
                    artifacts = %s,
                    error_summary = %s,
                    updated_at = NOW()
                WHERE run_id = %s
                """,
                (
                    status,
                    finished_at.isoformat(),
                    steps_completed,
                    (Json(dict(artifacts)) if artifacts else None),
                    error_summary,
                    run_id,
                ),
            )

    def fetch_pipeline_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        query = """
            SELECT *
            FROM pipeline_runs
            ORDER BY started_at DESC
            LIMIT %s
        """
        with self._cursor() as cur:
            cur.execute(query, (limit,))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def fetch_pipeline_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM pipeline_runs WHERE run_id = %s LIMIT 1"
        with self._cursor() as cur:
            cur.execute(query, (run_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def fetch_pipeline_run_steps(self, run_id: str) -> List[Dict[str, Any]]:
        query = """
            SELECT *
            FROM pipeline_run_steps
            WHERE run_id = %s
            ORDER BY order_index ASC
        """
        with self._cursor() as cur:
            cur.execute(query, (run_id,))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Misc utilities
    # ------------------------------------------------------------------
def get_adapter() -> PostgresAdapter:
    global _ADAPTER
    if _ADAPTER is None:
        _ADAPTER = PostgresAdapter()
    return _ADAPTER


__all__ = ["PostgresAdapter", "get_adapter"]

