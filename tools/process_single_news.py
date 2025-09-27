#!/usr/bin/env python3
"""
快速处理单个新闻链接的脚本：
1. 接收新闻链接
2. 调用 toutiao_fetch.py 获取内容并保存到数据库
3. 调用 summarize_news.py 生成摘要
4. 跳过评分阶段
5. 直接输出 title、summary、source_LLM 到控制台
"""

import argparse
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any

# 项目根目录和工具目录
REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "tools"
DEFAULT_DB_PATH = REPO_ROOT / "articles.sqlite3"
DEFAULT_KEYWORDS_PATH = REPO_ROOT / "education_keywords.txt"

def fetch_news_to_db(url: str, db_path: Path) -> Optional[str]:
    """
    使用 toutiao_fetch.py 获取新闻并保存到数据库
    返回 article_id 如果成功，否则返回 None
    """
    try:
        # 调用 toutiao_fetch.py
        cmd = [
            sys.executable,
            str(TOOLS_DIR / "toutiao_fetch.py"),
            url,
            "--db", str(db_path),
            "--format", "json"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, encoding='utf-8', errors='replace')

        if result.returncode != 0:
            print(f"Error fetching news: {result.stderr}", file=sys.stderr)
            return None

        # 从数据库中获取刚插入的文章ID
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT article_id FROM articles ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    except Exception as e:
        print(f"Error in fetch_news_to_db: {e}", file=sys.stderr)
        return None

def generate_summary(article_id: str, db_path: Path, keywords_path: Path) -> bool:
    """
    使用 summarize_news.py 为指定文章生成摘要
    """
    try:
        # 调用 summarize_news.py，限制只处理这一篇文章
        cmd = [
            sys.executable,
            str(TOOLS_DIR / "summarize_news.py"),
            "--db", str(db_path),
            "--keywords", str(keywords_path),
            "--limit", "1"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, encoding='utf-8', errors='replace')

        if result.returncode != 0:
            print(f"Error generating summary: {result.stderr}", file=sys.stderr)
            return False

        return True

    except Exception as e:
        print(f"Error in generate_summary: {e}", file=sys.stderr)
        return False

def get_news_data(article_id: str, db_path: Path) -> Optional[Dict[str, Any]]:
    """
    从数据库中获取处理后的新闻数据
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT title, summary, source, source_LLM FROM news_summaries WHERE article_id = ?",
                (article_id,)
            )
            row = cur.fetchone()
            if row:
                return {
                    "title": row["title"] or "",
                    "summary": row["summary"] or "",
                    "source": row["source"] or "",
                    "source_LLM": row["source_LLM"] or ""
                }
            return None
        finally:
            conn.close()
    except Exception as e:
        print(f"Error getting news data: {e}", file=sys.stderr)
        return None

def extract_url_from_paste(paste_content: str) -> Optional[str]:
    """
    从粘贴内容中提取今日头条链接
    支持格式如：https://m.toutiao.com/is/YEexSWXbGwQ/
    """
    # 匹配今日头条链接的正则表达式
    patterns = [
        r'https?://m\.toutiao\.com/is/[A-Za-z0-9]+/?',
        r'https?://www\.toutiao\.com/article/\d+/?',
        r'https?://m\.toutiao\.com/i\d+/?',
        r'https?://[^/]*toutiao\.com[^\s]*',
        r'https?://[^/]*bjd\.com\.cn[^\s]*'  # 支持北京日报网
    ]

    for pattern in patterns:
        match = re.search(pattern, paste_content)
        if match:
            return match.group(0)

    return None

def interactive_input() -> Optional[str]:
    """
    交互式获取新闻链接
    """
    print("请粘贴新闻分享内容（包含链接）：")
    print("例如：【AI赋能､人人参与!北京市2025年北京市中小学科学节(通... - 今日头条】")
    print("点击链接打开👉 https://m.toutiao.com/is/YEexSWXbGwQ/")
    print("按 Ctrl+C 退出")
    print("-" * 50)

    try:
        # 读取多行输入直到空行
        lines = []
        while True:
            try:
                line = input()
                if not line.strip():  # 空行表示结束
                    break
                lines.append(line)
            except EOFError:  # Ctrl+D
                break

        paste_content = "\n".join(lines)
        if not paste_content.strip():
            print("没有输入内容，退出。")
            return None

        # 提取链接
        url = extract_url_from_paste(paste_content)
        if url:
            print(f"\n提取到的链接: {url}")
            return url
        else:
            print("\n未能从输入中找到有效的新闻链接")
            return None

    except KeyboardInterrupt:
        print("\n用户取消，退出。")
        return None

def print_news_output(data: Dict[str, Any]) -> None:
    """
    按照 export_high_correlation.py 的格式输出新闻
    """
    title = data["title"].strip()
    summary = data["summary"].strip()
    source_llm = data["source"].strip()

    # 按照 export_high_correlation.py:109 的格式
    suffix = f" ({source_llm})" if source_llm else ""
    output = f"{title}\n{summary}{suffix}"

    print(output)

def main() -> int:
    parser = argparse.ArgumentParser(description="Process a single news URL")
    parser.add_argument("url", nargs="?", help="新闻链接URL（可选，不提供则进入交互模式）")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite 数据库路径")
    parser.add_argument("--keywords", type=Path, default=DEFAULT_KEYWORDS_PATH, help="关键词文件路径")

    args = parser.parse_args()

    db_path = args.db.resolve()
    keywords_path = args.keywords.resolve()

    # 检查必要文件
    if not keywords_path.exists():
        print(f"Error: Keywords file not found: {keywords_path}", file=sys.stderr)
        return 1

    # 获取URL：从命令行参数或交互式输入
    url = args.url
    if not url:
        url = interactive_input()
        if not url:
            return 1

    print("Step 1: Fetching news content...", file=sys.stderr)
    article_id = fetch_news_to_db(url, db_path)
    if not article_id:
        print("Failed to fetch news content", file=sys.stderr)
        return 1

    print(f"Step 2: Generating summary for article {article_id}...", file=sys.stderr)
    if not generate_summary(article_id, db_path, keywords_path):
        print("Failed to generate summary", file=sys.stderr)
        return 1

    print("Step 3: Retrieving processed data...", file=sys.stderr)
    news_data = get_news_data(article_id, db_path)
    if not news_data:
        print("Failed to retrieve processed news data", file=sys.stderr)
        return 1

    # 输出结果到控制台
    print("=" * 50, file=sys.stderr)
    print_news_output(news_data)

    return 0

if __name__ == "__main__":
    sys.exit(main())