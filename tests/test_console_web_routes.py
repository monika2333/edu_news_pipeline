from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from src.console.app import create_app
from src.console.security import ConsoleUser, require_console_user


def _anonymous_console_user() -> ConsoleUser:
    return ConsoleUser(method="test")


def _build_client() -> TestClient:
    app = create_app()
    app.dependency_overrides[require_console_user] = _anonymous_console_user
    return TestClient(app)


def _build_editor_client() -> TestClient:
    app = create_app()
    app.dependency_overrides[require_console_user] = lambda: ConsoleUser(
        method="test",
        user_id="editor-id",
        username="editor",
        display_name="周一编辑",
        role="duty_editor",
    )
    return TestClient(app)


def test_console_root_restores_admin_last_view_in_browser() -> None:
    client = _build_client()

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 200
    assert 'content="0; url=/admin/duty-summary"' in response.text
    assert 'src="/static/js/admin_last_view.js?v=' in response.text
    assert 'data-admin-view-user="tester"' in response.text
    assert 'data-admin-view-default="/admin/duty-summary"' in response.text


def test_console_root_redirects_duty_editor_to_duty_workspace() -> None:
    client = _build_editor_client()

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].endswith("/duty")


def test_admin_main_views_persist_only_their_canonical_view() -> None:
    client = _build_client()

    expected_views = {
        "/admin/duty-summary": "/admin/duty-summary",
        "/manual_filter": "/manual_filter",
        "/admin/review": "/admin/review",
        "/submission-archive": "/submission-archive",
    }
    for path, expected_view in expected_views.items():
        response = client.get(path)

        assert response.status_code == 200
        assert 'src="/static/js/admin_last_view.js?v=' in response.text
        assert 'data-admin-view-user="tester"' in response.text
        assert f'data-admin-view-current="{expected_view}"' in response.text

    for path in (
        "/submission-archive/new",
        "/submission-archive/link-queue",
        "/submission-archive/report-1",
    ):
        assert "/static/js/admin_last_view.js" not in client.get(path).text


def test_admin_last_view_script_scopes_storage_by_user_and_validates_paths() -> None:
    script = (
        Path(__file__).parents[1]
        / "src/console/web_static/js/admin_last_view.js"
    ).read_text(encoding="utf-8")

    assert "`admin_last_view:${userId}`" in script
    assert "allowedViews.has(savedView)" in script
    assert "localStorage.setItem(storageKey, currentView)" in script
    assert "window.location.replace(target)" in script


def test_removed_console_pages_are_not_registered() -> None:
    client = _build_client()

    assert client.get("/dashboard").status_code == 404
    assert client.get("/articles/search").status_code == 404
    assert client.get("/api/admin/duty-summary/uncovered").status_code == 404


def test_favicon_is_served_from_the_static_assets() -> None:
    app = create_app()

    response = TestClient(app).get("/favicon.ico")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert response.content.startswith(b"<svg")
    assert "/favicon.ico" not in app.openapi()["paths"]


def test_content_drawer_labels_created_at_as_collection_time() -> None:
    script = (
        Path(__file__).parents[1]
        / "src/console/web_static/js/manual_filter/content_drawer.js"
    ).read_text(encoding="utf-8")

    # 只锁语义，不锁实现细节（如格式化函数名）：
    # 「收录时间」这个称谓必须与 data.created_at 绑定在同一处展示，且不得出现「抓取时间」。
    assert re.search(r"收录时间[^\n]*data\.created_at", script)
    assert "抓取时间" not in script


def test_account_page_exposes_personal_password_change_form() -> None:
    response = _build_client().get("/account")
    login_stylesheet = (
        Path(__file__).parents[1]
        / "src/console/web_static/css/modules/login.css"
    ).read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "<title>修改密码 · 教育新闻控制台</title>" in response.text
    assert 'id="btn-logout" type="button">退出登录</button>' in response.text
    assert 'id="change-password-form"' in response.text
    assert 'minlength="10"' not in response.text
    assert "至少 10 位" not in response.text
    assert 'src="/static/js/account.js?v=' in response.text
    assert 'href="/">← 返回工作台</a>' in response.text
    assert ".account-card .login-heading h1" in login_stylesheet
    assert ".account-card .login-form input," in login_stylesheet
    assert "min-height: 42px;" in login_stylesheet
    assert ".login-page {" in login_stylesheet
    assert "box-sizing: border-box;" in login_stylesheet


def test_register_page_has_no_fixed_password_length_requirement() -> None:
    response = TestClient(create_app()).get("/register")

    assert response.status_code == 200
    assert 'minlength="10"' not in response.text
    assert "至少 10 位" not in response.text


