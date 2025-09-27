#!/usr/bin/env python3
"""
Cleanup used outputs under AuthorFetch/ after they've been imported.

What it does (safe by default):
- Scans `--src` (default: AuthorFetch)
- For each Excel (.xlsx) at the root of `--src`, parse URLs, extract numeric IDs,
  and check they all exist in `--db` (and, by default, have non-empty content).
  If all rows are present, marks the .xlsx as deletable.
- For each date folder `YYYY-MM-DD/` under `--src`, checks subfolders named by
  numeric article id. If that id exists in DB (and content non-empty by default),
  marks the subfolder removable. If a date folder becomes empty, it is removed.

Safety:
- Default is dry-run (no deletion). Use `--apply` to perform deletions.
- By default requires content to be present in DB (`--require-content`).

Stdlib-only.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
from typing import Dict, Iterable, List, Optional, Tuple


RE_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RE_NUMERIC_ID = re.compile(r"^\d{10,}$")
RE_EXTRACT_NUMERIC_ID = re.compile(r"(\d{10,})")


def extract_external_id_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    m = RE_EXTRACT_NUMERIC_ID.search(url)
    return m.group(1) if m else None


# Minimal XLSX reader (first sheet, sharedStrings)
def read_xlsx_rows(file_path: str) -> List[Dict[str, str]]:
    import zipfile
    import html as _html
    zf = zipfile.ZipFile(file_path)
    try:
        try:
            text = zf.read("xl/sharedStrings.xml").decode("utf-8", errors="ignore")
            shared: List[str] = []
            for si in re.findall(r"<si>(.*?)</si>", text, flags=re.S):
                ts = re.findall(r"<t[^>]*>(.*?)</t>", si, flags=re.S)
                joined = "".join(ts)
                shared.append(_html.unescape(joined))
        except KeyError:
            shared = []
        sheet_path = "xl/worksheets/sheet1.xml"
        data = zf.read(sheet_path).decode("utf-8", errors="ignore")
        rows: Dict[int, Dict[int, str]] = {}
        for row_block in re.findall(r"<row[^>]*>(.*?)</row>", data, flags=re.S):
            for raddr, inner in re.findall(r"<c[^>]*?r=\"([A-Z]+\d+)\"[^>]*>(.*?)</c>", row_block, flags=re.S):
                col_letters = re.findall(r"[A-Z]+", raddr)[0]
                row_digits = int(re.findall(r"\d+", raddr)[0])
                col_idx = 0
                for ch in col_letters:
                    col_idx = col_idx * 26 + (ord(ch) - ord('A') + 1)
                # extract value
                m = re.search(r"<is>.*?<t[^>]*>(.*?)</t>.*?</is>", inner, flags=re.S)
                if m:
                    val = _html.unescape(m.group(1))
                else:
                    m = re.search(r"<v>(\d+)</v>", inner)
                    if m and shared:
                        idx = int(m.group(1))
                        val = shared[idx] if 0 <= idx < len(shared) else ""
                    else:
                        m = re.search(r"<v>(.*?)</v>", inner, flags=re.S)
                        val = m.group(1).strip() if m else ""
                rows.setdefault(row_digits, {})[col_idx] = val
        if not rows:
            return []
        # build table
        max_cols = max((max(c.keys()) for c in rows.values() if c), default=0)
        ordered = [rows[r] for r in sorted(rows.keys())]
        table: List[List[str]] = []
        for rmap in ordered:
            table.append([rmap.get(ci, "") for ci in range(1, max_cols + 1)])
        if not table:
            return []
        headers = [str(h).strip() for h in table[0]]
        out: List[Dict[str, str]] = []
        for row in table[1:]:
            rec: Dict[str, str] = {}
            for i, v in enumerate(row):
                key = headers[i] if i < len(headers) else f"col{i+1}"
                rec[key] = str(v).strip()
            if any(rec.values()):
                out.append(rec)
        return out
    finally:
        try:
            zf.close()
        except Exception:
            pass


def db_has_article(conn: sqlite3.Connection, aid: str, require_content: bool) -> bool:
    if not aid:
        return False
    if require_content:
        cur = conn.execute(
            "SELECT 1 FROM articles WHERE article_id = ? AND content IS NOT NULL AND TRIM(content) <> '' LIMIT 1",
            (aid,),
        )
    else:
        cur = conn.execute("SELECT 1 FROM articles WHERE article_id = ? LIMIT 1", (aid,))
    return cur.fetchone() is not None


def collect_excel_targets(src_dir: str) -> List[str]:
    files: List[str] = []
    for name in os.listdir(src_dir):
        if name.lower().endswith('.xlsx'):
            files.append(os.path.join(src_dir, name))
    files.sort()
    return files


def collect_date_dirs(src_dir: str) -> List[str]:
    out: List[str] = []
    for e in os.scandir(src_dir):
        if e.is_dir() and RE_DATE_DIR.match(e.name):
            out.append(e.path)
    out.sort()
    return out


def cleanup(src: str, db: str, apply: bool, require_content: bool) -> None:
    conn = sqlite3.connect(db)
    try:
        # 1) Excel files
        xlsx_paths = collect_excel_targets(src)
        deletable_excels: List[str] = []
        for xp in xlsx_paths:
            try:
                rows = read_xlsx_rows(xp)
            except Exception as e:
                print(f"[xlsx] skip {os.path.basename(xp)}: read failed: {e}")
                continue
            ids: List[str] = []
            if rows:
                headers = list(rows[0].keys())
                # Try to find a URL-like column
                url_key = None
                for k in headers:
                    kl = k.lower()
                    if any(s in kl for s in ('url', 'link', '链接', '地址')):
                        url_key = k
                        break
                if url_key is None and headers:
                    url_key = headers[-1]
                for rec in rows:
                    ext_id = extract_external_id_from_url(str(rec.get(url_key, '')))
                    if ext_id:
                        ids.append(ext_id)
            if ids and all(db_has_article(conn, i, require_content) for i in ids):
                deletable_excels.append(xp)
                print(f"[xlsx] ready: {os.path.basename(xp)} (rows: {len(ids)})")
            else:
                print(f"[xlsx] keep : {os.path.basename(xp)} (ok/total: {sum(db_has_article(conn,i,require_content) for i in ids)}/{len(ids)})")

        # 2) Date folders
        date_dirs = collect_date_dirs(src)
        deletable_article_dirs: List[str] = []
        maybe_empty_dates: List[str] = []
        for dd in date_dirs:
            for sub in os.scandir(dd):
                if not sub.is_dir():
                    continue
                # allow one more nesting
                candidates = [sub]
                if not RE_NUMERIC_ID.match(sub.name):
                    candidates = [d for d in os.scandir(sub.path) if d.is_dir() and RE_NUMERIC_ID.match(d.name)]
                for artdir in candidates:
                    if db_has_article(conn, artdir.name, require_content):
                        deletable_article_dirs.append(artdir.path)
                        print(f"[dir ] ready: {os.path.relpath(artdir.path, src)}")
            maybe_empty_dates.append(dd)

    finally:
        conn.close()

    # Apply deletions if requested
    if apply:
        for p in deletable_article_dirs:
            try:
                shutil.rmtree(p)
            except Exception as e:
                print(f"[delete dir ] failed: {p}: {e}")
        # remove empty date dirs
        for dd in maybe_empty_dates:
            try:
                if os.path.isdir(dd) and not os.listdir(dd):
                    os.rmdir(dd)
            except Exception:
                pass
        for xp in deletable_excels:
            try:
                os.remove(xp)
            except Exception as e:
                print(f"[delete xlsx] failed: {xp}: {e}")
        print("--- Cleanup applied ---")
        print(f"Removed article dirs: {len(deletable_article_dirs)}; Excel files: {len(deletable_excels)}")
    else:
        print("--- Dry Run ---")
        print(f"Would remove article dirs: {len(deletable_article_dirs)}; Excel files: {len(deletable_excels)}")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Delete used AuthorFetch Excel and article folders after import")
    ap.add_argument('--src', default='AuthorFetch', help='Source folder with Excel and date-based subfolders')
    ap.add_argument('--db', default='articles.sqlite3', help='SQLite DB to verify presence')
    ap.add_argument('--apply', action='store_true', help='Actually delete files (default dry-run)')
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument('--require-content', dest='require_content', action='store_true', help='Only delete if article has non-empty content in DB (default)')
    grp.add_argument('--allow-empty-content', dest='require_content', action='store_false', help='Allow deletion even if content is empty')
    ap.set_defaults(require_content=True)
    args = ap.parse_args(argv)

    src = os.path.abspath(args.src)
    db = os.path.abspath(args.db)
    if not os.path.isdir(src):
        print(f"[error] source not found: {src}")
        return 2
    if not os.path.exists(db):
        print(f"[error] database not found: {db}")
        return 2
    cleanup(src, db, args.apply, args.require_content)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
