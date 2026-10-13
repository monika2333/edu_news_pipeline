"""原文抽屉头部分类徽章 + 分数（jsdom）行为测试的 pytest 入口。

覆盖 /api/articles/content 新增分类/评分字段到两套抽屉头部渲染的完整链路：
筛选页点「原文」（manual_filter/content_drawer.js）与
汇总页直调 loadArchiveDrawerArticle（submission_archive/content_drawer.js）。
页面经真实路由渲染（模板改动会被一并覆盖）后交给 tests/js/ 下的 node 测试。
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


def _render_page(path: str, role: str, monkeypatch: pytest.MonkeyPatch | None = None) -> str:
    app = create_app()
    app.dependency_overrides[require_console_user] = lambda: ConsoleUser(
        method="test",
        user_id=f"{role}-id",
        username=role,
        display_name="测试用户",
        role=role,
    )
    if path == "/admin/duty-summary":
        # 渲染会顺带补建未来 14 天的班次（写库）；浏览器端测试只关心模板与脚本渲染，
        # 把这一步换成空操作以避免测试写库（口径与 test_duty_summary_js_behavior.py 一致）。
        monkeypatch.setattr(web_routes, "generate_shifts", lambda *args, **kwargs: None)
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


def test_content_drawer_category_behaviour(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    node = _node_or_skip()
    filter_html = tmp_path / "manual_filter.html"
    duty_summary_html = tmp_path / "duty_summary.html"
    filter_html.write_text(_render_page("/manual_filter", "admin"), encoding="utf-8")
    duty_summary_html.write_text(
        _render_page("/admin/duty-summary", "admin", monkeypatch),
        encoding="utf-8",
    )

    env = {
        **os.environ,
        "CONSOLE_STATIC_DIR": str(STATIC_DIR),
        "FILTER_PAGE_ADMIN_HTML": str(filter_html),
        "DUTY_SUMMARY_PAGE_HTML": str(duty_summary_html),
    }
    result = subprocess.run(
        [
            node,
            "--test",
            "--test-reporter=spec",
            str(JS_TEST_DIR / "content_drawer_category.test.js"),
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
