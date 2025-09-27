#!/usr/bin/env python3
"""
教育新闻自动化流水线一键运行脚本
执行完整的抓取、摘要、评分、导出流程
"""

import os
import sys
import subprocess
import argparse
from datetime import datetime
from pathlib import Path

# Debug info removed for production

# 环境变量加载（使用项目自己的逻辑）
def _load_simple_env(path: Path) -> None:
    """简单的环境文件加载器"""
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            key = key.strip()
            value = value.strip()
            if (value.startswith("\"") and value.endswith("\"")) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


def load_env_files():
    """按项目约定加载环境文件"""
    for env_file in ['.env.local', '.env', 'config/abstract.env']:
        env_path = Path(env_file)
        if env_path.exists():
            _load_simple_env(env_path)
            print(f"[NOTE] 已加载环境文件: {env_file}")
            return True
    return False


def run_command(cmd, description):
    """运行命令并处理错误"""
    print(f"\n{'='*60}")
    print(f"开始执行: {description}")
    print(f"命令: {' '.join(cmd)}")
    print('='*60)

    try:
        result = subprocess.run(cmd, check=True, capture_output=False)
        print(f"[OK] 完成: {description}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[FAIL] 失败: {description}")
        print(f"错误代码: {e.returncode}")
        return False
    except FileNotFoundError:
        print(f"[FAIL] 找不到命令: {cmd[0]}")
        return False


def check_requirements():
    """检查运行环境和依赖"""
    print("[SEARCH] 检查运行环境...")

    # 检查Python版本
    if sys.version_info < (3, 10):
        print("[FAIL] 需要Python 3.10+")
        return False

    # 检查Python模块
    required_modules = [
        "supabase",
        "playwright"
    ]

    missing_modules = []
    for module in required_modules:
        try:
            __import__(module)
        except ImportError:
            missing_modules.append(module)

    if missing_modules:
        print(f"[FAIL] 缺少Python模块: {', '.join(missing_modules)}")
        print("[IDEA] 请安装依赖模块:")
        if sys.platform == "win32":
            print("   在Windows PowerShell中运行:")
            print("   pip install -r requirements.txt")
            print("   或者:")
            print("   python -m pip install -r requirements.txt")
        else:
            print("   python3 -m pip install -r requirements.txt")
        print("\n[WARN]  安装完成后再次运行此脚本")
        return False

    # 检查必要文件
    required_files = [
        "tools/toutiao_scraper.py",
        "tools/summarize_supabase.py",
        "tools/score_correlation_supabase.py",
        "tools/export_high_correlation_supabase.py",
        "tools/author.txt",
        "education_keywords.txt"
    ]

    for file_path in required_files:
        if not Path(file_path).exists():
            print(f"[FAIL] 缺少必要文件: {file_path}")
            return False

    # 检查环境变量
    required_env_vars = [
        "SUPABASE_URL",
        "SUPABASE_DB_PASSWORD",
        "SILICONFLOW_API_KEY"
    ]

    missing_vars = []
    for var in required_env_vars:
        if not os.getenv(var):
            missing_vars.append(var)

    if missing_vars:
        print(f"[FAIL] 缺少环境变量: {', '.join(missing_vars)}")
        return False

    print("[OK] 环境检查通过")
    return True


def main():
    print("[INFO] 教育新闻自动化流水线启动中...")
    if sys.platform == "win32":
        print("[WIN] 检测到Windows环境")
    else:
        print("[LINUX] 检测到Unix/Linux环境")

    parser = argparse.ArgumentParser(description="教育新闻自动化流水线一键运行")
    parser.add_argument("--scrape-limit", type=int, default=150,
                       help="抓取文章数量限制 (默认: 150)")
    parser.add_argument("--summary-limit", type=int, default=200,
                       help="摘要处理数量限制 (默认: 200)")
    parser.add_argument("--score-limit", type=int, default=200,
                       help="评分处理数量限制 (默认: 200)")
    parser.add_argument("--min-score", type=int, default=60,
                       help="导出最低相关度分数 (默认: 60)")
    parser.add_argument("--concurrency", type=int, default=5,
                       help="LLM并发数 (默认: 5)")
    parser.add_argument("--show-browser", action="store_true",
                       help="显示浏览器窗口 (用于调试)")
    parser.add_argument("--days-limit", type=int, default=3,
                       help="只抓取最近N天的文章 (默认: 3天)")
    parser.add_argument("--skip-scrape", action="store_true",
                       help="跳过抓取步骤")
    parser.add_argument("--skip-summary", action="store_true",
                       help="跳过摘要步骤")
    parser.add_argument("--skip-score", action="store_true",
                       help="跳过评分步骤")
    parser.add_argument("--skip-export", action="store_true",
                       help="跳过导出步骤")
    parser.add_argument("--report-tag", type=str,
                       help="导出报告标签 (默认: 当前日期时间)")

    args = parser.parse_args()

    # 加载环境变量
    load_env_files()

    # 检查环境
    if not check_requirements():
        sys.exit(1)

    # 生成报告标签
    if not args.report_tag:
        args.report_tag = datetime.now().strftime("%Y%m%d_%H%M")

    print(f"\n[LAUNCH] 开始运行教育新闻自动化流水线")
    print(f"报告标签: {args.report_tag}")

    success_count = 0
    total_steps = 4

    # 步骤1: 抓取今日头条作者文章
    if not args.skip_scrape:
        cmd = [
            "python3", "tools/toutiao_scraper.py",
            "--input", "tools/author.txt",
            "--limit", str(args.scrape_limit),
            "--days-limit", str(args.days_limit)
        ]
        if args.show_browser:
            cmd.append("--show-browser")

        if run_command(cmd, "抓取今日头条作者文章"):
            success_count += 1
    else:
        print("\n>>|  跳过抓取步骤")
        success_count += 1

    # 步骤2: 关键词过滤和摘要生成
    if not args.skip_summary:
        cmd = [
            "python3", "tools/summarize_supabase.py",
            "--keywords", "education_keywords.txt",
            "--limit", str(args.summary_limit),
            "--concurrency", str(args.concurrency)
        ]

        if run_command(cmd, "关键词过滤和摘要生成"):
            success_count += 1
    else:
        print("\n>>|  跳过摘要步骤")
        success_count += 1

    # 步骤3: 相关度评分
    if not args.skip_score:
        cmd = [
            "python3", "tools/score_correlation_supabase.py",
            "--limit", str(args.score_limit),
            "--concurrency", str(args.concurrency)
        ]

        if run_command(cmd, "相关度评分"):
            success_count += 1
    else:
        print("\n>>|  跳过评分步骤")
        success_count += 1

    # 步骤4: 导出高相关摘要
    if not args.skip_export:
        cmd = [
            "python3", "tools/export_high_correlation_supabase.py",
            "--min-score", str(args.min_score),
            "--report-tag", args.report_tag
        ]

        if run_command(cmd, "导出高相关摘要"):
            success_count += 1
    else:
        print("\n>>|  跳过导出步骤")
        success_count += 1

    # 流程总结
    print(f"\n{'='*60}")
    print(f"[TARGET] 流水线执行完成")
    print(f"成功步骤: {success_count}/{total_steps}")

    if success_count == total_steps:
        print("[OK] 所有步骤执行成功!")
        print(f"[STATS] 导出文件标签: {args.report_tag}")
        print("[FOLDER] 请查看 outputs/ 目录下的生成文件")
    else:
        print("[WARN]  部分步骤执行失败，请检查上述错误信息")
        sys.exit(1)


if __name__ == "__main__":
    main()