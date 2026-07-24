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


def test_duplicate_check_button_is_before_sort_mode() -> None:
    response = _build_client().get("/manual_filter")

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
    response = _build_client().get("/manual_filter")
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
