"""审阅页换组只在本工作区生效（2026-11-19）。

行为测试（真实 Postgres 临时表模式，照 tests/test_article_attribution_search.py
的写法）：临时表 LIKE 正式表建在当前连接上，测试结束连接关闭即消失。

覆盖验收标准 1–7：
1. 管理员换组不外溢（全局 news_summaries 不变、其他管理员的列表/候选池不变）；
2. 值班换组不外溢（其他班次与管理员不受影响，管理员查看该班次结果时看到班次分类）；
3. 拖回与全局一致的分组 → 覆盖值清空；
4. 筛选、计数、批量放弃/恢复、清理旧新闻都按生效分类圈选；
5. 待处理条目与聚类视图：有覆盖值的条目按生效分类作为单条出现，缓存簇不参与；
6. 值班结果导入管理员工作区时复制/清空/保留覆盖值；
7. 换组审计的 before/after 能看到覆盖值变化。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Any, Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from src.adapters import db_postgres_core
from src.config import get_settings


ADMIN_A = "11111111-1111-1111-1111-111111111111"
ADMIN_B = "22222222-2222-2222-2222-222222222222"
EDITOR = "33333333-3333-3333-3333-333333333333"

SHIFT_S = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SHIFT_OTHER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
FINALIZATION_BATCH = "cccccccc-cccc-cccc-cccc-cccccccccccc"

OLD_CREATED_AT = "2026-09-01T08:00:00+08:00"
SHIFT_CREATED_AT = "2026-10-07T08:00:00+08:00"

TEMP_TABLES = (
    "news_summaries",
    "manual_reviews",
    "shift_reviews",
    "duty_shifts",
    "shift_review_finalization_batches",
    "shift_review_admin_discards",
    "manual_clusters",
    "console_users",
    "score_feedbacks",
    "review_events",
)


def _connect() -> psycopg.Connection:
    settings = get_settings()
    try:
        return psycopg.connect(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            dbname=settings.db_name,
            autocommit=False,
            row_factory=dict_row,
            connect_timeout=5,
        )
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres 不可用，跳过真实 SQL 测试：{exc}")


@contextmanager
def _db_connection() -> Iterator[psycopg.Connection]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def conn() -> Iterator[psycopg.Connection]:
    with _db_connection() as connection:
        yield connection


@pytest.fixture()
def adapter(conn: psycopg.Connection) -> db_postgres_core.PostgresAdapter:
    return db_postgres_core.PostgresAdapter(connection=conn)


def _prepare_tables(cur: psycopg.Cursor) -> None:
    for table in TEMP_TABLES:
        cur.execute(
            f"CREATE TEMP TABLE {table} "
            f"(LIKE public.{table} INCLUDING DEFAULTS)"
        )
    # LIKE 不复制唯一约束；导入路径依赖这两个唯一键做 ON CONFLICT。
    cur.execute(
        "CREATE UNIQUE INDEX ON manual_reviews (owner_user_id, article_id)"
    )
    cur.execute(
        "CREATE UNIQUE INDEX ON shift_reviews (shift_id, article_id)"
    )


def _seed_article(
    cur: psycopg.Cursor,
    article_id: str,
    *,
    beijing: bool | None,
    sentiment: str | None,
    created_at: str = OLD_CREATED_AT,
    title: str | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO news_summaries (
            article_id, title, llm_summary, status, created_at,
            is_beijing_related, sentiment_label, external_importance_score
        )
        VALUES (%s, %s, %s, 'ready_for_export', %s, %s, %s, 60)
        """,
        (
            article_id,
            title or f"标题{article_id}",
            f"摘要{article_id}",
            created_at,
            beijing,
            sentiment,
        ),
    )


def _seed_manual_review(
    cur: psycopg.Cursor,
    *,
    owner_user_id: str,
    article_id: str,
    status: str = "pending",
    report_type: str | None = None,
    rank: float | None = None,
    version: int = 1,
    category_override: str | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO manual_reviews (
            owner_user_id, article_id, status, report_type, rank,
            version, category_override
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            owner_user_id,
            article_id,
            status,
            report_type,
            rank,
            version,
            category_override,
        ),
    )


