// Manual Filter JS - Duplicate Review

let duplicateReviewTrigger = null;

function collectDuplicateReviewEdits(items = null, { onlyDirty = false } = {}) {
    const scope = items || document.getElementById('duplicate-review-results');
    const edits = {};
    if (!scope) return edits;
    const targets = scope.matches && scope.matches('.duplicate-review-item')
        ? [scope]
        : Array.from(scope.querySelectorAll('.duplicate-review-item'));
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
    const articleIds = Object.keys(edits);
    const response = await workspaceFetch(`${API_BASE}/edit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            edits,
            versions: collectManualReviewVersions(articleIds),
            report_type: reportType
        })
    });
    await requireManualMutationSuccess(response, '保存编辑失败');
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
    const response = await workspaceFetch(`${API_BASE}/edit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            edits,
            versions: collectManualReviewVersions(Object.keys(edits)),
            report_type: scope.reportType
        })
    });
    await requireManualMutationSuccess(response, '保存当前编辑失败');
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

async function startDuplicateReviewCheck(scope) {
    const scopeKey = getDuplicateReviewScopeKey(scope);
    const existingJob = duplicateReviewJobs.get(scopeKey);
    if (existingJob?.status === 'running') return;
    const requestId = ++duplicateReviewRequestSequence;
    duplicateReviewJobs.set(scopeKey, { status: 'running', requestId, result: null });
    updateDuplicateReviewJobUI();
    try {
        await flushDuplicateModalEdits();
        if (isDuplicateReviewScopeActive(scope)) {
            await flushReviewEditsBeforeDuplicateCheck(scope);
        }
        const response = await workspaceFetch(`${API_BASE}/duplicate-check`, {
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
        updateDuplicateReviewJobUI();
    }
}

async function handleDuplicateCheck() {
    const checkButton = document.getElementById('btn-check-duplicates');
    if (!checkButton || checkButton.disabled) return;
    const scope = getDuplicateReviewScope();
    const job = duplicateReviewJobs.get(getDuplicateReviewScopeKey(scope));
    if (job?.status === 'ready' && job.result) {
        duplicateReviewTrigger = checkButton;
        renderDuplicateReviewResult(job.result, scope);
        return;
    }
    duplicateReviewTrigger = checkButton;
    await startDuplicateReviewCheck(scope);
}

function buildDuplicateDecisionPayload(value, articleId, reportType) {
    const payload = {
        selected_ids: [],
        backup_ids: [],
        discarded_ids: [],
        pending_ids: [],
        report_type: reportType,
        versions: collectManualReviewVersions([articleId])
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

function parseDuplicateDecisionState(value, fallbackReportType) {
    if (value.includes(':')) {
        const [reportType, status] = value.split(':');
        return {
            reportType: reportType === 'wanbao' ? 'wanbao' : 'zongbao',
            status
        };
    }
    return { reportType: fallbackReportType, status: value };
}

function getDuplicateDecisionValue(status, reportType) {
    if (status === 'selected' || status === 'backup') return `${reportType}:${status}`;
    return status;
}

function updateDuplicateItemDecisionState(item, value, fallbackReportType) {
    const nextState = parseDuplicateDecisionState(value, fallbackReportType);
    item.dataset.reportType = nextState.reportType;
    item.dataset.status = nextState.status;
}

async function postDuplicateDecision(payload) {
    const response = await workspaceFetch(`${API_BASE}/decide`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    return requireManualMutationSuccess(response, '状态更新失败');
}

async function handleDuplicateStatusChange(event) {
    const select = event.target;
    const item = select.closest('.duplicate-review-item');
    if (!item) return;
    const articleId = item.dataset.id;
    const previousStatus = item.dataset.status;
    const previousReportType = item.dataset.reportType || state.reviewReportType;
    const previousValue = getDuplicateDecisionValue(previousStatus, previousReportType);
    if (select.value === previousValue) return;
    const shouldHideAfterUpdate = select.value === 'discarded';

    select.disabled = true;
    try {
        await saveDuplicateReviewEdits(
            collectDuplicateReviewEdits(item, { onlyDirty: true }),
            previousReportType
        );
        await postDuplicateDecision(buildDuplicateDecisionPayload(select.value, articleId, previousReportType));
        updateDuplicateItemDecisionState(item, select.value, previousReportType);
        markDuplicateReviewItemProcessed(item);
        if (shouldHideAfterUpdate) hideDiscardedDuplicateReviewItem(item);
        await loadReviewData();
        loadStats();
        const undoAction = buildUndoToastAction(async () => {
            try {
                await postDuplicateDecision(buildDuplicateDecisionPayload(previousValue, articleId, previousReportType));
                updateDuplicateItemDecisionState(item, previousValue, previousReportType);
                restoreDuplicateReviewItem(item);
                if (shouldHideAfterUpdate) restoreDiscardedDuplicateReviewItem(item);
                select.value = previousValue;
                await loadReviewData();
                loadStats();
                showToast('已撤销操作');
            } catch (error) {
                showToast(error.message || '撤销失败', 'error');
            }
        });
        showToast('状态已更新', 'success', undoAction);
    } catch (error) {
        select.value = previousValue;
        showToast(error.message || '状态更新失败', 'error');
    } finally {
        select.disabled = false;
    }
}

function markDuplicateReviewItemProcessed(item) {
    item.classList.add('is-processed');
    const processed = item.querySelector('.duplicate-review-processed');
    if (processed) processed.hidden = false;
    updateDuplicateReviewSelectionState();
}

function restoreDuplicateReviewItem(item) {
    item.classList.remove('is-processed');
    const processed = item.querySelector('.duplicate-review-processed');
    if (processed) processed.hidden = true;
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
        await saveDuplicateReviewEdits(
            collectDuplicateReviewEdits(item),
            item.dataset.reportType || state.reviewReportType
        );
        showToast('摘要已保存');
    } catch (error) {
        showToast(error.message || '摘要保存失败', 'error');
    }
}

async function handleDuplicateSourceUpdate(event) {
    const item = event.target.closest('.duplicate-review-item');
    if (!item) return;
    try {
        await saveDuplicateReviewEdits(
            collectDuplicateReviewEdits(item),
            item.dataset.reportType || state.reviewReportType
        );
        showToast('来源已保存');
    } catch (error) {
        showToast(error.message || '来源保存失败', 'error');
    }
}

function getSelectableDuplicateItems() {
    const activeGroup = getActiveDuplicateReviewGroup();
    if (!activeGroup) return [];
    return Array.from(activeGroup.querySelectorAll('.duplicate-review-item'));
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
    const previousValue = getDuplicateDecisionValue(previousStatus, previousReportType);
    if (targetValue === previousValue) {
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
        payload.versions = collectManualReviewVersions(articleIds);
        await postDuplicateDecision(payload);
        selectedItems.forEach(item => {
            updateDuplicateItemDecisionState(item, targetValue, previousReportType);
            markDuplicateReviewItemProcessed(item);
            if (targetValue === 'discarded') hideDiscardedDuplicateReviewItem(item);
        });
        await loadReviewData();
        loadStats();
        const undoAction = buildUndoToastAction(async () => {
            try {
                const undoPayload = buildDuplicateDecisionPayload(
                    previousValue,
                    articleIds[0],
                    previousReportType
                );
                if (previousStatus === 'selected') undoPayload.selected_ids = articleIds;
                if (previousStatus === 'backup') undoPayload.backup_ids = articleIds;
                undoPayload.versions = collectManualReviewVersions(articleIds);
                await postDuplicateDecision(undoPayload);
                selectedItems.forEach(item => {
                    updateDuplicateItemDecisionState(item, previousValue, previousReportType);
                    restoreDuplicateReviewItem(item);
                    if (targetValue === 'discarded') restoreDiscardedDuplicateReviewItem(item);
                });
                await loadReviewData();
                loadStats();
                showToast('已撤销操作');
            } catch (error) {
                showToast(error.message || '撤销失败', 'error');
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
        showToast(error.message || '保存编辑失败，请重试', 'error');
    }
}

function setupDuplicateReview() {
    const checkButton = document.getElementById('btn-check-duplicates');
    const finishButton = document.getElementById('btn-finish-duplicate-review');
    const selectAll = document.getElementById('duplicate-review-select-all');
    const bulkStatus = document.getElementById('duplicate-review-bulk-status');
    const previousGroup = document.getElementById('btn-duplicate-prev-group');
    const nextGroup = document.getElementById('btn-duplicate-next-group');
    if (checkButton) checkButton.addEventListener('click', handleDuplicateCheck);
    if (finishButton) finishButton.addEventListener('click', finishDuplicateReview);
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
