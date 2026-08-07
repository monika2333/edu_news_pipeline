from __future__ import annotations

import argparse
from typing import Any, Optional

from src.adapters.db_postgres_core import get_adapter
from src.adapters.llm_source import MAX_LLM_SOURCE_LENGTH

SOURCE_FIELDS = (
    "news_summaries.llm_source",
    "manual_reviews.manual_llm_source",
    "shift_reviews.manual_llm_source",
)
MANUAL_LLM_SOURCE_MAX_LENGTH = 500


def _print_matches(matches: dict[str, list[dict[str, Any]]]) -> None:
    for field_name in SOURCE_FIELDS:
        rows = matches.get(field_name, [])
        print(f"[{field_name}] matched={len(rows)}")
        for row in rows:
            shift_note = f" shift_id={row['shift_id']}" if row.get("shift_id") else ""
            print(
                f"  article_id={row.get('article_id', '')}{shift_note} "
                f"length={row.get('character_length', 0)}"
            )


def clean_oversized_llm_sources(*, apply: bool) -> int:
    adapter = get_adapter()
    matches = adapter.news_summaries.inspect_oversized_source_values(
        MAX_LLM_SOURCE_LENGTH,
        MANUAL_LLM_SOURCE_MAX_LENGTH,
    )
    _print_matches(matches)

    news_matches = matches.get("news_summaries.llm_source", [])
    if not apply:
        print(
            "[cleanup] dry-run only; rerun with --apply to set oversized "
            "news_summaries.llm_source values to NULL"
        )
        return len(news_matches)

    updated = adapter.news_summaries.clear_oversized_llm_sources(
        MAX_LLM_SOURCE_LENGTH
    )
    remaining = adapter.news_summaries.inspect_oversized_source_values(
        MAX_LLM_SOURCE_LENGTH,
        MANUAL_LLM_SOURCE_MAX_LENGTH,
    )["news_summaries.llm_source"]
    if remaining:
        raise RuntimeError(
            f"Cleanup verification failed: {len(remaining)} oversized values remain"
        )
    print(f"[cleanup] cleared={updated} threshold={MAX_LLM_SOURCE_LENGTH}")
    return updated


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect oversized source values and optionally clear only "
            "news_summaries.llm_source"
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Set matching news_summaries.llm_source values to NULL",
    )
    args = parser.parse_args(argv)
    clean_oversized_llm_sources(apply=args.apply)


if __name__ == "__main__":
    main()
