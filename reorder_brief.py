from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

CATEGORY_RULES: Sequence[Tuple[str, Tuple[str, ...]]] = (
    (
        "市委教委",
        (
            "市委",
            "市委教育工委",
            "市教委",
            "教工委",
            "教育工委",
            "教育委员会",
            "首都教育两委",
            "教育两委",
        ),
    ),
    (
        "中小学",
        (
            "中小学",
            "小学",
            "初中",
            "高中",
            "义务教育",
            "基础教育",
            "幼儿园",
            "幼儿",
            "托育",
            "K12",
            "班主任",
            "青少年",
            "少儿",
            "少年"
        ),
    ),
    (
        "高校",
        (
            "高校",
            "大学",
            "学院",
            "本科",
            "研究生",
            "硕士",
            "博士",
        ),
    ),
)

DEFAULT_CATEGORY = "其他"
CATEGORY_ORDER: Tuple[str, ...] = tuple(rule[0] for rule in CATEGORY_RULES) + (DEFAULT_CATEGORY,)


def classify_category(*parts: str) -> str:
    haystack = " ".join(filter(None, parts)).lower()
    for category, keywords in CATEGORY_RULES:
        for keyword in keywords:
            if keyword.lower() in haystack:
                return category
    return DEFAULT_CATEGORY


def split_sections(text: str) -> Tuple[str, List[Tuple[str, List[str]]]]:
    pattern = re.compile(r"(^【[^】]+】)", re.MULTILINE)
    matches = list(pattern.finditer(text))

    if not matches:
        header = text.strip("\n")
        return header, []

    header = text[: matches[0].start()].strip("\n")
    sections: List[Tuple[str, List[str]]] = []

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        heading = match.group(1).strip()
        body = text[start:end].strip("\n")
        entries = _split_entries(body)
        sections.append((heading, entries))

    return header, sections


def _split_entries(body: str) -> List[str]:
    entries: List[str] = []
    buffer: List[str] = []
    for line in body.splitlines():
        if line.strip():
            buffer.append(line.rstrip())
            continue
        if buffer:
            entries.append("\n".join(buffer).strip("\n"))
            buffer = []
    if buffer:
        entries.append("\n".join(buffer).strip("\n"))
    return entries


def reorder_entries(entries: Iterable[str]) -> Tuple[List[str], Dict[str, int]]:
    buckets: Dict[str, List[str]] = {category: [] for category in CATEGORY_ORDER}
    counts: Dict[str, int] = defaultdict(int)

    for entry in entries:
        category = classify_category(entry)
        buckets[category].append(entry)
        counts[category] += 1

    ordered: List[str] = []
    for category in CATEGORY_ORDER:
        ordered.extend(buckets[category])
    return ordered, counts


def build_output(header: str, sections: List[Tuple[str, List[str]]]) -> str:
    parts: List[str] = []
    if header.strip():
        parts.append(header.strip())
    for heading, entries in sections:
        parts.append(heading.strip())
        parts.extend(entry.strip() for entry in entries)
    return "\n\n".join(parts).strip() + "\n"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reorder brief entries in a text file by category order.",
    )
    parser.add_argument("input", type=Path, help="Path to the source text file (e.g. 1008ZM.txt).")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Optional output path. Defaults to overwriting the input file.",
    )
    return parser.parse_args(argv)

def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    input_path = args.input

    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")

    text = input_path.read_text(encoding="utf-8")
    header, sections = split_sections(text)

    if not sections:
        raise ValueError("No sections found. Make sure the file contains headings like 【舆情速览】.")

    reordered_sections: List[Tuple[str, List[str]]] = []
    for heading, entries in sections:
        normalized_heading = heading.replace("�", "").replace("?", "")
        if "舆情参考" in normalized_heading or "舆情参" in normalized_heading:
            counts: Dict[str, int] = defaultdict(int)
            for entry in entries:
                counts[classify_category(entry)] += 1
            reordered_sections.append((heading, entries))
        else:
            ordered_entries, counts = reorder_entries(entries)
            reordered_sections.append((heading, ordered_entries))
        breakdown = ", ".join(f"{category}:{counts.get(category, 0)}" for category in CATEGORY_ORDER)
        print(f"{heading} -> {breakdown}")

    output_path = args.output or input_path

    output_text = build_output(header, reordered_sections)
    output_path.write_text(output_text, encoding="utf-8")

    print(f"Wrote reordered content to: {output_path}")


if __name__ == "__main__":
    main()
