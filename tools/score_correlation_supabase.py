#!/usr/bin/env python3
"""Score Supabase summaries for education relevance."""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, Optional, Tuple

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if (val.startswith('\"') and val.endswith('\"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


def load_environment() -> None:
    for candidate in (REPO_ROOT / ".env", REPO_ROOT / ".env.local", REPO_ROOT / "config" / "abstract.env"):
        _load_env_file(candidate)


load_environment()

try:
    from tools.supabase_adapter import SummaryForScoring, get_supabase_adapter
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parent))
    from supabase_adapter import SummaryForScoring, get_supabase_adapter  # type: ignore

BASE_URL = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
API_KEY = os.getenv("SILICONFLOW_API_KEY")
MODEL = os.getenv("MODEL_NAME", "Qwen/Qwen3-8B")
ENABLE_THINKING = os.getenv("ENABLE_THINKING", "true").lower() in ("1", "true", "yes", "y")
DEFAULT_CONCURRENCY = int(os.getenv("CONCURRENCY", "5") or "5")

THINKING_SUPPORTED_PREFIXES = (
    "Qwen/Qwen3-8B",
    "Qwen/Qwen3-14B",
    "Qwen/Qwen3-32B",
    "Qwen/Qwen3-30B-A3B",
    "Qwen/Qwen3-235B-A22B",
    "tencent/Hunyuan-A13B-Instruct",
    "zai-org/GLM-4.5V",
    "deepseek-ai/DeepSeek-V3.1",
    "Pro/deepseek-ai/DeepSeek-V3.1",
)


def supports_thinking(model: str) -> bool:
    return any(model.startswith(prefix) for prefix in THINKING_SUPPORTED_PREFIXES)


def call_score_api(text: str, retries: int = 4) -> str:
    if not API_KEY:
        raise RuntimeError("缺少环境变量 SILICONFLOW_API_KEY")
    url = f"{BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    prompt = (
        "请判断下面的内容和教育的相关程度，输出数字0-100，其中0为完全不相关，100为完全相关："
        + text
    )
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 16,
    }
    if ENABLE_THINKING and supports_thinking(MODEL):
        payload["enable_thinking"] = True
    backoff = 1.0
    last_error: Optional[Exception] = None
    for _ in range(retries):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(backoff)
                backoff = min(backoff * 2, 8)
                continue
            last_error = RuntimeError(f"API {resp.status_code}: {resp.text[:120]}")
        except Exception as exc:
            last_error = exc
        time.sleep(backoff)
    raise last_error or RuntimeError("相关度评分失败")


def parse_score(text: str) -> Optional[int]:
    if not text:
        return None
    match = re.search(r"(\d{1,3})", text)
    if not match:
        return None
    value = int(match.group(1))
    if value < 0:
        value = 0
    if value > 100:
        value = 100
    return value


def score(concurrency: int, limit: Optional[int]) -> None:
    adapter = get_supabase_adapter()
    rows = adapter.fetch_summaries_for_scoring(limit)
    if not rows:
        print("No Supabase summaries require scoring.")
        return
    total = len(rows)
    ok = 0
    failed = 0

    def worker(item: SummaryForScoring) -> Tuple[SummaryForScoring, Optional[int]]:
        text = item.summary or item.content
        if not text.strip():
            return item, None
        raw_score = call_score_api(text)
        return item, parse_score(raw_score)

    if concurrency == 1:
        for row in rows:
            try:
                _, score_value = worker(row)
                adapter.update_correlation(row.article_id, score_value)
                ok += 1
            except Exception as exc:
                failed += 1
                print(f"FAIL {row.article_id}: {exc}")
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_map = {executor.submit(worker, row): row.article_id for row in rows}
            for future in as_completed(future_map):
                article_id = future_map[future]
                try:
                    row, score_value = future.result()
                    adapter.update_correlation(row.article_id, score_value)
                    ok += 1
                except Exception as exc:
                    failed += 1
                    print(f"FAIL {article_id}: {exc}")
    print(f"done. ok={ok} failed={failed} total={total}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score Supabase summaries")
    parser.add_argument("--concurrency", type=int, default=None, help="Concurrency level")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of rows to score")
    return parser.parse_args()


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args()
    concurrency = args.concurrency if args.concurrency else DEFAULT_CONCURRENCY
    limit = args.limit if args.limit and args.limit > 0 else None
    score(max(1, concurrency), limit)


if __name__ == "__main__":
    main()
