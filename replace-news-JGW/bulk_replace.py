#!/usr/bin/env python3
"""
Batch cleanup for text files.

Replacements:
- Remove the literal prefix "来源："
- Turn ASCII parentheses into full-width Chinese parentheses
- Remove book title brackets that appear inside parentheses
- Trim spaces that appear immediately before closing Chinese parentheses
- Replace "北京日报客户端" with "北京日报"
- Replace "《新京报》官方账号" with "新京报"
"""

import argparse
import pathlib
import re
from typing import Tuple

ASCII_TO_CN_PARENS = str.maketrans({"(": "（", ")": "）"})

CLOSING_PAREN_SPACE_RE = re.compile(r'(?<=\S)\s+(?=）)')
PARENS_CONTENT_RE = re.compile(r'（([^）]*)）')


def remove_book_title_marks(text: str) -> str:
    """Strip 《》 from inside Chinese parentheses."""
    def repl(match: re.Match) -> str:
        inner = match.group(1)
        if "《" not in inner and "》" not in inner:
            return match.group(0)
        cleaned = inner.replace("《", "").replace("》", "")
        return f"（{cleaned}）"

    return PARENS_CONTENT_RE.sub(repl, text)


def transform_text(text: str) -> Tuple[str, bool]:
    """Apply all replacements; return updated text and a changed flag."""
    original = text
    text = text.replace("来源：", "")
    text = text.replace("北京日报客户端", "北京日报")
    text = text.replace("央视新闻客户端", "央视新闻")
    text = text.replace("@央视新闻", "央视新闻")
    text = text.replace("《北京日报》官方账号", "北京日报")
    text = text.replace("《新京报》官方账号", "新京报")
    text = text.replace("新黄河客户端", "新黄河")
    text = text.replace("人民日报客户端", "人民日报")
    text = text.replace("北晚在线", "北京晚报")
    text = text.replace("已获", "获")
    text = text.replace("次评论", "条评论")
    text = text.translate(ASCII_TO_CN_PARENS)
    text = remove_book_title_marks(text)
    text = CLOSING_PAREN_SPACE_RE.sub("", text)
    return text, text != original


def process_file(path: pathlib.Path, dry_run: bool) -> bool:
    """Update a single file; return True when a change was made."""
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"[skip] {path} (encoding not utf-8)")
        return False

    new_content, changed = transform_text(content)
    if not changed:
        print(f"[ok]   {path} (no change)")
        return False

    if dry_run:
        print(f"[dry]  {path} (would update)")
        return True

    path.write_text(new_content, encoding="utf-8")
    print(f"[edit] {path} (updated)")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch replace text inside .txt files."
    )
    parser.add_argument(
        "root",
        nargs="?",
        type=pathlib.Path,
        default=pathlib.Path.cwd(),
        help="Directory to scan for .txt files (defaults to current directory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report files that would change without writing",
    )
    args = parser.parse_args()

    if not args.root.exists():
        parser.error(f"Path does not exist: {args.root}")

    txt_files = sorted(args.root.rglob("*.txt"))
    if not txt_files:
        print("No .txt files found.")
        return

    changed_files = 0
    for file_path in txt_files:
        changed = process_file(file_path, args.dry_run)
        if changed:
            changed_files += 1

    print(
        f"\nDone. {'Would change' if args.dry_run else 'Changed'} "
        f"{changed_files} file(s); scanned {len(txt_files)} total."
    )


if __name__ == "__main__":
    main()
