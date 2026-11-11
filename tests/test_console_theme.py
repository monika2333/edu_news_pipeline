"""控制台亮色 / 暗色主题的静态契约测试。

覆盖：首帧引导脚本位置、账号菜单外观控件覆盖、token 明暗完整性、
CSS/模板/JS 无颜色字面值、所有 var() 引用都有定义。
浏览器端运行时行为由 ``tests/test_console_theme_js_behavior.py`` 覆盖。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from src.console.app import create_app
from src.console.security import ConsoleUser, require_console_user

ROOT = Path(__file__).parents[1]
CONSOLE_DIR = ROOT / "src" / "console"
TEMPLATES_DIR = CONSOLE_DIR / "web_templates"
STATIC_DIR = CONSOLE_DIR / "web_static"
CSS_DIR = STATIC_DIR / "css"
VARIABLES_CSS = CSS_DIR / "variables.css"

# 页面 -> (角色, 模板名)；角色为 None 表示匿名页面。
THEME_PAGES = [
    ("/login", None, "login.html"),
    ("/register", None, "register.html"),
    ("/account", "duty_editor", "account.html"),
    ("/admin/users/new", "admin", "admin_user_create.html"),
    ("/admin", "admin", "admin.html"),
    ("/admin/settings", "admin", "settings.html"),
    ("/manual_filter", "admin", "manual_filter.html"),
    ("/submission-archive", "admin", "submission_archive.html"),
    ("/admin/duty-summary", "admin", "duty_summary.html"),
    ("/", "admin", "admin_entry.html"),
]

ACCOUNT_MENU_PAGES = [
    ("/manual_filter", "admin"),
    ("/admin/review", "admin"),
    ("/duty", "duty_editor"),
    ("/submission-archive", "admin"),
    ("/admin/settings", "admin"),
    ("/admin", "admin"),
    ("/admin/duty-summary", "admin"),
]

NO_ACCOUNT_MENU_PAGES = [
    ("/login", None),
    ("/register", None),
    ("/account", "duty_editor"),
    ("/admin/users/new", "admin"),
]

BOOTSTRAP_MARKER = "document.documentElement.setAttribute('data-theme', resolved);"
THEME_SCRIPT_MARKER = '<script defer src="/static/js/theme.js?v='

COLOR_RE = re.compile(
    r"(?<!&)#[0-9a-fA-F]{3,8}\b"
    r"|\brgba?\([^)]*\)"
    r"|\bhsla?\([^)]*\)"
    r"|(?<![\w-])(?:white|black)(?![\w-])"
)
ALLOWED_COLOR_KEYWORDS = {"transparent", "currentColor", "inherit"}


def _render_page(path: str, role: str | None, monkeypatch: MonkeyPatch) -> str:
    monkeypatch.setattr(
        "src.console.web_routes.generate_shifts",
        lambda *args, **kwargs: {"inserted": 0},
    )
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


def _scanned_files() -> list[Path]:
    """除 variables.css 之外所有可能出现颜色字面值的前端文件。"""
    files = [path for path in CSS_DIR.rglob("*.css") if path != VARIABLES_CSS]
    files.extend(TEMPLATES_DIR.rglob("*.html"))
    files.extend((STATIC_DIR / "js").rglob("*.js"))
    return sorted(files)


def _strip_comments(text: str, suffix: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    if suffix in {".html"}:
        text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return text


def _vars_in_block(text: str, selector_pattern: str) -> dict[str, str]:
    match = re.search(selector_pattern + r"\s*\{(?P<body>.*?)\}", text, flags=re.S)
    assert match, f"variables.css 缺少 {selector_pattern} 块"
    return {
        name: value.strip()
        for name, value in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", match.group("body"))
    }


@pytest.mark.parametrize(("path", "role", "template_name"), THEME_PAGES)
def test_theme_bootstrap_renders_before_stylesheets(
    path: str, role: str | None, template_name: str, monkeypatch: MonkeyPatch
) -> None:
    html = _render_page(path, role, monkeypatch)

    assert html.count(BOOTSTRAP_MARKER) == 1, f"{template_name} 应只含一份首帧主题引导脚本"
    assert THEME_SCRIPT_MARKER in html, f"{template_name} 未加载 theme.js"

    bootstrap_index = html.index(BOOTSTRAP_MARKER)
    first_stylesheet = html.find('<link rel="stylesheet"')
    if first_stylesheet >= 0:
        assert bootstrap_index < first_stylesheet, (
            f"{template_name} 的主题引导脚本必须早于第一个 stylesheet"
        )
    else:
        # admin_entry.html 没有样式表，仍须在跳转脚本前完成首帧主题设置。
        first_script = html.find('<script src="/static/js/admin_last_view.js')
        assert first_script >= 0
        assert bootstrap_index < first_script


@pytest.mark.parametrize(("path", "role"), ACCOUNT_MENU_PAGES)
def test_account_menu_hosts_three_appearance_options(
    path: str, role: str, monkeypatch: MonkeyPatch
) -> None:
    html = _render_page(path, role, monkeypatch)

    assert 'role="group" aria-label="外观"' in html
    option_values = re.findall(r'data-theme-option="([^"]+)"', html)
    assert option_values == ["system", "light", "dark"], f"{path} 外观选项缺失或顺序错误"
    assert html.count('aria-pressed="false"') >= 3
    assert re.search(
        r'account-menu-group-label[^>]*>外观<', html
    ), f"{path} 缺少「外观」分组标签"


@pytest.mark.parametrize(("path", "role"), NO_ACCOUNT_MENU_PAGES)
def test_pages_without_account_menu_have_no_appearance_controls(
    path: str, role: str | None, monkeypatch: MonkeyPatch
) -> None:
    html = _render_page(path, role, monkeypatch)

    assert "data-theme-option" not in html
    assert 'aria-label="外观"' not in html


def test_variables_css_defines_dark_counterparts_for_every_color_token() -> None:
    text = VARIABLES_CSS.read_text(encoding="utf-8")
    light = _vars_in_block(text, r":root")
    dark = _vars_in_block(text, r':root\[data-theme="dark"\]')

    assert "color-scheme: light" in text
    assert "color-scheme: dark" in text
    assert "font-family" in light
    assert "font-family" not in dark

    light_colors = {name for name in light if name != "font-family"}
    dark_colors = set(dark)
    missing = sorted(light_colors - dark_colors)
    extra = sorted(dark_colors - light_colors)
    assert not missing, f"暗色缺少 token 定义：{missing}"
    assert not extra, f"暗色多出亮色没有的 token：{extra}"

    required = {
        "primary-color",
        "primary-hover",
        "danger-color",
        "success-color",
        "warning-color",
        "bg-color",
        "card-bg",
        "text-primary",
        "text-secondary",
        "border-color",
        "surface-raised",
        "surface-input",
        "surface-input-focus",
        "text-strong",
        "text-title",
        "text-body",
        "text-label",
        "text-muted",
        "text-on-accent",
        "border-strong",
        "border-input",
        "primary-fill",
        "primary-fill-hover",
        "primary-soft",
        "primary-soft-strong",
        "primary-border",
        "danger-text",
        "danger-bg",
        "danger-border",
        "success-text",
        "success-bg",
        "success-border",
        "warning-text",
        "warning-bg",
        "warning-border",
        "toast-bg",
        "toast-text",
        "toast-action-color",
        "login-card-bg",
        "login-card-shadow",
    }
    assert required <= light_colors, f"缺少语义 token：{sorted(required - light_colors)}"
    assert required <= dark_colors, f"暗色缺少语义 token：{sorted(required - dark_colors)}"


def test_no_color_literals_outside_variables_css() -> None:
    violations: list[str] = []
    for path in _scanned_files():
        text = _strip_comments(path.read_text(encoding="utf-8"), path.suffix)
        for match in COLOR_RE.finditer(text):
            literal = match.group(0)
            if literal in ALLOWED_COLOR_KEYWORDS:
                continue
            line = text.count("\n", 0, match.start()) + 1
            violations.append(f"{path.relative_to(ROOT)}:{line}: {literal}")
    assert not violations, "variables.css 之外仍有颜色字面值：\n" + "\n".join(violations)


def test_every_var_reference_is_defined() -> None:
    defined: set[str] = set()
    references: set[str] = set()
    css_texts = {
        path: _strip_comments(path.read_text(encoding="utf-8"), ".css")
        for path in CSS_DIR.rglob("*.css")
    }
    for text in css_texts.values():
        defined.update(re.findall(r"(--[\w-]+)\s*:", text))
        references.update(re.findall(r"var\(\s*(--[\w-]+)", text))

    missing = sorted(references - defined)
    assert not missing, f"var() 引用了未定义的 token：{missing}"

    level_tokens = re.findall(r"(--level-[\w-]+)\s*:\s*([^;]+);", "\n".join(css_texts.values()))
    assert {name for name, _ in level_tokens} >= {"--level-color", "--level-bg"}
    for name, value in level_tokens:
        assert value.strip().startswith("var("), f"局部 token {name} 必须引用语义 token"


def test_css_avoids_scheme_media_queries_and_inversion_hacks() -> None:
    css_text = "\n".join(
        path.read_text(encoding="utf-8") for path in CSS_DIR.rglob("*.css")
    )
    assert "@media (prefers-color-scheme" not in css_text
    assert "filter: invert" not in css_text
    assert "mix-blend-mode" not in css_text


def test_primary_buttons_use_fill_token_not_text_accent() -> None:
    """主色底必须用 --primary-fill：暗色下 --primary-color 是浅蓝，白字对比度不够。"""
    css_texts = {
        path.relative_to(CSS_DIR).as_posix(): path.read_text(encoding="utf-8")
        for path in CSS_DIR.rglob("*.css")
    }
    violations = [
        name
        for name, text in css_texts.items()
        if "background: var(--primary-color)" in text
        or "background-color: var(--primary-color)" in text
    ]
    assert not violations, f"主色背景应使用 --primary-fill：{violations}"
    assert "background: var(--primary-fill)" in css_texts["components.css"]
    assert "background: var(--primary-fill)" in css_texts["modules/filter.css"]


def test_theme_js_only_touches_console_theme_storage_key() -> None:
    script = (STATIC_DIR / "js" / "theme.js").read_text(encoding="utf-8")
    assert "var STORAGE_KEY = 'console_theme';" in script
    calls = re.findall(
        r"localStorage\.(getItem|setItem|removeItem)\(([^)]*)\)", script
    )
    assert {method for method, _ in calls} == {"getItem", "setItem"}
    assert all(
        argument.split(",", 1)[0].strip() == "STORAGE_KEY" for _, argument in calls
    )
    assert "sidebar_collapsed" not in script
    assert "search_filters" not in script
    assert "manual_filter_" not in script
