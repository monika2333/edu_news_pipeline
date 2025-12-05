#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path
from typing import Iterable

LINK_PATTERN = re.compile(r"http://xhslink\.com/o/[A-Za-z0-9]+")


def extract_xhslink_urls(source: Path) -> list[str]:
    text = source.read_text(encoding="utf-8")
    seen: set[str] = set()
    ordered_links: list[str] = []
    for match in LINK_PATTERN.findall(text):
        if match in seen:
            continue
        seen.add(match)
        ordered_links.append(match)
    return ordered_links


def write_links(links: Iterable[str], output_path: Path) -> None:
    output_path.write_text("\n".join(links), encoding="utf-8")
    print(f"已写入 {len(list(links))} 条链接到 {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract unique xhslink URLs from a text file.")
    parser.add_argument(
        "source",
        nargs="?",
        default="input_task.txt",
        help="Path to the input text file (default: input_task.txt).",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory to store the generated links file (default: current directory).",
    )
    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.exists():
        raise SystemExit(f"输入文件不存在: {source_path}")

    links = extract_xhslink_urls(source_path)
    if not links:
        print("未在文件中找到任何小红书链接。")
        return

    print(f"找到 {len(links)} 条小红书链接：")
    for idx, link in enumerate(links, 1):
        print(f"{idx}. {link}")

    date_str = dt.datetime.now().strftime("%Y%m%d")
    output_filename = f"{date_str}-xiaohongshu-links.txt"
    output_path = Path(args.output_dir) / output_filename
    output_path.write_text("\n".join(links), encoding="utf-8")
    print(f"\n链接列表已保存至 {output_path}")


if __name__ == "__main__":
    main()
