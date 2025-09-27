#!/usr/bin/env python3
"""Summarize Supabase raw articles using SiliconFlow API."""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import requests

try:
    from tools.supabase_adapter import SummaryCandidate, get_supabase_adapter
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parent))
    from supabase_adapter import SummaryCandidate, get_supabase_adapter  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEYWORDS_FILE = REPO_ROOT / "education_keywords.txt"


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
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


def load_environment() -> None:
    for candidate in (REPO_ROOT / ".env", REPO_ROOT / ".env.local", REPO_ROOT / "config" / "abstract.env"):
        _load_env_file(candidate)


load_environment()

BASE_URL = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
API_KEY = os.getenv("SILICONFLOW_API_KEY")
MODEL = os.getenv("MODEL_NAME", "Qwen/Qwen2.5-14B-Instruct")
ENABLE_THINKING = os.getenv("ENABLE_THINKING", "false").lower() in ("1", "true", "yes", "y")

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


ENV_PROCESS_LIMIT = os.getenv("PROCESS_LIMIT")
DEFAULT_CONCURRENCY = int(os.getenv("CONCURRENCY", "5") or "5")


@dataclass
class SummarizeConfig:
    keywords_path: Path
    limit: Optional[int]
    concurrency: int


def load_keywords(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"关键词文件不存在: {path}")
    keywords: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if raw and not raw.startswith("#"):
            keywords.append(raw)
    if not keywords:
        raise ValueError("关键词列表为空，请在 education_keywords.txt 中每行写一个关键词")
    return keywords


def contains_keywords(text: str, keywords: Sequence[str]) -> Tuple[bool, List[str]]:
    matched: List[str] = []
    lower_text = text.lower()
    for keyword in keywords:
        if keyword and keyword.lower() in lower_text:
            matched.append(keyword)
    return (bool(matched), matched)


def call_api(content: str, retries: int = 4) -> str:
    if not API_KEY:
        raise RuntimeError("缺少环境变量 SILICONFLOW_API_KEY")
    url = f"{BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": f"请总结下面的新闻，直接输出总结后的内容即可：{content}"}],
        "temperature": 0.2,
        "max_tokens": 512,
    }
    if ENABLE_THINKING and supports_thinking(MODEL):
        payload["enable_thinking"] = True
    backoff = 1.0
    last_error: Optional[Exception] = None
    for _ in range(retries):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=60)
            if response.status_code == 200:
                data = response.json()
                return (data["choices"][0]["message"]["content"] or "").strip()
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(backoff)
                backoff *= 2
                continue
            last_error = RuntimeError(f"API {response.status_code}: {response.text[:120]}")
        except Exception as exc:
            last_error = exc
        time.sleep(backoff)
        backoff = min(backoff * 2, 8)
    raise last_error or RuntimeError("调用摘要接口失败")


def call_source_api(content: str, retries: int = 3) -> Optional[str]:
    if not API_KEY:
        return None
    url = f"{BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": (
                    "请分析以下文本的来源类型，回答 '官方媒体'、'教育系统'、'社会新闻'、'自媒体' 其中之一，并说明理由："
                    + content
                ),
            }
        ],
        "temperature": 0.0,
        "max_tokens": 120,
    }
    backoff = 1.0
    last_error: Optional[Exception] = None
    for _ in range(retries):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return (data["choices"][0]["message"]["content"] or "").strip()
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(backoff)
                backoff *= 2
                continue
            last_error = RuntimeError(f"API {response.status_code}: {response.text[:120]}")
        except Exception as exc:
            last_error = exc
        time.sleep(backoff)
    return None


def summarize(config: SummarizeConfig) -> None:
    adapter = get_supabase_adapter()
    keywords = load_keywords(config.keywords_path)
    limit = config.limit

    candidates = adapter.fetch_summary_candidates(limit)
    if not candidates:
        print("No Supabase articles require summarization.")
        return

    filtered_candidates: List[Tuple[SummaryCandidate, List[str]]] = []
    filtered_out = 0
    for candidate in candidates:
        has_kw, matched = contains_keywords(candidate.content, keywords)
        if has_kw:
            filtered_candidates.append((candidate, matched))
        else:
            filtered_out += 1
    if not filtered_candidates:
        print(f"All {len(candidates)} candidates filtered out by keywords.")
        return

    concurrency = max(1, config.concurrency)
    ok = 0
    failed = 0
    reused = 0

    def worker(item: Tuple[SummaryCandidate, List[str]]) -> Tuple[SummaryCandidate, str, Optional[str], List[str], bool]:
        cand, hits = item
        if cand.existing_summary:
            return cand, cand.existing_summary, None, hits, True
        summary = call_api(cand.content)
        source_llm = call_source_api(cand.content)
        return cand, summary, source_llm, hits, False

    if concurrency == 1:
        for candidate, hits in filtered_candidates:
            try:
                if candidate.existing_summary:
                    summary = candidate.existing_summary
                    source_llm = None
                    reused += 1
                else:
                    summary = call_api(candidate.content)
                    source_llm = call_source_api(candidate.content)
                adapter.save_summary(candidate, summary, source_llm=source_llm, keywords=hits)
                ok += 1
                tag = "(reuse)" if candidate.existing_summary else ""
                print(f"OK {candidate.article_hash} {tag}")
            except Exception as exc:
                failed += 1
                print(f"FAIL {candidate.article_hash}: {exc}")
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_map = {executor.submit(worker, item): item[0].article_hash for item in filtered_candidates}
            for future in as_completed(future_map):
                cand_hash = future_map[future]
                try:
                    cand, summary, source_llm, hits, reused_flag = future.result()
                    adapter.save_summary(cand, summary, source_llm=source_llm, keywords=hits)
                    if reused_flag:
                        reused += 1
                        print(f"OK {cand_hash} (reuse)")
                    else:
                        print(f"OK {cand_hash}")
                    ok += 1
                except Exception as exc:
                    failed += 1
                    print(f"FAIL {cand_hash}: {exc}")

    skipped = max(0, len(candidates) - ok - failed - filtered_out)
    print(
        f"done. ok={ok} skipped={skipped} filtered={filtered_out} failed={failed} reused={reused}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize Supabase raw articles")
    parser.add_argument("--keywords", type=Path, default=DEFAULT_KEYWORDS_FILE, help="Keywords file path")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of articles")
    parser.add_argument("--concurrency", type=int, default=None, help="Number of worker threads")
    return parser.parse_args()


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args()
    limit = args.limit
    if limit is None and ENV_PROCESS_LIMIT and ENV_PROCESS_LIMIT.isdigit():
        limit = int(ENV_PROCESS_LIMIT)
    concurrency = args.concurrency if args.concurrency else DEFAULT_CONCURRENCY
    config = SummarizeConfig(
        keywords_path=args.keywords.resolve(),
        limit=limit if limit and limit > 0 else None,
        concurrency=max(1, concurrency),
    )
    summarize(config)


if __name__ == "__main__":
    main()
