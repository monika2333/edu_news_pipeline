// Manual Filter JS - Duplicate Review

let duplicateReviewTrigger = null;
let duplicateReviewActiveGroupIndex = 0;
let duplicateReviewDisplayedScope = null;
let duplicateReviewRequestSequence = 0;
const duplicateReviewJobs = new Map();

function escapeDuplicateHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function getDuplicateReviewScope(reportType = state.reviewReportType, decision = state.reviewView) {
    return {
        reportType: reportType === 'wanbao' ? 'wanbao' : 'zongbao',
        decision: decision === 'backup' ? 'backup' : 'selected'
    };
}

function getDuplicateReviewScopeKey(scope) {
    return `${scope.reportType}:${scope.decision}`;
}

function getDuplicateReviewColumnLabel(scope = getDuplicateReviewScope()) {
    const reportLabel = scope.reportType === 'wanbao' ? '晚报' : '综报';
    const decisionLabel = scope.decision === 'backup' ? '备选' : '采纳';
    return `${reportLabel}${decisionLabel}`;
}

function isDuplicateReviewScopeActive(scope) {
    return state.currentTab === 'review'
        && state.reviewReportType === scope.reportType
        && state.reviewView === scope.decision;
}

function safeDuplicateUrl(value) {
    const url = String(value || '').trim();
    return /^https?:\/\//i.test(url) ? url : '';
}

function duplicateStatusOptions(item) {
    const currentValue = `${item.report_type || state.reviewReportType}:${item.status || state.reviewView}`;
    const options = [
        ['zongbao:selected', '综报采纳'],
        ['zongbao:backup', '综报备选'],
        ['wanbao:selected', '晚报采纳'],
        ['wanbao:backup', '晚报备选'],
        ['discarded', '放弃'],
        ['pending', '待处理']
    ];
    return options.map(([value, label]) => (
        `<option value="${value}" ${value === currentValue ? 'selected' : ''}>${label}</option>`
    )).join('');
}

function renderDuplicateReviewItem(item) {
    const title = escapeDuplicateHtml(item.title || '(无标题)');
    const source = escapeDuplicateHtml(item.source || '-');
    const summary = escapeDuplicateHtml(item.summary || '');
    const summaryCount = formatReviewSummaryCount(countReviewSummaryChars(item.summary));
    const score = formatScore(item.score);
    const bonusText = (item.bonus_keywords || []).join(', ');
    const safeUrl = safeDuplicateUrl(item.url);
    const link = safeUrl
        ? `<a href="${escapeDuplicateHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">🔗</a>`
        : '';
    return `
        <article class="article-card duplicate-review-item" data-id="${escapeDuplicateHtml(item.article_id)}"
            data-status="${escapeDuplicateHtml(item.status)}"
            data-report-type="${escapeDuplicateHtml(item.report_type)}">
            <div class="duplicate-review-item-header">
                <label class="review-select-wrap" title="选择">
                    <input type="checkbox" class="duplicate-review-select" aria-label="选择《${title}》">
                </label>
                <h5>${title} ${link}</h5>
                <div class="review-card-actions">
                    <button type="button" class="review-discard-btn duplicate-review-discard"
                        title="放弃新闻" aria-label="放弃《${title}》">🗑️</button>
                    <select class="status-select duplicate-review-status" aria-label="修改《${title}》的栏目">
                        ${duplicateStatusOptions(item)}
                    </select>
                </div>
            </div>
            <div class="meta-row duplicate-review-item-meta">
                <div class="meta-item">来源：${source}</div>
                <div class="meta-item">分数：${escapeDuplicateHtml(score)}</div>
                ${bonusText ? `<div class="meta-item">Bonus：${escapeDuplicateHtml(bonusText)}</div>` : ''}
            </div>
            <div class="review-summary-wrap">
                <textarea class="summary-box duplicate-review-summary-box"
                    data-id="${escapeDuplicateHtml(item.article_id)}">${summary}</textarea>
                <span class="review-summary-count" title="摘要非空白字符数">${summaryCount}字</span>
            </div>
            <input class="source-box duplicate-review-source" data-id="${escapeDuplicateHtml(item.article_id)}"
                value="${source}" placeholder="新闻来源">
            <div class="duplicate-review-processed" hidden>已处理</div>
        </article>
    `;
}

