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
    # pill 只表示状态，不再渲染为打开抽屉的按钮；
    # 「原文」入口是标题后的 .content-drawer-trigger 标签
    assert "archive-link-pill-btn" not in core
    assert "new Set(['matched'])" not in core
    assert "detailOriginalTriggerHtml" in browser
    assert "content-drawer-trigger" in browser
    assert "report.matched_count" in browser
    assert "exact_count" not in browser
    assert "fuzzy_count" not in browser
    assert "manual_count" not in browser


def test_submission_archive_page_loads_manual_link_modal() -> None:
    template = Path(
        "src/console/web_templates/submission_archive.html"
    ).read_text(encoding="utf-8")

    assert 'id="archive-link-modal"' in template
    assert "submission_archive/manual_link.css" in template
    assert "submission_archive/manual_link.js" in template
    assert template.index("submission_archive/core.js") < template.index(
        "submission_archive/manual_link.js"
    )
    assert template.index("submission_archive/manual_link.js") < template.index(
        "submission_archive/init.js"
    )
    # Escape 优先级（抽屉开着先关抽屉）依赖 manual_link.js 先于 content_drawer.js 注册
    assert template.index("submission_archive/manual_link.js") < template.index(
        "submission_archive/content_drawer.js"
    )


def test_manual_link_updates_card_locally_without_report_reload() -> None:
    modal = Path(
        "src/console/web_static/js/submission_archive/manual_link.js"
    ).read_text(encoding="utf-8")
    browser = Path(
        "src/console/web_static/js/submission_archive/browser.js"
    ).read_text(encoding="utf-8")

    # 绑定/解绑成功后局部更新条目卡片，不重新拉取整份报告或报告列表
    assert "applyManualLinkResult(item)" in modal
    assert "selectReport(" not in modal
    assert "loadReportList(" not in modal
    # 局部更新复用轮询的组件更新，并同步状态指纹避免轮询误判触发整体重绘
    assert "function applyManualLinkResult(updatedItem)" in browser
    assert "updateReportStatusComponents(activeReportId, activeReportItems)" in browser
    assert "activeReportStatusSignature = reportStatusSignature(activeReportItems)" in browser


def test_manual_link_entries_follow_link_status() -> None:
    browser = Path(
        "src/console/web_static/js/submission_archive/browser.js"
    ).read_text(encoding="utf-8")

    # 未覆盖（unmatched/rejected）给「手动匹配」，已匹配给「解绑」
    assert "archive-manual-link-btn" in browser
    assert "archive-manual-unlink-btn" in browser
    assert "item.link_status === 'unmatched' || item.link_status === 'rejected'" in browser


def test_manual_link_modal_filters_self_from_linked_items() -> None:
    modal = Path(
        "src/console/web_static/js/submission_archive/manual_link.js"
    ).read_text(encoding="utf-8")

    # 后端 linked_items 不排除当前条目，渲染前必须过滤自己
    assert "entry.item_id) !== String(manualLinkState.itemId)" in modal
    # 候选的入库/发布时间是带 Z 的 UTC，必须走 formatLocalDateTime
    assert "formatLocalDateTime(candidate.ingested_at)" in modal
    assert "formatLocalDateTime(candidate.publish_time_iso)" in modal


def test_content_drawer_stacks_above_manual_link_modal() -> None:
    css = Path(
        "src/console/web_static/css/modules/content_drawer.css"
    ).read_text(encoding="utf-8")

    assert "body.archive-link-modal-open .content-drawer" in css


def test_archive_detail_edit_mode_is_admin_only() -> None:
    template = Path(
        "src/console/web_templates/submission_archive.html"
    ).read_text(encoding="utf-8")
    core = Path(
        "src/console/web_static/js/submission_archive/core.js"
    ).read_text(encoding="utf-8")
    browser = Path(
        "src/console/web_static/js/submission_archive/browser.js"
    ).read_text(encoding="utf-8")

    # 模板暴露角色，core.js 读取，browser.js 仅对管理员渲染「修改」入口
    assert 'data-user-role="{{ current_user.role }}"' in template
    assert "body.dataset.userRole === 'admin'" in core
    assert "isAdminUser" in browser
    assert "archive-edit-toggle" in browser


def test_archive_item_edit_saves_via_patch_and_updates_card_locally() -> None:
    browser = Path(
        "src/console/web_static/js/submission_archive/browser.js"
    ).read_text(encoding="utf-8")

    # 保存走单条 PATCH，成功后局部更新卡片展示区，不重拉整份报告
    assert "method: 'PATCH'" in browser
    assert "function applyItemEditResult(updatedItem)" in browser
    assert "selectReport(" not in browser.split(
        "function applyItemEditResult(updatedItem)", maxsplit=1
    )[1].split("async function saveItemEdit", maxsplit=1)[0]
