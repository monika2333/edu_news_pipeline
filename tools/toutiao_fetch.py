#!/usr/bin/env python3
"""
Simple CLI to fetch a Toutiao article by URL or ID and output cleaned content.

No third-party deps. Uses stdlib urllib + json.

Supported inputs:
- https://www.toutiao.com/article/<article_id>/...
- numeric article id (e.g., 7549818805088223780)

Fetches:
- https://m.toutiao.com/i<article_id}/info/

Outputs:
- Markdown (default) or plain text or raw JSON
"""

from __future__ import annotations

import sys
import re
import json
import argparse
import sqlite3
from typing import Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import html
import io
from pathlib import Path
try:
    from tools.supabase_adapter import ArticleInput, get_supabase_adapter, is_supabase_configured
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    try:
        from tools.supabase_adapter import ArticleInput, get_supabase_adapter, is_supabase_configured
    except ModuleNotFoundError:
        ArticleInput = None
        def get_supabase_adapter():
            raise RuntimeError("Supabase adapter is unavailable. Set SUPABASE_URL to enable.")
        def is_supabase_configured() -> bool:
            return False
else:
    pass
_SUPABASE_ENABLED = ArticleInput is not None and is_supabase_configured()
_SUPABASE_ADAPTER: Optional[object] = None

# Fix Windows console encoding issues
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


INFO_ENDPOINT = "https://m.toutiao.com/i{article_id}/info/"


def resolve_short_url(url: str, timeout: int = 15) -> str:
    """
    闂佽崵鍠愰悷杈╁緤妤ｅ啯鍊靛ù鐘差儐閸庢銇勯幘鍗炵仾闁瑰磭濞€閺岀喖骞侀幒鎴濆Х缂備浇椴哥换鍫ュ箠閵忕姷鏆嬮柡澶庢硶閹ジ姊洪崫鍕闁稿鎸剧槐鎾存媴閸濄儱鈪卞┑鐐茬墛閸ㄥ潡寮荤仦绛嬪悑闁告侗鍨卞В搴ㄦ⒑閸涘﹦顣查柛娆忓盁L
    """
    try:
        from urllib.request import Request, urlopen
        from urllib.error import URLError, HTTPError

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        }
        req = Request(url, headers=headers)

        # 闂備礁鎲￠悷顖涚閿濆宓侀煫鍥ㄧ☉閻鏌″畵顔绘缁捇姊绘笟鍥ф灁闁告柨顑嗙粚杈ㄧ節閸パ呯暢濡炪倖鐗撻崐妤冪矆婢跺ň妲堥柟鎯х－灏忕紓浣诡殔閹冲繐顭囬鍫熷癄濠㈣泛鏈▓顕€姊烘潪鎵妽婵炲吋鐟╁畷顒傗偓鐢电《閸?
        with urlopen(req, timeout=timeout) as resp:
            return resp.url
    except Exception:
        return url  # 濠电姷顣介埀顒€鍟块埀顒€缍婇幃妯诲緞鐏炴儳鏋傞梺鎸庣箓濡盯鍩€椤掑啫袚鐎垫澘瀚蹇涱敃閵夋劖娲熼弻銊モ槈濡偐鍔紓浣虹帛閸ㄥ灝鐣烽崼鏇熷€烽柟缁樺笚閺嬬栋RL

def extract_article_id(input_str: str) -> str:
    s = input_str.strip()

    # If it's numeric-like, return directly
    if re.fullmatch(r"\d{16,20}", s):
        return s

    # Check if it's a short URL that needs resolving
    if "m.toutiao.com/is/" in s:
        resolved_url = resolve_short_url(s)
        s = resolved_url

    # Try parse from URL path /article/<id>/ or /i<id>/
    try:
        u = urlparse(s)
        path = u.path or ""
        m = re.search(r"/(?:a|article|i)(\d{16,20})", path)
        if m:
            return m.group(1)
    except Exception:
        pass

    # Try in full string
    m = re.search(r"(\d{16,20})", s)
    if m:
        return m.group(1)

    raise ValueError("Could not extract Toutiao article_id from input")


