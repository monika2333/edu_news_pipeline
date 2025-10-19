from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Iterable, Optional, Sequence

import psycopg
from psycopg.rows import dict_row

from src.config import get_settings


def _connect():
    settings = get_settings()
    return psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        dbname=settings.db_name,
        row_factory=dict_row,
    )


def _resolve_since(days: Optional[int]) -> Optional[datetime]:
    if days is None:
        return None
    return datetime.now(timezone.utc) - timedelta(days=max(0, days))


def _scalar(cur, query: str, params: Sequence[object]) -> float:
    cur.execute(query, params)
    row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


def _collect_scores(cur, since: Optional[datetime]) -> tuple[list[float], list[float], list[float]]:
    if since:
        cur.execute(
            """
            SELECT raw_relevance_score, keyword_bonus_score, score
            FROM primary_articles
            WHERE raw_relevance_score IS NOT NULL
              AND created_at >= %s
            """,
            (since,),
        )
    else:
        cur.execute(
            """
            SELECT raw_relevance_score, keyword_bonus_score, score
            FROM primary_articles
            WHERE raw_relevance_score IS NOT NULL
            """
        )
    raw_values: list[float] = []
    bonus_values: list[float] = []
    final_values: list[float] = []
    for row in cur.fetchall():
        raw_values.append(float(row["raw_relevance_score"]))
        bonus_values.append(float(row["keyword_bonus_score"] or 0))
        final_values.append(float(row["score"] or 0))
    return raw_values, bonus_values, final_values


def _status_counts(cur, table: str, since: Optional[datetime]) -> Counter[str]:
    if since:
        cur.execute(
            f"""
            SELECT status, COUNT(*) AS count
            FROM {table}
            WHERE created_at >= %s
            GROUP BY status
            ORDER BY status
            """,
            (since,),
        )
    else:
        cur.execute(
            f"""
            SELECT status, COUNT(*) AS count
            FROM {table}
            GROUP BY status
            ORDER BY status
            """
        )
    return Counter({row["status"]: row["count"] for row in cur.fetchall()})


def _matched_rule_counts(cur, since: Optional[datetime]) -> Counter[str]:
    if since:
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE jsonb_array_length(score_details->'matched_rules') > 0) AS matched,
                COUNT(*) FILTER (WHERE jsonb_array_length(score_details->'matched_rules') = 0) AS empty
            FROM primary_articles
            WHERE created_at >= %s
            """,
            (since,),
        )
    else:
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE jsonb_array_length(score_details->'matched_rules') > 0) AS matched,
                COUNT(*) FILTER (WHERE jsonb_array_length(score_details->'matched_rules') = 0) AS empty
            FROM primary_articles
            """
        )
    row = cur.fetchone() or {"matched": 0, "empty": 0}
    return Counter({"matched_rules": row["matched"] or 0, "no_rules": row["empty"] or 0})


def _summaries_with_bonus(cur, since: Optional[datetime]) -> Counter[str]:
    if since:
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE keyword_bonus_score > 0) AS bonus,
                COUNT(*) FILTER (WHERE keyword_bonus_score = 0 OR keyword_bonus_score IS NULL) AS no_bonus
            FROM news_summaries
            WHERE summary_generated_at >= %s
            """,
            (since,),
        )
    else:
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE keyword_bonus_score > 0) AS bonus,
                COUNT(*) FILTER (WHERE keyword_bonus_score = 0 OR keyword_bonus_score IS NULL) AS no_bonus
            FROM news_summaries
            """
        )
    row = cur.fetchone() or {"bonus": 0, "no_bonus": 0}
    return Counter({"bonus": row["bonus"] or 0, "no_bonus": row["no_bonus"] or 0})


def _format_stats(values: Iterable[float]) -> str:
    seq = list(values)
    if not seq:
        return "n=0"
    return f"n={len(seq)} avg={mean(seq):.2f} min={min(seq):.2f} max={max(seq):.2f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Show pipeline scoring metrics.")
    parser.add_argument("--days", type=int, default=7, help="Limit metrics to the most recent N days (default: 7). Use 0 to include all rows.")
    args = parser.parse_args()

    since = _resolve_since(args.days if args.days and args.days > 0 else None)
    if since:
        print(f"Metrics since {since.isoformat()}")
    else:
        print("Metrics across all available data")

    with closing(_connect()) as conn, conn.cursor() as cur:
        primary_status = _status_counts(cur, "primary_articles", since)
        summaries_status = _status_counts(cur, "news_summaries", since)
        raw_values, bonus_values, final_values = _collect_scores(cur, since)
        matched_counts = _matched_rule_counts(cur, since)
        summary_bonus_counts = _summaries_with_bonus(cur, since)

    print("\nPrimary Articles by status:")
    for status, count in sorted(primary_status.items()):
        print(f"  {status:>15}: {count}")

    print("\nNews Summaries by status:")
    for status, count in sorted(summaries_status.items()):
        print(f"  {status:>15}: {count}")

    print("\nScore distributions:")
    print(f"  raw_relevance_score   -> {_format_stats(raw_values)}")
    print(f"  keyword_bonus_score   -> {_format_stats(bonus_values)}")
    print(f"  final score (raw+bonus)-> {_format_stats(final_values)}")

    print("\nKeyword bonus coverage:")
    print(f"  primary_articles with matched rules: {matched_counts['matched_rules']}")
    print(f"  primary_articles without matches   : {matched_counts['no_rules']}")
    print(f"  news_summaries with bonuses        : {summary_bonus_counts['bonus']}")
    print(f"  news_summaries without bonuses     : {summary_bonus_counts['no_bonus']}")


if __name__ == "__main__":
    main()
