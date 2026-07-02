// Manual Filter JS - Duplicate Review

let duplicateReviewTrigger = null;

function escapeDuplicateHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function getDuplicateReviewColumnLabel() {
    const reportLabel = state.reviewReportType === 'wanbao' ? '晚报' : '综报';
    const decisionLabel = state.reviewView === 'backup' ? '备选' : '采纳';
    return `${reportLabel}${decisionLabel}`;
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
    const score = item.score ?? '-';
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

function renderDuplicateReviewResult(result) {
    const modal = document.getElementById('duplicate-review-modal');
    const meta = document.getElementById('duplicate-review-meta');
    const results = document.getElementById('duplicate-review-results');
    const toolbar = document.getElementById('duplicate-review-toolbar');
    if (!modal || !meta || !results || !toolbar) return;

    const groups = result.groups || [];
    toolbar.hidden = !groups.length;
    meta.textContent = `${getDuplicateReviewColumnLabel()} · 已检查 ${result.checked_count || 0} 条 · 发现 ${groups.length} 组重复`;
    if (!groups.length) {
        results.innerHTML = `
            <div class="duplicate-review-empty">
                <strong>未发现重复新闻</strong>
                <span>当前栏目中的新闻未被识别为同一事件报道。</span>
            </div>
        `;
    } else {
        results.innerHTML = groups.map((group, index) => `
            <section class="duplicate-review-group" data-group-id="${escapeDuplicateHtml(group.group_id)}">
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
    bindDuplicateReviewStatusControls();
    updateDuplicateReviewSelectionState();
    results.querySelectorAll('.duplicate-review-summary-box').forEach(box => {
        refreshReviewSummaryBox(box);
    });
    modal.classList.add('active');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('duplicate-review-open');
    const closeButton = document.getElementById('btn-close-duplicate-review');
    if (closeButton) closeButton.focus();
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

async function saveDuplicateReviewEdits(edits) {
    if (!Object.keys(edits).length) return;
    const response = await fetch(`${API_BASE}/edit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ edits, actor: state.actor, report_type: state.reviewReportType })
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
    await saveDuplicateReviewEdits(collectDuplicateReviewEdits(null, { onlyDirty: true }));
}

async function flushReviewEditsBeforeDuplicateCheck() {
    const view = state.reviewView === 'backup' ? 'backup' : 'selected';
    const items = state.reviewData[view] || [];
    const itemLookup = {};
    items.forEach(item => {
        if (item.article_id) itemLookup[item.article_id] = item;
    });
    const edits = {};
    const scope = getActiveReviewContainer();
    scope.querySelectorAll('.article-card').forEach(card => {
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
        body: JSON.stringify({ edits, actor: state.actor, report_type: state.reviewReportType })
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

async function handleDuplicateCheck() {
    const button = document.getElementById('btn-check-duplicates');
    if (!button || button.disabled) return;
    duplicateReviewTrigger = button;
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = '正在检查…';
    try {
        await flushDuplicateModalEdits();
        await flushReviewEditsBeforeDuplicateCheck();
        const response = await fetch(`${API_BASE}/duplicate-check`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                report_type: state.reviewReportType,
                decision: state.reviewView === 'backup' ? 'backup' : 'selected'
            })
        });
        if (!response.ok) throw new Error(await readDuplicateError(response));
        renderDuplicateReviewResult(await response.json());
    } catch (error) {
        showToast(error.message || 'AI 查重失败，请稍后重试', 'error');
    } finally {
        button.disabled = false;
        button.textContent = originalText;
    }
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
        await saveDuplicateReviewEdits(collectDuplicateReviewEdits(item));
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
    return Array.from(document.querySelectorAll('.duplicate-review-item:not(.is-processed)'));
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
    count.textContent = `已选择 ${checkedCount} 条`;
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
        selectedItems.forEach(item => Object.assign(edits, collectDuplicateReviewEdits(item)));
        await saveDuplicateReviewEdits(edits);
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
    try {
        await flushDuplicateModalEdits();
        closeDuplicateReviewModal();
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
    document.addEventListener('keydown', event => {
        const modal = document.getElementById('duplicate-review-modal');
        if (event.key === 'Escape' && modal && modal.classList.contains('active')) {
            finishDuplicateReview();
        }
    });
}
