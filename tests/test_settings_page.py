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
    for tab, label in [("bonuses", "加分词典"), ("advanced", "高级")]:
        assert f'data-settings-tab="{tab}">{label}</button>' in html
        assert f'aria-controls="settings-panel-{tab}"' in html
        assert f'id="settings-panel-{tab}"' in html
    assert 'class="settings-tab settings-tab-advanced"' in html
    for script in ["section_editor", "bonus_tab", "advanced_tab"]:
        assert html.index("/static/js/settings/core.js") < html.index(
            f"/static/js/settings/{script}.js"
        ) < html.index("/static/js/settings/init.js")
    # 抓取账号页签已并入数据源页签，不再存在独立页签与面板
    assert 'data-settings-tab="accounts"' not in html
    assert 'id="settings-panel-models"' in html
    assert 'id="settings-panel-sources"' in html
    assert 'id="settings-panel-accounts"' not in html
    # 脚本顺序：core 最先，endpoints 在 models_tab 之前，init 最后
    assert html.index("/static/js/settings/core.js") < html.index(
        "/static/js/settings/endpoints.js"
    )
    assert html.index("/static/js/settings/endpoints.js") < html.index(
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


def test_m3_refresh_names_rejects_more_than_twenty_ids(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.console.settings_service.refresh_account_names",
        lambda _ids: pytest.fail("oversized request reached the service"),
    )
    response = _build_client().post(
        "/api/admin/crawl-accounts/refresh-names",
        json={"account_ids": [f"account-{index}" for index in range(21)]},
    )

    assert response.status_code == 422


def test_m4_patch_rejects_display_name_as_unknown_field(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.console.settings_service.update_account",
        lambda *_args, **_kwargs: pytest.fail("display_name reached the service"),
    )
    response = _build_client().patch(
        "/api/admin/crawl-accounts/account-1",
        json={"display_name": "人工名称"},
    )

    assert response.status_code == 422


def test_duty_editor_cannot_refresh_account_names() -> None:
    response = _build_client("duty_editor").post(
        "/api/admin/crawl-accounts/refresh-names",
        json={"account_ids": ["account-1"]},
    )

    assert response.status_code == 403


def test_environment_reload_endpoint_is_gone() -> None:
    """「重新加载环境变量」按钮与接口已删：env 文件改动由控制台进程在处理
    请求前自动检测并重读（见 src/config.refresh_environment_if_changed），
    不再有手动重载入口。"""

    client = _build_client()
    response = client.post("/api/admin/settings/environment/reload")

    assert response.status_code in {404, 405}


def test_console_requests_refresh_env_files_automatically(
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    """中间件接线：env 文件改过，下一个控制台请求无需任何重载入口就能
    读到新 Key。手动按钮已删，这条守住 app.py 里的统一检测不被静默拆掉。"""

    import src.config as config
    from src.console import settings_service
    from types import SimpleNamespace

    env_file = tmp_path / "auto.env"
    monkeypatch.setattr(config, "_ENV_FILES", (env_file,))
    monkeypatch.setattr(config, "_ENV_LOADED", False)
    monkeypatch.setattr(config, "_ENV_FILE_KEYS", set())
    monkeypatch.setattr(config, "_ENV_FILES_SIGNATURE", None)
    monkeypatch.delenv("MW_PROBE_TEST_API_KEY", raising=False)
    monkeypatch.delenv("MW_BASE_TEST_VAR", raising=False)
    config.get_settings.cache_clear()
    try:
        # 初始文件不含 Key 变量；第一个请求经中间件完成初始读取
        env_file.write_text("MW_BASE_TEST_VAR=1\n", encoding="utf-8")
        client = _build_client()
        assert client.get("/admin/settings").status_code == 200

        # 用户在服务器上补了 Key；不调用任何重载函数
        env_file.write_text(
            "MW_BASE_TEST_VAR=1\nMW_PROBE_TEST_API_KEY=added\n",
            encoding="utf-8",
        )

        def fetch_settings():
            return []

        app_config = SimpleNamespace(app_config=SimpleNamespace(fetch_settings=fetch_settings))
        monkeypatch.setattr(settings_service, "get_adapter", lambda: app_config)
        payload = client.get("/api/admin/settings").json()

        configured = payload["endpoints"]["configured_api_key_envs"]
        assert "MW_PROBE_TEST_API_KEY" in configured
        assert configured == sorted(configured)
    finally:
        import os

        os.environ.pop("MW_PROBE_TEST_API_KEY", None)
        os.environ.pop("MW_BASE_TEST_VAR", None)
        config.get_settings.cache_clear()
