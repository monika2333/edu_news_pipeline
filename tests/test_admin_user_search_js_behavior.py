"""管理页账号搜索（jsdom）行为测试的 pytest 入口。

页面经 /admin 真实路由渲染后传给 tests/js/admin_user_search.test.js：
验证空格分词 AND、大小写不敏感、空串展示全部与无命中空态。
本机未安装 Node 或未执行 ``npm ci --prefix tests/js`` 时跳过；
CI 环境（``CI`` 变量存在）下改为失败，保证行为测试不会被静默跳过。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.console import web_routes
from src.console.app import create_app
from src.console.security import ConsoleUser, require_console_user

ROOT = Path(__file__).parents[1]
JS_TEST_DIR = ROOT / "tests" / "js"
STATIC_DIR = ROOT / "src" / "console" / "web_static"


def _render_admin_page(monkeypatch: pytest.MonkeyPatch) -> str:
    app = create_app()
    app.dependency_overrides[require_console_user] = lambda: ConsoleUser(
        method="test",
        user_id="admin-id",
        username="admin",
        display_name="测试用户",
        role="admin",
    )
    # 渲染会顺带补建未来 14 天的班次（写库）；浏览器端测试只关心模板与脚本渲染。
    monkeypatch.setattr(web_routes, "generate_shifts", lambda *args, **kwargs: None)
    response = TestClient(app).get("/admin")
    assert response.status_code == 200, f"/admin 渲染失败：{response.status_code}"
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


def test_admin_user_search_behaviour(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = _node_or_skip()
    admin_html = tmp_path / "admin.html"
    admin_html.write_text(_render_admin_page(monkeypatch), encoding="utf-8")

    env = {
        **os.environ,
        "CONSOLE_STATIC_DIR": str(STATIC_DIR),
        "ADMIN_PAGE_HTML": str(admin_html),
    }
    result = subprocess.run(
        [
            node,
            "--test",
            "--test-reporter=spec",
            str(JS_TEST_DIR / "admin_user_search.test.js"),
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
