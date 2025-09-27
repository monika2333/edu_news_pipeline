#!/usr/bin/env python3
"""Import AuthorFetch outputs into Supabase raw_articles table."""
from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
import sys
try:
    from tools.supabase_adapter import ArticleInput, get_supabase_adapter
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parent))
    from supabase_adapter import ArticleInput, get_supabase_adapter  # type: ignore
RE_NUMERIC_ID = re.compile(r"^\d{10,}$")
RE_EXTRACT_NUMERIC_ID = re.compile(r"(\d{10,})")


@dataclass
class ImportStats:
    excel_rows: int = 0
    content_files: int = 0


@dataclass
class MappingEntry:
    article_id: str
    title: Optional[str]
    url: Optional[str]
    source: Optional[str]
    publish_time: Optional[int]
    raw: Dict[str, str]


@dataclass
class ExcelMapping:
    entries: Dict[str, MappingEntry]


# ---------------------------------------------------------------------------
# Excel parsing (reuse logic from SQLite importer)
# ---------------------------------------------------------------------------

def load_excel_mapping(src_dir: str) -> ExcelMapping:
    mapping: Dict[str, MappingEntry] = {}
    for entry in os.scandir(src_dir):
        if not entry.is_file() or not entry.name.lower().endswith(".xlsx"):
            continue
        rows = read_xlsx_rows(entry.path)
        for row in rows:
            ext_id = extract_external_id(row)
            if not ext_id:
                continue
            publish_time = parse_publish_time_to_epoch(row.get("publish_time"))
            mapping[ext_id] = MappingEntry(
                article_id=ext_id,
                title=row.get("title"),
                url=row.get("url") or row.get("link"),
                source=row.get("source"),
                publish_time=publish_time,
                raw=row,
            )
    return ExcelMapping(mapping)


def extract_external_id(row: Dict[str, str]) -> Optional[str]:
    for key in ("article_id", "id", "external_id"):
        value = str(row.get(key, "")).strip()
        if RE_NUMERIC_ID.fullmatch(value):
            return value
    for key in ("url", "link"):
        value = str(row.get(key, "")).strip()
        match = RE_EXTRACT_NUMERIC_ID.search(value)
        if match:
            return match.group(1)
    return None


def read_xlsx_rows(file_path: str) -> List[Dict[str, str]]:
    import html
    import zipfile

    with zipfile.ZipFile(file_path) as zf:
        try:
            shared_xml = zf.read("xl/sharedStrings.xml").decode("utf-8", errors="ignore")
            shared_strings: List[str] = []
            for chunk in re.findall(r"<si>(.*?)</si>", shared_xml, flags=re.S):
                pieces = re.findall(r"<t[^>]*>(.*?)</t>", chunk, flags=re.S)
                shared_strings.append(html.unescape("".join(pieces)))
        except KeyError:
            shared_strings = []
        data = zf.read("xl/worksheets/sheet1.xml").decode("utf-8", errors="ignore")
        rows: Dict[int, Dict[int, str]] = {}
        for row_block in re.findall(r"<row[^>]*>(.*?)</row>", data, flags=re.S):
            for cell_ref, inner in re.findall(r"<c[^>]*?r=\"([A-Z]+\d+)\"[^>]*>(.*?)</c>", row_block, flags=re.S):
                col_letters = re.findall(r"[A-Z]+", cell_ref)[0]
                row_idx = int(re.findall(r"\d+", cell_ref)[0])
                col_index = 0
                for ch in col_letters:
                    col_index = col_index * 26 + (ord(ch) - ord("A") + 1)
                value = ""
                text_match = re.search(r"<is>.*?<t[^>]*>(.*?)</t>.*?</is>", inner, flags=re.S)
                if text_match:
                    value = html.unescape(text_match.group(1))
                else:
                    v_match = re.search(r"<v>(.*?)</v>", inner, flags=re.S)
                    if v_match:
                        raw_value = v_match.group(1)
                        if shared_strings and raw_value.isdigit():
                            idx = int(raw_value)
                            value = shared_strings[idx] if 0 <= idx < len(shared_strings) else raw_value
                        else:
                            value = raw_value
                rows.setdefault(row_idx, {})[col_index] = value
        if not rows:
            return []
        ordered = [rows[idx] for idx in sorted(rows.keys())]
        max_cols = max((max(row.keys()) for row in ordered if row), default=0)
        table: List[List[str]] = []
        for mapping_row in ordered:
            table.append([mapping_row.get(ci, "") for ci in range(1, max_cols + 1)])
        if not table:
            return []
        headers = [str(h).strip() for h in table[0]]
        result: List[Dict[str, str]] = []
        for row_values in table[1:]:
            rec: Dict[str, str] = {}
            for idx, value in enumerate(row_values):
                key = headers[idx] if idx < len(headers) else f"col{idx+1}"
                rec[key] = str(value).strip()
            if any(rec.values()):
                result.append(rec)
        return result