function reconcileDuplicateReviewResult(result, scope) {
    if (!isDuplicateReviewScopeActive(scope)) return result;
    const currentItems = state.reviewData[scope.decision] || [];
    const itemLookup = new Map(
        currentItems.filter(item => item.article_id).map(item => [item.article_id, item])
    );
    const checkedIds = new Set(result.checked_article_ids || []);
    const groups = (result.groups || []).map(group => ({
        ...group,
        items: (group.items || []).filter(item => itemLookup.has(item.article_id)).map(item => {
            const current = itemLookup.get(item.article_id);
            return {
                ...item,
                title: current.title || item.title,
                summary: current.summary || '',
                source: current.llm_source_display || current.source || '',
                url: current.url || item.url,
                status: current.manual_status || current.status || scope.decision,
                report_type: current.report_type || scope.reportType,
                score: current.external_importance_score ?? current.score ?? item.score,
                bonus_keywords: current.bonus_keywords || item.bonus_keywords || []
            };
        })
    })).filter(group => group.items.length >= 2);
    const currentIds = new Set(itemLookup.keys());
    return {
        ...result,
        current_count: currentItems.length,
        added_count: Array.from(currentIds).filter(articleId => !checkedIds.has(articleId)).length,
        removed_count: Array.from(checkedIds).filter(articleId => !currentIds.has(articleId)).length,
        groups
    };
}

function renderDuplicateReviewResult(rawResult, scope = getDuplicateReviewScope()) {
    const modal = document.getElementById('duplicate-review-modal');
    const meta = document.getElementById('duplicate-review-meta');
    const results = document.getElementById('duplicate-review-results');
    const toolbar = document.getElementById('duplicate-review-toolbar');
    if (!modal || !meta || !results || !toolbar) return;

    const result = reconcileDuplicateReviewResult(rawResult, scope);
    const groups = result.groups || [];
    duplicateReviewDisplayedScope = scope;
    toolbar.hidden = !groups.length;
    const addedText = result.added_count
        ? ` · ${result.added_count} 条新增新闻未参与本次检查`
        : '';
    meta.textContent = `${getDuplicateReviewColumnLabel(scope)} · 已检查 ${result.checked_count || 0} 条 · 发现 ${groups.length} 组重复${addedText}`;
    if (!groups.length) {
        results.innerHTML = `
            <div class="duplicate-review-empty">
                <strong>未发现重复新闻</strong>
                <span>当前栏目中的新闻未被识别为同一事件报道。</span>
            </div>
        `;
    } else {
        results.innerHTML = groups.map((group, index) => `
            <section class="duplicate-review-group" data-group-id="${escapeDuplicateHtml(group.group_id)}"
                data-group-index="${index}" ${index === 0 ? '' : 'hidden'}>
                <div class="duplicate-review-group-heading">
                    <h4>重复组 ${index + 1}</h4>
                    <span>${group.items.length} 条新闻</span>
                </div>
                <div class="duplicate-review-group-items">
                    ${group.items.map(renderDuplicateReviewItem).join('')}
                </div>
            </section>
        `).join('');
    }
    duplicateReviewActiveGroupIndex = 0;
    bindDuplicateReviewStatusControls();
    showDuplicateReviewGroup(0);
    modal.classList.add('active');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('duplicate-review-open');
    const closeButton = document.getElementById('btn-close-duplicate-review');
    if (closeButton) closeButton.focus();
}

function getDuplicateReviewGroups() {
    return Array.from(document.querySelectorAll('.duplicate-review-group'));
}

function getActiveDuplicateReviewGroup() {
    const groups = getDuplicateReviewGroups();
    return groups[duplicateReviewActiveGroupIndex] || null;
}