def test_submission_archive_pages_follow_role_permissions() -> None:
    admin = _build_client()
    editor = _build_editor_client()

    assert admin.get("/submission-archive").status_code == 200
    assert admin.get("/submission-archive/new").status_code == 200
    assert admin.get("/submission-archive/link-queue").status_code == 200
    assert admin.get("/submission-archive/report-1").status_code == 200
    search_response = admin.get("/submission-archive/search", follow_redirects=False)
    assert search_response.status_code == 302
    assert search_response.headers["location"].endswith("/submission-archive")

    # 值班编辑访问存档页面时重定向回值班工作区，而不是看到管理员页面。
    for path in (
        "/submission-archive",
        "/submission-archive/new",
        "/submission-archive/link-queue",
        "/submission-archive/search",
        "/submission-archive/report-1",
    ):
        response = editor.get(path, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"].endswith("/duty")


def test_submission_archive_separates_all_type_filter_from_report_types() -> None:
    response = _build_client().get("/submission-archive")
    stylesheet = "".join(
        (
            Path(__file__).parents[1]
            / f"src/console/web_static/css/modules/submission_archive/{name}.css"
        ).read_text(encoding="utf-8")
        for name in (
            "browser",
            "item_card",
            "widgets",
            "create",
            "link_queue",
            "responsive",
        )
    )

    assert response.status_code == 200
    html = response.text
    all_filter = 'data-type="" type="button">全部</button>'
    divider = '<span class="archive-type-divider" aria-hidden="true"></span>'
    report_filter = 'data-type="zongbao" type="button">综报</button>'
    assert html.index(all_filter) < html.index(divider) < html.index(report_filter)
    assert ".archive-type-divider {" in stylesheet
    assert "background: #cbd5e1;" in stylesheet


def test_submission_archive_browser_wires_content_drawer() -> None:
    response = _build_client().get("/submission-archive")

    assert response.status_code == 200
    html = response.text
    assert 'id="content-drawer"' in html
    assert "/static/css/modules/content_drawer.css?v=" in html
    assert "/static/js/submission_archive/content_drawer.js?v=" in html
    # 抽屉只挂在存档库视图，录入与回链确认视图不渲染
    for path in ("/submission-archive/new", "/submission-archive/link-queue"):
        assert 'id="content-drawer"' not in _build_client().get(path).text


def test_submission_archive_pages_include_search_drawer() -> None:
    # 检索抽屉（报送存档检索 + 全库文章检索）在存档各视图都应有入口
    for path in (
        "/submission-archive",
        "/submission-archive/new",
        "/submission-archive/link-queue",
    ):
        html = _build_client().get(path).text
        assert 'id="search-drawer-toggle"' in html
        assert 'id="search-drawer"' in html
        assert "/static/css/modules/search.css" in html
        assert "/static/js/manual_filter/search_drawer.js?v=" in html


def test_admin_page_separates_user_search_from_account_creation(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.console.web_routes.generate_shifts",
        lambda **kwargs: {"inserted": 0},
    )
    response = _build_client().get("/admin")
    root = Path(__file__).parents[1]
    admin_script = (
        root / "src/console/web_static/js/admin.js"
    ).read_text(encoding="utf-8")
    admin_stylesheet = (
        root / "src/console/web_static/css/modules/admin.css"
    ).read_text(encoding="utf-8")
    shift_date_script = (
        root / "src/console/web_static/js/shift_date.js"
    ).read_text(encoding="utf-8")

    assert response.status_code == 200
    html = response.text
    assert "<title>用户与排班 · 新闻筛选控制台</title>" in html
    assert '<div class="container admin-shell">' in html
    assert '<p class="admin-header-eyebrow">控制台管理</p>' in html
    assert "<h1>用户与排班</h1>" in html
    assert 'class="admin-page-heading"' not in html
    assert 'aria-label="管理员主视图"' in html
    assert 'class="admin-view-link" href="/manual_filter">全量新闻筛选</a>' in html
    assert 'class="admin-view-link" href="/admin/duty-summary">值班结果筛选</a>' in html
    assert html.index('href="/admin/duty-summary"') < html.index('href="/manual_filter"')
    assert 'class="admin-view-stage-divider" aria-hidden="true"' in html
    assert 'class="admin-view-link" href="/admin/review">汇总审阅</a>' in html
    assert '<details class="account-menu">' in html
    assert 'class="account-menu-item is-active" href="/admin"' in html
    assert 'aria-current="page">用户与排班</a>' in html
    assert 'class="account-menu-item" href="/account">修改密码</a>' in html
    assert 'class="account-menu-item" id="btn-logout"' in html
    assert 'type="button">退出登录</button>' in html
    assert "返回人工筛选" not in html
    assert ">值班汇总</a>" not in html
    assert 'id="admin-user-search"' in html
    assert 'href="/admin/users/new">创建账号</a>' in html
    assert 'id="create-user-form"' not in html
    assert 'name="password"' not in html
    assert "<h2>账号管理</h2>" in html
    assert "<h2>轮值模板</h2>" in html
    assert "<h2>排班日历</h2>" in html
    assert html.index("<h2>轮值模板</h2>") < html.index("<h2>排班日历</h2>")
    assert html.index("<h2>排班日历</h2>") < html.index("<h2>账号管理</h2>")
    assert "<p>账号</p>" not in html
    assert "<h2>控制台用户</h2>" not in html
    assert "<h2>周一至周日</h2>" not in html
    assert "<p>具体班次</p>" not in html
    assert "<th>首选值班日</th>" in html
    assert "<th>值班日期</th>" not in html
    assert 'id="shift-calendar-month"' in html
    assert 'id="shift-calendar-grid"' in html
    assert 'id="btn-calendar-previous"' in html
    assert 'id="btn-calendar-next"' in html
    assert 'id="btn-calendar-today"' in html
    assert 'id="shift-editor-modal"' in html
    assert 'id="shift-editor-assignee"' in html
    assert 'id="btn-clear-shift-assignee"' in html
    assert 'id="btn-restore-shift-template"' in html
    assert 'src="/static/js/shift_date.js?v=' in html
    assert 'src="/static/js/manual_filter/utils.js?v=' in html
    assert "editor.preferred_weekday" in admin_script
    assert "首选${preference}" in admin_script
    assert "const query = elements.userSearch.value" in admin_script
    assert "window.addEventListener('pageshow', renderUsers)" in admin_script
    assert "没有找到匹配的用户。" in admin_script
    assert 'data-user-action="delete">删除</button>' in admin_script
    assert admin_script.index('data-user-action="password"') < admin_script.index(
        'data-user-action="toggle"'
    )
    assert admin_script.index('data-user-action="toggle"') < admin_script.index(
        'data-user-action="delete"'
    )
    assert 'id="delete-user-modal"' in html
    assert 'id="delete-user-confirmation"' in html
    assert 'id="btn-confirm-delete-user"' in html
    assert "openDeleteUserModal(user, button)" in admin_script
    assert "至少 10 位" not in admin_script
    assert "elements.deleteInput.value !== '确认删除'" in admin_script
    assert "button.textContent = '确认删除'" not in admin_script
    assert "method: 'DELETE'" in admin_script
    assert "window.formatDutyShiftDate(coverage.coverage_end)" in admin_script
    assert "formatDateTime(shift.starts_at)" not in admin_script
    assert "function renderShiftCalendar()" in admin_script
    assert "function updateActiveShift(body, successMessage)" in admin_script
    assert "{ user_id: userId, cancelled: false }" in admin_script
    assert "updateActiveShift({ cancelled: true }, '已清除当天负责人')" in admin_script
    assert "buildUndoToastAction" in admin_script
    assert "timeZone: businessTimeZone" in shift_date_script
    assert "month: 'long'" in shift_date_script
    assert ".admin-header-eyebrow," in admin_stylesheet
    assert ".admin-panel-heading h2" in admin_stylesheet
    assert "font-size: 1.25rem;" in admin_stylesheet
    assert "font-size: 1.45rem;" not in admin_stylesheet
    assert ".admin-delete-user.is-confirming" not in admin_stylesheet
    assert ".admin-modal-content" in admin_stylesheet
    assert ".admin-confirm-delete:disabled" in admin_stylesheet
    assert ".shift-calendar-grid" in admin_stylesheet
    assert ".shift-calendar-day.is-today" in admin_stylesheet
    assert "min-height: 88px;" in admin_stylesheet
    assert "font-size: 1rem;" in admin_stylesheet


def test_admin_create_user_page_reuses_account_form_layout() -> None:
    response = _build_client().get("/admin/users/new")
    root = Path(__file__).parents[1]
    create_script = (
        root / "src/console/web_static/js/admin_user_create.js"
    ).read_text(encoding="utf-8")

    assert response.status_code == 200
    html = response.text
    assert "<title>创建账号 · 用户与排班</title>" in html
    assert '<body class="login-page">' in html
    assert '<main class="login-card account-card">' in html
    assert 'class="auth-back-link" href="/admin"' in html
    assert "← 返回用户与排班" in html
    assert "<h1>创建账号</h1>" in html
    assert "ACCOUNT MANAGEMENT" in html
    assert 'aria-label="管理员主视图"' not in html
    assert 'id="btn-logout"' not in html
    assert 'id="admin-create-user-form"' in html
    assert 'name="display_name"' in html
    assert 'name="username"' in html
    assert 'name="role"' in html
    assert 'name="preferred_weekday"' in html
    assert 'name="password"' in html
    assert 'minlength="10"' not in html
    assert "至少 10 位" not in html
    assert 'href="/admin">取消</a>' not in html
    assert 'href="/static/css/modules/admin.css' not in html
    assert 'href="/static/css/modules/login.css?v=' in html
    assert 'src="/static/js/admin_user_create.js?v=' in html
    assert "fetch('/api/admin/users'" in create_script
    assert "window.location.assign('/admin?created=1')" in create_script


def test_duty_editor_cannot_open_admin_create_user_page() -> None:
    response = _build_editor_client().get("/admin/users/new")

    assert response.status_code == 403


def test_duty_page_reuses_manual_filter_workspace_without_admin_entries() -> None:
    response = _build_editor_client().get("/duty")
    scripts_dir = (
        Path(__file__).parents[1]
        / "src/console/web_static/js/manual_filter"
    )
    workspace_script = (scripts_dir / "workspace.js").read_text(encoding="utf-8")
    filter_actions_script = (
        scripts_dir / "filter_tab_actions.js"
    ).read_text(encoding="utf-8")
    filter_data_script = (
        scripts_dir / "filter_tab_data.js"
    ).read_text(encoding="utf-8")
    init_script = (scripts_dir / "init.js").read_text(encoding="utf-8")
    utils_script = (scripts_dir / "utils.js").read_text(encoding="utf-8")
    finalization_script = (
        scripts_dir / "review_tab_finalization.js"
    ).read_text(encoding="utf-8")

    assert response.status_code == 200
    html = response.text
    assert "<title>新闻筛选控制台</title>" in html
    assert "<h1>新闻筛选控制台</h1>" in html
    assert 'data-workspace-mode="duty"' in html
    assert "/static/js/admin_last_view.js" not in html
    assert 'class="stats"' in html
    assert 'id="btn-refresh"' in html
    assert 'aria-describedby="refresh-cluster-hint"' in html
    assert 'aria-label="管理员主视图"' not in html
    assert 'aria-label="值班编辑主视图"' not in html
    assert 'href="/submission-archive"' not in html
    assert 'id="workspace-shift-select"' in html
    assert 'data-tab="filter">筛选</button>' in html
    assert 'data-tab="review">已选结果</button>' in html
    assert 'data-tab="discard">放弃</button>' in html
    assert 'class="workspace-tabs-row"' in html
    assert 'class="workspace-tab-actions"' in html
    assert 'data-workspace-action-tab="filter"' in html
    assert 'data-workspace-action-tab="review"' in html
    assert 'id="duty-finalization-status"' in html
    assert html.index('data-tab="discard"') < html.index('id="btn-refresh"')
    assert html.index('id="btn-refresh"') < html.index('id="filter-tab"')
    assert html.index('id="duty-finalization-status"') < html.index('id="review-tab"')
    assert '/static/js/manual_filter/workspace.js?v=' in html
    assert '/static/js/shift_date.js?v=' in html
    assert '/static/js/manual_filter/filter_tab_data.js?v=' in html
    assert 'href="/admin">用户与排班</a>' not in html
    assert 'href="/admin/duty-summary">值班汇总</a>' not in html
    assert 'id="btn-check-duplicates"' in html
    assert 'id="duplicate-review-modal"' in html
    assert html.index('id="btn-check-duplicates"') < html.index('id="btn-toggle-sort"')
    assert 'id="btn-archive"' not in html
    assert 'id="btn-filter-bulk-discard"' in html
    assert 'id="search-drawer-toggle"' in html
    assert 'id="stat-exported"' not in html
    assert "已放弃" not in html
    assert 'id="btn-finalize-review"' in html
    assert "定稿并清空列表" in html
    assert 'id="duty-finalization-status"' in html
    assert 'id="duty-finalization-status-text"' in html
    assert 'id="btn-restore-finalization"' in html
    assert "撤回定稿" in html
    assert 'id="btn-finalized-batches"' not in html
    assert 'id="finalization-history-modal"' not in html
    assert "查看已定稿批次" not in html
    assert "/static/js/manual_filter/review_tab_finalization.js?v=" in html
    assert '<details class="account-menu">' in html
    assert '<summary class="account-menu-trigger current-user" id="current-user">' in html
    assert 'class="account-menu-item" href="/admin"' not in html
    assert 'class="account-menu-item" href="/account">修改密码</a>' in html
    assert 'class="account-menu-item" id="btn-logout" type="button">退出登录</button>' in html
    assert "window.formatDutyShiftDate(shift.ends_at)" in workspace_script
    assert "formatWorkspaceDateTime" not in workspace_script
    assert "window.fetch(`${API_BASE}/edit`, options)" in workspace_script
    assert "window.fetch(`${API_BASE}/decide`, options)" in workspace_script
    assert "window.fetch(`${API_BASE}/duplicate-check`, options)" in workspace_script
    assert "reviews/${encodeURIComponent(articleId)}" not in workspace_script
    assert "action === '/finalizations'" in workspace_script
    assert "action.startsWith('/finalizations/')" in workspace_script
    assert "if (elements.reviewList)" in init_script
    assert "!IS_DUTY_WORKSPACE && elements.reviewList" not in init_script
    assert "syncWorkspaceTabActions(state.currentTab);" in utils_script
    assert "action.hidden = action.dataset.workspaceActionTab !== currentTab;" in utils_script
    assert "async function loadDutyClusters" not in workspace_script
    assert "include_items: 'true'" in workspace_script
    assert "['region', 'sentiment', 'force_refresh']" in workspace_script
    assert "`${API_BASE}/clusters?${clusterParams.toString()}`" in workspace_script
    assert "cluster: searchMode ? 'false' : 'true'" in filter_data_script
    assert "searchMode || state.hideSubmitted" not in filter_data_script
    assert "loadAllDutyItems('pending')" not in workspace_script
    assert "function dutyCandidateBackendParams" in workspace_script
    assert "async function dutyCandidatesResponse" in workspace_script
    assert "dutyBulkDiscardResponse" not in workspace_script
    assert "loadAllDutyCandidateMatches" not in workspace_script
    assert (
        "if (action === '/bulk-discard') "
        "return window.fetch(`${API_BASE}/bulk-discard`, options);"
    ) in workspace_script
    assert "值班账号不能执行此操作" in workspace_script
    assert "async function dutyStatsResponse" not in workspace_script
    assert "`${API_BASE}/stats${url.search}`" in workspace_script
    assert "if (response.ok) invalidateDutyListCache();" in workspace_script
    assert "detachDutyFilterRemoval(removal);" in filter_actions_script
    assert "updateDutyFilterDecisionCounts(status, 1, 1, reportType);" in filter_actions_script
    assert "restoreDutyFilterRemoval(removal, undoMutation.versions || {});" in filter_actions_script
    assert "撤销失败，原操作保持不变" in filter_actions_script
    card_handler = filter_actions_script.split(
        "async function handleCardDecisionChange",
        maxsplit=1,
    )[1].split(
        "async function handleClusterDecisionChange",
        maxsplit=1,
    )[0]
    assert card_handler.index("await submitDecisions") < card_handler.index(
        "detachDutyFilterRemoval(removal);"
    )
    duty_card_branch = card_handler.split(
        "if (IS_DUTY_WORKSPACE)",
        maxsplit=1,
    )[1].split("} else {", maxsplit=1)[0]
    assert "loadFilterData" not in duty_card_branch
    assert "loadStats" not in duty_card_branch
    assert "const pageEmptied = detachDutyFilterRemoval(removal);" in duty_card_branch
    assert "if (pageEmptied) await reloadFilterPageAfterRemoval();" in duty_card_branch
    assert "{ reloadOnUndo: pageEmptied, reportType }" in duty_card_branch
    assert "if (card.isConnected) setInputsDisabled(radios, false);" not in card_handler
    assert "finally {\n        setInputsDisabled(radios, false);\n    }" in card_handler
    cluster_handler = filter_actions_script.split(
        "async function handleClusterDecisionChange",
        maxsplit=1,
    )[1].split(
        "function collectCardEdits",
        maxsplit=1,
    )[0]
    duty_cluster_branch = cluster_handler.split(
        "if (IS_DUTY_WORKSPACE)",
        maxsplit=1,
    )[1].split("} else {", maxsplit=1)[0]
    assert "const pageEmptied = detachDutyFilterRemoval(removal);" in duty_cluster_branch
    assert "if (pageEmptied) await reloadFilterPageAfterRemoval();" in duty_cluster_branch
    assert "{ reloadOnUndo: pageEmptied, reportType }" in duty_cluster_branch
    assert "if (cluster.isConnected) setInputsDisabled(radios, false);" not in cluster_handler
    assert "finally {\n        setInputsDisabled(radios, false);\n    }" in cluster_handler
    assert "return pageEmptied;" in filter_actions_script
    discard_handler = filter_actions_script.split(
        "async function discardRemainingItems",
        maxsplit=1,
    )[1].split(
        "async function bulkDiscard",
        maxsplit=1,
    )[0]
    duty_discard_branch = discard_handler.split(
        "if (IS_DUTY_WORKSPACE)",
        maxsplit=1,
    )[1].split("} else {", maxsplit=1)[0]
    assert "await reloadFilterPageAfterRemoval();" in duty_discard_branch
    assert "{ reloadOnUndo: true }" in duty_discard_branch
    assert "await Promise.all([loadFilterData(), loadStats()]);" in filter_actions_script
    assert "window.scrollTo({ top: 0, behavior: 'auto' });" in filter_actions_script
    assert "编辑保存失败，请重试" in (
        scripts_dir / "filter_tab_data.js"
    ).read_text(encoding="utf-8")
    assert "loadFilterData({ forceClusterRefresh: true })" not in filter_actions_script
    assert "item.content_markdown" not in workspace_script
    assert "function loadDutyFinalizationStatus()" in finalization_script
    assert "function renderDutyFinalizationStatus(finalization)" in finalization_script
    assert "payload.finalization" in finalization_script
    assert "撤回定稿" not in finalization_script
    assert "FinalizationHistory" not in finalization_script
    assert "finalization-history" not in finalization_script


def test_duty_workspace_defaults_to_latest_ended_shift_before_upcoming() -> None:
    workspace_script = (
        Path(__file__).parents[1]
        / "src/console/web_static/js/manual_filter/workspace.js"
    ).read_text(encoding="utf-8")
    chooser = workspace_script.split(
        "function chooseInitialWorkspaceShift", 1
    )[1].split("function escapeWorkspaceHtml", 1)[0]

    assert chooser.index("shift.status === 'active'") < chooser.index(
        "shift.status === 'ended'"
    )
    assert chooser.index("shift.status === 'ended'") < chooser.index(
        "shift.status === 'upcoming'"
    )
    assert (
        "return left.status === 'ended' ? rightTime - leftTime : leftTime - rightTime;"
        in workspace_script
    )
    assert "const initial = chooseInitialWorkspaceShift(shifts);" in workspace_script


def test_admin_manual_filter_keeps_admin_only_entries() -> None:
    response = _build_client().get("/manual_filter")
    root = Path(__file__).parents[1]
    filter_render_script = (
        root / "src/console/web_static/js/manual_filter/filter_tab_render.js"
    ).read_text(encoding="utf-8")
    filter_core_script = (
        root / "src/console/web_static/js/manual_filter/core.js"
    ).read_text(encoding="utf-8")
    filter_init_script = (
        root / "src/console/web_static/js/manual_filter/init.js"
    ).read_text(encoding="utf-8")
    components_stylesheet = (
        root / "src/console/web_static/css/components.css"
    ).read_text(encoding="utf-8")

    assert response.status_code == 200
    html = response.text
    assert "<title>新闻筛选控制台 · 全量新闻筛选</title>" in html
    assert "<h1>新闻筛选控制台</h1>" in html
    assert 'data-workspace-mode="admin"' in html
    assert 'aria-label="管理员主视图"' in html
    assert 'class="admin-view-link is-active" href="/manual_filter"' in html
    assert 'aria-current="page"' in html
    assert "全量新闻筛选" in html
    assert 'class="admin-view-link" href="/admin/duty-summary">值班结果筛选</a>' in html
    assert html.index('href="/admin/duty-summary"') < html.index('href="/manual_filter"')
    assert 'class="admin-view-stage-divider" aria-hidden="true"' in html
    assert 'class="admin-view-link" href="/admin/review"' in html
    assert 'class="stats"' not in html
    assert 'id="stat-pending"' not in html
    assert 'id="stat-selected"' not in html
    assert 'id="stat-backup"' not in html
    assert 'data-tab="review"' not in html
    assert 'id="review-tab"' not in html
    assert 'id="btn-check-duplicates"' not in html
    assert 'id="btn-archive"' not in html
    assert 'id="btn-finalize-review"' not in html
    assert 'id="duty-finalization-status"' not in html
    assert 'id="btn-restore-finalization"' not in html
    assert 'id="btn-filter-bulk-discard"' in html
    assert 'id="filter-search-clear"' in html
    assert 'class="review-search-clear"' in html
    assert 'aria-label="清除检索条件"' in html
    assert 'id="btn-filter-clear"' not in html
    assert "filterSearchClear: document.getElementById('filter-search-clear')" in filter_core_script
    assert "function syncFilterSearchClearButton()" in filter_render_script
    assert "elements.filterSearchClear.addEventListener('click', async () => {" in filter_init_script
    assert 'id="search-drawer-toggle"' in html
    assert 'id="btn-refresh"' in html
    assert 'aria-describedby="refresh-cluster-hint"' in html
    assert "刷新会重新聚类，可能需要等待约 1 分钟" in html
    assert 'class="workspace-tabs-row"' in html
    assert 'class="workspace-tab-actions"' in html
    assert html.index('data-tab="discard"') < html.index('id="btn-refresh"')
    assert html.index('id="btn-refresh"') < html.index('id="filter-tab"')
    assert '<details class="account-menu">' in html
    assert '<summary class="account-menu-trigger current-user" id="current-user">' in html
    assert 'class="btn btn-secondary" href="/admin">用户与排班</a>' not in html
    assert 'class="account-menu-item" href="/admin">用户与排班</a>' in html
    assert 'class="account-menu-item" href="/account">修改密码</a>' in html
    assert 'class="account-menu-item" id="btn-logout" type="button">退出登录</button>' in html
    assert 'id="stat-exported"' not in html
    assert "已导出" not in html
    assert 'class="empty empty-state"' in filter_render_script
    assert ".empty-state {" in components_stylesheet
    assert "border: 1px dashed #cbd5e1;" in components_stylesheet


def test_admin_review_is_an_independent_workspace() -> None:
    response = _build_client().get("/admin/review")

    assert response.status_code == 200
    html = response.text
    assert "<title>新闻筛选控制台 · 汇总审阅</title>" in html
    assert 'data-initial-tab="review"' in html
    assert 'href="/admin/review"' in html
    assert 'class="admin-view-link is-active" href="/admin/review"' in html
    assert 'class="admin-view-stage-divider" aria-hidden="true"' in html
    assert 'aria-current="page"' in html
    assert 'id="review-tab"' in html
    assert 'id="filter-tab"' not in html
    assert 'id="discard-tab"' not in html
    assert 'id="btn-check-duplicates"' in html
    assert 'id="btn-archive"' in html
    assert 'class="stats"' not in html
    assert 'id="btn-refresh"' not in html
    assert "刷新会重新聚类" not in html
    assert 'id="filter-hide-submitted"' not in html


def test_duty_summary_collapses_shift_panel_by_default(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.console.web_routes.generate_shifts",
        lambda **kwargs: {"inserted": 0},
    )

    response = _build_client().get("/admin/duty-summary")

    assert response.status_code == 200
    assert "<title>新闻筛选控制台 · 值班结果筛选</title>" in response.text
    assert "<h1>新闻筛选控制台</h1>" in response.text
    assert '<div class="container">' in response.text
    assert '<div class="header-right">' in response.text
    assert 'class="summary-layout is-shifts-collapsed"' in response.text
    assert 'class="filter-sidebar summary-shifts"' in response.text
    assert 'id="summary-shifts-panel" hidden' in response.text
    assert 'id="btn-toggle-shifts"' in response.text
    assert 'aria-expanded="false"' in response.text
    assert 'aria-label="管理员主视图"' in response.text
    assert 'class="admin-view-link" href="/manual_filter">全量新闻筛选</a>' in response.text
    assert 'href="/admin/duty-summary" aria-current="page">' in response.text
    assert response.text.index('href="/admin/duty-summary"') < response.text.index(
        'href="/manual_filter"'
    )
    assert 'class="admin-view-stage-divider" aria-hidden="true"' in response.text
    assert 'class="admin-view-link" href="/admin/review">汇总审阅</a>' in response.text
    assert "值班结果筛选" in response.text
    assert "值班结果汇总" not in response.text
    assert 'id="btn-summary-refresh"' not in response.text
    assert '/static/js/shift_date.js?v=' in response.text
    assert '<details class="account-menu">' in response.text
    assert (
        'class="admin-view-link" href="/submission-archive">报送存档</a>'
        in response.text
    )
    assert (
        'class="account-menu-item" href="/submission-archive">报送存档</a>'
        not in response.text
    )
    assert 'class="account-menu-item" href="/admin">用户与排班</a>' in response.text
    assert 'href="/static/css/layout.css"' in response.text
    assert 'href="/static/css/modules/filter.css"' in response.text
    assert 'href="/static/css/modules/review.css?v=' in response.text
    assert 'href="/static/css/modules/search.css"' in response.text

    stylesheet = (
        Path(__file__).parents[1]
        / "src/console/web_static/css/modules/duty_summary.css"
    ).read_text(encoding="utf-8")
    assert ".summary-shell" not in stylesheet
    assert ".summary-header {" not in stylesheet
    assert "grid-template-columns: minmax(0, 1fr) 240px;" in stylesheet
    assert "background: var(--card-bg);" in stylesheet
    assert "box-shadow: 0 1px 3px rgb(0 0 0 / 5%);" in stylesheet
    assert "#summary-shift-list" in stylesheet
    assert ".summary-shift-card {" in stylesheet
    assert "width: 100%;" in stylesheet
    assert ".summary-shift-owner" not in stylesheet
    assert ".summary-shift-counts" not in stylesheet
    assert ".uncovered-button" not in stylesheet
    assert ".summary-shifts:not([hidden])" in stylesheet
    assert "transform: translateX(18px);" in stylesheet
    assert "overflow: visible;" in stylesheet
    assert ".summary-filter-layout.is-discarded .summary-column-tabs" in stylesheet
    assert ".summary-filter-layout.is-discarded .summary-import-bar .bulk-group" in stylesheet
    assert ".summary-section-heading h2" in stylesheet
    assert ".summary-empty {" not in stylesheet
    assert ".summary-items {" not in stylesheet
    assert ".summary-workspace-context {" in stylesheet
    assert ".summary-filters" not in stylesheet
    assert "font-size: 1rem;" in stylesheet
    assert "font-size: 1.25rem;" in stylesheet
    assert "font-size: 0.78rem;" in stylesheet


def test_duty_summary_exposes_column_tabs_search_and_select_all(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.console.web_routes.generate_shifts",
        lambda **kwargs: {"inserted": 0},
    )
    response = _build_client().get("/admin/duty-summary")
    script = "".join(
        (
            Path(__file__).parents[1]
            / f"src/console/web_static/js/duty_summary/{name}.js"
        ).read_text(encoding="utf-8")
        for name in (
            "core",
            "utils",
            "mutations",
            "import_conflict",
            "render",
            "admin_actions",
            "data",
            "init",
        )
    )

    assert response.status_code == 200
    html = response.text
    assert html.count('data-report-type=') == 4
    assert "综报采纳（0）" in html
    assert "综报备选（0）" in html
    assert "晚报采纳（0）" in html
    assert "晚报备选（0）" in html
    assert "查看历史班次" in html
    assert 'id="summary-shifts-close"' in html
    assert 'class="btn-icon"' in html
    assert 'aria-label="关闭班次"' in html
    assert 'id="search-drawer-toggle"' in html
    assert 'id="search-drawer"' in html
    assert 'id="btn-drawer-search"' in html
    assert 'src="/static/js/manual_filter/utils.js?v=' in html
    assert 'src="/static/js/manual_filter/search_drawer.js?v=' in html
    assert "无有效班次覆盖" not in html
    assert 'id="btn-uncovered"' not in html
    assert "'收起历史班次'" in script
    assert "'查看历史班次'" in script
    assert "shiftsClose: document.getElementById('summary-shifts-close')" in script
    assert "elements.shiftsClose.addEventListener('click'" in script
    assert 'class="summary-shift-date"' in script
    assert 'class="summary-shift-owner"' not in script
    assert 'class="summary-shift-counts"' not in script
    assert 'class="tabs summary-workspace-tabs"' in html
    assert 'class="workspace-tabs-row summary-workspace-row"' in html
    assert 'data-summary-view="filter">筛选</button>' in html
    assert 'data-summary-view="discarded">放弃</button>' in html
    assert html.index('id="summary-context"') < html.index('id="summary-layout"')
    assert 'id="summary-title"' not in html
    assert 'data-admin-discarded=' not in html
    assert 'class="filter-sidebar summary-column-tabs"' in html
    assert 'id="summary-search-input"' in html
    assert 'id="summary-select-all"' in html
    assert 'class="toolbar review-toolbar summary-import-bar"' in html
    assert 'class="search-group"' in html
    assert 'class="bulk-group"' in html
    assert 'class="filter-tab-btn summary-column-tab active"' in html
    assert 'id="summary-report-type"' not in html
    assert 'id="summary-import-target"' in html
    assert 'id="summary-selection-count"' not in html
    assert '<option value="" selected disabled>送入栏目</option>' in html
    assert '<option value="zongbao:selected">综报采纳</option>' in html
    assert '<option value="zongbao:backup">综报备选</option>' in html
    assert '<option value="wanbao:selected">晚报采纳</option>' in html
    assert '<option value="wanbao:backup">晚报备选</option>' in html
    assert "送入当前栏目" not in html
    assert 'id="btn-discard-selected">放弃</button>' in html
    assert 'id="summary-decision"' not in html
    assert 'id="summary-search-clear"' in html
    assert 'class="review-search-clear"' in html
    assert 'id="summary-search-input" class="search-input" type="text"' in html
    assert "function syncSearchClearButton()" in script
    assert "elements.searchClear.addEventListener('click'" in script
    assert 'id="summary-comparison"' not in html
    assert "与管理员结论不同" not in html
    assert "mismatch_only" not in script
    assert 'id="summary-process-filter"' in html
    assert 'data-admin-process-scope="unprocessed">未处理</button>' in html
    assert 'data-admin-process-scope="all">全部</button>' in html
    assert 'summary-process-tab admin-view-link' in html
    assert html.index('id="summary-import-bar"') < html.index('id="summary-process-filter"')
    assert html.index('id="summary-process-filter"') < html.index('id="summary-import-target"')
    assert 'id="btn-import-results"' not in html
    assert "elements.importTarget.addEventListener('change', importSelectedItems)" in script
    assert "buildUndoToastAction" in script
    assert 'id="summary-import-conflict-modal"' in html
    assert 'id="summary-existing-summary"' in html
    assert 'id="summary-existing-source"' in html
    assert 'id="summary-duty-summary"' in html
    assert 'id="summary-duty-source"' in html
    assert 'id="btn-keep-existing"' in html
    assert 'id="btn-keep-duty"' in html
    assert "function getVisibleItems()" in script
    assert "adminProcessScope: 'unprocessed'" in script
    assert "function adminProcessLabel(item)" in script
    assert "function isRecoveredManualDiscard(item)" in script
    assert "if (isRecoveredManualDiscard(item)) return '未处理'" in script
    assert "const discardedActive = !recoveredManualDiscard" in script
    assert "const nextDiscarded = isRecoveredManualDiscard(item)" in script
    assert "function articleCategoryLabel(item)" in script
    assert "item.is_beijing_related ? '京内' : '京外'" in script
    assert "SUMMARY_CATEGORY_ORDER" in script
    assert "const renderSummaryItem = (item) =>" in script
    assert "collapsedCategories" in script
    assert "? '负面'" in script
    assert ": '正面'" in script
    assert 'class="review-group' in script
    assert "review-group-header" in script
    assert "data-category" in script
    assert 'class="summary-admin-process-tag' in script
    assert "elements.processTabs.forEach" in script
    assert "params.set('admin_unprocessed_only', 'true')" in script
    assert "params.set('include_admin_discarded', 'true')" in script
    assert "已采纳" in script
    assert "已放弃" in script
    assert "elements.selectAll.indeterminate" in script
    assert "tab.dataset.reportType" in script
    assert "tab.dataset.targetStatus" in script
    assert "function renderColumnCounts()" in script
    assert "state.adminProcessScope === 'all'" in script
    assert "? `${baseField}_all`" in script
    assert "shift?.[countField]" in script
    assert "elements.title" not in script
    assert "function activeColumnLabel()" not in script
    assert "当前没有待处理新闻" in script
    assert 'class="summary-empty empty-state"' in script
    assert 'class="summary-finalization-tag' in script
    assert "已定稿" in script
    assert "未定稿" in script
    assert "尚未定稿" not in script
    assert "item.finalized_at" in script
    assert "formatDateTime(item.finalized_at)" not in script
    assert "当前筛选没有记录。" not in script
    assert "tab.dataset.summaryView === 'discarded'" in script
    assert "elements.filterLayout.classList.toggle('is-discarded'" in script
    assert "params.set('decision', state.targetStatus)" in script
    assert "params.set('report_type', state.targetReportType)" in script
    assert "function renderImportTargets()" not in script
    assert "function selectedImportTarget()" in script
    assert "elements.importTarget.addEventListener('change'" in script
    assert "function captureImportUndoTargets(articleIds)" in script
    assert "'/api/manual_filter/decide'" in script
    assert "/api/admin/duty-summary/import-preview" in script
    assert "/api/admin/duty-summary/discard-bulk" in script
    assert "elements.discardButton.addEventListener('click', discardSelectedItems)" in script
    assert "conflict_resolutions: conflictResolutions" in script
    assert "function chooseImportConflict(choice)" in script
    assert "window.formatDutyShiftDate(shift.ends_at)" in script
    assert "formatDateTime(shift.starts_at)" not in script
    assert "summary-shift-coverage" not in script
    assert '<article class="article-card summary-item">' in script
    assert '<div class="card-header">' in script
    assert '<div class="summary-title-line">' in script
    assert '<div class="summary-title-tags">' in script
    assert '<div class="meta-row">' in script
    assert "<span>值班：${escapeHtml(item.decision || '未覆盖')}</span>" not in script
    assert "<span>${escapeHtml(item.report_type || '')}</span>" not in script
    assert '<p class="summary-box">' in script
    assert 'data-quick-status="selected"' in script
    assert 'data-quick-status="discarded"' in script
    assert 'data-cancel-selected="${String(selectedActive)}"' in script
    assert 'data-cancel-discarded="${String(discardedActive)}"' in script
    assert "const selectedActive = !discardedActive" in script
    assert "data-admin-undo-action" in script
    assert "function canUndoAdminProcess(item)" in script
    assert "async function resetAdminDecision(button, item)" in script
    assert "async function undoAdminProcessing(button)" in script
    assert "await setManualReviewStatus(item, 'pending')" in script
    assert "已撤回到未处理" in script
    assert "buildUndoToastAction" in script
    assert "async function quickDecideItem(button)" in script
    assert "async function setAdminDiscarded(button, discarded)" in script
    assert "/api/admin/duty-summary/discard" in script
    assert "params.set('admin_discarded_only', 'true')" in script
    assert 'data-admin-discard-action="restore"' in script
    assert "state.uncovered" not in script
    assert "/api/admin/duty-summary/uncovered" not in script
    assert "window.confirm" not in script


def test_duplicate_check_button_is_before_sort_mode() -> None:
    response = _build_client().get("/admin/review")
    scripts_dir = Path(__file__).parents[1] / "src/console/web_static/js/manual_filter"
    modal_script = (scripts_dir / "review_duplicates_modal.js").read_text(
        encoding="utf-8"
    )
    controller_script = (scripts_dir / "review_tab_duplicates.js").read_text(
        encoding="utf-8"
    )

    assert response.status_code == 200
    html = response.text
    duplicate_modal = html.split('id="duplicate-review-modal"', 1)[1].split(
        "<!-- Toast -->",
        1,
    )[0]
    assert html.index('id="btn-check-duplicates"') < html.index('id="btn-toggle-sort"')
    assert 'id="duplicate-review-modal"' in html
    assert 'id="duplicate-review-select-all"' in html
    assert 'id="duplicate-review-bulk-status"' in html
    assert 'id="btn-duplicate-prev-group"' in html
    assert 'id="btn-duplicate-next-group"' in html
    assert 'id="btn-recheck-duplicates"' not in html
    assert 'id="btn-close-duplicate-review"' not in html
    assert '>关闭并刷新列表</button>' in html
    assert '/static/css/modules/review.css?v=' in html
    assert '/static/js/manual_filter/review_duplicates_state.js?v=' in html
    assert '/static/js/manual_filter/review_duplicates_modal.js?v=' in html
    assert '<option value="pending">待处理</option>' not in duplicate_modal
    assert "['pending', '待处理']" not in modal_script
    assert "value === 'pending'" not in controller_script
    assert "pending_ids" not in controller_script


def test_sort_mode_hides_incompatible_review_toolbar_controls() -> None:
    root = Path(__file__).parents[1]
    response = _build_client().get("/admin/review")
    review_css = (
        root / "src/console/web_static/css/modules/review.css"
    ).read_text(encoding="utf-8")

    assert response.status_code == 200
    html = response.text
    assert 'class="search-group sort-incompatible"' in html
    assert 'class="bulk-group sort-incompatible"' in html
    assert 'class="btn btn-secondary sort-incompatible" id="btn-check-duplicates"' in html
    assert "#review-tab.review-sort-mode .sort-incompatible" in review_css


def test_review_toolbar_search_can_shrink_without_overflowing() -> None:
    root = Path(__file__).parents[1]
    review_css = (
        root / "src/console/web_static/css/modules/review.css"
    ).read_text(encoding="utf-8")

    assert "flex: 0 1 180px;" in review_css
    assert "min-width: 140px;" in review_css
    assert "max-width: 180px;" in review_css
    assert "box-sizing: border-box;" in review_css
    assert "max-width: none;" in review_css


def test_duplicate_check_tracks_loading_state_by_review_column() -> None:
    scripts_dir = Path(__file__).parents[1] / "src/console/web_static/js/manual_filter"
    state_script = (scripts_dir / "review_duplicates_state.js").read_text(
        encoding="utf-8"
    )
    controller_script = (scripts_dir / "review_tab_duplicates.js").read_text(
        encoding="utf-8"
    )

    assert "const duplicateReviewJobs = new Map()" in state_script
    assert "getDuplicateReviewScopeKey(scope)" in state_script
    assert "status: 'running'" in controller_script
    assert "status: 'ready'" in controller_script
    assert "查看查重结果" in state_script
    assert "btn-recheck-duplicates" not in state_script
    assert "btn-recheck-duplicates" not in controller_script


def test_processed_duplicate_items_remain_editable_and_selectable() -> None:
    script_path = (
        Path(__file__).parents[1]
        / "src/console/web_static/js/manual_filter/review_tab_duplicates.js"
    )
    script = script_path.read_text(encoding="utf-8")
    mark_section = script.split("function markDuplicateReviewItemProcessed", 1)[1].split(
        "function restoreDuplicateReviewItem", 1
    )[0]

    assert ".duplicate-review-item:not(.is-processed)" not in script
    assert "control.disabled = true" not in mark_section
    assert "activeGroup.querySelectorAll('.duplicate-review-item')" in script


def test_discarded_duplicate_items_are_hidden_until_undo() -> None:
    root = Path(__file__).parents[1]
    controller = (
        root / "src/console/web_static/js/manual_filter/review_tab_duplicates.js"
    ).read_text(encoding="utf-8")
    modal = (
        root / "src/console/web_static/js/manual_filter/review_duplicates_modal.js"
    ).read_text(encoding="utf-8")

    assert "if (shouldHideAfterUpdate) hideDiscardedDuplicateReviewItem(item)" in controller
    assert "if (targetValue === 'discarded') hideDiscardedDuplicateReviewItem(item)" in controller
    assert controller.count("restoreDiscardedDuplicateReviewItem(item)") == 2
    assert "item.hidden = true" in modal
    assert "item.hidden = false" in modal
    assert "if (!itemCount) group.hidden = true" in modal
    assert "duplicate-review-session-empty" in modal
    assert ".duplicate-review-group:not(.is-empty)" in modal


def test_review_sort_mode_supports_cross_group_dragging() -> None:
    root = Path(__file__).parents[1]
    scripts_dir = root / "src/console/web_static/js/manual_filter"
    render_script = (scripts_dir / "review_tab.js").read_text(encoding="utf-8")
    sort_script = (scripts_dir / "review_tab_sort.js").read_text(encoding="utf-8")
    data_script = (scripts_dir / "review_tab_data.js").read_text(encoding="utf-8")
    workspace_script = (scripts_dir / "workspace.js").read_text(encoding="utf-8")
    review_css = (root / "src/console/web_static/css/modules/review.css").read_text(encoding="utf-8")

    assert "if (!groupItems.length) return" not in render_script
    assert "sort-group-body${groupItems.length ? '' : ' is-empty'}" in render_script
    assert "group: { name: 'review-groups', pull: true, put: true }" in sort_script
    assert "group_orders: groupOrders" in data_script
    assert "group_orders: payload.group_orders || {}" in workspace_script
    assert "if (!response.ok) throw new Error" in data_script
    assert ".sort-group-body.is-empty" in review_css


def test_manual_review_pages_only_display_external_importance_score() -> None:
    root = Path(__file__).parents[1]
    scripts_dir = root / "src/console/web_static/js/manual_filter"
    utils_script = (scripts_dir / "utils.js").read_text(encoding="utf-8")
    review_script = (scripts_dir / "review_tab.js").read_text(encoding="utf-8")
    duplicate_script = (scripts_dir / "review_duplicates_modal.js").read_text(encoding="utf-8")
    discard_script = (scripts_dir / "discard_tab.js").read_text(encoding="utf-8")
    feedback_script = (scripts_dir / "score_feedback.js").read_text(encoding="utf-8")

    assert "renderScoreFeedbackControl(safe)" in utils_script
    assert "safe.external_importance_score ?? safe.score" not in utils_script
    assert "renderScoreFeedbackControl(item)" in review_script
    assert "item.external_importance_score ?? item.score" not in review_script
    assert "score: current.external_importance_score" in duplicate_script
    assert "current.external_importance_score ?? current.score" not in duplicate_script
    assert "renderScoreFeedbackControl(item)" in discard_script
    assert "formatScore(item.score)" not in discard_script
    assert "formatScore(safe.external_importance_score)" in feedback_script


def test_score_feedback_control_is_shared_across_manual_filter_tabs() -> None:
    root = Path(__file__).parents[1]
    scripts_dir = root / "src/console/web_static/js/manual_filter"
    response = _build_client().get("/manual_filter")
    feedback_script = (scripts_dir / "score_feedback.js").read_text(encoding="utf-8")
    workspace_script = (scripts_dir / "workspace.js").read_text(encoding="utf-8")
    feedback_css = (
        root / "src/console/web_static/css/modules/score_feedback.css"
    ).read_text(encoding="utf-8")

    assert response.status_code == 200
    assert '/static/js/manual_filter/score_feedback.js?v=' in response.text
    assert '/static/css/modules/score_feedback.css?v=' in response.text
    assert "aria-haspopup=\"dialog\"" in feedback_script
    assert "aria-pressed" in feedback_script
    assert "symbol: 'ⓘ'" in feedback_script
    assert "maxlength=\"${SCORE_FEEDBACK_MAX_NOTES}\"" in feedback_script
    assert "requestScoreFeedback('/score-feedback', 'PUT'" in feedback_script
    assert "requestScoreFeedback('/score-feedback/clear', 'POST'" in feedback_script
    assert "document.addEventListener('change'" in feedback_script
    assert "document.addEventListener('input'" in feedback_script
    assert "if (event.key !== 'Escape'" in feedback_script
    assert "if (IS_DUTY_WORKSPACE)" not in feedback_script
    assert "action === '/score-feedback'" in workspace_script
    assert "action === '/score-feedback/clear'" in workspace_script
    assert ".score-feedback-popover" in feedback_css
