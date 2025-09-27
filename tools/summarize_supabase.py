#!/usr/bin/env python3
"""Summarize Supabase Toutiao articles using SiliconFlow API."""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
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


def _chunked(values: Sequence[str], size: int = 100) -> Iterable[Sequence[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def fetch_existing_news_summaries(adapter, article_ids: List[str]) -> dict[str, dict]:
    if not article_ids:
        return {}
    existing: dict[str, dict] = {}
    for chunk in _chunked(article_ids, 100):
        response = (
            adapter.client
            .table("news_summaries")
            .select("article_id, llm_summary, llm_keywords, summary_generated_at")
            .in_("article_id", list(chunk))
            .execute()
        )
        for row in response.data or []:
            article_id = str(row.get("article_id"))
            existing[article_id] = row
    return existing


def fetch_toutiao_candidates(adapter, limit: Optional[int]) -> List[SummaryCandidate]:
    batch = max(1, (limit or 50)) * 4
    query = (
        adapter.client
        .table("toutiao_articles")
        .select(
            "article_id, title, source, publish_time_iso, publish_time, url, content_markdown, summary, "
            "comment_count, digg_count"
        )
        .not_.is_("content_markdown", "null")
        .order("fetched_at", desc=False)
        .limit(batch)
    )
    response = query.execute()
    rows = response.data or []
    article_ids = [str(row.get("article_id")) for row in rows if row.get("article_id")]
    existing_map = fetch_existing_news_summaries(adapter, article_ids)
    candidates: List[SummaryCandidate] = []
    for row in rows:
        article_id = str(row.get("article_id")) if row.get("article_id") else None
        content = row.get("content_markdown") or ""
        if not article_id or not str(content).strip():
            continue
        existing_entry = existing_map.get(article_id)
        existing_summary = (existing_entry or {}).get("llm_summary")
        processed_payload = {
            "original_summary": row.get("summary"),
            "comment_count": row.get("comment_count"),
            "digg_count": row.get("digg_count"),
            "publish_time": row.get("publish_time"),
            "existing_keywords": (existing_entry or {}).get("llm_keywords") or [],
            "existing_summary_generated_at": (existing_entry or {}).get("summary_generated_at"),
        }
        candidate = SummaryCandidate(
            raw_article_id=article_id,
            article_hash=article_id,
            title=row.get("title"),
            source=row.get("source"),
            published_at=row.get("publish_time_iso"),
            original_url=row.get("url"),
            content=str(content),
            existing_summary=existing_summary,
            filtered_article_id=None,
            processed_payload=processed_payload,
        )
        candidates.append(candidate)
        if limit and len(candidates) >= limit:
            break
    return candidates


def write_news_summary(
    adapter,
    candidate: SummaryCandidate,
    summary: str,
    *,
    keywords: Optional[Sequence[str]] = None,
) -> None:
    deduped_keywords: List[str] = []
    if keywords:
        deduped_keywords = [kw for kw in dict.fromkeys(keywords) if kw]
    if not deduped_keywords:
        deduped_keywords = list(dict.fromkeys(candidate.processed_payload.get("existing_keywords", [])))
    previous_generated_at = candidate.processed_payload.get("existing_summary_generated_at")
    payload = {
        "article_id": candidate.raw_article_id,
        "title": candidate.title,
        "llm_summary": summary,
        "content_markdown": candidate.content,
        "source": candidate.source,
        "publish_time_iso": candidate.published_at,
        "publish_time": candidate.processed_payload.get("publish_time"),
        "url": candidate.original_url,
        "llm_keywords": deduped_keywords or None,
        "summary_generated_at": None,
    }
    if candidate.existing_summary and summary == candidate.existing_summary and previous_generated_at:
        payload["summary_generated_at"] = previous_generated_at
    else:
        payload["summary_generated_at"] = datetime.now(timezone.utc).isoformat()
    adapter.client.table("news_summaries").upsert(payload, on_conflict="article_id").execute()


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


def summarize(config: SummarizeConfig) -> None:
    adapter = get_supabase_adapter()
    keywords = load_keywords(config.keywords_path)
    limit = config.limit

    candidates = fetch_toutiao_candidates(adapter, limit)
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

    def worker(item: Tuple[SummaryCandidate, List[str]]) -> Tuple[SummaryCandidate, str, List[str], bool]:
        cand, hits = item
        if cand.existing_summary:
            return cand, cand.existing_summary, hits, True
        summary = call_api(cand.content)
        return cand, summary, hits, False

    if concurrency == 1:
        for candidate, hits in filtered_candidates:
            try:
                if candidate.existing_summary:
                    summary = candidate.existing_summary
                    reused += 1
                else:
                    summary = call_api(candidate.content)
                write_news_summary(adapter, candidate, summary, keywords=hits)
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
                    cand, summary, hits, reused_flag = future.result()
                    write_news_summary(adapter, cand, summary, keywords=hits)
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
    parser = argparse.ArgumentParser(description="Summarize Supabase Toutiao articles")
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