function showDuplicateReviewGroup(index) {
    const groups = getDuplicateReviewGroups();
    const pager = document.getElementById('duplicate-review-pager');
    const indicator = document.getElementById('duplicate-review-page-indicator');
    const previousButton = document.getElementById('btn-duplicate-prev-group');
    const nextButton = document.getElementById('btn-duplicate-next-group');
    if (!groups.length) {
        if (pager) pager.hidden = true;
        return;
    }

    duplicateReviewActiveGroupIndex = Math.max(0, Math.min(index, groups.length - 1));
    groups.forEach((group, groupIndex) => {
        group.hidden = groupIndex !== duplicateReviewActiveGroupIndex;
    });
    if (pager) pager.hidden = groups.length <= 1;
    if (indicator) indicator.textContent = `第 ${duplicateReviewActiveGroupIndex + 1} / ${groups.length} 组`;
    if (previousButton) previousButton.disabled = duplicateReviewActiveGroupIndex === 0;
    if (nextButton) nextButton.disabled = duplicateReviewActiveGroupIndex === groups.length - 1;

    const activeGroup = getActiveDuplicateReviewGroup();
    if (activeGroup) {
        activeGroup.querySelectorAll('.duplicate-review-summary-box').forEach(box => {
            refreshReviewSummaryBox(box);
        });
    }
    const results = document.getElementById('duplicate-review-results');
    if (results) results.scrollTop = 0;
    updateDuplicateReviewSelectionState();
}

function moveDuplicateReviewGroup(offset) {
    showDuplicateReviewGroup(duplicateReviewActiveGroupIndex + offset);
}

function collectDuplicateReviewEdits(items = null, { onlyDirty = false } = {}) {
    const scope = items || document.getElementById('duplicate-review-results');
    const edits = {};
    if (!scope) return edits;
    const targets = scope.matches && scope.matches('.duplicate-review-item:not(.is-processed)')
        ? [scope]
        : Array.from(scope.querySelectorAll('.duplicate-review-item:not(.is-processed)'));
    targets.forEach(item => {
        if (onlyDirty && !item.classList.contains('is-dirty')) return;
        const articleId = item.dataset.id;
        if (!articleId) return;
        const summaryBox = item.querySelector('.duplicate-review-summary-box');
        const sourceBox = item.querySelector('.duplicate-review-source');
        edits[articleId] = {
            summary: summaryBox ? summaryBox.value : '',
            llm_source: sourceBox ? sourceBox.value : ''
        };
    });
    return edits;
}

async function saveDuplicateReviewEdits(edits, reportType = state.reviewReportType) {
    if (!Object.keys(edits).length) return;
    const response = await fetch(`${API_BASE}/edit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ edits, actor: state.actor, report_type: reportType })
    });
    if (!response.ok) throw new Error('保存编辑失败');
    Object.entries(edits).forEach(([articleId, edit]) => {
        applyReviewEditsToState(articleId, edit.summary, edit.llm_source);
        document.querySelectorAll('.duplicate-review-item').forEach(item => {
            if (item.dataset.id === articleId) item.classList.remove('is-dirty');
        });
    });
}

async function flushDuplicateModalEdits() {
    const modal = document.getElementById('duplicate-review-modal');
    if (!modal || !modal.classList.contains('active')) return;
    const reportType = duplicateReviewDisplayedScope?.reportType || state.reviewReportType;
    await saveDuplicateReviewEdits(
        collectDuplicateReviewEdits(null, { onlyDirty: true }),
        reportType
    );
}

async function flushReviewEditsBeforeDuplicateCheck(scope = getDuplicateReviewScope()) {
    const view = scope.decision;
    const items = state.reviewData[view] || [];
    const itemLookup = {};
    items.forEach(item => {
        if (item.article_id) itemLookup[item.article_id] = item;
    });
    const edits = {};
    const reviewContainer = getActiveReviewContainer();
    reviewContainer.querySelectorAll('.article-card').forEach(card => {
        const articleId = card.dataset.id;
        const item = itemLookup[articleId];
        if (!articleId || !item) return;
        const summaryBox = card.querySelector('.summary-box');
        const sourceBox = card.querySelector('.source-box');
        const summary = summaryBox ? summaryBox.value : (item.summary || '');
        const source = sourceBox ? sourceBox.value : (item.llm_source_display || '');
        const edit = {};
        if (summary !== (item.summary || '')) edit.summary = summary;
        if (source !== (item.llm_source_display || '')) edit.llm_source = source;
        if (Object.keys(edit).length) edits[articleId] = edit;
    });
    if (!Object.keys(edits).length) return;
    const response = await fetch(`${API_BASE}/edit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ edits, actor: state.actor, report_type: scope.reportType })
    });
    if (!response.ok) throw new Error('保存当前编辑失败');
    Object.entries(edits).forEach(([articleId, edit]) => {
        applyReviewEditsToState(articleId, edit.summary, edit.llm_source);
    });
}

