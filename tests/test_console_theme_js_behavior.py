"""控制台主题（亮色 / 暗色 / 跟随系统）浏览器端行为测试的 pytest 入口。

页面经真实路由渲染（模板 include 与账号菜单控件会被一并覆盖），写入临时
文件后交给 ``tests/js/theme.test.js`` 运行。本机未安装 Node 或未执行
``npm ci --prefix tests/js`` 时跳过；CI 环境（``CI`` 变量存在）下改为失败，
保证行为测试不会被静默跳过。
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


def _render_page(path: str, role: str | None) -> str:
    app = create_app()
    if role is not None:
        app.dependency_overrides[require_console_user] = lambda: ConsoleUser(
            method="test",
            user_id=f"{role}-id",
            username=role,
            display_name="测试用户",
            role=role,
        )
    response = TestClient(app).get(path)
    assert response.status_code == 200, f"{path} 渲染失败：{response.status_code}"
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


def test_console_theme_js_behavior(tmp_path: Path) -> None:
    node = _node_or_skip()
    admin_html = tmp_path / "admin.html"
    duty_html = tmp_path / "duty.html"
    login_html = tmp_path / "login.html"
    admin_html.write_text(_render_page("/manual_filter", "admin"), encoding="utf-8")
    duty_html.write_text(_render_page("/duty", "duty_editor"), encoding="utf-8")
    login_html.write_text(_render_page("/login", None), encoding="utf-8")

    env = {
        **os.environ,
        "CONSOLE_STATIC_DIR": str(STATIC_DIR),
        "THEME_PAGE_ADMIN_HTML": str(admin_html),
        "THEME_PAGE_DUTY_HTML": str(duty_html),
        "THEME_PAGE_LOGIN_HTML": str(login_html),
    }
    result = subprocess.run(
        [
            node,
            "--test",
            "--test-reporter=spec",
            str(JS_TEST_DIR / "theme.test.js"),
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
