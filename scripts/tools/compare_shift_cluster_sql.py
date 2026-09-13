from __future__ import annotations

import argparse
import re
from typing import Any, Sequence

import psycopg
from psycopg.rows import dict_row

from src.adapters.db_postgres_shift_reviews import fetch_shift_clusters
from src.config import get_settings
from src.domain.report_type import DEFAULT_REPORT_TYPE, NEWS_REPORT_TYPE_ORDER


OLD_SQL = """
WITH shift_pending AS (
    SELECT
        ns.article_id,
        ns.external_importance_score,
        sr.rank AS manual_rank,
        ns.score,
        ns.publish_time_iso,
        CASE
            WHEN ns.is_beijing_related THEN 'internal'
            ELSE 'external'
        END || '_' || CASE
            WHEN lower(COALESCE(ns.sentiment_label, '')) = 'negative'
                THEN 'negative'
            ELSE 'positive'
        END AS bucket_key
    FROM duty_shifts s
    JOIN news_summaries ns
      ON ns.created_at >= s.starts_at
     AND ns.created_at < s.ends_at
    LEFT JOIN shift_reviews sr
      ON sr.shift_id = s.id
     AND sr.article_id = ns.article_id
    WHERE s.id = %s
      AND s.cancelled_at IS NULL
      AND ns.status = 'ready_for_export'
      AND COALESCE(sr.decision, 'pending') = 'pending'
      AND COALESCE(sr.report_type, 'zongbao') = %s
),
cluster_memberships AS (
    SELECT
        mc.cluster_id,
        mc.bucket_key,
        pending.article_id,
        pending.external_importance_score,
        pending.manual_rank,
        pending.score,
        pending.publish_time_iso
    FROM manual_clusters mc
    CROSS JOIN LATERAL unnest(mc.item_ids)
        AS cluster_item(article_id)
    JOIN shift_pending pending
      ON pending.article_id = cluster_item.article_id
),
unclustered_items AS (
    SELECT
        'single-' || pending.article_id AS cluster_id,
        pending.bucket_key,
        pending.article_id,
        pending.external_importance_score,
        pending.manual_rank,
        pending.score,
        pending.publish_time_iso
    FROM shift_pending pending
    WHERE NOT EXISTS (
        SELECT 1
        FROM manual_clusters mc
        CROSS JOIN LATERAL unnest(mc.item_ids)
            AS cluster_item(article_id)
        WHERE cluster_item.article_id = pending.article_id
    )
),
all_cluster_items AS (
    SELECT * FROM cluster_memberships
    UNION ALL
    SELECT * FROM unclustered_items
),
ranked_cluster_items AS (
    SELECT
        cluster_id,
        bucket_key,
        article_id,
        external_importance_score,
        manual_rank,
        score,
        publish_time_iso,
        row_number() OVER (
            PARTITION BY cluster_id
            ORDER BY
                external_importance_score DESC NULLS LAST,
                manual_rank DESC NULLS LAST,
                score DESC NULLS LAST,
                publish_time_iso DESC NULLS LAST,
                article_id
        ) AS item_rank
    FROM all_cluster_items
)
SELECT
    cluster_id,
    bucket_key,
    array_agg(article_id ORDER BY item_rank) AS item_ids,
    max(external_importance_score)
        FILTER (WHERE item_rank = 1)
        AS representative_external_importance_score,
    max(manual_rank)
        FILTER (WHERE item_rank = 1)
        AS representative_manual_rank,
    max(score)
        FILTER (WHERE item_rank = 1)
        AS representative_score,
    max(publish_time_iso)
        FILTER (WHERE item_rank = 1)
        AS representative_publish_time
FROM ranked_cluster_items
GROUP BY cluster_id, bucket_key
ORDER BY
    representative_external_importance_score DESC NULLS LAST,
    representative_manual_rank DESC NULLS LAST,
    representative_score DESC NULLS LAST,
    representative_publish_time DESC NULLS LAST,
    cluster_id
"""


Row = dict[str, Any]
Triple = tuple[Any, Any, tuple[Any, ...]]


class RecordingCursor:
    def __init__(self) -> None:
        self.query: str | None = None
        self.params: tuple[Any, ...] | None = None

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.query = query
        self.params = params

    def fetchall(self) -> list[Row]:
        return []


def _production_sql(*, shift_id: str, report_type: str) -> str:
    cursor = RecordingCursor()
    result = fetch_shift_clusters(
        cursor,
        shift_id=shift_id,
        report_type=report_type,
    )
    expected_params = (shift_id, report_type)
    if cursor.query is None:
        raise AssertionError("fetch_shift_clusters did not execute SQL")
    if cursor.params != expected_params:
        raise AssertionError(
            "Production SQL parameters do not match (shift_id, report_type): "
            f"captured={cursor.params!r}"
        )
    if result:
        raise AssertionError("Recording cursor unexpectedly returned rows")
    return cursor.query


