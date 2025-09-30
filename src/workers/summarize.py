from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from src.adapters.db_supabase import get_adapter
from src.adapters.llm_summary import summarise
from src.config import get_settings
from src.domain import SummaryCandidate
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "summarize"


def _load_keywords(path: Path) -> List[str]:
    if not path.exists():
        return []
    keywords: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if raw and not raw.startswith("#"):
            keywords.append(raw)
    return keywords


def _contains_keywords(text: str, keywords: Sequence[str]) -> Tuple[bool, List[str]]:
    if not keywords:
        return True, []
    hits: List[str] = []
    lowered = text.lower()
    for kw in keywords:
        if kw and kw.lower() in lowered:
            hits.append(kw)
    return (bool(hits), hits)


def run(limit: int = 50, *, concurrency: Optional[int] = None, keywords_path: Optional[Path] = None) -> None:
    settings = get_settings()
    adapter = get_adapter()

    with worker_session(WORKER, limit=limit):
        keywords_file = keywords_path or settings.keywords_path
        keywords = _load_keywords(Path(keywords_file))
        candidates = adapter.fetch_summary_candidates(limit)
        if not candidates:
            log_info(WORKER, "No articles pending summarisation.")
            return

        filtered: List[Tuple[SummaryCandidate, List[str]]] = []
        skipped = 0
        for candidate in candidates:
            ok, hits = _contains_keywords(candidate.content, keywords)
            if ok:
                filtered.append((candidate, hits))
            else:
                skipped += 1

        if not filtered:
            log_info(WORKER, f"All {len(candidates)} candidates filtered out by keyword rules.")
            return

        workers = concurrency or settings.default_concurrency
        workers = max(1, workers)

        success = 0
        failed = 0

        def _process(item: Tuple[SummaryCandidate, List[str]]) -> None:
            candidate, hits = item
            summary_payload = {
                "title": getattr(candidate, "title", None),
                "content": candidate.content,
            }
            result = summarise(summary_payload)
            summary_text = result.get("summary", "").strip()
            if not summary_text:
                raise RuntimeError("Summarisation returned empty text")
            adapter.save_summary(
                candidate,
                summary_text,
                source_llm=result.get("model"),
                keywords=hits,
            )

        if workers == 1:
            for item in filtered:
                candidate = item[0]
                try:
                    _process(item)
                    success += 1
                    log_info(WORKER, f"OK {candidate.article_hash}")
                except Exception as exc:
                    failed += 1
                    log_error(WORKER, candidate.article_hash, exc)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                future_map = {pool.submit(_process, item): item[0].article_hash for item in filtered}
                for future in as_completed(future_map):
                    cand_hash = future_map[future]
                    try:
                        future.result()
                        success += 1
                        log_info(WORKER, f"OK {cand_hash}")
                    except Exception as exc:
                        failed += 1
                        log_error(WORKER, cand_hash, exc)

        log_summary(WORKER, ok=success, failed=failed, skipped=skipped)


__all__ = ["run"]