async function readDuplicateError(response) {
    try {
        const payload = await response.json();
        return payload.detail || 'AI 查重失败，请稍后重试';
    } catch (error) {
        return 'AI 查重失败，请稍后重试';
    }
}

function updateDuplicateReviewJobUI() {
    const scope = getDuplicateReviewScope();
    const job = duplicateReviewJobs.get(getDuplicateReviewScopeKey(scope));
    const checkButton = document.getElementById('btn-check-duplicates');
    if (checkButton) {
        checkButton.disabled = job?.status === 'running';
        checkButton.classList.toggle('has-result', job?.status === 'ready');
        if (job?.status === 'running') checkButton.textContent = '正在检查…';
        else if (job?.status === 'ready') {
            checkButton.textContent = `查看查重结果（${(job.result?.groups || []).length}组）`;
        } else if (job?.status === 'error') checkButton.textContent = '重新检查';
        else checkButton.textContent = '检查重复';
    }
    elements.reviewRailButtons.forEach(button => {
        const buttonScope = getDuplicateReviewScope(button.dataset.reportType, button.dataset.view);
        const buttonJob = duplicateReviewJobs.get(getDuplicateReviewScopeKey(buttonScope));
        button.classList.toggle('duplicate-check-running', buttonJob?.status === 'running');
        button.classList.toggle('duplicate-check-ready', buttonJob?.status === 'ready');
    });
    const recheckButton = document.getElementById('btn-recheck-duplicates');
    if (recheckButton && duplicateReviewDisplayedScope) {
        const displayedJob = duplicateReviewJobs.get(
            getDuplicateReviewScopeKey(duplicateReviewDisplayedScope)
        );
        recheckButton.disabled = displayedJob?.status === 'running';
        recheckButton.textContent = displayedJob?.status === 'running' ? '正在检查…' : '重新检查';
    }
}

function setDuplicateReviewModalBusy(isBusy) {
    const modal = document.getElementById('duplicate-review-modal');
    if (!modal || !modal.classList.contains('active')) return;
    const content = modal.querySelector('.duplicate-review-modal-content');
    const results = document.getElementById('duplicate-review-results');
    const toolbar = document.getElementById('duplicate-review-toolbar');
    if (content) content.classList.toggle('is-checking', isBusy);
    if (results) results.inert = isBusy;
    if (toolbar) toolbar.inert = isBusy;
    modal.setAttribute('aria-busy', isBusy ? 'true' : 'false');
    ['btn-close-duplicate-review', 'btn-finish-duplicate-review'].forEach(buttonId => {
        const button = document.getElementById(buttonId);
        if (button) button.disabled = isBusy;
    });
}

function notifyDuplicateReviewComplete(scope, result) {
    const groupCount = (result.groups || []).length;
    showToast(`${getDuplicateReviewColumnLabel(scope)}查重完成，发现 ${groupCount} 组重复`);
}

async function startDuplicateReviewCheck(scope) {
    const scopeKey = getDuplicateReviewScopeKey(scope);
    const existingJob = duplicateReviewJobs.get(scopeKey);
    if (existingJob?.status === 'running') return;
    const requestId = ++duplicateReviewRequestSequence;
    duplicateReviewJobs.set(scopeKey, { status: 'running', requestId, result: null });
    updateDuplicateReviewJobUI();
    const modal = document.getElementById('duplicate-review-modal');
    const isModalRecheck = modal?.classList.contains('active')
        && duplicateReviewDisplayedScope
        && getDuplicateReviewScopeKey(duplicateReviewDisplayedScope) === scopeKey;
    if (isModalRecheck) setDuplicateReviewModalBusy(true);
    try {
        await flushDuplicateModalEdits();
        if (isDuplicateReviewScopeActive(scope)) {
            await flushReviewEditsBeforeDuplicateCheck(scope);
        }
        const response = await fetch(`${API_BASE}/duplicate-check`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                report_type: scope.reportType,
                decision: scope.decision
            })
        });
        if (!response.ok) throw new Error(await readDuplicateError(response));
        const result = await response.json();
        const currentJob = duplicateReviewJobs.get(scopeKey);
        if (!currentJob || currentJob.requestId !== requestId) return;
        duplicateReviewJobs.set(scopeKey, { status: 'ready', requestId, result });
        if (isDuplicateReviewScopeActive(scope)) renderDuplicateReviewResult(result, scope);
        else notifyDuplicateReviewComplete(scope, result);
    } catch (error) {
        const currentJob = duplicateReviewJobs.get(scopeKey);
        if (currentJob?.requestId === requestId) {
            duplicateReviewJobs.set(scopeKey, {
                status: 'error',
                requestId,
                result: null,
                error: error.message || 'AI 查重失败，请稍后重试'
            });
            showToast(`${getDuplicateReviewColumnLabel(scope)}：${error.message || 'AI 查重失败，请稍后重试'}`, 'error');
        }
    } finally {
        if (isModalRecheck) setDuplicateReviewModalBusy(false);
        updateDuplicateReviewJobUI();
    }
}