def _connect() -> psycopg.Connection[Row]:
    settings = get_settings()
    return psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        dbname=settings.db_name,
        row_factory=dict_row,
    )


def _fetch_rows(
    cur: psycopg.Cursor[Row], query: str, params: Sequence[object]
) -> list[Row]:
    cur.execute(query, params)
    return list(cur.fetchall())


def _triple(row: Row) -> Triple:
    return row["cluster_id"], row["bucket_key"], tuple(row["item_ids"])


def _first_difference(old: Sequence[Any], new: Sequence[Any]) -> str:
    for index, (old_value, new_value) in enumerate(zip(old, new)):
        if old_value != new_value:
            return f"index={index}, old={old_value!r}, new={new_value!r}"
    return f"common_prefix={min(len(old), len(new))}"


def _assert_equivalent(old_rows: list[Row], new_rows: list[Row]) -> None:
    if len(old_rows) != len(new_rows):
        raise AssertionError(
            f"Row count mismatch: old={len(old_rows)}, new={len(new_rows)}"
        )

    old_triples = [_triple(row) for row in old_rows]
    new_triples = [_triple(row) for row in new_rows]
    if set(old_triples) != set(new_triples):
        missing = set(old_triples) - set(new_triples)
        extra = set(new_triples) - set(old_triples)
        raise AssertionError(
            "Triple set mismatch: "
            f"missing_from_new={next(iter(missing), None)!r}, "
            f"extra_in_new={next(iter(extra), None)!r}"
        )
    if old_triples != new_triples:
        raise AssertionError(
            "Row order mismatch: " + _first_difference(old_triples, new_triples)
        )
    if old_rows != new_rows:
        raise AssertionError(
            "Full row mismatch: " + _first_difference(old_rows, new_rows)
        )


def _explain(
    cur: psycopg.Cursor[Row], query: str, params: Sequence[object]
) -> tuple[float, list[str]]:
    cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + query, params)
    plan = [str(next(iter(row.values()))) for row in cur.fetchall()]
    execution_line = next(
        (line for line in plan if "Execution Time:" in line), None
    )
    if execution_line is None:
        raise AssertionError("EXPLAIN output did not contain Execution Time")
    match = re.search(r"Execution Time: ([0-9.]+) ms", execution_line)
    if match is None:
        raise AssertionError(f"Could not parse execution time: {execution_line}")
    return float(match.group(1)), plan


def _cluster_plan_lines(plan: Sequence[str]) -> list[str]:
    markers = (
        "CTE cluster_items",
        "manual_clusters",
        "Function Scan on unnest",
        "CTE Scan on cluster_items",
    )
    return [line for line in plan if any(marker in line for marker in markers)]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare the old and rewritten shift-cluster SQL on one database."
    )
    parser.add_argument("shift_id", help="Duty shift ID to compare")
    parser.add_argument(
        "--report-type",
        choices=NEWS_REPORT_TYPE_ORDER,
        default=DEFAULT_REPORT_TYPE,
        help=f"News report type (default: {DEFAULT_REPORT_TYPE})",
    )
    args = parser.parse_args()
    params = (args.shift_id, args.report_type)
    new_sql = _production_sql(
        shift_id=args.shift_id,
        report_type=args.report_type,
    )

    with _connect() as conn, conn.cursor() as cur:
        old_rows = _fetch_rows(cur, OLD_SQL, params)
        new_rows = _fetch_rows(cur, new_sql, params)
        _assert_equivalent(old_rows, new_rows)
        old_time_ms, _ = _explain(cur, OLD_SQL, params)
        new_time_ms, new_plan = _explain(cur, new_sql, params)

    print(f"shift_id={args.shift_id}")
    print(f"report_type={args.report_type}")
    print(f"old_row_count={len(old_rows)}")
    print(f"new_row_count={len(new_rows)}")
    print("row_count_match=PASS")
    print("triple_set_and_item_order_match=PASS")
    print("overall_row_order_match=PASS")
    print("full_row_match=PASS")
    print(f"old_execution_time_ms={old_time_ms:.3f}")
    print(f"new_execution_time_ms={new_time_ms:.3f}")
    if new_time_ms > 0:
        print(f"speedup={old_time_ms / new_time_ms:.2f}x")
    print("new_cluster_items_plan_nodes:")
    for line in _cluster_plan_lines(new_plan):
        print(line)


if __name__ == "__main__":
    main()
