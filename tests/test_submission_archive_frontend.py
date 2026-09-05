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

    # 回链标签表只保留 processing/pending 两个键（规则收在 linkPill 一处）；
    # matched 状态的表达是标题后的「原文」标签，不再是状态标签
    assert "matched: { label: '已匹配'" not in core
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
PRIOR_MATCHES_JS = "src/console/web_static/js/submission_archive/prior_matches.js"
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


def test_detail_stats_prior_match_chips_are_filters() -> None:
    source = Path(BROWSER_JS).read_text(encoding="utf-8")
    stats_body = source.split("function detailStats(items) {", maxsplit=1)[1].split(
        "function detailItemMetaHtml", maxsplit=1
    )[0]

    # 已报送/未报送 chip 仅反馈报告且判定结束后渲染（进行中计数没有含义），
    # 是可点击的筛选按钮，与 link_status 的 data-status-filter 是不同的维度
    assert "activeReportType === 'feedback' && !activeReportPriorMatchPending" in stats_body
    assert "data-prior-filter" in stats_body
    assert "priorChip('matched', '已报送'" in stats_body
    assert "priorChip('unmatched', '未报送'" in stats_body


def test_prior_match_filter_is_independent_second_dimension() -> None:
    source = _strip_js_comments(Path(BROWSER_JS).read_text(encoding="utf-8"))
    filter_body = source.split("function applyDetailFilter()", maxsplit=1)[1].split(
        "function reportStatusSignature", maxsplit=1
    )[0]

    # 两个筛选维度叠加判定显隐；卡片带 data-prior-group，局部更新时同步
    assert "card.dataset.priorGroup !== detailPriorFilter" in filter_body
    assert "card.dataset.linkGroup !== detailStatusFilter" in filter_body
    assert "statusHidden || priorHidden" in filter_body
    assert 'data-prior-group="${detailPriorGroup(item)}"' in source
    assert "card.dataset.priorGroup = detailPriorGroup(item)" in source


def test_local_update_covers_prior_match_pill_transitions() -> None:
    source = Path(BROWSER_JS).read_text(encoding="utf-8")
    update_body = _strip_js_comments(
        source.split(
            "function updateReportStatusComponents(id, items) {", maxsplit=1
        )[1].split("function applyManualLinkResult", maxsplit=1)[0]
    )

    # 局部更新通过恒定渲染的 flags 容器整块替换 innerHTML，铅笔入口、回链标签、
    # 已报送标签的三种迁移（无→有、内容变化、有→无）都被覆盖
    assert "querySelector('.archive-item-flags')" in update_body
    assert "flags.innerHTML" in update_body
    assert "detailPriorMatchPill(item)" in update_body
    # 不再以某个标签元素作为重插锚点（matched/unmatched/rejected 不渲染
    # 回链标签，锚点在这些状态上不存在）
    assert "querySelector('.archive-link-pill')" not in update_body
    assert "pill.outerHTML" not in update_body


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


def test_prior_match_pill_dismissed_is_button_but_no_match_stays_span() -> None:
    core = _strip_js_comments(Path(CORE_JS).read_text(encoding="utf-8"))
    pill_body = core.split(
        "const priorMatchPill = (item, { showUnmatched = false } = {}) => {",
        maxsplit=1,
    )[1].split("let archiveToastTimer", maxsplit=1)[0]

    # dismissed（人工判定未报送）有独立状态项，走可点 button 分支（有明细可查、判定可撤销）
    assert "dismissed: { label: '未报送', className: 'is-dismissed' }" in core
    assert '<button type="button" class="archive-prior-match-pill' in pill_body
    # 无命中的「未报送」仍是纯展示 span，两个分支不得合并
    assert "'<span class=\"archive-prior-match-pill is-unmatched\"'" in pill_body
    assert ">未报送</span>" in pill_body


def test_report_status_signature_includes_prior_match_decision() -> None:
    source = Path(BROWSER_JS).read_text(encoding="utf-8")
    signature_body = _strip_js_comments(
        source.split(
            "function reportStatusSignature(items, priorMatchPending = false) {",
            maxsplit=1,
        )[1].split("function reportCountsFromItems", maxsplit=1)[0]
    )

    # 撤销与再判定的组合下 status 可能相同而弹窗底部入口状态不同，指纹必须含 decision
    assert "item.prior_match.status" in signature_body
    assert "item.prior_match.decision" in signature_body