def _seed_shift(
    cur: psycopg.Cursor,
    shift_id: str,
    *,
    starts_at: str = "2026-10-06T22:00:00+08:00",
    ends_at: str = "2026-10-07T22:00:00+08:00",
) -> None:
    cur.execute(
        """
        INSERT INTO duty_shifts (id, user_id, starts_at, ends_at)
        VALUES (%s, %s, %s, %s)
        """,
        (shift_id, EDITOR, starts_at, ends_at),
    )


def _seed_shift_review(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    article_id: str,
    decision: str = "pending",
    report_type: str | None = None,
    rank: int | None = None,
    version: int = 1,
    category_override: str | None = None,
    finalized_batch_id: str | None = None,
    finalized_rank: int | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO shift_reviews (
            shift_id, article_id, created_by_user_id, updated_by_user_id,
            report_type, decision, rank, version, category_override,
            finalized_batch_id, finalized_rank
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            shift_id,
            article_id,
            EDITOR,
            EDITOR,
            report_type,
            decision,
            rank,
            version,
            category_override,
            finalized_batch_id,
            finalized_rank,
        ),
    )


def _seed_finalization_batch(cur: psycopg.Cursor, shift_id: str) -> None:
    cur.execute(
        """
        INSERT INTO shift_review_finalization_batches (
            id, shift_id, report_type, finalized_by_user_id
        )
        VALUES (%s, %s, 'zongbao', %s)
        """,
        (FINALIZATION_BATCH, shift_id, EDITOR),
    )


def _global_category(
    cur: psycopg.Cursor,
    article_id: str,
) -> tuple[bool | None, str | None]:
    cur.execute(
        "SELECT is_beijing_related, sentiment_label "
        "FROM news_summaries WHERE article_id = %s",
        (article_id,),
    )
    row = cur.fetchone()
    assert row is not None
    return row["is_beijing_related"], row["sentiment_label"]


def _manual_override(
    cur: psycopg.Cursor,
    *,
    owner_user_id: str,
    article_id: str,
) -> str | None:
    cur.execute(
        "SELECT category_override FROM manual_reviews "
        "WHERE owner_user_id = %s AND article_id = %s",
        (owner_user_id, article_id),
    )
    row = cur.fetchone()
    assert row is not None
    return row["category_override"]


def _shift_override(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    article_id: str,
) -> str | None:
    cur.execute(
        "SELECT category_override FROM shift_reviews "
        "WHERE shift_id = %s AND article_id = %s",
        (shift_id, article_id),
    )
    row = cur.fetchone()
    assert row is not None
    return row["category_override"]


def _manual_items(
    adapter: db_postgres_core.PostgresAdapter,
    *,
    owner_user_id: str,
    status: str = "pending",
    only_ready: bool = True,
    **filters: Any,
) -> dict[str, dict[str, Any]]:
    rows, _ = adapter.manual_reviews.fetch(
        owner_user_id=owner_user_id,
        status=status,
        limit=50,
        offset=0,
        only_ready=only_ready,
        **filters,
    )
    return {str(row["article_id"]): row for row in rows}


def _shift_items(
    adapter: db_postgres_core.PostgresAdapter,
    *,
    shift_id: str,
    decision: str | None = None,
    **kwargs: Any,
) -> dict[str, dict[str, Any]]:
    rows, _ = adapter.shift_reviews.fetch_items(
        shift_id=shift_id,
        decision=decision,
        report_type=kwargs.pop("report_type", None),
        limit=50,
        offset=0,
        **kwargs,
    )
    return {str(row["article_id"]): row for row in rows}


def _order_admin(
    adapter: db_postgres_core.PostgresAdapter,
    *,
    owner_user_id: str,
    actor_username: str,
    selected: list[str],
    group_orders: dict[str, list[str]],
    backup: list[str] | None = None,
    request_id: str | None = None,
) -> tuple[int, int]:
    backup = backup or []
    return adapter.update_manual_review_order_as_user(
        [
            {
                "article_id": article_id,
                "status": "selected",
                "rank": float(index),
                "report_type": "zongbao",
            }
            for index, article_id in enumerate(selected, start=1)
        ]
        + [
            {
                "article_id": article_id,
                "status": "backup",
                "rank": float(index),
                "report_type": "zongbao",
            }
            for index, article_id in enumerate(backup, start=1)
        ],
        [
            {"article_id": article_id, "category_override": group_key}
            for group_key, article_ids in group_orders.items()
            for article_id in article_ids
        ],
        actor_username=actor_username,
        actor_user_id=owner_user_id,
        report_type="zongbao",
        request_id=request_id,
    )


