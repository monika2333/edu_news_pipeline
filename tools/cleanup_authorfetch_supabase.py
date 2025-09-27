#!/usr/bin/env python3
"""Cleanup AuthorFetch outputs using Supabase presence check."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

try:
    from tools.supabase_adapter import get_supabase_adapter
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parent))
    from supabase_adapter import get_supabase_adapter  # type: ignore

PARENT_DIR = Path(__file__).resolve().parents[1]
if str(PARENT_DIR) not in sys.path:
    sys.path.append(str(PARENT_DIR))
from tools.import_authorfetch_supabase import extract_external_id, read_xlsx_rows

RE_DATE_DIR = re.compile(r"^\\d{4}-\\d{2}-\\d{2}$")
RE_NUMERIC_ID = re.compile(r"^\\d{10,}$")
RE_EXTRACT_NUMERIC_ID = re.compile(r"(\\d{10,})")


def collect_excel_targets(src_dir: Path) -> List[Path]:
    return sorted([p for p in src_dir.iterdir() if p.is_file() and p.suffix.lower() == ".xlsx"], key=lambda p: p.name)


def collect_date_dirs(src_dir: Path) -> List[Path]:
    return sorted([p for p in src_dir.iterdir() if p.is_dir() and RE_DATE_DIR.match(p.name)], key=lambda p: p.name)


def extract_ids_from_excel(file_path: Path) -> List[str]:
    rows = read_xlsx_rows(str(file_path))
    ids: List[str] = []
    for row in rows:
        ext_id = extract_external_id(row)
        if ext_id:
            ids.append(ext_id)
    return ids


def collect_article_ids_from_dir(article_dir: Path) -> List[str]:
    if RE_NUMERIC_ID.match(article_dir.name):
        return [article_dir.name]
    ids: List[str] = []
    for sub in article_dir.iterdir():
        if sub.is_dir() and RE_NUMERIC_ID.match(sub.name):
            ids.append(sub.name)
    return ids


def cleanup(src: Path, apply: bool, require_content: bool) -> None:
    adapter = get_supabase_adapter()

    # Process Excel files
    excel_paths = collect_excel_targets(src)
    deletable_excels: List[Path] = []
    for excel in excel_paths:
        try:
            ids = extract_ids_from_excel(excel)
        except Exception as exc:
            print(f"[xlsx] skip {excel.name}: {exc}")
            continue
        status = adapter.articles_exist(ids, require_content=require_content)
        ok = sum(1 for value in status.values() if value)
        if ids and ok == len(ids):
            deletable_excels.append(excel)
            print(f"[xlsx] ready: {excel.name} (rows: {len(ids)})")
        else:
            print(f"[xlsx] keep : {excel.name} (ok/total: {ok}/{len(ids)})")

    # Process date directories
    deletable_dirs: List[Path] = []
    maybe_empty_dates: List[Path] = []
    for date_dir in collect_date_dirs(src):
        for candidate in date_dir.iterdir():
            if not candidate.is_dir():
                continue
            article_ids = collect_article_ids_from_dir(candidate)
            if not article_ids:
                continue
            status = adapter.articles_exist(article_ids, require_content=require_content)
            if article_ids and all(status.get(aid) for aid in article_ids):
                deletable_dirs.append(candidate)
                print(f"[dir ] ready: {candidate.relative_to(src)}")
        maybe_empty_dates.append(date_dir)

    if apply:
        for directory in deletable_dirs:
            try:
                shutil.rmtree(directory)
            except Exception as exc:
                print(f"[delete dir ] failed: {directory}: {exc}")
        for date_dir in maybe_empty_dates:
            try:
                if not any(date_dir.iterdir()):
                    date_dir.rmdir()
            except Exception:
                pass
        for excel in deletable_excels:
            try:
                excel.unlink()
            except Exception as exc:
                print(f"[delete xlsx] failed: {excel}: {exc}")
        print("--- Cleanup applied ---")
        print(f"Removed article dirs: {len(deletable_dirs)}; Excel files: {len(deletable_excels)}")
    else:
        print("--- Dry Run ---")
        print(f"Would remove article dirs: {len(deletable_dirs)}; Excel files: {len(deletable_excels)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cleanup AuthorFetch outputs using Supabase data")
    parser.add_argument("--src", default="AuthorFetch", help="AuthorFetch source directory")
    parser.add_argument("--apply", action="store_true", help="Apply deletions (default: dry-run)")
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--require-content", dest="require_content", action="store_true", help="Require article content before deletion (default)")
    grp.add_argument("--allow-empty-content", dest="require_content", action="store_false", help="Allow deletion even if content is empty")
    parser.set_defaults(require_content=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    src = Path(args.src).resolve()
    if not src.is_dir():
        print(f"[error] source not found: {src}")
        return 2
    cleanup(src, args.apply, args.require_content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