def test_dismissed_prior_match_groups_as_unmatched() -> None:
    core = _strip_js_comments(Path(CORE_JS).read_text(encoding="utf-8"))
    browser = Path(BROWSER_JS).read_text(encoding="utf-8")
    stats_body = browser.split("function detailStats(items) {", maxsplit=1)[1].split(
        "function detailItemMetaHtml", maxsplit=1
    )[0]
    group_body = _strip_js_comments(
        browser.split("const detailPriorGroup = item =>", maxsplit=1)[1].split(
            "const detailPriorMatchPill", maxsplit=1
        )[0]
    )

    # 计数与分组共用 core.js 的 isPriorSubmitted：dismissed 一律归入未报送
    assert "const isPriorSubmitted" in core
    assert "status !== 'dismissed'" in core
    assert "items.filter(isPriorSubmitted)" in stats_body
    assert "isPriorSubmitted(item) ? 'matched' : 'unmatched'" in group_body


def test_prior_match_decision_footer_markup_and_script_order() -> None:
    template = Path(TEMPLATE_HTML).read_text(encoding="utf-8")
    modal = Path(PRIOR_MATCHES_JS).read_text(encoding="utf-8")

    # 弹窗底部有稳定 DOM id 的判定区与三个按钮
    assert 'id="archive-prior-matches-footer"' in template
    assert "archive-prior-matches-confirm" in modal
    assert "archive-prior-matches-reject" in modal
    assert "archive-prior-matches-undo" in modal
    # 底部在 modal-body 之后渲染，decidable === false 时整体隐藏
    assert template.index('id="archive-prior-matches-results"') < template.index(
        'id="archive-prior-matches-footer"'
    )
    assert "priorMatch.decidable === false" in modal
    # Escape 关闭顺序不变：prior_matches.js 仍排在 content_drawer.js 之前
    assert template.index("submission_archive/prior_matches.js") < template.index(
        "submission_archive/content_drawer.js"
    )


def test_prior_match_decision_updates_card_locally_without_report_reload() -> None:
    modal = _strip_js_comments(Path(PRIOR_MATCHES_JS).read_text(encoding="utf-8"))
    browser = Path(BROWSER_JS).read_text(encoding="utf-8")

    # 判定走专用 POST；成功后不关闭弹窗、就地重渲染底部，并局部更新卡片，不重拉报告
    assert "/prior-match-decision`" in modal
    assert "applyPriorMatchDecisionResult(itemId, data.prior_match)" in modal
    assert "renderPriorMatchesFooter(updated)" in modal
    assert "selectReport(" not in modal
    assert "loadReportList(" not in modal
    # 局部更新复用轮询的组件更新，并同步状态指纹避免轮询误判触发整体重绘
    assert "function applyPriorMatchDecisionResult(itemId, priorMatch)" in browser
    decision_body = _strip_js_comments(
        browser.split(
            "function applyPriorMatchDecisionResult(itemId, priorMatch)", maxsplit=1
        )[1].split("function rerenderDetailItemCard", maxsplit=1)[0]
    )
    assert "updateReportStatusComponents(activeReportId, activeReportItems)" in decision_body
    assert "activeReportStatusSignature = reportStatusSignature(" in decision_body


def test_dismissed_pill_css_keeps_pointer_and_footer_hidden_works() -> None:
    css = Path(
        "src/console/web_static/css/modules/submission_archive/item_card.css"
    ).read_text(encoding="utf-8")

    # dismissed 配色同 is-unmatched，但不覆盖指针/hover（可点击）
    dismissed_block = css.split(
        ".archive-prior-match-pill.is-dismissed", maxsplit=1
    )[1].split("}")[0]
    assert "#fee2e2" in dismissed_block
    assert "cursor" not in dismissed_block
    # .modal-footer 的 display:flex 会盖掉 hidden，必须显式压制
    assert ".archive-prior-matches-footer[hidden]" in css


BATCH_DECISION_JS = "src/console/web_static/js/submission_archive/batch_decision.js"
ITEM_CARD_CSS = "src/console/web_static/css/modules/submission_archive/item_card.css"
WIDGETS_CSS = "src/console/web_static/css/modules/submission_archive/widgets.css"