def fetch_info(article_id: str, timeout: int = 15, ua: str | None = None, lang: str | None = None) -> dict:
    url = INFO_ENDPOINT.format(article_id=article_id)
    headers = {
        "User-Agent": ua
        or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    }
    if lang:
        headers["Accept-Language"] = lang
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except HTTPError as e:
        raise RuntimeError(f"HTTP error {e.code} fetching info: {e.reason}") from e
    except URLError as e:
        raise RuntimeError(f"Network error fetching info: {e.reason}") from e
    try:
        obj = json.loads(data.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise RuntimeError("Failed to parse JSON from Toutiao info endpoint") from e
    if not obj.get("success"):
        raise RuntimeError("Toutiao info API responded with success=false")
    return obj.get("data") or {}


def parse_iso_to_epoch(iso_str: str | None) -> int | None:
    if not iso_str:
        return None
    from datetime import datetime
    # Try patterns with and without fractional seconds
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"):
        try:
            dt = datetime.strptime(iso_str, fmt)
            return int(dt.timestamp())
        except Exception:
            continue
    # Try inserting colon in timezone if missing, e.g. +0800 -> +08:00
    try:
        if iso_str[-5] in ['+', '-'] and iso_str[-3] != ':':
            iso2 = iso_str[:-2] + ":" + iso_str[-2:]
            from datetime import datetime
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
                try:
                    dt = datetime.strptime(iso2, fmt)
                    return int(dt.timestamp())
                except Exception:
                    pass
    except Exception:
        pass
    return None


def fetch_bjd(url: str, timeout: int = 15, ua: str | None = None, lang: str | None = None) -> dict:
    # Convert content page to .json endpoint if needed
    parsed = urlparse(url)
    json_url = url
    if parsed.path.endswith('.html'):
        json_url = url.replace('.html', '.json')
    headers = {
        "User-Agent": ua
        or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    }
    if lang:
        headers["Accept-Language"] = lang
    req = Request(json_url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except HTTPError as e:
        raise RuntimeError(f"HTTP error {e.code} fetching BJD JSON: {e.reason}") from e
    except URLError as e:
        raise RuntimeError(f"Network error fetching BJD JSON: {e.reason}") from e
    obj = json.loads(data.decode("utf-8", errors="replace"))
    if obj.get("code") != 0:
        raise RuntimeError("BJD JSON API responded with non-zero code")
    d = obj.get("data") or {}
    # Normalize to Toutiao-like dict keys used by save_to_sqlite/format_output
    publish_iso = d.get("publishTime") or d.get("updated")
    publish_epoch = parse_iso_to_epoch(publish_iso)
    norm = {
        "gid": d.get("id"),
        "title": d.get("title") or d.get("htmlTitle") or "",
        "source": d.get("source") or d.get("columnName") or "",
        "publish_time": publish_epoch,
        "url": d.get("url") or url,
        "content": d.get("content") or "",
    }
    return norm


def html_to_text(html_str: str) -> str:
    # Unescape and normalize line breaks for <p> and <br>
    s = html.unescape(html_str or "")
    # Replace block tags with newlines
    s = re.sub(r"<(?:/)?p[^>]*>", "\n\n", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    # Remove other tags
    s = re.sub(r"<[^>]+>", "", s)
    # Collapse excessive blank lines
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s


def html_to_markdown(html_str: str) -> str:
    # Very light conversion: <p> -> blank line; <br> -> newline.
    # For simplicity we keep it near-plain text; links/images are not critical for news.
    text = html_to_text(html_str)
    return text


def format_output(data: dict, fmt: str = "md") -> str:
    title = (data.get("title") or "").strip()
    source = (data.get("source") or data.get("detail_source") or "").strip()
    publish_ts = data.get("publish_time")  # epoch seconds as string
    try:
        ts_int = int(publish_ts) if publish_ts else None
    except Exception:
        ts_int = None
    # Convert epoch to ISO date if available
    iso_date = None
    if ts_int:
        try:
            from datetime import datetime, timezone

            iso_date = datetime.fromtimestamp(ts_int, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        except Exception:
            iso_date = None
    content_html = data.get("content") or ""
    content_md = html_to_markdown(content_html)
    original_url = data.get("url") or ""
    article_id = data.get("gid") or data.get("group_id") or ""

    if fmt == "json":
        return json.dumps(
            {
                "title": title,
                "source": source,
                "publish_time": ts_int,
                "publish_time_iso": iso_date,
                "article_id": article_id,
                "original_url": original_url,
                "content": content_md,
            },
            ensure_ascii=False,
            indent=2,
        )

    if fmt == "text":
        parts = []
        if title:
            parts.append(title)
        meta_bits = []
        if source:
            meta_bits.append("Source: " + source)
        if iso_date:
            meta_bits.append("Published: " + iso_date)
        if meta_bits:
            parts.append(" | ".join(meta_bits))
        if original_url:
            parts.append("Original URL: " + original_url)
        if article_id:
            parts.append("Article ID: " + article_id)
        parts.append("")
        parts.append(content_md)
        return "\\n".join(parts).strip() + "\\n"

    # default markdown
    md = []
    if title:
        md.append(f"# {title}")
    meta = []
    if source:
        meta.append(f"Source: {source}")
    if iso_date:
        meta.append(f"Published: {iso_date}")
    if meta:
        md.append("\n" + " | ".join(meta) + "\n")
    if original_url:
        md.append(f"Original URL: {original_url}\n")
    if article_id:
        md.append(f"Article ID: `{article_id}`\n")
    md.append("\n" + content_md + "\n")
    return "\n".join(md)
def save_to_sqlite(db_path: str, rec: dict) -> None:
    if _SUPABASE_ENABLED and ArticleInput is not None:
        global _SUPABASE_ADAPTER
        if _SUPABASE_ADAPTER is None:
            _SUPABASE_ADAPTER = get_supabase_adapter()
        try:
            publish_time_raw = rec.get("publish_time")
            publish_time = int(publish_time_raw) if publish_time_raw else None
        except Exception:
            publish_time = None
        record = ArticleInput(
            article_id=(rec.get("gid") or rec.get("group_id") or rec.get("id") or "").strip() or None,
            title=(rec.get("title") or "").strip() or None,
            source=(rec.get("source") or rec.get("detail_source") or "").strip() or None,
            publish_time=publish_time,
            original_url=(rec.get("url") or "").strip() or None,
            content=html_to_markdown(rec.get("content") or ""),
            raw_payload=rec,
            metadata={"fetched_by": "toutiao_fetch"},
        )
        _SUPABASE_ADAPTER.upsert_article(record)
        return
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id TEXT UNIQUE,
                title TEXT,
                source TEXT,
                publish_time INTEGER,
                original_url TEXT,
                content TEXT,
                inserted_at TEXT DEFAULT (datetime('now','localtime'))
            )
            """
        )
        cur.execute(
            """
            INSERT INTO articles (article_id, title, source, publish_time, original_url, content)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(article_id) DO UPDATE SET
                title=excluded.title,
                source=excluded.source,
                publish_time=excluded.publish_time,
                original_url=excluded.original_url,
                content=excluded.content,
                inserted_at=datetime('now','localtime')
            """,
            (
                (rec.get("gid") or rec.get("group_id") or "").strip(),
                (rec.get("title") or "").strip(),
                (rec.get("source") or rec.get("detail_source") or "").strip(),
                int(rec.get("publish_time")) if rec.get("publish_time") else None,
                (rec.get("url") or "").strip(),
                html_to_markdown(rec.get("content") or ""),
            ),
        )
        conn.commit()
    finally:
        conn.close()

def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Fetch and tidy Toutiao article content")
    p.add_argument("input", help="Toutiao article URL or numeric ID")
    p.add_argument("-f", "--format", choices=["md", "text", "json"], default="md", help="Output format")
    p.add_argument("-o", "--output", help="Output file path (default: stdout)")
    p.add_argument("--db", help="SQLite database file path to store the article")
    p.add_argument("--timeout", type=int, default=15)
    p.add_argument("--lang", default="zh-CN,zh;q=0.9")
    args = p.parse_args(argv)

    data = None
    # Detect BJD site
    try:
        u = urlparse(args.input)
    except Exception:
        u = None
    if u and u.netloc and ("bjd.com.cn" in u.netloc):
        try:
            data = fetch_bjd(args.input, timeout=args.timeout, lang=args.lang)
        except Exception as e:
            print(f"[error] {e}", file=sys.stderr)
            return 3
    else:
        # Default: Toutiao by article_id or URL
        try:
            aid = extract_article_id(args.input)
        except Exception as e:
            print(f"[error] {e}", file=sys.stderr)
            return 2
        try:
            data = fetch_info(aid, timeout=args.timeout, lang=args.lang)
        except Exception as e:
            print(f"[error] {e}", file=sys.stderr)
            return 3

    # Optional DB write
    if args.db:
        try:
            save_to_sqlite(args.db, data)
        except Exception as e:
            print(f"[error] failed to write SQLite: {e}", file=sys.stderr)
            return 4

    out = format_output(data, fmt=args.format)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        try:
            sys.stdout.write(out)
        except UnicodeEncodeError:
# Windows console encoding issue - use safe fallback
            safe_out = out.encode('utf-8', errors='replace').decode('utf-8')
# Windows console encoding issue - use safe fallback
                sys.stdout.write(safe_out)
            except UnicodeEncodeError:
# Fallback to ASCII if console still fails
# Fallback to ASCII if console still fails
                sys.stdout.write(ascii_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
