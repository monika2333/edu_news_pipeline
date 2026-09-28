"""一次性补录：把指定的中国日报文章按正常抓取路径写入流水线。

用法：
    python scripts/backfill_chinadaily_url.py <detail_url>

复用 crawl_sources 的 linked-page flow（feed upsert -> detail 更新 ->
关键词候选入队），保证数据形态与正式抓取完全一致。补录后文章会由
常规调度（hash-primary -> score）继续处理。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from src.adapters.db_postgres_core import get_adapter
from src.adapters.http_chinadaily import (
    FeedItemLike,
    build_detail_update as cd_build_detail_update,
    fetch_detail as cd_fetch_detail,
    feed_item_to_row as cd_feed_item_to_row,
    make_article_id as cd_make_article_id,
)
from src.business_config import get_business_config
from src.workers import crawl_sources as cs


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/backfill_chinadaily_url.py <detail_url>")
        return 2
    url = sys.argv[1].strip()

    detail = cd_fetch_detail(url)
    if not detail.get("title"):
        print(f"ERROR: no title parsed from {url}")
        return 1

    item = FeedItemLike(
        title=detail["title"],
        url=detail["url"],
        section=None,
        publish_time_iso=detail.get("publish_time_iso"),
        raw={},
    )

    flow = cs._linked_page_flow(
        source="chinadaily",
        display_name="China Daily",
        list_items=lambda limit, existing: [item],
        make_article_id=cd_make_article_id,
        feed_item_to_row_func=cd_feed_item_to_row,
        fetch_detail_func=cd_fetch_detail,
        build_detail_update_func=cd_build_detail_update,
    )

    adapter = get_adapter()
    keywords = get_business_config().education_keywords
    stats = cs._run_source_flow(
        adapter=adapter,
        flow=flow,
        keywords=keywords,
        remaining_limit=1,
    )
    print("backfill stats:", dict(stats))
    print("article_id:", cd_make_article_id(url))
    return 0 if stats.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
