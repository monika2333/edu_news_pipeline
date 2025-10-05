from __future__ import annotations

import hashlib
from datetime import datetime, timezone, date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

try:
    from supabase import Client, create_client
except ImportError:  # supabase library not available in runtime
    Client = Any  # type: ignore
    create_client = None  # type: ignore
try:
    from supabase.lib.client_options import ClientOptions
except ImportError:  # supabase lib missing
    ClientOptions = Any  # type: ignore

from src.config import get_settings
from src.domain import (
    ExportCandidate,
    SummaryForScoring,
)

_client: Optional[Client] = None
_adapter: Optional["SupabaseAdapter"] = None


def _require_client() -> Client:
    settings = get_settings()
    if not settings.supabase_url:
        raise RuntimeError("SUPABASE_URL is not configured.")
    key = settings.effective_supabase_key
    if not key:
        raise RuntimeError("Supabase key is not configured (SUPABASE_SERVICE_ROLE_KEY / SUPABASE_KEY / SUPABASE_ANON_KEY).")
    options = ClientOptions(schema=settings.supabase_db_schema or "public")
    return create_client(settings.supabase_url, key, options=options)


def get_client() -> Client:
    """Return a shared Supabase client instance configured from settings."""

    global _client
    if _client is None:
        _client = _require_client()
    return _client


