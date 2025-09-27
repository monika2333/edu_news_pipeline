#!/usr/bin/env python3
"""
Import AuthorFetch outputs (Excel link lists and TXT contents) into the
simple `articles` table used by tools/toutiao_fetch.py.

- Scans a source directory (default: AuthorFetch/) for:
  - Excel files: rows with title + url; parsed to external_id
  - Date-based subdirs (YYYY-MM-DD)/<external_id>/ with TXT content
    (uses the longest filename as title; reads UTF-8 text)

- Upserts into a SQLite DB (default: articles.sqlite3) with schema:
  articles(article_id UNIQUE, title, source, publish_time, original_url, content)

Python stdlib only.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple
import datetime as dt
import zipfile
import html as _html


# --- Config / Patterns ---

RE_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RE_NUMERIC_ID = re.compile(r"^\d{10,}$")
RE_EXTRACT_NUMERIC_ID = re.compile(r"(\d{10,})")


# --- DB helpers (match tools/toutiao_fetch.py) ---

CREATE_TABLE_SQL = """
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


def ensure_db(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)


def upsert_article_row(
    conn: sqlite3.Connection,
    *,
    article_id: str,
    title: Optional[str] = None,
    source: Optional[str] = None,
    publish_time: Optional[int] = None,
    original_url: Optional[str] = None,
    content: Optional[str] = None,
) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO articles (article_id, title, source, publish_time, original_url, content)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(article_id) DO UPDATE SET
            title=COALESCE(excluded.title, title),
            source=COALESCE(excluded.source, source),
            publish_time=COALESCE(excluded.publish_time, publish_time),
            original_url=COALESCE(excluded.original_url, original_url),
            content=CASE
                WHEN excluded.content IS NULL THEN content
                WHEN content IS NULL THEN excluded.content
                WHEN length(excluded.content) > length(content) THEN excluded.content
                ELSE content
            END,
            inserted_at=datetime('now','localtime')
        """,
        (
            (article_id or "").strip(),
            (title or "").strip() if title else None,
            (source or "").strip() if source else None,
            int(publish_time) if publish_time else None,
            (original_url or "").strip() if original_url else None,
            (content or "").strip() if content else None,
        ),
    )


# --- XLSX reading (self-contained minimal parser) ---

class MinimalXlsx:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.zf = zipfile.ZipFile(file_path)
        self.shared_strings: List[str] = self._load_shared_strings()
        self.sheet_paths: List[str] = self._find_sheet_paths()

    def close(self):
        try:
            self.zf.close()
        except Exception:
            pass

    def _load_shared_strings(self) -> List[str]:
        try:
            with self.zf.open("xl/sharedStrings.xml") as f:
                data = f.read()
        except KeyError:
            return []
        text = data.decode("utf-8", errors="ignore")
        strings: List[str] = []
        for si in re.findall(r"<si>(.*?)</si>", text, flags=re.S):
            ts = re.findall(r"<t[^>]*>(.*?)</t>", si, flags=re.S)
            joined = "".join(ts)
            strings.append(_html.unescape(joined))
        return strings

    def _find_sheet_paths(self) -> List[str]:
        candidates = ["xl/worksheets/sheet1.xml"]
        for c in candidates:
            try:
                self.zf.getinfo(c)
                return [c]
            except KeyError:
                continue
        paths = [n for n in self.zf.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
        paths.sort()
        if not paths:
            raise RuntimeError(f"No worksheets found in {self.file_path}")
        return [paths[0]]

    def iter_rows(self) -> Iterable[List[str]]:
        sheet_path = self.sheet_paths[0]
        with self.zf.open(sheet_path) as f:
            data = f.read().decode("utf-8", errors="ignore")
        rows: Dict[int, Dict[int, str]] = {}
        for row_block in re.findall(r"<row[^>]*>(.*?)</row>", data, flags=re.S):
            for raddr, inner in re.findall(r"<c[^>]*?r=\"([A-Z]+\d+)\"[^>]*>(.*?)</c>", row_block, flags=re.S):
                col_letters = re.findall(r"[A-Z]+", raddr)[0]
                row_digits = int(re.findall(r"\d+", raddr)[0])
                col_idx = self.col_letters_to_index(col_letters)
                m = re.search(r"<is>.*?<t[^>]*>(.*?)</t>.*?</is>", inner, flags=re.S)
                if m:
                    val = _html.unescape(m.group(1))
                else:
                    m = re.search(r"<v>(\d+)</v>", inner)
                    if m and self.shared_strings:
                        idx = int(m.group(1))
                        val = self.shared_strings[idx] if 0 <= idx < len(self.shared_strings) else ""
                    else:
                        m = re.search(r"<v>(.*?)</v>", inner, flags=re.S)
                        val = m.group(1).strip() if m else ""
                rows.setdefault(row_digits, {})[col_idx] = val
        if not rows:
            return
        for r in sorted(rows.keys()):
            row_map = rows[r]
            if not row_map:
                yield []
                continue
            max_c = max(row_map.keys())
            yield [row_map.get(ci, "") for ci in range(1, max_c + 1)]

    @staticmethod
    def col_letters_to_index(letters: str) -> int:
        idx = 0
        for ch in letters:
            idx = idx * 26 + (ord(ch) - ord('A') + 1)
        return idx


def read_xlsx_rows(file_path: str) -> List[Dict[str, str]]:
    mx = MinimalXlsx(file_path)
    try:
        it = mx.iter_rows()
        rows_list: List[List[str]] = list(it)
    finally:
        mx.close()
    if not rows_list:
        return []
    headers = [h.strip() for h in rows_list[0]]
    headers = [_html.unescape(h or '') for h in headers]
    results: List[Dict[str, str]] = []
    for row in rows_list[1:]:
        row = [_html.unescape(v or '') for v in row]
        rec: Dict[str, str] = {}
        for i, v in enumerate(row):
            key = headers[i] if i < len(headers) else f"col{i+1}"
            rec[key] = v.strip() if isinstance(v, str) else str(v)
        if any(v for v in rec.values()):
            results.append(rec)
    return results


# --- Core ingestion ---

def extract_external_id_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    m = RE_EXTRACT_NUMERIC_ID.search(url)
    return m.group(1) if m else None


def guess_header_key(headers: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    cand = {c.lower() for c in candidates}
    # direct match or contains
    for h in headers:
        hl = (h or "").strip().lower()
        if hl in cand:
            return h
    for h in headers:
        hl = (h or "").strip().lower()
        if any(c in hl for c in cand):
            return h
    return None


def load_excel_mapping(src_dir: str) -> Dict[str, Dict[str, str]]:
    """Return mapping: external_id -> {url, title} from all .xlsx files under src_dir.
    Later files can override earlier blanks; prefer longer titles.
    """
    mapping: Dict[str, Dict[str, str]] = {}
    for name in sorted(os.listdir(src_dir)):
        if not name.lower().endswith('.xlsx'):
            continue
        fp = os.path.join(src_dir, name)
        try:
            rows = read_xlsx_rows(fp)
        except Exception:
            continue
        if not rows:
            continue
        headers = list(rows[0].keys())
        url_key = guess_header_key(headers, ["链接", "link", "url", "地址"]) or (headers[1] if len(headers) > 1 else headers[0])
        title_key = guess_header_key(headers, ["标题", "title", "名称", "文章标题", "name"]) or headers[0]
        source_key = guess_header_key(headers, ["作者名称", "作者", "author", "账号", "媒体", "来源"])  # optional
        time_key = guess_header_key(headers, ["发表时间", "发布时间", "时间", "date", "publish_time"])  # optional
        for rec in rows:
            url = str(rec.get(url_key, "")).strip()
            title = str(rec.get(title_key, "")).strip()
            source = str(rec.get(source_key, "")).strip() if source_key else ""
            time_raw = str(rec.get(time_key, "")).strip() if time_key else ""
            if not url:
                continue
            ext_id = extract_external_id_from_url(url)
            if not ext_id:
                continue
            cur = mapping.get(ext_id) or {}
            cur_url = cur.get('url') or ''
            cur_title = cur.get('title') or ''
            cur_source = cur.get('source') or ''
            cur_pt = cur.get('publish_time')
            if url and not cur_url:
                cur['url'] = url
            if title and (not cur_title or len(title) > len(cur_title)):
                cur['title'] = title
            if source and (not cur_source or len(source) > len(cur_source)):
                cur['source'] = source
            if time_raw:
                pt = parse_publish_time_to_epoch(time_raw)
                if pt is not None and cur_pt in (None, "", 0):
                    cur['publish_time'] = pt
            mapping[ext_id] = cur
    return mapping


@dataclass
class ImportStats:
    inserted: int = 0
    updated: int = 0
    excel_rows: int = 0
    content_files: int = 0


def import_from_excels(conn: sqlite3.Connection, mapping: Dict[str, Dict[str, str]], stats: ImportStats) -> None:
    for ext_id, meta in mapping.items():
        upsert_article_row(
            conn,
            article_id=ext_id,
            title=meta.get('title'),
            original_url=meta.get('url'),
            source=meta.get('source'),
            publish_time=meta.get('publish_time'),
        )
        stats.excel_rows += 1


def import_from_content_dirs(conn: sqlite3.Connection, src_dir: str, mapping: Dict[str, Dict[str, str]], stats: ImportStats) -> None:
    for entry in os.scandir(src_dir):
        if not entry.is_dir():
            continue
        date_dir = entry.name
        if not RE_DATE_DIR.match(date_dir):
            continue
        date_path = entry.path
        for sub in os.scandir(date_path):
            if not sub.is_dir():
                continue
            # either numeric id dir or one more nested level
            cons = [sub]
            if not RE_NUMERIC_ID.match(sub.name):
                cons = [d for d in os.scandir(sub.path) if d.is_dir() and RE_NUMERIC_ID.match(d.name)]
                if not cons:
                    continue
            for artdir in cons:
                ext_id = artdir.name
                # choose longest .txt filename
                txts = [f for f in os.listdir(artdir.path) if f.lower().endswith('.txt')]
                if not txts:
                    continue
                txts.sort(key=lambda s: len(s), reverse=True)
                chosen = txts[0]
                content_fp = os.path.join(artdir.path, chosen)
                try:
                    with open(content_fp, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read().strip()
                except Exception:
                    continue
                # title from filename; may be overridden by excel mapping if longer
                title_from_name = os.path.splitext(chosen)[0].strip()
                m = mapping.get(ext_id) or {}
                title = m.get('title')
                if title and len(title) < len(title_from_name):
                    title = title_from_name
                if not title:
                    title = title_from_name
                url = m.get('url')
                source = m.get('source')
                publish_time = m.get('publish_time')
                upsert_article_row(
                    conn,
                    article_id=ext_id,
                    title=title,
                    original_url=url,
                    source=source,
                    publish_time=publish_time,
                    content=content,
                )
                stats.content_files += 1


# --- Time parsing helpers ---

def parse_publish_time_to_epoch(value: str) -> Optional[int]:
    """Parse various date/time strings to epoch seconds (assume local time if tz missing).
    Supports:
    - 'YYYY-MM-DD HH:MM[:SS]'
    - 'YYYY/MM/DD HH:MM[:SS]'
    - ISO-like 'YYYY-MM-DDTHH:MM[:SS]'
    - 10 or 13-digit timestamps (sec/ms)
    - Excel serial date numbers (>= 20000 and < 60000 roughly)
    """
    s = (value or "").strip()
    if not s:
        return None
    # numeric timestamps
    if re.fullmatch(r"\d{10}", s):
        try:
            return int(s)
        except Exception:
            pass
    if re.fullmatch(r"\d{13}", s):
        try:
            return int(int(s) / 1000)
        except Exception:
            pass
    # excel serial number
    try:
        f = float(s)
        if 20000 <= f <= 60000:
            base = dt.datetime(1899, 12, 30)
            d = base + dt.timedelta(days=f)
            return int(_local_epoch(d))
    except Exception:
        pass
    # try common formats
    fmts = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
    ]
    for fmt in fmts:
        try:
            d = dt.datetime.strptime(s, fmt)
            return int(_local_epoch(d))
        except Exception:
            continue
    # last resort: only date
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            d = dt.datetime.strptime(s, fmt)
            return int(_local_epoch(d))
        except Exception:
            continue
    return None


def _local_epoch(d: dt.datetime) -> float:
    # Interpret naive datetime as local time
    if d.tzinfo is not None:
        return d.timestamp()
    import time as _time
    return _time.mktime(d.timetuple())


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Import AuthorFetch outputs into articles.sqlite3")
    ap.add_argument('--src', default='AuthorFetch', help='Source folder containing Excel and date subfolders')
    ap.add_argument('--db', default='articles.sqlite3', help='Target SQLite DB file path')
    args = ap.parse_args(argv)

    src_dir = os.path.abspath(args.src)
    db_path = os.path.abspath(args.db)
    if not os.path.isdir(src_dir):
        print(f"[error] source folder not found: {src_dir}")
        return 2

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        ensure_db(conn)
        stats = ImportStats()

        mapping = load_excel_mapping(src_dir)
        import_from_excels(conn, mapping, stats)
        conn.commit()

        import_from_content_dirs(conn, src_dir, mapping, stats)
        conn.commit()

        # quick summary
        cur = conn.execute('SELECT COUNT(*) FROM articles')
        total = cur.fetchone()[0]
        cur = conn.execute('SELECT COUNT(*) FROM articles WHERE content IS NOT NULL AND content <> \"\"')
        with_content = cur.fetchone()[0]
        print('--- Import Summary ---')
        print(f'Source: {src_dir}')
        print(f'Database: {db_path}')
        print(f'Excel rows processed: {stats.excel_rows}')
        print(f'Content files imported: {stats.content_files}')
        print(f'Articles total: {total}, with content: {with_content}')
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
