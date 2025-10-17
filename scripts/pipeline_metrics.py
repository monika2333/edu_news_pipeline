from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from src.adapters.db import get_adapter
from src.config import get_settings, load_environment
from src.workers import log_info


def format_ratio(count: int, total: int) -> str:
    if total <= 0:
        return "0.00%"
    return f"{(count / total) * 100:.2f}%"


def collect_metrics(threshold: float) -> Dict[str, Any]:
    adapter = get_adapter()
    metrics = adapter.gather_pipeline_metrics(sentiment_threshold=threshold)
    raw = metrics.get("raw_articles", {})
    total_raw = int(raw.get("total", 0))
    duplicate_count = int(raw.get("duplicate_count", 0))
    master_count = int(raw.get("master_count", 0))
    sentiment_scored = int(raw.get("sentiment_scored", 0))
    sentiment_low_conf = int(raw.get("sentiment_low_confidence", 0))
    raw["duplicate_ratio"] = format_ratio(duplicate_count, total_raw)
    raw["master_ratio"] = format_ratio(master_count, total_raw)
    raw["sentiment_coverage"] = format_ratio(sentiment_scored, total_raw)
    raw["sentiment_low_ratio"] = format_ratio(sentiment_low_conf, max(sentiment_scored, 1))
    metrics["raw_articles"] = raw
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect pipeline health metrics.")
    parser.add_argument(
        "--sentiment-threshold",
        type=float,
        help="Override confidence阈值进行低置信占比统计（默认读取环境配置）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出结果，便于集成监控。",
    )
    args = parser.parse_args()

    load_environment()
    settings = get_settings()
    threshold = args.sentiment_threshold or settings.sentiment_confidence_threshold
    metrics = collect_metrics(threshold)

    if args.json:
        print(json.dumps({"threshold": threshold, "metrics": metrics}, ensure_ascii=False, indent=2))
    else:
        log_info("metrics", f"threshold={threshold}")
        raw = metrics.get("raw_articles", {})
        log_info(
            "metrics",
            "raw_articles: total={total} master={master_count} ({master_ratio}) "
            "duplicate={duplicate_count} ({duplicate_ratio})".format(**raw),
        )
        log_info(
            "metrics",
            "sentiment: scored={sentiment_scored} ({sentiment_coverage}) "
            "positive={sentiment_positive} negative={sentiment_negative} "
            "low_confidence={sentiment_low_confidence} ({sentiment_low_ratio})".format(**raw),
        )
        summaries = metrics.get("news_summaries", {})
        log_info(
            "metrics",
            "news_summaries: total={total} scored={scored_count} high_score={high_score_count} beijing_marked={beijing_marked}".format(
                **summaries
            ),
        )


if __name__ == "__main__":
    main()