def test_detail_stats_drops_pending_filter_chip() -> None:
    source = Path(BROWSER_JS).read_text(encoding="utf-8")
    stats_body = _strip_js_comments(
        source.split("function detailStats(items) {", maxsplit=1)[1].split(
            "function detailHeadActionsHtml", maxsplit=1
        )[0]
    )

    # 「待确认」筛选 chip 已删除（数量缺口由标题栏批量按钮补上）；
    # 「已匹配」「未覆盖」两个筛选 chip 保留
    assert "filterChip('pending'" not in stats_body
    assert "filterChip('linked', '已匹配'" in stats_body
    assert "filterChip('uncovered', '未覆盖'" in stats_body
    # linkStatusGroup 的 pending 分组保留：卡片仍要正确标注 data-link-group
    assert "if (status === 'pending') return 'pending';" in source


def test_link_pill_only_renders_statuses_without_actions() -> None:
    core = _strip_js_comments(Path(CORE_JS).read_text(encoding="utf-8"))
    meta_body = core.split("const linkStatusMeta = {", maxsplit=1)[1].split(
        "};", maxsplit=1
    )[0]
    pill_body = core.split("const linkPill = (status) => {", maxsplit=1)[1].split(
        "};", maxsplit=1
    )[0]

    # 渲染规则收在 core.js 一处：只给「没有操作入口」的状态（processing/pending）
    # 贴回链标签；matched/unmatched/rejected 的状态已由操作入口
    # （原文标签/解绑/手动匹配）表达，不在表内的一律不渲染
    assert "processing" in meta_body
    assert "pending" in meta_body
    assert "matched" not in meta_body
    assert "unmatched" not in meta_body
    assert "rejected" not in meta_body
    # 表外取值返回空串，不做兜底渲染（linkPill 内不存在「未知」这类兜底文案）
    assert "if (!meta) return '';" in pill_body
    assert "'未知'" not in pill_body
    # browser.js 不再有门控 wrapper，两处调用点直接用 linkPill
    browser = _strip_js_comments(Path(BROWSER_JS).read_text(encoding="utf-8"))
    assert "detailLinkPill" not in browser


def test_detail_item_card_has_constant_flags_container() -> None:
    source = _strip_js_comments(Path(BROWSER_JS).read_text(encoding="utf-8"))
    card_body = source.split("function detailItemCard(item) {", maxsplit=1)[1].split(
        "function detailItemsHtml", maxsplit=1
    )[0]

    # flags 容器恒定渲染，铅笔按钮、回链标签、已报送标签都挂在里面
    assert '<span class="archive-item-flags">' in card_body
    assert (
        "${detailEditTriggerHtml(item)}${linkPill(item.link_status)}${detailPriorMatchPill(item)}"
        in card_body
    )


def test_link_pill_css_keeps_only_processing_and_pending_modifiers() -> None:
    item_card = Path(ITEM_CARD_CSS).read_text(encoding="utf-8")
    widgets = Path(WIDGETS_CSS).read_text(encoding="utf-8")

    # 不再渲染的三种状态（matched/unmatched/rejected）的修饰样式已删除；
    # pill 样式集中在 item_card.css，widgets.css 不再保留
    for css in (item_card, widgets):
        assert ".archive-link-pill.is-linked" not in css
        assert ".archive-link-pill.is-unmatched" not in css
        assert ".archive-link-pill.is-rejected" not in css
    assert ".archive-link-pill" not in widgets
    assert ".archive-link-pill.is-processing" in item_card
    assert ".archive-link-pill.is-pending" in item_card
    # flags 容器样式：横向排列、空容器不占位
    assert ".archive-item-flags" in item_card
    assert ".archive-item-flags:empty" in item_card


def test_detail_head_actions_rendered_on_both_paths() -> None:
    source = Path(BROWSER_JS).read_text(encoding="utf-8")
    select_body = source.split(
        "async function selectReport(id, pushUrl = true) {", maxsplit=1
    )[1].split("async function initBrowserView()", maxsplit=1)[0]
    update_body = _strip_js_comments(
        source.split(
            "function updateReportStatusComponents(id, items) {", maxsplit=1
        )[1].split("function applyManualLinkResult", maxsplit=1)[0]
    )

    # 两条渲染路径都要走 detailHeadActionsHtml：selectReport 首次渲染、
    # updateReportStatusComponents 随统计 chips 刷新
    assert 'id="archive-detail-actions"' in select_body
    assert "detailHeadActionsHtml(items)" in select_body
    assert "archive-detail-actions" in update_body
    assert "detailHeadActionsHtml(items)" in update_body


