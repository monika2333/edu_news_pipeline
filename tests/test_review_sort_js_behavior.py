"""审阅页「自动排序」浏览器端行为测试（jsdom）的 pytest 入口。

页面经真实路由渲染（模板改动会被一并覆盖）；渲染时把
``web_routes.get_adapter`` 替换为返回固定词表行的假 adapter，保证嵌入页面的
``#review-sort-rules`` 内容确定，供 tests/js/review_sort_flow.test.js 断言。
本机未安装 Node 或未执行 ``npm ci --prefix tests/js`` 时跳过；CI 环境下改为
失败，保证行为测试不会被静默跳过。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.console import web_routes
from src.console.app import create_app
from src.console.security import ConsoleUser, require_console_user

ROOT = Path(__file__).parents[1]
JS_TEST_DIR = ROOT / "tests" / "js"
STATIC_DIR = ROOT / "src" / "console" / "web_static"

# 与 tests/js/review_sort_flow.test.js 的断言一一对应
FIXED_RULES: dict[str, list[str]] = {
    "市教委": ["市教委", "教工委"],
    "中小学": ["中小学", "小学", "k12"],
    "高校": ["高校", "大学"],
}


class _FakeAppConfig:
    def fetch_setting(self, section: str) -> dict[str, Any] | None:
        if section == "review_sort_keywords":
            return {"section": section, "value": FIXED_RULES, "version": 7}
        return None


class _FakeAdapter:
    app_config = _FakeAppConfig()


def _render_review_page() -> str:
    app = create_app()
    app.dependency_overrides[require_console_user] = lambda: ConsoleUser(
        method="test",
        user_id="admin-id",
        username="admin",
        display_name="测试管理员",
        role="admin",
    )
    original_get_adapter = web_routes.get_adapter
    web_routes.get_adapter = lambda: _FakeAdapter()
    try:
        response = TestClient(app).get("/admin/review")
    finally:
        web_routes.get_adapter = original_get_adapter
    assert response.status_code == 200, f"/admin/review 渲染失败：{response.status_code}"
    return response.text


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


def test_review_sort_behaviour(tmp_path: Path) -> None:
    node = _node_or_skip()
    page_html = tmp_path / "review.html"
    page_html.write_text(_render_review_page(), encoding="utf-8")

    env = {
        **os.environ,
        "CONSOLE_STATIC_DIR": str(STATIC_DIR),
        "FILTER_PAGE_ADMIN_HTML": str(page_html),
    }
    result = subprocess.run(
        [
            node,
            "--test",
            "--test-reporter=spec",
            str(JS_TEST_DIR / "review_sort_flow.test.js"),
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
