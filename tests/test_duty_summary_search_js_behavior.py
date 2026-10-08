"""值班汇总页搜索过滤（getVisibleItems 多词 AND）浏览器端行为测试（jsdom）的 pytest 入口。

值班汇总路由渲染时会写班次库，不适合作为测试承载页；轻量夹具
tests/js/duty_summary_harness.js 直接按真实加载顺序注入页面脚本，
不渲染整页。本机未安装 Node 或未执行 ``npm ci --prefix tests/js``
时跳过；CI 环境（``CI`` 变量存在）下改为失败，保证行为测试不会被静默跳过。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
JS_TEST_DIR = ROOT / "tests" / "js"
STATIC_DIR = ROOT / "src" / "console" / "web_static"


def _node_or_skip() -> str:
    node = shutil.which("node")
    has_deps = (JS_TEST_DIR / "node_modules" / "jsdom").is_dir()
    if node and has_deps:
        return node
    reason = (
        "未安装 Node" if not node else "未安装 tests/js 依赖"
    ) + "：安装 Node 20+ 后执行 npm ci --prefix tests/js"
    if os.environ.get("CI"):
        pytest.fail(f"CI 中必须运行浏览器端行为测试（{reason}）")
    pytest.skip(reason)


def test_duty_summary_search_behaviour() -> None:
    node = _node_or_skip()
    env = {
        **os.environ,
        "CONSOLE_STATIC_DIR": str(STATIC_DIR),
    }
    result = subprocess.run(
        [
            node,
            "--test",
            "--test-reporter=spec",
            str(JS_TEST_DIR / "duty_summary_search.test.js"),
        ],
        cwd=JS_TEST_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
