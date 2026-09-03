from __future__ import annotations

from pathlib import Path


def _strip_js_comments(text: str) -> str:
    """去掉 JS 行注释，避免注释里的词误命中源码切片上的断言。"""
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def test_status_poll_updates_components_without_rerendering_report() -> None:
    source = Path(
        "src/console/web_static/js/submission_archive/browser.js"
    ).read_text(encoding="utf-8")
    poll_body = _strip_js_comments(
        source.split(
            "async function pollReportStatus(id) {",
            maxsplit=1,
        )[1].split(
            "async function selectReport(id, pushUrl = true) {",
            maxsplit=1,
        )[0]
    )

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
    assert "activeReportStatusSignature = reportStatusSignature(" in browser
    assert "activeReportItems, activeReportPriorMatchPending" in browser


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

    # 模板暴露角色，core.js 读取，browser.js 仅对管理员渲染铅笔修改入口
    assert 'data-user-role="{{ current_user.role }}"' in template
    assert "body.dataset.userRole === 'admin'" in core
    assert "isAdminUser" in browser
    assert "archive-item-edit-btn" in browser
    # 全局修改模式开关已移除，入口在每条卡片的状态标签左侧
    assert "archive-edit-toggle" not in browser


def test_archive_item_edit_entry_follows_link_status() -> None:
    browser = Path(
        "src/console/web_static/js/submission_archive/browser.js"
    ).read_text(encoding="utf-8")

    # 未覆盖（unmatched/rejected）与已匹配（matched）给铅笔入口，processing/pending 不给
    assert "['unmatched', 'rejected', 'matched'].includes(item.link_status)" in browser


def test_archive_item_edit_saves_via_patch_and_updates_card_locally() -> None:
    browser = Path(
        "src/console/web_static/js/submission_archive/browser.js"
    ).read_text(encoding="utf-8")

    # 保存走单条 PATCH，成功后局部更新卡片展示区，不重拉整份报告
    assert "method: 'PATCH'" in browser
    assert "function applyItemEditResult(updatedItem)" in browser
    apply_edit_body = _strip_js_comments(
        browser.split(
            "function applyItemEditResult(updatedItem)", maxsplit=1
        )[1].split("async function saveItemEdit", maxsplit=1)[0]
    )
    assert "selectReport(" not in apply_edit_body


BROWSER_JS = "src/console/web_static/js/submission_archive/browser.js"
CORE_JS = "src/console/web_static/js/submission_archive/core.js"
TEMPLATE_HTML = "src/console/web_templates/submission_archive.html"


def test_report_status_signature_includes_prior_match() -> None:
    source = Path(BROWSER_JS).read_text(encoding="utf-8")
    signature_body = _strip_js_comments(
        source.split(
            "function reportStatusSignature(items, priorMatchPending = false) {",
            maxsplit=1,
        )[1].split("function reportCountsFromItems", maxsplit=1)[0]
    )

    # prior_match 在回链完成后的某一轮轮询里才出现，指纹不含它页面不会重绘；
    # 判定进度（pending→结束）也要入指纹，否则全部未命中时「未报送」贴不上
    assert "item.prior_match ?" in signature_body
    assert "item.prior_match.status" in signature_body
    assert "priorMatchPending" in signature_body


def test_status_poll_continues_until_prior_match_done() -> None:
    source = Path(BROWSER_JS).read_text(encoding="utf-8")
    poll_body = _strip_js_comments(
        source.split(
            "async function pollReportStatus(id) {", maxsplit=1
        )[1].split("async function selectReport(id, pushUrl = true) {", maxsplit=1)[0]
    )
    select_body = _strip_js_comments(
        source.split(
            "async function selectReport(id, pushUrl = true) {", maxsplit=1
        )[1].split("async function initBrowserView()", maxsplit=1)[0]
    )

    for body in (poll_body, select_body):
        assert "link_status === 'processing'" in body
        assert "report.prior_match_pending" in body


def test_prior_match_only_polling_is_capped() -> None:
    source = Path(BROWSER_JS).read_text(encoding="utf-8")

    assert "PRIOR_MATCH_POLL_LIMIT" in source
    assert "priorMatchPollCount < PRIOR_MATCH_POLL_LIMIT" in source
    assert "priorMatchPollCount += 1" in source