class SupabaseAdapter:
    """High-level helpers for Supabase interactions used by the pipeline."""

    def __init__(self, client: Optional[Client] = None) -> None:
        self.client = client or get_client()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    @staticmethod
    def _article_hash(article_id: Optional[str], original_url: Optional[str], title: Optional[str]) -> str:
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
        query = (
            self.client
            .table("toutiao_articles")
            .select(
                "article_id, title, source, publish_time, publish_time_iso, url, content_markdown, fetched_at"
            )
            .not_.is_("content_markdown", "null")
            .order("fetched_at", desc=False)
        )
        if after_fetched_at:
            query = query.gte("fetched_at", after_fetched_at)
        query = query.limit(fetch_target)
        resp = query.execute()
        return resp.data or []

    def get_existing_news_summary_ids(self, article_ids: Sequence[str]) -> Set[str]:
        unique_ids = list({str(item) for item in article_ids if item})
        if not unique_ids:
            return set()
        resp = (
            self.client
            .table("news_summaries")
            .select("article_id")
            .in_("article_id", unique_ids)
            .execute()
        )
        return {str(row.get("article_id")) for row in resp.data or [] if row.get("article_id")}

    def upsert_news_summary(
        self,
        article: Dict[str, Any],
        summary: str,
        *,
        keywords: Optional[Sequence[str]] = None,
    ) -> None:
        article_id = str(article.get("article_id") or "")
        if not article_id:
            raise ValueError("Supabase upsert requires article_id")
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
        deduped_keywords: List[str] = []
        if keywords:
            for kw in keywords:
                if kw and kw not in deduped_keywords:
                    deduped_keywords.append(kw)
        if deduped_keywords:
            payload["llm_keywords"] = deduped_keywords
        cleaned_payload = {key: value for key, value in payload.items() if value is not None}
        try:
            self.client.table("news_summaries").upsert(cleaned_payload, on_conflict="article_id").execute()
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            if "fetched_at" in message and "news_summaries" in message:
                fallback_payload = {key: value for key, value in cleaned_payload.items() if key != "fetched_at"}
                if fallback_payload == cleaned_payload:
                    raise
                self.client.table("news_summaries").upsert(fallback_payload, on_conflict="article_id").execute()
            else:
                raise

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    def fetch_summaries_for_scoring(self, limit: Optional[int] = None) -> List[SummaryForScoring]:
        query = (
            self.client
            .table("news_summaries")
            .select("article_id, content_markdown, llm_summary")
            .is_("correlation", "null")
            .not_.is_("llm_summary", "null")
            .order("summary_generated_at", desc=False)
        )
        if limit and limit > 0:
            query = query.limit(limit)
        resp = query.execute()
        out: List[SummaryForScoring] = []
        for row in resp.data or []:
            summary = row.get("llm_summary")
            if not summary:
                continue
            content = row.get("content_markdown") or ""
            article_id = row.get("article_id")
            if not article_id:
                continue
            out.append(
                SummaryForScoring(
                    article_id=str(article_id),
                    content=str(content),
                    summary=str(summary),
                )
            )
        return out

    def update_correlation(self, article_id: str, score: Optional[float]) -> None:
        self.client.table("news_summaries").update({"correlation": score}).eq("article_id", article_id).execute()

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------
    def fetch_export_candidates(self, min_score: float) -> List[ExportCandidate]:
        columns = "article_id, title, llm_summary, content_markdown, correlation, url, source, publish_time_iso, publish_time, llm_source"
        resp = (
            self.client
            .table("news_summaries")
            .select(columns)
            .gte("correlation", min_score)
            .order("correlation", desc=True)
            .execute()
        )
        out: List[ExportCandidate] = []
        for row in resp.data or []:
            article_id = str(row.get("article_id") or "")
            if not article_id:
                continue
            title = row.get("title")
            summary = row.get("llm_summary") or ""
            content = row.get("content_markdown") or ""
            correlation = float(row.get("correlation") or 0)
            url = row.get("url")
            published_at = row.get("publish_time_iso") or row.get("publish_time")
            source_name = row.get("source")
            llm_source_value = row.get("llm_source")
            article_hash = self._article_hash(article_id, url, title)
            out.append(
                ExportCandidate(
                    filtered_article_id=article_id,
                    raw_article_id=article_id,
                    article_hash=article_hash,
                    title=title,
                    summary=str(summary),
                    content=str(content),
                    source=source_name,
                    llm_source=llm_source_value,
                    relevance_score=correlation,
                    original_url=url,
                    published_at=published_at,
                )
            )
        return out

    def _get_batch_by_tag(self, report_tag: str) -> Optional[Dict[str, Any]]:
        resp = (
            self.client
            .table("brief_batches")
            .select("id, report_date, sequence_no, export_payload")
            .eq("generated_by", report_tag)
            .limit(1)
            .execute()
        )
        data = resp.data or []
        return data[0] if data else None

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
        resp = (
            self.client
            .table("brief_batches")
            .select("sequence_no")
            .eq("report_date", report_date.isoformat())
            .order("sequence_no", desc=True)
            .limit(1)
            .execute()
        )
        next_seq = 1
        if resp.data:
            try:
                next_seq = int(resp.data[0]["sequence_no"]) + 1
            except Exception:
                next_seq = 1
        payload = {
            "report_date": report_date.isoformat(),
            "sequence_no": next_seq,
            "generated_by": report_tag,
            "export_payload": {"report_tag": report_tag, "suffix": suffix},
        }
        create_resp = self.client.table("brief_batches").insert(payload, returning="representation").execute()
        if not create_resp.data:
            raise RuntimeError("Failed to create brief batch")
        return create_resp.data[0]

    def get_export_history(self, report_tag: str) -> Tuple[Set[str], Optional[str]]:
        batch = self._get_batch_by_tag(report_tag)
        if not batch:
            return set(), None
        batch_id = str(batch["id"])
        resp = self.client.table("brief_items").select("article_id").eq("brief_batch_id", batch_id).execute()
        ids = {str(row.get("article_id")) for row in resp.data or [] if row.get("article_id")}
        return ids, batch_id

    def get_all_exported_article_ids(self) -> Set[str]:
        batch_size = 1000
        start = 0
        seen: Set[str] = set()
        while True:
            resp = (
                self.client
                .table("brief_items")
                .select("article_id")
                .order("id")
                .range(start, start + batch_size - 1)
                .execute()
            )
            rows = resp.data or []
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
        self.client.table("brief_batches").update(
            {"export_payload": {"report_tag": report_tag, "output_path": output_path}}
        ).eq("id", batch_id).execute()
        insert_payload: List[Dict[str, Any]] = []
        order_index_start = 0
        if existing_ids:
            resp = (
                self.client
                .table("brief_items")
                .select("order_index")
                .eq("brief_batch_id", batch_id)
                .order("order_index", desc=True)
                .limit(1)
                .execute()
            )
            if resp.data:
                try:
                    order_index_start = int(resp.data[0]["order_index"]) + 1
                except Exception:
                    order_index_start = 0
        for offset, (candidate, section) in enumerate(exported):
            article_id = candidate.filtered_article_id
            if article_id in existing_ids:
                continue
            record = {
                "brief_batch_id": batch_id,
                "article_id": article_id,
                "section": section,
                "order_index": order_index_start + offset,
                "final_summary": candidate.summary,
                "metadata": {
                    "title": candidate.title,
                    "correlation": candidate.relevance_score,
                    "original_url": candidate.original_url,
                    "published_at": candidate.published_at,
                    "source": candidate.source,
                },
            }
            insert_payload.append(record)
        if insert_payload:
            self.client.table("brief_items").insert(insert_payload).execute()

    def fetch_latest_brief_batch(self) -> Optional[Dict[str, Any]]:
        resp = (
            self.client
            .table("brief_batches")
            .select("*")
            .order("report_date", desc=True)
            .order("sequence_no", desc=True)
            .limit(1)
            .execute()
        )
        data = resp.data or []
        return data[0] if data else None

    def fetch_brief_items_by_batch(self, batch_id: str) -> List[Dict[str, Any]]:
        resp = (
            self.client
            .table("brief_items")
            .select("id,article_id,section,order_index,final_summary,metadata")
            .eq("brief_batch_id", batch_id)
            .order("order_index")
            .execute()
        )
        return resp.data or []

    def fetch_brief_item_count(self, batch_id: str) -> int:
        resp = (
            self.client
            .table("brief_items")
            .select("id", count="exact")
            .eq("brief_batch_id", batch_id)
            .execute()
        )
        return int(resp.count or 0)

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
            "plan": list(plan),
            "started_at": started_at.isoformat(),
            "finished_at": None,
            "steps_completed": 0,
            "artifacts": None,
            "error_summary": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.client.table("pipeline_runs").upsert(payload, on_conflict="run_id").execute()

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
        payload = {
            "run_id": run_id,
            "order_index": order_index,
            "step_name": step_name,
            "status": status,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": duration_seconds,
            "error": error,
        }
        self.client.table("pipeline_run_steps").insert(payload).execute()
        self.client.table("pipeline_runs").update(
            {
                "steps_completed": order_index,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("run_id", run_id).execute()

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
        payload = {
            "status": status,
            "finished_at": finished_at.isoformat(),
            "steps_completed": steps_completed,
            "artifacts": dict(artifacts) if artifacts else None,
            "error_summary": error_summary,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.client.table("pipeline_runs").update(payload).eq("run_id", run_id).execute()

    def fetch_pipeline_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        resp = (
            self.client
            .table("pipeline_runs")
            .select("*")
            .order("started_at", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []

    def fetch_pipeline_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        resp = (
            self.client
            .table("pipeline_runs")
            .select("*")
            .eq("run_id", run_id)
            .limit(1)
            .execute()
        )
        data = resp.data or []
        return data[0] if data else None

    def fetch_pipeline_run_steps(self, run_id: str) -> List[Dict[str, Any]]:
        resp = (
            self.client
            .table("pipeline_run_steps")
            .select("*")
            .eq("run_id", run_id)
            .order("order_index")
            .execute()
        )
        return resp.data or []

    # ------------------------------------------------------------------
    # Misc utilities
    # ------------------------------------------------------------------
def get_adapter() -> SupabaseAdapter:
    """Return a cached `SupabaseAdapter` instance."""

    global _adapter
    if _adapter is None:
        _adapter = SupabaseAdapter()
    return _adapter


__all__ = [
    "SupabaseAdapter",
    "get_client",
    "get_adapter",
]










