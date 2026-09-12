"""系统设置页浏览器端行为测试（jsdom）的 pytest 入口。

页面经真实路由渲染（模板改动会被一并覆盖），写入临时文件后交给
tests/js/settings_behavior.test.js 运行，后端由
tests/js/settings_harness.js 中的 fake server 模拟。本机未安装 Node 或
未执行 ``npm ci --prefix tests/js`` 时跳过；CI 环境（``CI`` 变量存在）下
改为失败。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.console.app import create_app
from src.console.security import ConsoleUser, require_console_user

ROOT = Path(__file__).parents[1]
JS_TEST_DIR = ROOT / "tests" / "js"
STATIC_DIR = ROOT / "src" / "console" / "web_static"


def _render_settings_page() -> str:
    app = create_app()
    app.dependency_overrides[require_console_user] = lambda: ConsoleUser(
        method="test",
        user_id="admin-id",
        username="admin",
        display_name="测试管理员",
        role="admin",
    )
    response = TestClient(app).get("/admin/settings")
    assert response.status_code == 200, f"/admin/settings 渲染失败：{response.status_code}"
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


def test_settings_page_behaviour(tmp_path: Path) -> None:
    node = _node_or_skip()
    settings_html = tmp_path / "settings.html"
    settings_html.write_text(_render_settings_page(), encoding="utf-8")

    env = {
        **os.environ,
        "CONSOLE_STATIC_DIR": str(STATIC_DIR),
        "SETTINGS_PAGE_HTML": str(settings_html),
    }
    result = subprocess.run(
        [
            node,
            "--test",
            "--test-reporter=spec",
            str(JS_TEST_DIR / "settings_behavior.test.js"),
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