# ---------------------------------------------------------------------------
# Content directory handling
# ---------------------------------------------------------------------------

def parse_publish_time_to_epoch(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    s = value.strip()
    if not s:
        return None
    if re.fullmatch(r"\d{10}", s):
        try:
            return int(s)
        except Exception:
            return None
    if re.fullmatch(r"\d{13}", s):
        try:
            return int(int(s) / 1000)
        except Exception:
            return None
    from datetime import datetime
    from datetime import timedelta

    try:
        serial = float(s)
        if 20000 <= serial <= 60000:
            base = datetime(1899, 12, 30)
            dt = base + timedelta(days=serial)
            return int(dt.timestamp())
    except Exception:
        pass
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            return int(dt.timestamp())
        except Exception:
            continue
    return None


def import_from_excels(adapter, mapping: ExcelMapping, stats: ImportStats) -> None:
    for entry in mapping.entries.values():
        stats.excel_rows += 1
        record = ArticleInput(
            article_id=entry.article_id,
            title=entry.title,
            source=entry.source,
            publish_time=entry.publish_time,
            original_url=entry.url,
            content=None,
            raw_payload=entry.raw,
            metadata={"import_source": "AuthorFetch"},
        )
        adapter.upsert_article(record)


def pick_longest_text_file(dir_path: Path) -> Optional[str]:
    longest_content = None
    longest_len = -1
    for entry in dir_path.glob("*.txt"):
        try:
            text = entry.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            continue
        if len(text) > longest_len:
            longest_len = len(text)
            longest_content = text
    return longest_content


def import_from_content_dirs(adapter, src_dir: str, mapping: ExcelMapping, stats: ImportStats) -> None:
    base = Path(src_dir)
    if not base.exists():
        return
    for date_dir in base.iterdir():
        if not date_dir.is_dir() or not RE_DATE_DIR.match(date_dir.name):
            continue
        for article_dir in date_dir.iterdir():
            if not article_dir.is_dir():
                continue
            candidates = [article_dir]
            if not RE_NUMERIC_ID.match(article_dir.name):
                candidates = [sub for sub in article_dir.iterdir() if sub.is_dir() and RE_NUMERIC_ID.match(sub.name)]
            for candidate in candidates:
                article_id = candidate.name
                mapping_entry = mapping.entries.get(article_id)
                content = pick_longest_text_file(candidate)
                if not content:
                    continue
                stats.content_files += 1
                title = mapping_entry.title if mapping_entry else None
                source = mapping_entry.source if mapping_entry else None
                publish_time = mapping_entry.publish_time if mapping_entry else None
                url = mapping_entry.url if mapping_entry else None
                raw_payload = mapping_entry.raw if mapping_entry else {}
                record = ArticleInput(
                    article_id=article_id,
                    title=title,
                    source=source,
                    publish_time=publish_time,
                    original_url=url,
                    content=content,
                    raw_payload=raw_payload,
                    metadata={"import_source": "AuthorFetch"},
                )
                adapter.upsert_article(record)


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import AuthorFetch outputs into Supabase")
    parser.add_argument("--src", default="AuthorFetch", help="AuthorFetch source directory")
    parser.add_argument("--db", default="articles.sqlite3", help="SQLite compatibility flag (ignored in Supabase mode)")
    return parser.parse_args()


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args()
    src_dir = os.path.abspath(args.src)
    if not os.path.isdir(src_dir):
        print(f"[error] source folder not found: {src_dir}")
        return 2

    adapter = get_supabase_adapter()
    stats = ImportStats()
    mapping = load_excel_mapping(src_dir)
    import_from_excels(adapter, mapping, stats)
    import_from_content_dirs(adapter, src_dir, mapping, stats)

    total, with_content = adapter.get_article_counts()
    print("--- Supabase Import Summary ---")
    print(f"Source: {src_dir}")
    print(f"Excel rows processed: {stats.excel_rows}")
    print(f"Content files imported: {stats.content_files}")
    print(f"Articles total: {total}, with content: {with_content}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