def test_prior_match_pill_rendered_only_when_present() -> None:
    core = _strip_js_comments(Path(CORE_JS).read_text(encoding="utf-8"))
    browser = _strip_js_comments(Path(BROWSER_JS).read_text(encoding="utf-8"))

    # 判定未结束时 prior_match 为 null 也不能贴标签；「未报送」由调用方按
    # 报告级状态（反馈报告且判定已结束）显式开启
    assert "const priorMatchPill = (item, { showUnmatched = false } = {}) => {" in core
    assert "if (!showUnmatched) return '';" in core
    assert "未报送" in core
    assert "is-unmatched" in core
    # 「未报送」是纯展示的 span，不可点击；弹窗委托只匹配 button
    assert ">未报送</span>" in core
    assert "button.archive-prior-match-pill" in Path(
        "src/console/web_static/js/submission_archive/prior_matches.js"
    ).read_text(encoding="utf-8")
    assert "activeReportType === 'feedback' && !activeReportPriorMatchPending" in browser
    assert '<button type="button" class="archive-prior-match-pill' in core
    assert "data-item-id" in core
    pill_body = core.split(
        "const priorMatchPill = (item, { showUnmatched = false } = {}) => {",
        maxsplit=1,
    )[1]
    assert "archive-link-pill" not in pill_body
    assert "${linkPill(item.link_status)}${detailPriorMatchPill(item)}" in browser


def test_detail_stats_prior_match_chip_is_feedback_only() -> None:
    source = Path(BROWSER_JS).read_text(encoding="utf-8")

    assert "activeReportType === 'feedback'" in source
    assert "activeReportType = report.report_type || ''" in source
    chip_line = next(
        line for line in source.splitlines() if "is-prior-matched" in line
    )
    assert "<span" in chip_line
    assert "data-status-filter" not in chip_line


def test_local_update_covers_prior_match_pill_transitions() -> None:
    source = Path(BROWSER_JS).read_text(encoding="utf-8")
    update_body = _strip_js_comments(
        source.split(
            "function updateReportStatusComponents(id, items) {", maxsplit=1
        )[1].split("function applyManualLinkResult", maxsplit=1)[0]
    )

    # 局部更新必须处理三种迁移：先移除旧标签（有→无/变化），
    # 再借 linkPill 的 outerHTML 插入新标签（无→有）
    assert "archive-prior-match-pill" in update_body
    assert "priorPill.remove()" in update_body
    assert "detailPriorMatchPill(item)" in update_body


def test_prior_match_pending_flag_tracked_per_report() -> None:
    source = _strip_js_comments(Path(BROWSER_JS).read_text(encoding="utf-8"))

    # 初次加载与轮询都要同步报告级判定进度：「未报送」标签的门控和指纹都依赖它
    assert source.count(
        "activeReportPriorMatchPending = Boolean(report.prior_match_pending)"
    ) == 2


def test_prior_match_pill_color_semantics() -> None:
    css = Path(
        "src/console/web_static/css/modules/submission_archive/item_card.css"
    ).read_text(encoding="utf-8")

    assert ".archive-prior-match-pill.is-suspected" in css
    assert ".archive-prior-match-pill.is-unmatched" in css
    suspected_block = css.split(".archive-prior-match-pill.is-suspected", maxsplit=1)[1]
    assert "#fee2e2" not in suspected_block.split("}")[0]
    unmatched_block = css.split(".archive-prior-match-pill.is-unmatched", maxsplit=1)[1]
    assert "#fee2e2" in unmatched_block.split("}")[0]


def test_prior_matches_modal_markup_and_script_order() -> None:
    template = Path(TEMPLATE_HTML).read_text(encoding="utf-8")

    assert 'id="archive-prior-matches-modal"' in template
    assert "submission_archive/prior_matches.js" in template
    # Escape 关闭顺序依赖脚本加载顺序：晚于 browser.js、早于 content_drawer.js
    assert template.index("submission_archive/browser.js") < template.index(
        "submission_archive/prior_matches.js"
    )
    assert template.index("submission_archive/prior_matches.js") < template.index(
        "submission_archive/content_drawer.js"
    )


def test_prior_matches_modal_escapes_and_labels() -> None:
    modal = Path(
        "src/console/web_static/js/submission_archive/prior_matches.js"
    ).read_text(encoding="utf-8")

    assert "article: '同一篇原文'" in modal
    assert "title_hash: '标题一致'" in modal
    assert "vector: '语义相似'" in modal
    assert "/prior-matches`" in modal
    assert "escapeHtml(entry.title" in modal
    assert "escapeHtml(entry.body)" in modal
    assert "escapeHtml(entry.source)" in modal
