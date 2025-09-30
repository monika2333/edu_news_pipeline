#!/usr/bin/env python3
"""Scrape Toutiao authors listed in a file, fetch article contents, and push to Supabase."""

from __future__ import annotations

import argparse
import html
import json

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Set
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from playwright.async_api import async_playwright

try:  # Optional import; Supabase upload requires psycopg
    import psycopg
    from psycopg import sql, errors
except ImportError:  # pragma: no cover - handled at runtime
    psycopg = None  # type: ignore
    sql = None  # type: ignore
    errors = None  # type: ignore

INFO_ENDPOINT = "https://m.toutiao.com/i{article_id}/info/"
PROFILE_URL_TEMPLATE = "https://www.toutiao.com/c/user/token/{token}/"
DEFAULT_LIMIT = 100
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
TOKEN_PATTERN = re.compile(r"/token/([^/?#]+)/?")
ARTICLE_ID_PATTERN = re.compile(r"(\d{16,20})")
FETCH_FEED_JS = """
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
SUPABASE_ENV_DEFAULT = Path(".env.local")


@dataclass
class FeedItem:
    token: str
    profile_url: str
    title: str
    summary: str
    source: str
    publish_time: Optional[int]
    publish_time_iso: Optional[str]
    article_url: str
    comment_count: int
    digg_count: int
    raw: Dict[str, Any]

    @classmethod
    def from_raw(cls, token: str, profile_url: str, item: Dict[str, Any]) -> "FeedItem":
        publish_time = item.get("publish_time")
        ts: Optional[int] = None
        iso: Optional[str] = None
        if publish_time:
            try:
                ts = int(publish_time)
            except (TypeError, ValueError):
                ts = None
        if ts:
            try:
                iso = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat()
            except Exception:
                iso = None
        article_url = item.get("display_url") or item.get("article_url") or ""
        return cls(
            token=token,
            profile_url=profile_url,
            title=(item.get("title") or "").strip(),
            summary=(item.get("abstract") or "").strip(),
            source=(item.get("source") or item.get("media_name") or "").strip(),
            publish_time=ts,
            publish_time_iso=iso,
            article_url=article_url,
            comment_count=int(item.get("comment_count") or 0),
            digg_count=int(item.get("digg_count") or 0),
            raw=item,
        )


@dataclass
class ArticleRecord:
    token: str
    profile_url: str
    article_id: str
    title: str
    source: str
    publish_time: Optional[int]
    publish_time_iso: Optional[str]
    url: str
    summary: str
    comment_count: int
    digg_count: int
    content_markdown: str
    fetched_at: str


@dataclass
class SupabaseConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    schema: str
    table: str
    reset_table: bool


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value and key not in os.environ:
            os.environ[key] = value


def extract_token_from_url(url: str) -> str:
    match = TOKEN_PATTERN.search(url)
    if not match:
        raise ValueError(f"Could not extract token from: {url}")
    return match.group(1)


def load_author_tokens(path: Path) -> List[Tuple[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    entries: List[Tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("http"):
            token = extract_token_from_url(stripped)
            profile_url = stripped if stripped.endswith("/") else f"{stripped}/"
        else:
            token = stripped
            profile_url = PROFILE_URL_TEMPLATE.format(token=token)
        entries.append((token, profile_url))
    if not entries:
        raise ValueError("No author tokens found in input file.")
    return entries


def resolve_short_url(url: str, timeout: int = 15) -> str:
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=timeout) as resp:
            return resp.geturl()
    except Exception:
        return url


def extract_article_id(value: str) -> str:
    s = (value or "").strip()
    if not s:
        raise ValueError("Empty input")
    if re.fullmatch(r"\d{16,20}", s):
        return s
    if "m.toutiao.com/is/" in s:
        s = resolve_short_url(s)
    try:
        parsed = urlparse(s)
        path = parsed.path or ""
        match = re.search(r"/(?:a|article|i)(\d{16,20})", path)
        if match:
            return match.group(1)
    except Exception:
        pass
    match = ARTICLE_ID_PATTERN.search(s)
    if match:
        return match.group(1)
    raise ValueError(f"Could not extract article_id from: {value}")


def fetch_info(article_id: str, timeout: int = 15, lang: Optional[str] = None) -> Dict[str, Any]:
    url = INFO_ENDPOINT.format(article_id=article_id)
    headers = {"User-Agent": USER_AGENT}
    if lang:
        headers["Accept-Language"] = lang
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except HTTPError as exc:
        raise RuntimeError(f"HTTP error {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc.reason}") from exc
    try:
        data = json.loads(payload.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Failed to parse JSON from Toutiao info endpoint") from exc
    if not data.get("success"):
        raise RuntimeError("Toutiao info API responded with success=false")
    return data.get("data") or {}


def html_to_text(html_str: str) -> str:
    text = html.unescape(html_str or "")
    text = re.sub(r"<(?:/)?p[^>]*>", "\n\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def html_to_markdown(html_str: str) -> str:
    return html_to_text(html_str)


def to_iso(ts: Optional[int]) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat()
    except Exception:
        return None


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        if value.endswith("Z"):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None


async def _collect_feed_from_page(
    page, token: str, profile_url: str, limit: Optional[int], existing_ids: Optional[Set[str]]
) -> Tuple[List[FeedItem], bool]:
    if limit == 0:
        return [], False
    collected: List[FeedItem] = []
    max_behot_time = "0"
    reached_existing = False
    for _ in range(200):
        try:
            payload = await page.evaluate(FETCH_FEED_JS, {"token": token, "max_behot_time": max_behot_time})
        except Exception as exc:
            print(f"[warn] Failed to fetch feed page for token {token}: {exc}", file=sys.stderr)
            break
        for raw in payload.get("data", []):
            if not raw.get("title"):
                continue
            item = FeedItem.from_raw(token, profile_url, raw)
            article_id = try_resolve_article_id_from_feed(item) if existing_ids is not None else None
            if article_id and existing_ids is not None and article_id in existing_ids:
                reached_existing = True
                break
            collected.append(item)
            if limit is not None and len(collected) >= limit:
                break
        if reached_existing:
            break
        if limit is not None and len(collected) >= limit:
            break
        if not payload.get("has_more"):
            break
        max_behot_time = str(payload.get("next", {}).get("max_behot_time") or "0")
        if max_behot_time in {"0", "None", ""}:
            break
    if limit is not None:
        collected = collected[:limit]
    return collected, reached_existing


async def fetch_feed_items(
    entries: List[Tuple[str, str]],
    limit: Optional[int],
    show_browser: bool,
    existing_ids: Optional[Set[str]],
) -> List[FeedItem]:
    all_items: List[FeedItem] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not show_browser)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            locale="zh-CN",
            ignore_https_errors=True,
        )
        for token, profile_url in entries:
            if limit is not None and len(all_items) >= limit:
                break
            remaining: Optional[int]
            if limit is None:
                remaining = None
            else:
                remaining = max(limit - len(all_items), 0)
            page = await context.new_page()
            print(f"[info] Collecting feed for {profile_url}", file=sys.stderr)
            await page.goto(profile_url)
            await page.wait_for_selector("body")
            items, reached_existing = await _collect_feed_from_page(
                page, token, profile_url, remaining, existing_ids
            )
            all_items.extend(items)
            await page.close()
            if reached_existing:
                print("[info] Reached existing Supabase data; stopping pagination for this author.", file=sys.stderr)
        await browser.close()
    if limit is not None:
        return all_items[:limit]
    return all_items


def try_resolve_article_id_from_feed(item: FeedItem) -> Optional[str]:
    candidates = [item.article_url, str(item.raw.get("group_id") or ""), str(item.raw.get("item_id") or "")]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return extract_article_id(candidate)
        except Exception:
            continue
    return None



def resolve_article_id_from_feed(item: FeedItem) -> str:
    article_id = try_resolve_article_id_from_feed(item)
    if article_id is None:
        raise ValueError(f"Could not resolve article_id for feed item: {item.title}")
    return article_id


def build_article_record(item: FeedItem, article_id: str, data: Dict[str, Any]) -> ArticleRecord:
    publish_time: Optional[int] = None
    for option in (data.get("publish_time"), item.publish_time):
        if not option:
            continue
        try:
            publish_time = int(option)
            break
        except (TypeError, ValueError):
            continue
    publish_iso = data.get("publish_time_iso") or to_iso(publish_time) or item.publish_time_iso
    url = (data.get("url") or item.article_url or "").strip()
    title = (data.get("title") or item.title or "").strip()
    source = (data.get("source") or data.get("detail_source") or item.source or "").strip()
    content_md = html_to_markdown(data.get("content") or "")
    return ArticleRecord(
        token=item.token,
        profile_url=item.profile_url,
        article_id=str(article_id),
        title=title,
        source=source,
        publish_time=publish_time,
        publish_time_iso=publish_iso,
        url=url,
        summary=item.summary,
        comment_count=item.comment_count,
        digg_count=item.digg_count,
        content_markdown=content_md,
        fetched_at=datetime.now(timezone.utc).astimezone().isoformat(),
    )


def fetch_article_records(
    feed_items: Iterable[FeedItem],
    timeout: int,
    lang: Optional[str],
    existing_ids: Optional[Set[str]],
) -> List[ArticleRecord]:
    records: List[ArticleRecord] = []
    baseline_ids: Set[str] = set(existing_ids or [])
    seen_new: Set[str] = set()
    for item in feed_items:
        try:
            article_id = resolve_article_id_from_feed(item)
        except Exception as exc:
            print(f"[warn] Skip article because ID could not be resolved: {exc}", file=sys.stderr)
            continue
        if article_id in baseline_ids:
            print(f"[info] Article {article_id} already in Supabase; skipping.", file=sys.stderr)
            continue
        if article_id in seen_new:
            continue
        try:
            data = fetch_info(article_id, timeout=timeout, lang=lang)
        except Exception as exc:
            print(f"[warn] Failed to fetch article {article_id}: {exc}", file=sys.stderr)
            continue
        records.append(build_article_record(item, article_id, data))
        seen_new.add(article_id)
    if existing_ids is not None:
        existing_ids.update(seen_new)
    return records


def derive_supabase_host(supabase_url: str) -> str:
    parsed = urlparse(supabase_url)
    host = parsed.netloc or supabase_url
    project_ref = host.split(".")[0]
    return f"db.{project_ref}.supabase.co"


def build_supabase_config(args: argparse.Namespace) -> Optional[SupabaseConfig]:
    if args.skip_supabase_upload:
        return None
    if psycopg is None:
        print("[warn] psycopg is not installed; skipping Supabase upload.", file=sys.stderr)
        return None
    supabase_url = os.getenv("SUPABASE_URL")
    password = os.getenv("SUPABASE_DB_PASSWORD")
    if not supabase_url or not password:
        print("[warn] Missing SUPABASE_URL or SUPABASE_DB_PASSWORD; skipping Supabase upload.", file=sys.stderr)
        return None
    user = os.getenv("SUPABASE_DB_USER", "postgres")
    database = os.getenv("SUPABASE_DB_NAME", "postgres")
    schema = os.getenv("SUPABASE_DB_SCHEMA", "public")
    host = os.getenv("SUPABASE_DB_HOST", derive_supabase_host(supabase_url))
    try:
        port = int(os.getenv("SUPABASE_DB_PORT", "5432"))
    except ValueError:
        port = 5432
    return SupabaseConfig(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        schema=schema,
        table=args.supabase_table,
        reset_table=args.reset_supabase_table,
    )


def fetch_existing_article_ids(config: SupabaseConfig) -> Set[str]:
    if psycopg is None or sql is None:
        return set()
    if config.reset_table:
        return set()
    table_ident = sql.Identifier(config.schema, config.table)
    query = sql.SQL("SELECT article_id FROM {}").format(table_ident)
    article_ids: Set[str] = set()
    try:
        with psycopg.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            dbname=config.database,
            sslmode="require",
        ) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(query)
                except Exception as exc:
                    if errors is not None and isinstance(exc, errors.UndefinedTable):
                        return set()
                    raise
                rows = cur.fetchall()
                for row in rows:
                    if row and row[0]:
                        article_ids.add(str(row[0]))
    except Exception as exc:
        print(f"[warn] Could not read existing Supabase records: {exc}", file=sys.stderr)
        return set()
    return article_ids



def upload_records_to_supabase(records: List[ArticleRecord], config: SupabaseConfig) -> bool:
    if not records:
        print("[warn] No records to upload to Supabase.", file=sys.stderr)
        return True
    if psycopg is None or sql is None:  # safety net
        print("[warn] psycopg not available; cannot upload to Supabase.", file=sys.stderr)
        return False
    table_ident = sql.Identifier(config.schema, config.table)
    create_sql = sql.SQL(
        """
        CREATE TABLE IF NOT EXISTS {} (
            token TEXT,
            profile_url TEXT,
            article_id TEXT PRIMARY KEY,
            title TEXT,
            source TEXT,
            publish_time BIGINT,
            publish_time_iso TIMESTAMPTZ,
            url TEXT,
            summary TEXT,
            comment_count INTEGER,
            digg_count INTEGER,
            content_markdown TEXT,
            fetched_at TIMESTAMPTZ
        )
        """
    ).format(table_ident)
    columns = [
        "token",
        "profile_url",
        "article_id",
        "title",
        "source",
        "publish_time",
        "publish_time_iso",
        "url",
        "summary",
        "comment_count",
        "digg_count",
        "content_markdown",
        "fetched_at",
    ]
    placeholders = sql.SQL(", ").join(sql.Placeholder(col) for col in columns)
    insert_sql = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        table_ident,
        sql.SQL(", ").join(sql.Identifier(col) for col in columns),
        placeholders,
    )
    rows: List[Dict[str, Any]] = []
    for record in records:
        rows.append(
            {
                "token": record.token,
                "profile_url": record.profile_url,
                "article_id": record.article_id,
                "title": record.title,
                "source": record.source,
                "publish_time": record.publish_time,
                "publish_time_iso": parse_iso_datetime(record.publish_time_iso),
                "url": record.url,
                "summary": record.summary,
                "comment_count": record.comment_count,
                "digg_count": record.digg_count,
                "content_markdown": record.content_markdown,
                "fetched_at": parse_iso_datetime(record.fetched_at),
            }
        )
    try:
        with psycopg.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            dbname=config.database,
            sslmode="require",
        ) as conn:
            with conn.cursor() as cur:
                if config.reset_table:
                    cur.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(table_ident))
                cur.execute(create_sql)
                cur.executemany(insert_sql, rows)
            conn.commit()
    except Exception as exc:
        print(f"[error] Failed to upload to Supabase: {exc}", file=sys.stderr)
        return False
    print(f"[info] Uploaded {len(rows)} record(s) to Supabase table {config.schema}.{config.table}", file=sys.stderr)
    return True

__all__ = ["FeedItem", "ArticleRecord", "SupabaseConfig", "SUPABASE_ENV_DEFAULT", "load_env_file", "load_author_tokens", "fetch_feed_items", "fetch_article_records", "build_supabase_config", "fetch_existing_article_ids", "upload_records_to_supabase", "DEFAULT_LIMIT"]