async function handleDuplicateCheck(event = null) {
    const checkButton = document.getElementById('btn-check-duplicates');
    const button = event && event.currentTarget ? event.currentTarget : checkButton;
    if (!button || button.disabled) return;
    const isRecheck = button.id === 'btn-recheck-duplicates';
    const scope = isRecheck && duplicateReviewDisplayedScope
        ? duplicateReviewDisplayedScope
        : getDuplicateReviewScope();
    const job = duplicateReviewJobs.get(getDuplicateReviewScopeKey(scope));
    if (!isRecheck && job?.status === 'ready' && job.result) {
        duplicateReviewTrigger = checkButton;
        renderDuplicateReviewResult(job.result, scope);
        return;
    }
    if (!isRecheck) duplicateReviewTrigger = checkButton;
    await startDuplicateReviewCheck(scope);
}

function buildDuplicateDecisionPayload(value, articleId, reportType) {
    const payload = {
        selected_ids: [],
        backup_ids: [],
        discarded_ids: [],
        pending_ids: [],
        actor: state.actor,
        report_type: reportType
    };
    if (value.includes(':')) {
        const [targetReportType, targetStatus] = value.split(':');
        payload.report_type = targetReportType === 'wanbao' ? 'wanbao' : 'zongbao';
        if (targetStatus === 'selected') payload.selected_ids = [articleId];
        if (targetStatus === 'backup') payload.backup_ids = [articleId];
    } else if (value === 'discarded') {
        payload.discarded_ids = [articleId];
    } else if (value === 'pending') {
        payload.pending_ids = [articleId];
    }
    return payload;
}

