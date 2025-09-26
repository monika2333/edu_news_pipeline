"""Scrape Toutiao profile feed items using Playwright."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from playwright.async_api import async_playwright

PROFILE_TOKEN = (
    "Cifx50zdtEmvBfmuANgpNNx-jaATjuC5UeaWb9MEKciWNAhtGCki3TUaSQo8AAAAAAAAAAAAAE-"
    "FStkgtlJ2xUNf2NwTc1BR1H1P-NHsFkockMP2g3WkjJS6PJvajfenhG0IygROY2L0EJye_Q0Yw8WD6gQiAQN8i-x8"
)
BASE_PROFILE_URL = f"https://www.toutiao.com/c/user/token/{PROFILE_TOKEN}/"
DEFAULT_LIMIT = 100


@dataclass
class FeedItem:
    """Normalized representation of a Toutiao feed entry."""

    title: str
    summary: str
    source: str
    publish_time: str
    article_url: str
    comment_count: int
    digg_count: int

    @classmethod
    def from_raw(cls, item: Dict[str, Any]) -> "FeedItem":
        publish_time = item.get("publish_time")
        if publish_time:
            published = datetime.fromtimestamp(int(publish_time), tz=timezone.utc)
            publish_str = published.isoformat()
        else:
            publish_str = ""

        article_url = item.get("display_url") or item.get("article_url") or ""

        return cls(
            title=item.get("title", "").strip(),
            summary=item.get("abstract", "").strip(),
            source=(item.get("source") or item.get("media_name") or "").strip(),
            publish_time=publish_str,
            article_url=article_url,
            comment_count=int(item.get("comment_count") or 0),
            digg_count=int(item.get("digg_count") or 0),
        )


async def _fetch_feed_page(page, token: str, max_behot_time: str) -> Dict[str, Any]:
    """Request a single page from the Toutiao profile feed."""

    fetch_script = """
        async ({ token, max_behot_time }) => {
            const params = new URLSearchParams({
                category: 'profile_all',
                token,
                max_behot_time: String(max_behot_time),
                entrance_gid: '',
                aid: '24',
                app_name: 'toutiao_web',
            });
            const response = await fetch('https://www.toutiao.com/api/pc/list/user/feed?' + params.toString(), {
                credentials: 'include'
            });
            if (!response.ok) {
                throw new Error(`Request failed with status ${response.status}`);
            }
            return await response.json();
        }
    """

    return await page.evaluate(fetch_script, {"token": token, "max_behot_time": max_behot_time})


async def collect_feed_items(limit: int = DEFAULT_LIMIT) -> List[FeedItem]:
    """Collect `limit` feed items from the configured Toutiao profile."""

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            ignore_https_errors=True,
        )
        page = await context.new_page()
        await page.goto(BASE_PROFILE_URL)
        await page.wait_for_selector("body")

        items: List[FeedItem] = []
        max_behot_time = "0"

        for _ in range(100):  # safety limit for pagination
            payload = await _fetch_feed_page(page, PROFILE_TOKEN, max_behot_time)
            for raw in payload.get("data", []):
                if not raw.get("title"):
                    continue
                items.append(FeedItem.from_raw(raw))
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break

            if not payload.get("has_more"):
                break
            max_behot_time = str(payload.get("next", {}).get("max_behot_time") or "0")
            if max_behot_time in {"0", "None", ""}:
                break

        await browser.close()

    return items[:limit]


def save_items(items: List[FeedItem], output: Path) -> None:
    """Persist items to JSON and CSV files under the given output path."""

    output.parent.mkdir(parents=True, exist_ok=True)
    json_path = output.with_suffix(".json")
    csv_path = output.with_suffix(".csv")

    data = [asdict(item) for item in items]
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    with csv_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(data[0].keys()))
        writer.writeheader()
        writer.writerows(data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Toutiao profile items.")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help="Number of items to collect (default: 100).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/toutiao_profile_items"),
        help="Output path prefix (without extension).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    items = asyncio.run(collect_feed_items(limit=args.limit))
    if not items:
        raise SystemExit("No items collected from the feed.")
    save_items(items, args.output)
    print(f"Saved {len(items)} items to {args.output.with_suffix('.json')} and .csv")


if __name__ == "__main__":
    main()