def test_detail_head_action_button_visibility_conditions() -> None:
    source = _strip_js_comments(Path(BROWSER_JS).read_text(encoding="utf-8"))
    actions_body = source.split(
        "function detailHeadActionsHtml(items) {", maxsplit=1
    )[1].split("function detailItemMetaHtml", maxsplit=1)[0]

    # 按钮 A：存在 pending 条目才渲染，文案带计数
    assert "item.link_status === 'pending'" in actions_body
    assert "待确认回链" in actions_body
    # 按钮 B：反馈报别 + 判定未在进行中 + suspected 计数大于 0，三项缺一不可
    assert "activeReportType === 'feedback'" in actions_body
    assert "!activeReportPriorMatchPending" in actions_body
    assert "item.prior_match.status === 'suspected'" in actions_body
    assert "疑似已报送" in actions_body


def test_batch_link_modal_requests_current_report_queue() -> None:
    batch = _strip_js_comments(Path(BATCH_DECISION_JS).read_text(encoding="utf-8"))

    # 回链批量弹窗只拉当期报告的待确认条目
    assert "/link-queue?report_id=" in batch
    assert "encodeURIComponent(activeReportId)" in batch
    # 回链确认队列页已下线，对照卡的 linkCard 并入本模块，不再依赖 link_queue.js
    assert "function linkCard(item) {" in batch
    assert "items.map(linkCard)" in batch


def test_batch_decisions_update_cards_locally_without_report_reload() -> None:
    batch = _strip_js_comments(Path(BATCH_DECISION_JS).read_text(encoding="utf-8"))

    # 两个弹窗的成功回调分别走局部更新，不允许重拉整份报告或报告列表
    assert "applyManualLinkResult(updated)" in batch
    assert "applyPriorMatchDecisionResult(itemId, data.prior_match)" in batch
    assert "selectReport(" not in batch
    assert "loadReportList(" not in batch
    # 提交走既有接口；回链弹窗关闭时刷新顶部全局待确认提示
    assert "/link-decision`" in batch
    assert "/prior-match-decision`" in batch
    assert "loadNavPending()" in batch


def test_batch_decision_assets_loaded_in_template() -> None:
    template = Path(TEMPLATE_HTML).read_text(encoding="utf-8")

    assert 'id="archive-batch-link-modal"' in template
    assert 'id="archive-batch-prior-modal"' in template
    assert "submission_archive/batch_decision.css" in template
    # batch_decision.js 在 prior_matches.js 之后、init.js 之前加载
    # （linkCard 已并入本模块，link_queue.js 的 script 标签随队列页下线删除）
    assert "submission_archive/link_queue.js" not in template
    assert template.index("submission_archive/prior_matches.js") < template.index(
        "submission_archive/batch_decision.js"
    )
    assert template.index("submission_archive/batch_decision.js") < template.index(
        "submission_archive/init.js"
    )


def test_batch_prior_modal_method_labels_cover_all_match_methods() -> None:
    batch = _strip_js_comments(Path(BATCH_DECISION_JS).read_text(encoding="utf-8"))
    prior = Path(PRIOR_MATCHES_JS).read_text(encoding="utf-8")

    # 疑似已报送批量弹窗复用 prior_matches.js 的 priorMatchEntryHtml 渲染命中明细，
    # 判定依据文案由该模块的映射覆盖 article / title_hash / vector 三种取值
    assert "priorMatchEntryHtml" in batch
    assert "article: '同一篇原文'" in prior
    assert "title_hash: '标题一致'" in prior
    assert "vector: '语义相似'" in prior


SUBMISSION_ARCHIVE_JS_DIR = "src/console/web_static/js/submission_archive"
SUBMISSION_ARCHIVE_CSS_DIR = "src/console/web_static/css/modules/submission_archive"


def test_link_queue_page_and_assets_removed() -> None:
    template = Path(TEMPLATE_HTML).read_text(encoding="utf-8")

    # 回链确认页面分支、视图切换栏与两个模块的资源引用全部下线
    assert 'archive_view == "link-queue"' not in template
    assert "archive_view == 'link-queue'" not in template
    assert '<nav class="tabs archive-tabs">' not in template
    assert "submission_archive/link_queue.js" not in template
    assert "submission_archive/link_queue.css" not in template
    # 两个模块文件已从仓库删除（linkCard 并入 batch_decision.js，
    # 仍在服役的对照卡样式并入 batch_decision.css）
    assert not Path(f"{SUBMISSION_ARCHIVE_JS_DIR}/link_queue.js").exists()
    assert not Path(f"{SUBMISSION_ARCHIVE_CSS_DIR}/link_queue.css").exists()