async function postDuplicateDecision(payload) {
    const response = await fetch(`${API_BASE}/decide`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    if (!response.ok) throw new Error('状态更新失败');
}

async function handleDuplicateStatusChange(event) {
    const select = event.target;
    const item = select.closest('.duplicate-review-item');
    if (!item) return;
    const articleId = item.dataset.id;
    const previousStatus = item.dataset.status;
    const previousReportType = item.dataset.reportType || state.reviewReportType;
    const previousValue = `${previousReportType}:${previousStatus}`;
    if (select.value === previousValue) return;

    select.disabled = true;
    try {
        await saveDuplicateReviewEdits(
            collectDuplicateReviewEdits(item, { onlyDirty: true }),
            previousReportType
        );
        await postDuplicateDecision(buildDuplicateDecisionPayload(select.value, articleId, previousReportType));
        markDuplicateReviewItemProcessed(item);
        await loadReviewData();
        loadStats();
        const undoAction = buildUndoToastAction(async () => {
            try {
                await postDuplicateDecision(buildDuplicateDecisionPayload(previousValue, articleId, previousReportType));
                restoreDuplicateReviewItem(item);
                select.value = previousValue;
                await loadReviewData();
                loadStats();
                showToast('已撤销操作');
            } catch (error) {
                showToast('撤销失败', 'error');
            }
        });
        showToast('状态已更新', 'success', undoAction);
    } catch (error) {
        select.value = previousValue;
        select.disabled = false;
        showToast(error.message || '状态更新失败', 'error');
    }
}

function markDuplicateReviewItemProcessed(item) {
    item.classList.add('is-processed');
    const processed = item.querySelector('.duplicate-review-processed');
    if (processed) processed.hidden = false;
    item.querySelectorAll('textarea, input, select, button').forEach(control => {
        control.disabled = true;
    });
    updateDuplicateReviewSelectionState();
}

function restoreDuplicateReviewItem(item) {
    item.classList.remove('is-processed');
    const processed = item.querySelector('.duplicate-review-processed');
    if (processed) processed.hidden = true;
    item.querySelectorAll('textarea, input, select, button').forEach(control => {
        control.disabled = false;
    });
    updateDuplicateReviewSelectionState();
}

async function handleDuplicateDiscardClick(event) {
    const item = event.currentTarget.closest('.duplicate-review-item');
    if (!item) return;
    const select = item.querySelector('.duplicate-review-status');
    if (!select) return;
    select.value = 'discarded';
    await handleDuplicateStatusChange({ target: select });
}

async function handleDuplicateSummaryUpdate(event) {
    const item = event.target.closest('.duplicate-review-item');
    if (!item) return;
    try {
        await saveDuplicateReviewEdits(collectDuplicateReviewEdits(item));
        showToast('摘要已保存');
    } catch (error) {
        showToast('摘要保存失败', 'error');
    }
}

async function handleDuplicateSourceUpdate(event) {
    const item = event.target.closest('.duplicate-review-item');
    if (!item) return;
    try {
        await saveDuplicateReviewEdits(collectDuplicateReviewEdits(item));
        showToast('来源已保存');
    } catch (error) {
        showToast('来源保存失败', 'error');
    }
}

function getSelectableDuplicateItems() {
    const activeGroup = getActiveDuplicateReviewGroup();
    if (!activeGroup) return [];
    return Array.from(activeGroup.querySelectorAll('.duplicate-review-item:not(.is-processed)'));
}

function updateDuplicateReviewSelectionState() {
    const selectAll = document.getElementById('duplicate-review-select-all');
    const count = document.getElementById('duplicate-review-selection-count');
    if (!selectAll || !count) return;
    const checkboxes = getSelectableDuplicateItems()
        .map(item => item.querySelector('.duplicate-review-select'))
        .filter(Boolean);
    const checkedCount = checkboxes.filter(checkbox => checkbox.checked).length;
    selectAll.checked = checkboxes.length > 0 && checkedCount === checkboxes.length;
    selectAll.indeterminate = checkedCount > 0 && checkedCount < checkboxes.length;
    selectAll.disabled = !checkboxes.length;
    count.textContent = `本组已选择 ${checkedCount} 条`;
}

function toggleDuplicateReviewSelectAll(checked) {
    getSelectableDuplicateItems().forEach(item => {
        const checkbox = item.querySelector('.duplicate-review-select');
        if (checkbox) checkbox.checked = checked;
    });
    updateDuplicateReviewSelectionState();
}

async function applyDuplicateBulkStatus() {
    const bulkSelect = document.getElementById('duplicate-review-bulk-status');
    if (!bulkSelect || !bulkSelect.value) return;
    const selectedItems = getSelectableDuplicateItems().filter(item => {
        const checkbox = item.querySelector('.duplicate-review-select');
        return checkbox && checkbox.checked;
    });
    if (!selectedItems.length) {
        bulkSelect.value = '';
        showToast('请先选择要移动的条目', 'error');
        return;
    }

    const targetValue = bulkSelect.value;
    bulkSelect.value = '';
    const articleIds = selectedItems.map(item => item.dataset.id).filter(Boolean);
    const previousReportType = selectedItems[0].dataset.reportType || state.reviewReportType;
    const previousStatus = selectedItems[0].dataset.status || state.reviewView;
    if (targetValue === `${previousReportType}:${previousStatus}`) {
        showToast('所选新闻已在当前栏目', 'error');
        return;
    }
    bulkSelect.disabled = true;
    try {
        const edits = {};
        selectedItems.forEach(item => Object.assign(
            edits,
            collectDuplicateReviewEdits(item, { onlyDirty: true })
        ));
        await saveDuplicateReviewEdits(edits, previousReportType);
        const payload = buildDuplicateDecisionPayload(targetValue, articleIds[0], previousReportType);
        ['selected_ids', 'backup_ids', 'discarded_ids', 'pending_ids'].forEach(key => {
            if (payload[key].length) payload[key] = articleIds;
        });
        await postDuplicateDecision(payload);
        selectedItems.forEach(markDuplicateReviewItemProcessed);
        await loadReviewData();
        loadStats();
        const undoAction = buildUndoToastAction(async () => {
            try {
                const previousValue = `${previousReportType}:${previousStatus}`;
                const undoPayload = buildDuplicateDecisionPayload(previousValue, articleIds[0], previousReportType);
                if (previousStatus === 'selected') undoPayload.selected_ids = articleIds;
                if (previousStatus === 'backup') undoPayload.backup_ids = articleIds;
                await postDuplicateDecision(undoPayload);
                selectedItems.forEach(restoreDuplicateReviewItem);
                await loadReviewData();
                loadStats();
                showToast('已撤销操作');
            } catch (error) {
                showToast('撤销失败', 'error');
            }
        });
        showToast(`已更新 ${articleIds.length} 条新闻`, 'success', undoAction);
    } catch (error) {
        showToast(error.message || '批量移动失败', 'error');
    } finally {
        bulkSelect.disabled = false;
        updateDuplicateReviewSelectionState();
    }
}

function bindDuplicateReviewStatusControls() {
    document.querySelectorAll('.duplicate-review-status').forEach(select => {
        select.addEventListener('change', handleDuplicateStatusChange);
    });
    document.querySelectorAll('.duplicate-review-discard').forEach(button => {
        button.addEventListener('click', handleDuplicateDiscardClick);
    });
    document.querySelectorAll('.duplicate-review-select').forEach(checkbox => {
        checkbox.addEventListener('change', updateDuplicateReviewSelectionState);
    });
    document.querySelectorAll('.duplicate-review-summary-box').forEach(box => {
        box.addEventListener('input', () => {
            const item = box.closest('.duplicate-review-item');
            if (item) item.classList.add('is-dirty');
            refreshReviewSummaryBox(box);
        });
        box.addEventListener('change', handleDuplicateSummaryUpdate);
    });
    document.querySelectorAll('.duplicate-review-source').forEach(input => {
        input.addEventListener('input', () => {
            const item = input.closest('.duplicate-review-item');
            if (item) item.classList.add('is-dirty');
        });
        input.addEventListener('change', handleDuplicateSourceUpdate);
    });
}

function closeDuplicateReviewModal() {
    const modal = document.getElementById('duplicate-review-modal');
    if (!modal) return;
    modal.classList.remove('active');
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('duplicate-review-open');
    if (duplicateReviewTrigger) duplicateReviewTrigger.focus();
}

async function finishDuplicateReview() {
    const displayedScopeKey = duplicateReviewDisplayedScope
        ? getDuplicateReviewScopeKey(duplicateReviewDisplayedScope)
        : null;
    try {
        await flushDuplicateModalEdits();
        closeDuplicateReviewModal();
        if (displayedScopeKey) duplicateReviewJobs.delete(displayedScopeKey);
        duplicateReviewDisplayedScope = null;
        updateDuplicateReviewJobUI();
        await loadReviewData();
        loadStats();
    } catch (error) {
        showToast('保存编辑失败，请重试', 'error');
    }
}

function setupDuplicateReview() {
    const checkButton = document.getElementById('btn-check-duplicates');
    const closeButton = document.getElementById('btn-close-duplicate-review');
    const finishButton = document.getElementById('btn-finish-duplicate-review');
    const recheckButton = document.getElementById('btn-recheck-duplicates');
    const selectAll = document.getElementById('duplicate-review-select-all');
    const bulkStatus = document.getElementById('duplicate-review-bulk-status');
    const previousGroup = document.getElementById('btn-duplicate-prev-group');
    const nextGroup = document.getElementById('btn-duplicate-next-group');
    if (checkButton) checkButton.addEventListener('click', handleDuplicateCheck);
    if (closeButton) closeButton.addEventListener('click', finishDuplicateReview);
    if (finishButton) finishButton.addEventListener('click', finishDuplicateReview);
    if (recheckButton) recheckButton.addEventListener('click', handleDuplicateCheck);
    if (selectAll) {
        selectAll.addEventListener('change', event => {
            toggleDuplicateReviewSelectAll(Boolean(event.target.checked));
        });
    }
    if (bulkStatus) bulkStatus.addEventListener('change', applyDuplicateBulkStatus);
    if (previousGroup) previousGroup.addEventListener('click', () => moveDuplicateReviewGroup(-1));
    if (nextGroup) nextGroup.addEventListener('click', () => moveDuplicateReviewGroup(1));
    document.addEventListener('keydown', event => {
        const modal = document.getElementById('duplicate-review-modal');
        if (event.key === 'Escape' && modal && modal.classList.contains('active')) {
            finishDuplicateReview();
        }
    });
}
