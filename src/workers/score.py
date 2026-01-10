from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from src.adapters.db import get_adapter
from src.adapters.llm_scoring import score_text
from src.config import get_settings
from src.domain import PrimaryArticleForScoring
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "score"

DEFAULT_KEYWORD_BONUS_RULES: Dict[str, int] = {
    "\u5317\u4eac\u5e02\u59d4\u6559\u80b2\u5de5\u59d4": 100,
    "\u5317\u4eac\u5e02\u6559\u80b2\u59d4\u5458\u4f1a": 100,
}

ScoreSuccess = Tuple[PrimaryArticleForScoring, Optional[int], int, Optional[int], Dict[str, Any]]


def _score_item(item: PrimaryArticleForScoring) -> Optional[int]:
    text = item.content or ""
    if not text.strip():
        return None
    return score_text(text)


def _collect_text_sources(item: PrimaryArticleForScoring) -> List[str]:
    sources: List[str] = []
    if item.title:
        sources.append(str(item.title))
    if item.content:
        sources.append(str(item.content))
    for keyword in item.keywords or []:
        if keyword:
            sources.append(str(keyword))
    return sources


def _calculate_keyword_bonus(
    item: PrimaryArticleForScoring, rules: Dict[str, int]
) -> Tuple[int, List[Dict[str, Any]]]:
    if not rules:
        return 0, []
    haystacks = _collect_text_sources(item)
    matched_rules: List[Dict[str, Any]] = []
    total_bonus = 0
    for keyword, bonus in rules.items():
        if not keyword:
            continue
        if any(keyword in text for text in haystacks):
            matched_rules.append(
                {
                    "rule_id": f"keyword:{keyword}",
                    "label": keyword,
                    "bonus": int(bonus),
                }
            )
            total_bonus += int(bonus)
    return total_bonus, matched_rules


def _compose_score_details(
    raw_score: Optional[int],
    bonus: int,
    final_score: Optional[int],
    matched_rules: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "raw_relevance_score": raw_score,
        "keyword_bonus_score": bonus,
        "final_score": final_score,
        "matched_rules": matched_rules,
    }


def _process_scores_single_worker(
    rows: List[PrimaryArticleForScoring],
    bonus_rules: Dict[str, int]
) -> Tuple[List[ScoreSuccess], List[str]]:
    successes: List[ScoreSuccess] = []
    failures: List[str] = []
    
    for row in rows:
        try:
            raw_score = _score_item(row)
            bonus_score = 0
            matched_rules: List[Dict[str, Any]] = []
            final_score: Optional[int] = None
            if raw_score is not None:
                bonus_score, matched_rules = _calculate_keyword_bonus(row, bonus_rules)
                final_score = raw_score + bonus_score
            score_details = _compose_score_details(raw_score, bonus_score, final_score, matched_rules)
            successes.append((row, raw_score, bonus_score, final_score, score_details))
            rules_info = ",".join(rule["rule_id"] for rule in matched_rules) if matched_rules else "-"
            log_info(
                WORKER,
                f"OK {row.article_id}: raw={raw_score} bonus={bonus_score} final={final_score} rules={rules_info}",
            )
        except Exception as exc:
            failures.append(row.article_id)
            log_error(WORKER, row.article_id, exc)
            
    return successes, failures


def _process_scores_multi_worker(
    rows: List[PrimaryArticleForScoring],
    workers: int,
    bonus_rules: Dict[str, int]
) -> Tuple[List[ScoreSuccess], List[str]]:
    successes: List[ScoreSuccess] = []
    failures: List[str] = []
    
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {pool.submit(_score_item, row): row for row in rows}
        for future in as_completed(future_map):
            item = future_map[future]
            article_id = item.article_id
            try:
                raw_score = future.result()
                bonus_score = 0
                matched_rules: List[Dict[str, Any]] = []
                final_score: Optional[int] = None
                if raw_score is not None:
                    bonus_score, matched_rules = _calculate_keyword_bonus(item, bonus_rules)
                    final_score = raw_score + bonus_score
                score_details = _compose_score_details(raw_score, bonus_score, final_score, matched_rules)
                successes.append((item, raw_score, bonus_score, final_score, score_details))
                rules_info = ",".join(rule["rule_id"] for rule in matched_rules) if matched_rules else "-"
                log_info(
                    WORKER,
                    f"OK {article_id}: raw={raw_score} bonus={bonus_score} final={final_score} rules={rules_info}",
                )
            except Exception as exc:
                failures.append(article_id)
                log_error(WORKER, article_id, exc)
                
    return successes, failures


def _prepare_updates(
    successes: List[ScoreSuccess],
    failures: List[str],
    threshold: int
) -> Tuple[List[dict], List[dict]]:
    updates: List[dict] = []
    promotion_payloads: List[dict] = []
    for item, raw_score, bonus_score, final_score, score_details in successes:
        threshold_met = final_score is not None and final_score >= threshold
        status = "scored" if threshold_met else "filtered_out"
        updates.append(
            {
                "article_id": item.article_id,
                "score": final_score,
                "raw_relevance_score": raw_score,
                "keyword_bonus_score": bonus_score,
                "score_details": score_details,
                "status": status,
            }
        )
        if threshold_met:
            promotion_payloads.append(
                {
                    "article_id": item.article_id,
                    "title": item.title,
                    "source": item.source,
                    "publish_time": item.publish_time,
                    "publish_time_iso": item.publish_time_iso,
                    "url": item.url,
                    "content_markdown": item.content,
                    "score": final_score,
                    "raw_relevance_score": raw_score,
                    "keyword_bonus_score": bonus_score,
                    "score_details": score_details,
                    "status": "pending",
                    "keywords": list(item.keywords),
                }
            )

    if failures:
        updates.extend(
            {
                "article_id": article_id,
                "score": None,
                "raw_relevance_score": None,
                "keyword_bonus_score": 0,
                "score_details": {},
                "status": "failed",
            }
            for article_id in failures
        )
        
    return updates, promotion_payloads


def run(limit: int = 500, *, concurrency: Optional[int] = None) -> None:
    settings = get_settings()
    adapter = get_adapter()
    threshold = getattr(settings, "score_promotion_threshold", 60)

    with worker_session(WORKER, limit=limit):
        rows = adapter.fetch_primary_articles_for_scoring(limit)
        if not rows:
            log_info(WORKER, "No primary articles pending relevance scoring.")
            return

        workers = concurrency or settings.default_concurrency or 5
        workers = max(1, workers)
        bonus_rules = settings.score_keyword_bonus_rules or DEFAULT_KEYWORD_BONUS_RULES

        if workers == 1:
            successes, failures = _process_scores_single_worker(rows, bonus_rules)
        else:
            successes, failures = _process_scores_multi_worker(rows, workers, bonus_rules)

        updates, promotion_payloads = _prepare_updates(successes, failures, threshold)

        if updates:
            adapter.update_primary_article_scores(updates)

        if promotion_payloads:
            adapter.upsert_news_summaries_from_primary(promotion_payloads)
            log_info(WORKER, f"Promoted {len(promotion_payloads)} primary articles to news_summaries")

        success_count = len(successes)
        failed_count = len(failures)
        skipped = len(rows) - success_count - failed_count
        log_summary(WORKER, ok=success_count, failed=failed_count, skipped=skipped or None)


__all__ = ["run"]