def test_tabs_row_keeps_create_button_and_adds_global_pending_hint() -> None:
    template = Path(TEMPLATE_HTML).read_text(encoding="utf-8")

    # 外层行保留：「新增存档」按钮在这一行；全局待确认提示是链接（不是按钮），
    # 放在原 nav 的左侧位置，位于「新增存档」之前
    assert "workspace-tabs-row archive-tabs-row" in template
    assert "workspace-tab-actions" in template
    assert '<a class="archive-nav-pending" id="archive-nav-pending"' in template
    assert template.index('id="archive-nav-pending"') < template.index(
        'href="/submission-archive/new"'
    )


def test_pending_item_meta_no_longer_links_to_queue_page() -> None:
    browser = Path(BROWSER_JS).read_text(encoding="utf-8")

    # 队列页下线后「去确认」是死链，pending 条目 meta 行不再渲染任何链接；
    # 待确认统一由标题栏「待确认回链 N」批量按钮处理
    assert "/submission-archive/link-queue" not in browser
    assert "去确认" not in browser


def test_tab_bar_css_rules_removed() -> None:
    browser_css = Path(f"{SUBMISSION_ARCHIVE_CSS_DIR}/browser.css").read_text(
        encoding="utf-8"
    )
    responsive_css = Path(f"{SUBMISSION_ARCHIVE_CSS_DIR}/responsive.css").read_text(
        encoding="utf-8"
    )

    assert ".archive-tab {" not in browser_css
    assert ".archive-tab-badge" not in browser_css
    assert ".archive-tabs-row .archive-tabs" not in browser_css
    assert ".archive-tabs" not in responsive_css


def test_comparison_card_css_moved_into_batch_decision() -> None:
    css = Path(f"{SUBMISSION_ARCHIVE_CSS_DIR}/batch_decision.css").read_text(
        encoding="utf-8"
    )

    # 两个批量弹窗共用的对照卡与剩余计数样式整体并入
    for selector in (
        ".archive-queue-count {",
        ".archive-queue-count:empty",
        ".archive-link-card {",
        ".archive-link-grid {",
        ".archive-link-col {",
        ".archive-link-score {",
        ".archive-score-bar {",
        ".archive-score-nums {",
        ".archive-link-actions {",
    ):
        assert selector in css, selector
    # 队列页专属的 heading 规则彻底删除，全模块 CSS 无残留引用
    all_css = "".join(
        path.read_text(encoding="utf-8")
        for path in Path(SUBMISSION_ARCHIVE_CSS_DIR).glob("*.css")
    )
    assert ".archive-queue-heading" not in all_css


def test_nav_pending_hint_renders_link_to_target_report() -> None:
    # 不剥注释：断言里的 URL 含 //，会被 _strip_js_comments 截断
    core = Path(CORE_JS).read_text(encoding="utf-8")

    # setNavPending 同时接收总数与目标报告 id：0 时隐藏，大于 0 时渲染
    # 「待确认回链 N」并指向该报告详情页
    assert "function setNavPending(total, reportId)" in core
    assert "hint.hidden = count <= 0;" in core
    assert "待确认回链 ${count}" in core
    assert "/submission-archive/${encodeURIComponent(reportId" in core
    # loadNavPending 只取 limit=1：总数之外还要从 items[0] 拿待处理报告的 id
    load_body = core.split("async function loadNavPending()", maxsplit=1)[1]
    assert "/link-queue?limit=1" in load_body
    assert "first.report_id" in load_body
    assert "setNavPending(data.total," in load_body


def test_nav_pending_hint_css_reuses_badge_warning_semantics() -> None:
    css = Path(f"{SUBMISSION_ARCHIVE_CSS_DIR}/browser.css").read_text(encoding="utf-8")

    block = css.split(".archive-nav-pending {", maxsplit=1)[1].split("}")[0]
    assert "var(--warning-color)" in block
    # display:inline-flex 会盖掉 hidden 属性，必须显式压制
    assert ".archive-nav-pending[hidden]" in css


def test_init_drops_link_queue_branch_but_keeps_nav_pending_refresh() -> None:
    init = Path(f"{SUBMISSION_ARCHIVE_JS_DIR}/init.js").read_text(encoding="utf-8")

    assert "link-queue" not in init
    assert "loadLinkQueue" not in init
    assert "loadNavPending()" in init


