"""系统设置页（/admin/settings）的路由与模板测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from src.console.app import create_app
from src.console.security import ConsoleUser, require_console_user


def _build_client(role: str = "admin") -> TestClient:
    app = create_app()
    app.dependency_overrides[require_console_user] = lambda: ConsoleUser(
        method="test",
        user_id=f"{role}-id",
        username=role,
        display_name="测试管理员",
        role=role,
    )
    return TestClient(app)


def test_admin_settings_page_serves_admin_only() -> None:
    admin = _build_client("admin")
    editor = _build_client("duty_editor")

    assert admin.get("/admin/settings").status_code == 200
    assert editor.get("/admin/settings").status_code == 403


def test_admin_settings_page_structure() -> None:
    response = _build_client().get("/admin/settings")

    assert response.status_code == 200
    html = response.text
    assert "<title>系统设置 · 新闻筛选控制台</title>" in html
    assert "<h1>系统设置</h1>" in html
    assert 'data-settings-tab="models">模型</button>' in html
    assert 'data-settings-tab="sources">数据源</button>' in html
    # 抓取账号页签已并入数据源页签，不再存在独立页签与面板
    assert 'data-settings-tab="accounts"' not in html
    assert 'id="settings-panel-models"' in html
    assert 'id="settings-panel-sources"' in html
    assert 'id="settings-panel-accounts"' not in html
    assert 'id="delete-account-modal"' in html
    # 脚本顺序：core 最先，init 最后
    assert html.index("/static/js/settings/core.js") < html.index(
        "/static/js/settings/models_tab.js"
    )
    assert html.index("/static/js/settings/models_tab.js") < html.index(
        "/static/js/settings/sources_tab.js"
    )
    assert html.index("/static/js/settings/sources_tab.js") < html.index(
        "/static/js/settings/source_accounts.js"
    )
    assert html.index("/static/js/settings/source_accounts.js") < html.index(
        "/static/js/settings/init.js"
    )
    assert 'href="/static/css/modules/settings.css' in html
    # 头部导航样式在 header.css（原 auth.css 改名），漏引会让标题栏完全无样式
    assert 'href="/static/css/modules/header.css' in html
    # 设置页不是管理员主视图，不参与"记住上次访问页面"
    assert "admin_last_view.js" not in html


def test_account_menu_highlights_current_page(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.console.web_routes.generate_shifts",
        lambda **kwargs: {"inserted": 0},
    )
    client = _build_client()

    admin_html = client.get("/admin").text
    assert 'class="account-menu-item is-active" href="/admin" aria-current="page"' in admin_html
    assert 'class="account-menu-item" href="/admin/settings">系统设置</a>' in admin_html

    settings_html = client.get("/admin/settings").text
    assert (
        'class="account-menu-item is-active" href="/admin/settings"'
        ' aria-current="page">系统设置</a>' in settings_html
    )
    assert 'class="account-menu-item" href="/admin">用户与排班</a>' in settings_html
    # 菜单顺序：用户与排班 → 系统设置 → 修改密码
    assert settings_html.index('href="/admin"') < settings_html.index(
        'href="/admin/settings"'
    ) < settings_html.index('href="/account"')


@pytest.mark.parametrize("path", ["/admin", "/admin/settings"])
def test_account_menu_includes_settings_entry(
    monkeypatch: MonkeyPatch, path: str
) -> None:
    monkeypatch.setattr(
        "src.console.web_routes.generate_shifts",
        lambda **kwargs: {"inserted": 0},
    )
    html = _build_client().get(path).text

    assert "系统设置" in html
    assert 'href="/admin/settings"' in html