def _order_shift(
    adapter: db_postgres_core.PostgresAdapter,
    *,
    shift_id: str,
    selected: list[str],
    group_orders: dict[str, list[str]],
    backup: list[str] | None = None,
    request_id: str | None = None,
) -> int:
    return adapter.update_shift_review_order(
        shift_id=shift_id,
        actor_user_id=EDITOR,
        selected_order=selected,
        backup_order=backup or [],
        category_updates=[
            {"article_id": article_id, "category_override": group_key}
            for group_key, article_ids in group_orders.items()
            for article_id in article_ids
        ],
        request_id=request_id,
    )


def _latest_audit(cur: psycopg.Cursor, action: str) -> dict[str, Any]:
    cur.execute(
        """
        SELECT action, before_data, after_data
        FROM review_events
        WHERE action = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (action,),
    )
    row = cur.fetchone()
    assert row is not None, f"没有找到审计事件 {action}"
    return row


# ---------------------------------------------------------------------------
# 验收 1 + 7：管理员换组不外溢，审计记录覆盖值前后变化
# ---------------------------------------------------------------------------


def test_admin_group_change_is_workspace_scoped_and_audited(
    adapter: db_postgres_core.PostgresAdapter,
) -> None:
    conn = adapter._conn
    with conn.cursor() as cur:
        _prepare_tables(cur)
        _seed_article(
            cur,
            "x",
            beijing=True,
            sentiment="positive",
            created_at=SHIFT_CREATED_AT,
        )
        _seed_manual_review(
            cur,
            owner_user_id=ADMIN_A,
            article_id="x",
            status="selected",
            report_type="zongbao",
            rank=1,
        )
        _seed_manual_review(
            cur,
            owner_user_id=ADMIN_B,
            article_id="x",
            status="pending",
        )
        # 只改情感维度的覆盖（全局京外负面 → 本工作区京外正面），
        # 保证列表 SELECT 如果退回全局 sentiment_label 会被断言抓到。
        _seed_article(
            cur,
            "w",
            beijing=False,
            sentiment="negative",
            created_at=SHIFT_CREATED_AT,
        )
        _seed_manual_review(
            cur,
            owner_user_id=ADMIN_A,
            article_id="w",
            status="selected",
            report_type="zongbao",
            rank=2,
        )
        _seed_manual_review(
            cur,
            owner_user_id=ADMIN_B,
            article_id="w",
            status="pending",
        )
        _seed_shift(cur, SHIFT_S)
        _seed_shift_review(cur, shift_id=SHIFT_S, article_id="x", decision="pending")
    conn.commit()

    updated_rows, updated_categories = _order_admin(
        adapter,
        owner_user_id=ADMIN_A,
        actor_username="admin-a",
        selected=["x", "w"],
        group_orders={"external_positive": ["x", "w"]},
        request_id="req-admin-order",
    )
    assert (updated_rows, updated_categories) == (2, 2)

    with conn.cursor() as cur:
        # 全局分类不再被人工换组改写
        assert _global_category(cur, "x") == (True, "positive")
        assert _global_category(cur, "w") == (False, "negative")
        # 只有换组人自己的工作区有覆盖值
        assert _manual_override(cur, owner_user_id=ADMIN_A, article_id="x") == (
            "external_positive"
        )
        assert _manual_override(cur, owner_user_id=ADMIN_A, article_id="w") == (
            "external_positive"
        )
        assert _manual_override(cur, owner_user_id=ADMIN_B, article_id="x") is None
        assert _manual_override(cur, owner_user_id=ADMIN_B, article_id="w") is None
        # 审计 before/after 能看到覆盖值变化
        audit = _latest_audit(cur, "manual_review.reorder")
        assert all(
            item["category_override"] is None
            for item in audit["before_data"]["items"]
        )
        assert {
            item["article_id"]: item["category_override"]
            for item in audit["after_data"]["items"]
        } == {"x": "external_positive", "w": "external_positive"}

    # 管理员 A 的审阅列表返回生效分类
    a_items = _manual_items(
        adapter,
        owner_user_id=ADMIN_A,
        status="selected",
        only_ready=False,
    )
    assert a_items["x"]["is_beijing_related"] is False
    assert a_items["x"]["sentiment_label"] == "positive"
    # w 只有情感维度变化：全局负面 → 本工作区正面
    assert a_items["w"]["is_beijing_related"] is False
    assert a_items["w"]["sentiment_label"] == "positive"

    # 管理员 B 的候选池仍按全局分类
    b_items = _manual_items(adapter, owner_user_id=ADMIN_B, status="pending")
    assert b_items["x"]["is_beijing_related"] is True
    assert b_items["x"]["sentiment_label"] == "positive"
    assert b_items["w"]["is_beijing_related"] is False
    assert b_items["w"]["sentiment_label"] == "negative"

    # 值班班次看到的仍是全局分类
    shift_items = _shift_items(adapter, shift_id=SHIFT_S)
    assert shift_items["x"]["is_beijing_related"] is True
    assert shift_items["x"]["sentiment_label"] == "positive"


# ---------------------------------------------------------------------------
# 验收 2：值班换组不外溢；管理员查看该班次结果时看到班次分类
# ---------------------------------------------------------------------------


def test_duty_group_change_is_shift_scoped_and_admin_views_shift_category(
    adapter: db_postgres_core.PostgresAdapter,
) -> None:
    conn = adapter._conn
    with conn.cursor() as cur:
        _prepare_tables(cur)
        _seed_article(
            cur,
            "y",
            beijing=True,
            sentiment="negative",
            created_at=SHIFT_CREATED_AT,
        )
        _seed_manual_review(cur, owner_user_id=ADMIN_A, article_id="y")
        _seed_shift(cur, SHIFT_S)
        _seed_shift(cur, SHIFT_OTHER)
        _seed_shift_review(
            cur,
            shift_id=SHIFT_S,
            article_id="y",
            decision="selected",
            report_type="zongbao",
            rank=1,
        )
        _seed_shift_review(
            cur,
            shift_id=SHIFT_OTHER,
            article_id="y",
            decision="pending",
        )
    conn.commit()

    updated = _order_shift(
        adapter,
        shift_id=SHIFT_S,
        selected=["y"],
        group_orders={"external_positive": ["y"]},
        request_id="req-shift-order",
    )
    assert updated == 1

    with conn.cursor() as cur:
        assert _global_category(cur, "y") == (True, "negative")
        assert _shift_override(cur, shift_id=SHIFT_S, article_id="y") == (
            "external_positive"
        )
        assert _shift_override(cur, shift_id=SHIFT_OTHER, article_id="y") is None
        audit = _latest_audit(cur, "shift_review.reorder")
        assert audit["before_data"]["items"][0]["category_override"] is None
        assert (
            audit["after_data"]["items"][0]["category_override"]
            == "external_positive"
        )

    # 只有当班次的列表按班次分类
    s_items = _shift_items(adapter, shift_id=SHIFT_S, decision="selected")
    assert s_items["y"]["is_beijing_related"] is False
    assert s_items["y"]["sentiment_label"] == "positive"

    # 其他班次、管理员自己的工作区不受影响
    other_items = _shift_items(adapter, shift_id=SHIFT_OTHER)
    assert other_items["y"]["is_beijing_related"] is True
    assert other_items["y"]["sentiment_label"] == "negative"
    admin_items = _manual_items(adapter, owner_user_id=ADMIN_A)
    assert admin_items["y"]["is_beijing_related"] is True
    assert admin_items["y"]["sentiment_label"] == "negative"

    # 管理员查看该班次结果（列表 + 预览取数 + 定稿）时看到班次分类
    admin_view = _shift_items(
        adapter,
        shift_id=SHIFT_S,
        include_admin_state=True,
        viewer_user_id=ADMIN_A,
    )
    assert admin_view["y"]["is_beijing_related"] is False
    assert admin_view["y"]["sentiment_label"] == "positive"

    with conn.cursor() as cur:
        _seed_finalization_batch(cur, SHIFT_S)
        cur.execute(
            "UPDATE shift_reviews SET finalized_batch_id = %s, finalized_rank = 1 "
            "WHERE shift_id = %s AND article_id = 'y'",
            (FINALIZATION_BATCH, SHIFT_S),
        )
    conn.commit()
    from src.adapters import db_postgres_shift_reviews

    with conn.cursor() as cur:
        finalized = db_postgres_shift_reviews.fetch_shift_finalized_items(
            cur,
            shift_id=SHIFT_S,
            report_type="zongbao",
        )
    assert len(finalized) == 1
    assert finalized[0]["article_id"] == "y"
    assert finalized[0]["is_beijing_related"] is False
    assert finalized[0]["sentiment_label"] == "positive"


# ---------------------------------------------------------------------------
# 验收 3：拖回与全局一致的分组 → 覆盖值清空
# ---------------------------------------------------------------------------


def test_dragging_back_to_global_group_clears_override(
    adapter: db_postgres_core.PostgresAdapter,
) -> None:
    conn = adapter._conn
    with conn.cursor() as cur:
        _prepare_tables(cur)
        _seed_article(cur, "x", beijing=True, sentiment="positive")
        _seed_manual_review(
            cur,
            owner_user_id=ADMIN_A,
            article_id="x",
            status="selected",
            report_type="zongbao",
            rank=1,
            category_override="external_positive",
        )
    conn.commit()

    updated_rows, updated_categories = _order_admin(
        adapter,
        owner_user_id=ADMIN_A,
        actor_username="admin-a",
        selected=["x"],
        group_orders={"internal_positive": ["x"]},
    )
    assert (updated_rows, updated_categories) == (1, 1)

    with conn.cursor() as cur:
        assert _manual_override(cur, owner_user_id=ADMIN_A, article_id="x") is None
        assert _global_category(cur, "x") == (True, "positive")
    # 生效分类回到全局值
    a_items = _manual_items(
        adapter,
        owner_user_id=ADMIN_A,
        status="selected",
        only_ready=False,
    )
    assert a_items["x"]["is_beijing_related"] is True
    assert a_items["x"]["sentiment_label"] == "positive"


# ---------------------------------------------------------------------------
# 验收 4：筛选、计数、批量放弃/恢复、清理旧新闻都用生效分类
# ---------------------------------------------------------------------------


def test_admin_filters_counts_and_bulk_operations_use_effective_category(
    adapter: db_postgres_core.PostgresAdapter,
) -> None:
    conn = adapter._conn
    with conn.cursor() as cur:
        _prepare_tables(cur)
        _seed_article(cur, "x", beijing=True, sentiment="positive")
        _seed_manual_review(
            cur,
            owner_user_id=ADMIN_A,
            article_id="x",
            category_override="external_positive",
        )
    conn.commit()

    # 筛选与计数按生效分类：外部正面包含，内部正面不包含
    assert adapter.manual_reviews.count_candidates_before_date(
        owner_user_id=ADMIN_A,
        region="external",
        sentiment="positive",
        created_before=date(2026, 10, 1),
    ) == 1
    assert adapter.manual_reviews.count_candidates_before_date(
        owner_user_id=ADMIN_A,
        region="internal",
        sentiment="positive",
        created_before=date(2026, 10, 1),
    ) == 0
    external_items = _manual_items(
        adapter,
        owner_user_id=ADMIN_A,
        region="external",
        sentiment="positive",
    )
    assert "x" in external_items
    internal_items = _manual_items(
        adapter,
        owner_user_id=ADMIN_A,
        region="internal",
        sentiment="positive",
    )
    assert "x" not in internal_items

    # 按分类批量放弃按生效分类圈选
    discarded = adapter.discard_manual_candidates_before_date_as_user(
        region="external",
        sentiment="positive",
        terms=None,
        created_before=date(2026, 10, 1),
        report_type="zongbao",
        actor_username="admin-a",
        actor_user_id=ADMIN_A,
        request_id="req-bulk-discard",
    )
    assert [row["article_id"] for row in discarded] == ["x"]

    # 放弃列表同样按生效分类可筛出
    discard_items = _manual_items(
        adapter,
        owner_user_id=ADMIN_A,
        status="discarded",
        only_ready=False,
        order_by_decided_at=True,
        region="external",
        sentiment="positive",
    )
    assert "x" in discard_items

    # 按条件恢复：预览计数与实际恢复范围一致
    dry_run = adapter.restore_discarded_manual_reviews_as_user(
        actor_username="admin-a",
        actor_user_id=ADMIN_A,
        region="external",
        sentiment="positive",
        dry_run=True,
    )
    assert dry_run == {"matched": 1, "updated": 0}
    restored = adapter.restore_discarded_manual_reviews_as_user(
        actor_username="admin-a",
        actor_user_id=ADMIN_A,
        region="external",
        sentiment="positive",
        dry_run=False,
    )
    assert restored == {"matched": 1, "updated": 1}

    # 恢复为待处理后覆盖值保留；清理旧新闻仍按生效分类圈选
    with conn.cursor() as cur:
        assert (
            _manual_override(cur, owner_user_id=ADMIN_A, article_id="x")
            == "external_positive"
        )
    cleanup = adapter.cleanup_manual_candidates_before_date_as_user(
        buckets=[{"region": "external", "sentiment": "positive"}],
        created_before=date(2026, 10, 1),
        actor_username="admin-a",
        actor_user_id=ADMIN_A,
        request_id="req-cleanup",
    )
    assert cleanup["matched"] == 1
    assert [row["article_id"] for row in cleanup["discarded"]] == ["x"]
    with conn.cursor() as cur:
        assert _global_category(cur, "x") == (True, "positive")
        assert (
            _manual_override(cur, owner_user_id=ADMIN_A, article_id="x")
            == "external_positive"
        )


def test_duty_filters_and_bulk_operations_use_effective_category(
    adapter: db_postgres_core.PostgresAdapter,
) -> None:
    conn = adapter._conn
    with conn.cursor() as cur:
        _prepare_tables(cur)
        _seed_article(
            cur,
            "y",
            beijing=True,
            sentiment="positive",
            created_at=SHIFT_CREATED_AT,
        )
        _seed_shift(cur, SHIFT_S)
        _seed_shift_review(
            cur,
            shift_id=SHIFT_S,
            article_id="y",
            category_override="external_positive",
        )
    conn.commit()

    # 值班列表筛选按班次生效分类
    external = _shift_items(
        adapter,
        shift_id=SHIFT_S,
        region="external",
        sentiment="positive",
    )
    assert "y" in external
    internal = _shift_items(
        adapter,
        shift_id=SHIFT_S,
        region="internal",
        sentiment="positive",
    )
    assert "y" not in internal
    assert external["y"]["is_beijing_related"] is False

    # 预览计数与实际放弃范围一致
    preview = adapter.discard_shift_candidates_as_user(
        shift_id=SHIFT_S,
        actor_user_id=EDITOR,
        region="external",
        sentiment="positive",
        terms=None,
        created_before=None,
        report_type="zongbao",
        dry_run=True,
    )
    assert preview["matched"] == 1
    assert preview["updated"] == 0
    discarded = adapter.discard_shift_candidates_as_user(
        shift_id=SHIFT_S,
        actor_user_id=EDITOR,
        region="external",
        sentiment="positive",
        terms=None,
        created_before=None,
        report_type="zongbao",
        dry_run=False,
    )
    assert discarded["updated"] == 1
    with conn.cursor() as cur:
        assert _shift_override(cur, shift_id=SHIFT_S, article_id="y") == (
            "external_positive"
        )

    # 值班放弃列表与按条件恢复也按生效分类
    discarded_items = _shift_items(
        adapter,
        shift_id=SHIFT_S,
        decision="discarded",
        report_type="zongbao",
        region="external",
        sentiment="positive",
    )
    assert "y" in discarded_items
    restore_preview = adapter.restore_shift_reviews_by_filter_as_user(
        shift_id=SHIFT_S,
        actor_user_id=EDITOR,
        region="external",
        sentiment="positive",
        terms=None,
        dry_run=True,
    )
    assert restore_preview == {"matched": 1, "updated": 0}
    restored = adapter.restore_shift_reviews_by_filter_as_user(
        shift_id=SHIFT_S,
        actor_user_id=EDITOR,
        region="external",
        sentiment="positive",
        terms=None,
        dry_run=False,
    )
    assert restored == {"matched": 1, "updated": 1}
    with conn.cursor() as cur:
        assert _global_category(cur, "y") == (True, "positive")
        assert _shift_override(cur, shift_id=SHIFT_S, article_id="y") == (
            "external_positive"
        )


# ---------------------------------------------------------------------------
# 验收 5：待处理条目与聚类视图按生效分类；缓存簇不被覆盖值影响
# ---------------------------------------------------------------------------


def test_pending_candidates_and_clusters_use_effective_category(
    adapter: db_postgres_core.PostgresAdapter,
) -> None:
    conn = adapter._conn
    with conn.cursor() as cur:
        _prepare_tables(cur)
        # 管理员侧：X 全局京内正面，A 的待处理行覆盖为京外正面
        _seed_article(cur, "x", beijing=True, sentiment="positive")
        _seed_manual_review(
            cur,
            owner_user_id=ADMIN_A,
            article_id="x",
            category_override="external_positive",
        )
        _seed_manual_review(cur, owner_user_id=ADMIN_B, article_id="x")
        # 值班侧：Y 全局京内正面，班次 S 的待处理行覆盖为京外正面
        _seed_article(
            cur,
            "y",
            beijing=True,
            sentiment="positive",
            created_at=SHIFT_CREATED_AT,
        )
        _seed_shift(cur, SHIFT_S)
        _seed_shift(cur, SHIFT_OTHER)
        _seed_shift_review(
            cur,
            shift_id=SHIFT_S,
            article_id="y",
            category_override="external_positive",
            report_type="zongbao",
        )
        # 共享聚类缓存按全局分类分桶：X/Y 都在 internal_positive 簇里
        cur.execute(
            "INSERT INTO manual_clusters (bucket_key, cluster_id, item_ids) VALUES "
            "('internal_positive', 'internal_positive-0', ARRAY['x', 'y'])"
        )
    conn.commit()

    # 管理员 A：聚类开模式 → X 不参与缓存簇，按生效分类单条出现
    a_clusters = adapter.manual_reviews.fetch_clusters(owner_user_id=ADMIN_A)
    a_cluster_ids = {row["cluster_id"]: row for row in a_clusters}
    assert "internal_positive-0" not in a_cluster_ids
    assert a_cluster_ids["single-x"]["bucket_key"] == "external_positive"
    # 管理员 B：没有覆盖值，仍在全局簇中
    b_clusters = adapter.manual_reviews.fetch_clusters(owner_user_id=ADMIN_B)
    b_rows = {row["cluster_id"]: row for row in b_clusters}
    assert b_rows["internal_positive-0"]["bucket_key"] == "internal_positive"
    assert "x" in b_rows["internal_positive-0"]["article_id"]
    # 聚类关模式（平铺候选池）同样按生效分类
    flat = _manual_items(adapter, owner_user_id=ADMIN_A, region="external", sentiment="positive")
    assert "x" in flat

    # 值班：有覆盖值的待处理条目不参与缓存簇，另一端班次不受影响
    s_clusters = adapter.shift_reviews.fetch_clusters(
        shift_id=SHIFT_S,
        report_type="zongbao",
    )
    s_rows = {row["cluster_id"]: row for row in s_clusters}
    assert "internal_positive-0" not in s_rows
    assert s_rows["single-y"]["bucket_key"] == "external_positive"
    other_clusters = adapter.shift_reviews.fetch_clusters(
        shift_id=SHIFT_OTHER,
        report_type="zongbao",
    )
    other_rows = {row["cluster_id"]: row for row in other_clusters}
    assert "internal_positive-0" in other_rows
    assert "y" in other_rows["internal_positive-0"]["item_ids"]
    # 缓存本身没有被读取路径改写
    with conn.cursor() as cur:
        cur.execute("SELECT cluster_id, item_ids FROM manual_clusters")
        rows = cur.fetchall()
    assert [(row["cluster_id"], list(row["item_ids"])) for row in rows] == [
        ("internal_positive-0", ["x", "y"]),
    ]


# ---------------------------------------------------------------------------
# 验收 6：值班结果导入管理员工作区时复制/清空/保留覆盖值
# ---------------------------------------------------------------------------


def test_import_shift_reviews_copies_duty_category_override(
    adapter: db_postgres_core.PostgresAdapter,
) -> None:
    conn = adapter._conn
    with conn.cursor() as cur:
        _prepare_tables(cur)
        _seed_shift(cur, SHIFT_S)
        # 新导入：值班有覆盖 → 管理员带上；值班没有覆盖 → 管理员清空（NULL）
        _seed_article(cur, "y", beijing=True, sentiment="positive")
        _seed_article(cur, "z", beijing=True, sentiment="positive")
        _seed_shift_review(
            cur,
            shift_id=SHIFT_S,
            article_id="y",
            decision="selected",
            report_type="zongbao",
            rank=1,
            category_override="external_positive",
        )
        _seed_shift_review(
            cur,
            shift_id=SHIFT_S,
            article_id="z",
            decision="selected",
            report_type="zongbao",
            rank=2,
        )
        # 冲突：管理员已有记录与覆盖值
        for article_id in ("w", "v", "u"):
            _seed_article(cur, article_id, beijing=True, sentiment="positive")
            _seed_manual_review(
                cur,
                owner_user_id=ADMIN_A,
                article_id=article_id,
                status="selected",
                report_type="zongbao",
                version=3,
                category_override="internal_negative",
            )
            _seed_shift_review(
                cur,
                shift_id=SHIFT_S,
                article_id=article_id,
                decision="selected",
                report_type="zongbao",
                category_override=(
                    "external_negative"
                    if article_id == "w"
                    else ("external_positive" if article_id == "v" else None)
                ),
            )
    conn.commit()

    imported = adapter.import_shift_reviews_into_manual(
        shift_id=SHIFT_S,
        article_ids=["y", "z", "w", "v", "u"],
        target_status="selected",
        report_type="zongbao",
        actor_username="admin-a",
        actor_user_id=ADMIN_A,
        conflict_resolutions=[
            {
                "article_id": "w",
                "choice": "duty",
                "summary": "值班摘要w",
                "manual_llm_source": "值班来源w",
                "existing_version": 3,
            },
            {
                "article_id": "v",
                "choice": "existing",
                "summary": "保留摘要v",
                "manual_llm_source": "保留来源v",
                "existing_version": 3,
            },
            {
                "article_id": "u",
                "choice": "duty",
                "summary": "值班摘要u",
                "manual_llm_source": "值班来源u",
                "existing_version": 3,
            },
        ],
    )
    assert len(imported) == 5

    with conn.cursor() as cur:
        # 新导入：复制值班覆盖值（y）与 NULL（z）
        assert _manual_override(cur, owner_user_id=ADMIN_A, article_id="y") == (
            "external_positive"
        )
        assert _manual_override(cur, owner_user_id=ADMIN_A, article_id="z") is None
        # 冲突选「值班版本」：覆盖值被替换（w）/ 值班没有覆盖则清空（u）
        assert _manual_override(cur, owner_user_id=ADMIN_A, article_id="w") == (
            "external_negative"
        )
        assert _manual_override(cur, owner_user_id=ADMIN_A, article_id="u") is None
        # 冲突选「保留现有」：管理员原有覆盖值不动
        assert _manual_override(cur, owner_user_id=ADMIN_A, article_id="v") == (
            "internal_negative"
        )
        # 全局分类全程不变
        for article_id in ("y", "z", "w", "v", "u"):
            assert _global_category(cur, article_id) == (True, "positive")
    # 导入后管理员列表返回生效分类
    items = _manual_items(
        adapter,
        owner_user_id=ADMIN_A,
        status="selected",
        only_ready=False,
    )
    assert items["y"]["is_beijing_related"] is False
    assert items["y"]["sentiment_label"] == "positive"
    assert items["w"]["is_beijing_related"] is False
    assert items["w"]["sentiment_label"] == "negative"