def test_init_browser_view_auto_selects_latest_report_without_push_url() -> None:
    source = _strip_js_comments(Path(BROWSER_JS).read_text(encoding="utf-8"))
    init_body = source.split("async function initBrowserView()", maxsplit=1)[1]

    # 无 initialReportId 且列表非空时自动选中第一条（列表按 report_date desc,
    # imported_at desc 排序，第一条即最近一份），且必须 pushUrl = false：
    # 自动选中是详情区的显示默认值，改写地址栏会把用户钉死在旧报告上
    assert "await selectReport(items[0].id, false)" in init_body
    assert "selectReport(items[0].id, true)" not in init_body
    assert "selectReport(items[0].id)" not in init_body.replace(
        "selectReport(items[0].id, false)", ""
    )
    # 有 initialReportId（详情页直达）时行为不变，只加载该报告
    assert "await selectReport(initialReportId, false)" in init_body


def test_load_report_list_returns_null_only_on_non_append_failure() -> None:
    source = Path(BROWSER_JS).read_text(encoding="utf-8")
    load_body = _strip_js_comments(
        source.split("async function loadReportList(append = false) {", maxsplit=1)[
            1
        ].split("function detailStats", maxsplit=1)[0]
    )

    # 成功分支仍返回条目数组；追加失败仍返回空数组（调用方忽略返回值）；
    # 只有非追加失败返回 null，与「库是空的」空数组区分
    assert "return data.items;" in load_body
    assert "return [];" in load_body
    assert load_body.count("return null;") == 1
    # null 在非追加分支（写错误信息到列表区）与追加分支（toast）之间
    assert load_body.index("escapeHtml(error.message)") < load_body.index(
        "return null;"
    ) < load_body.index("toast(error.message")


def test_auto_select_only_on_initial_load_not_filter_paths() -> None:
    source = _strip_js_comments(Path(BROWSER_JS).read_text(encoding="utf-8"))
    load_body = source.split("async function loadReportList(append = false) {", maxsplit=1)[
        1
    ].split("function detailStats", maxsplit=1)[0]
    init_body = source.split("async function initBrowserView()", maxsplit=1)[1]

    # 自动选中不得藏在 loadReportList 里：类型筛选、日期筛选、加载更多都调用它，
    # 筛选时详情区不应自己跳转
    assert "selectReport(" not in load_body
    # initBrowserView 中只有首次加载的收尾段按列表内容选中；之前的事件绑定段
    # （类型筛选、日期筛选、加载更多）不得按 items 选中
    head, _, tail = init_body.partition("const items = await loadReportList(false)")
    assert "selectReport(items" not in head
    assert "selectReport(items[0].id, false)" in tail


def test_init_browser_view_distinguishes_empty_archive_from_load_failure() -> None:
    source = _strip_js_comments(Path(BROWSER_JS).read_text(encoding="utf-8"))
    init_body = source.split("async function initBrowserView()", maxsplit=1)[1]

    # 空数组 = 库里没有报告：引导去「新增存档」，与左侧空列表口径一致
    assert "items.length === 0" in init_body
    assert "新增存档" in init_body
    # null = 加载失败：详情区保持中性提示，不出现「新增存档」口径的文案
    assert "items === null" in init_body
    failure_branch = init_body.split("items === null", maxsplit=1)[1].split(
        "items.length === 0", maxsplit=1
    )[0]
    assert "新增存档" not in failure_branch


def test_archive_detail_initial_content_is_neutral_loading() -> None:
    template = Path(TEMPLATE_HTML).read_text(encoding="utf-8")

    # 初始内容只在 JS 跑完前短暂可见，是中性加载提示，不再说「从左侧选择」
    assert "从左侧选择" not in template
    detail_block = template.split('id="archive-detail"', maxsplit=1)[1].split(
        "</section>", maxsplit=1
    )[0]
    assert "正在加载" in detail_block


def test_init_browser_view_has_no_duplicate_direct_link_branches() -> None:
    source = _strip_js_comments(Path(BROWSER_JS).read_text(encoding="utf-8"))
    init_body = source.split("async function initBrowserView()", maxsplit=1)[1]

    # 详情页直达不再按「是否在筛选结果里」分两个相同分支，只加载一次
    assert init_body.count("selectReport(initialReportId, false)") == 1
    assert "items.some(item => item.id === initialReportId)" not in init_body
