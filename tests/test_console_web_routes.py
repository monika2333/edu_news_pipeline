from __future__ import annotations

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


def test_console_root_redirects_to_manual_filter() -> None:
    client = _build_client()

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].endswith("/manual_filter")


def test_removed_console_pages_are_not_registered() -> None:
    client = _build_client()

    assert client.get("/dashboard").status_code == 404
    assert client.get("/articles/search").status_code == 404


def test_account_page_exposes_personal_password_change_form() -> None:
    response = _build_client().get("/account")

    assert response.status_code == 200
    assert "<title>修改密码 · 教育新闻控制台</title>" in response.text
    assert 'id="btn-logout" type="button">退出登录</button>' in response.text
    assert 'id="change-password-form"' in response.text
    assert 'src="/static/js/account.js?v=' in response.text
    assert 'href="/">← 返回工作台</a>' in response.text


def test_admin_page_shows_registration_preferences_for_scheduling(
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

    assert response.status_code == 200
    assert 'name="preferred_weekday"' in response.text
    assert "<th>首选值班日</th>" in response.text
    assert "editor.preferred_weekday" in admin_script
    assert "首选${preference}" in admin_script


def test_duty_page_reuses_manual_filter_workspace_without_admin_entries() -> None:
    response = _build_editor_client().get("/duty")

    assert response.status_code == 200
    html = response.text
    assert "<title>新闻筛选控制台</title>" in html
    assert "<h1>新闻筛选控制台</h1>" in html
    assert 'data-workspace-mode="duty"' in html
    assert 'class="stats"' in html
    assert 'id="btn-refresh"' in html
    assert 'aria-describedby="refresh-cluster-hint"' in html
    assert 'aria-label="管理员主视图"' not in html
    assert 'id="workspace-shift-select"' in html
    assert 'data-tab="filter">筛选</button>' in html
    assert 'data-tab="review">已选结果</button>' in html
    assert 'data-tab="discard">放弃</button>' in html
    assert '/static/js/manual_filter/workspace.js?v=' in html
    assert '/static/js/manual_filter/filter_tab_data.js?v=' in html
    assert 'src="/static/js/duty.js' not in html
    assert 'href="/admin">用户与排班</a>' not in html
    assert 'href="/admin/duty-summary">值班汇总</a>' not in html
    assert 'id="btn-check-duplicates"' not in html
    assert 'id="btn-archive"' not in html
    assert 'id="btn-filter-discard-before-date"' in html
    assert 'id="search-drawer-toggle"' in html
    assert 'id="stat-exported"' not in html
    assert "已放弃" not in html
    assert '<details class="account-menu">' in html
    assert '<summary class="account-menu-trigger current-user" id="current-user">' in html
    assert 'class="account-menu-item" href="/admin"' not in html
    assert 'class="account-menu-item" href="/account">修改密码</a>' in html
    assert 'class="account-menu-item" id="btn-logout" type="button">退出登录</button>' in html


def test_admin_manual_filter_keeps_admin_only_entries() -> None:
    response = _build_client().get("/manual_filter")

    assert response.status_code == 200
    html = response.text
    assert "<title>新闻筛选控制台</title>" in html
    assert "<h1>新闻筛选控制台</h1>" in html
    assert 'data-workspace-mode="admin"' in html
    assert 'aria-label="管理员主视图"' in html
    assert 'class="admin-view-link is-active" href="/manual_filter"' in html
    assert 'aria-current="page"' in html
    assert "全量新闻筛选" in html
    assert 'class="admin-view-link" href="/admin/duty-summary">值班结果筛选</a>' in html
    assert 'class="admin-view-link" href="/admin/review"' in html
    assert 'class="stats"' not in html
    assert 'id="stat-pending"' not in html
    assert 'id="stat-selected"' not in html
    assert 'id="stat-backup"' not in html
    assert 'data-tab="review"' not in html
    assert 'id="review-tab"' not in html
    assert 'id="btn-check-duplicates"' not in html
    assert 'id="btn-archive"' not in html
    assert 'id="btn-filter-discard-before-date"' in html
    assert 'id="search-drawer-toggle"' in html
    assert 'id="btn-refresh"' in html
    assert 'aria-describedby="refresh-cluster-hint"' in html
    assert "刷新会重新聚类，可能需要等待约 1 分钟" in html
    assert 'class="workspace-tabs-row"' in html
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


def test_admin_review_is_an_independent_workspace() -> None:
    response = _build_client().get("/admin/review")

    assert response.status_code == 200
    html = response.text
    assert "<title>新闻筛选控制台 · 汇总审阅</title>" in html
    assert 'data-initial-tab="review"' in html
    assert 'href="/admin/review"' in html
    assert 'class="admin-view-link is-active" href="/admin/review"' in html
    assert 'aria-current="page"' in html
    assert 'id="review-tab"' in html
    assert 'id="filter-tab"' not in html
    assert 'id="discard-tab"' not in html
    assert 'id="btn-check-duplicates"' in html
    assert 'id="btn-archive"' in html
    assert 'class="stats"' not in html
    assert 'id="btn-refresh"' not in html
    assert "刷新会重新聚类" not in html


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
    assert 'class="summary-layout is-shifts-collapsed"' in response.text
    assert 'id="summary-shifts-panel" hidden' in response.text
    assert 'id="btn-toggle-shifts"' in response.text
    assert 'aria-expanded="false"' in response.text
    assert 'aria-label="管理员主视图"' in response.text
    assert 'class="admin-view-link" href="/manual_filter">全量新闻筛选</a>' in response.text
    assert 'href="/admin/duty-summary" aria-current="page">' in response.text
    assert 'class="admin-view-link" href="/admin/review">汇总审阅</a>' in response.text
    assert "值班结果筛选" in response.text
    assert "值班结果汇总" not in response.text
    assert 'id="btn-summary-refresh"' not in response.text
    assert '<details class="account-menu">' in response.text
    assert 'class="account-menu-item" href="/admin">用户与排班</a>' in response.text

    stylesheet = (
        Path(__file__).parents[1]
        / "src/console/web_static/css/modules/duty_summary.css"
    ).read_text(encoding="utf-8")
    assert "max-width: 1200px;" in stylesheet
    assert "padding: clamp(12px, 3vw, 24px);" in stylesheet
    assert "margin-bottom: 24px;" in stylesheet


def test_duty_summary_exposes_column_tabs_search_and_select_all(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.console.web_routes.generate_shifts",
        lambda **kwargs: {"inserted": 0},
    )
    response = _build_client().get("/admin/duty-summary")
    script = (
        Path(__file__).parents[1]
        / "src/console/web_static/js/duty_summary.js"
    ).read_text(encoding="utf-8")

    assert response.status_code == 200
    html = response.text
    assert html.count('data-report-type=') == 4
    assert "综报采纳" in html
    assert "综报备选" in html
    assert "晚报采纳" in html
    assert "晚报备选" in html
    assert 'id="summary-search-input"' in html
    assert 'id="summary-select-all"' in html
    assert 'id="summary-report-type"' not in html
    assert 'id="summary-import-target"' not in html
    assert 'id="btn-import-results">送入汇总审阅</button>' in html
    assert 'id="summary-import-conflict-modal"' in html
    assert 'id="summary-existing-summary"' in html
    assert 'id="summary-existing-source"' in html
    assert 'id="summary-duty-summary"' in html
    assert 'id="summary-duty-source"' in html
    assert 'id="btn-keep-existing"' in html
    assert 'id="btn-keep-duty"' in html
    assert "function getVisibleItems()" in script
    assert "elements.selectAll.indeterminate" in script
    assert "tab.dataset.reportType" in script
    assert "tab.dataset.targetStatus" in script
    assert "params.set('report_type', state.targetReportType)" in script
    assert "/api/admin/duty-summary/import-preview" in script
    assert "conflict_resolutions: conflictResolutions" in script
    assert "function chooseImportConflict(choice)" in script


def test_duplicate_check_button_is_before_sort_mode() -> None:
    response = _build_client().get("/admin/review")

    assert response.status_code == 200
    html = response.text
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
    review_css = (root / "src/console/web_static/css/modules/review.css").read_text(encoding="utf-8")

    assert "if (!groupItems.length) return" not in render_script
    assert "sort-group-body${groupItems.length ? '' : ' is-empty'}" in render_script
    assert "group: { name: 'review-groups', pull: true, put: true }" in sort_script
    assert "group_orders: groupOrders" in data_script
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
    assert ".score-feedback-popover" in feedback_css
