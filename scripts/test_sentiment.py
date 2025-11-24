import argparse
import json
import sys
from pathlib import Path

from src.adapters.sentiment_classifier import classify_sentiment
from src.config import load_environment


def _read_text(args: argparse.Namespace) -> str:
    if args.text:
        return args.text.strip()
    if args.file:
        return Path(args.file).read_text(encoding="utf-8").strip()
    piped = sys.stdin.read().strip()
    if piped:
        return piped
    raise SystemExit("No input text provided. Use --text, --file or pipe content.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run standalone sentiment classification for a news text.")
    parser.add_argument("-t", "--text", help="News text to classify.")
    parser.add_argument(
        "-f",
        "--file",
        help="Path to a text file containing the news to classify.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Optional override for request timeout (seconds). Defaults to settings.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print full model response instead of the compact summary.",
    )
    args = parser.parse_args()

    load_environment()
    content = _read_text(args)
    result = classify_sentiment(content, timeout=args.timeout)
    output = result if args.raw else {"label": result["label"], "confidence": result["confidence"]}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
