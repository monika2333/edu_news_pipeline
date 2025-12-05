from __future__ import annotations

import argparse
import re
from pathlib import Path


def read_text_with_fallback(path: Path) -> tuple[str, str]:
    """Read text with a best-effort encoding fallback chain."""
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", b"", 0, 1, "Unable to decode file with known encodings")


def ensure_section_spacing(text: str) -> str:
    """Ensure there is exactly one blank line between sections."""
    lines = text.splitlines()
    normalized: list[str] = []
    for line in lines:
        if line.startswith("★ ") or line.startswith("说明"):
            while normalized and normalized[-1] == "":
                normalized.pop()
            if normalized:
                normalized.append("")
        if line.strip():
            normalized.append(line)
            continue
        if normalized and normalized[-1] != "":
            normalized.append("")
    return "\n".join(normalized)


def normalize_text(content: str) -> str:
    """Apply the required cleanup operations."""
    line_ending = "\r\n" if "\r\n" in content else "\n"
    text = content.replace("\r\n", "\n")
    text = re.sub(r'(\*\*[^\n]+\*\*)\n\s*\n', r"\1\n", text)
    text = re.sub(r'^\s*---\s*\n?', "", text, flags=re.MULTILINE)
    text = re.sub(r'"([^"\n]*)"', r"“\1”", text)
    text = text.replace("**", "")
    text = re.sub(r"\d+次收藏、", "", text)
    text = ensure_section_spacing(text)
    text = text.rstrip("\n") + "\n"
    return text.replace("\n", line_ending)


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize summary formatting and punctuation.")
    parser.add_argument("--dry-run", action="store_true", help="Print the would-be result without writing the file.")
    args = parser.parse_args()

    target_input = input("请输入需要处理的文件路径：").strip()
    if not target_input:
        raise SystemExit("未提供有效的文件路径。")

    target_path = Path(target_input)
    text, encoding = read_text_with_fallback(target_path)
    normalized = normalize_text(text)

    if args.dry_run:
        print(normalized)
        return

    target_path.write_text(normalized, encoding=encoding)
    print(f"Updated {target_path} using {encoding} encoding.")


if __name__ == "__main__":
    main()
