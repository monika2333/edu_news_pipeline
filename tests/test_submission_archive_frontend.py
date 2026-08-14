from __future__ import annotations

from pathlib import Path


def test_status_poll_updates_components_without_rerendering_report() -> None:
    source = Path(
        "src/console/web_static/js/submission_archive/browser.js"
    ).read_text(encoding="utf-8")
    poll_body = source.split(
        "async function pollReportStatus(id) {",
        maxsplit=1,
    )[1].split(
        "async function selectReport(id, pushUrl = true) {",
        maxsplit=1,
    )[0]

    assert "updateReportStatusComponents(id, items)" in poll_body
    assert "selectReport(" not in poll_body
    assert "loadReportList(" not in poll_body


def test_manual_filter_page_loads_submission_duplicates_modal() -> None:
    template = Path(
        "src/console/web_templates/manual_filter.html"
    ).read_text(encoding="utf-8")

    assert 'id="submission-duplicates-modal"' in template
    assert "submission_duplicates_modal.js" in template
    assert template.index("submission_duplicates_modal.js") < template.index(
        "manual_filter/init.js"
    )


def test_duplicate_badge_is_rendered_as_clickable_button() -> None:
    source = Path(
        "src/console/web_static/js/manual_filter/utils.js"
    ).read_text(encoding="utf-8")

    assert '<button type="button" class="submission-duplicate-badge' in source
    assert "data-duplicate-state" in source


def test_review_card_renders_submission_duplicate_badge() -> None:
    review_tab = Path(
        "src/console/web_static/js/manual_filter/review_tab.js"
    ).read_text(encoding="utf-8")
    modal = Path(
        "src/console/web_static/js/manual_filter/submission_duplicates_modal.js"
    ).read_text(encoding="utf-8")
    init = Path(
        "src/console/web_static/js/manual_filter/init.js"
    ).read_text(encoding="utf-8")

    # 审阅页卡片复用筛选页的徽章 markup，且徽章点击与「不是重复」在两个列表都委托。
    assert "renderSubmissionDuplicateBadge(item)" in review_tab
    assert "bindBadgeClick(elements.reviewList)" in modal
    assert "elements.reviewList.addEventListener('click', handleDuplicateDismissClick)" in init


def test_archive_frontend_consumes_only_unified_matched_status() -> None:
    core = Path(
        "src/console/web_static/js/submission_archive/core.js"
    ).read_text(encoding="utf-8")
    browser = Path(
        "src/console/web_static/js/submission_archive/browser.js"
    ).read_text(encoding="utf-8")

    assert "matched: { label: '已匹配'" in core
    assert "new Set(['matched'])" in core
    assert "report.matched_count" in browser
    assert "exact_count" not in browser
    assert "fuzzy_count" not in browser
    assert "manual_count" not in browser
