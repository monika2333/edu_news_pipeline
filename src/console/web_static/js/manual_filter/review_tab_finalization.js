// Duty-editor finalization batches. This is independent from administrator archive.

function finalizationReportLabel(reportType = state.reviewReportType) {
    return reportType === 'wanbao' ? '晚报' : '综报';
}

async function requestDutyFinalization(path, fallbackMessage, payload) {
    const options = payload === undefined ? undefined : {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    };
    const response = await workspaceFetch(`${API_BASE}${path}`, options);
    return requireManualMutationSuccess(response, fallbackMessage);
}

function renderDutyFinalizationStatus(finalization) {
    const container = document.getElementById('duty-finalization-status');
    const text = document.getElementById('duty-finalization-status-text');
    const restoreButton = document.getElementById('btn-restore-finalization');
    const finalizeButton = document.getElementById('btn-finalize-review');
    if (!container || !text || !restoreButton) return;

    const finalized = Boolean(finalization?.batch_id);
    container.dataset.state = finalized ? 'finalized' : 'empty';
    if (finalized) {
        text.textContent = `${finalizationReportLabel()}已定稿`;
        restoreButton.dataset.batchId = finalization.batch_id;
        restoreButton.hidden = false;
    } else {
        text.textContent = `${finalizationReportLabel()}尚未定稿`;
        delete restoreButton.dataset.batchId;
        restoreButton.hidden = true;
    }
    if (finalizeButton) {
        finalizeButton.hidden = finalized;
        finalizeButton.disabled = finalized;
    }
}

async function loadDutyFinalizationStatus() {
    if (!IS_DUTY_WORKSPACE) return;
    const reportType = state.reviewReportType;
    const container = document.getElementById('duty-finalization-status');
    const text = document.getElementById('duty-finalization-status-text');
    const restoreButton = document.getElementById('btn-restore-finalization');
    const finalizeButton = document.getElementById('btn-finalize-review');
    if (container) container.dataset.state = 'loading';
    if (text) text.textContent = `正在读取${finalizationReportLabel(reportType)}定稿状态…`;
    if (restoreButton) restoreButton.hidden = true;
    if (finalizeButton) finalizeButton.disabled = true;
    const params = new URLSearchParams({
        report_type: reportType,
        _t: String(Date.now())
    });
    try {
        const payload = await requestDutyFinalization(
            `/finalizations?${params.toString()}`,
            '定稿状态加载失败'
        );
        if (state.reviewReportType !== reportType) return;
        renderDutyFinalizationStatus(payload.finalization);
    } catch (error) {
        if (state.reviewReportType !== reportType) return;
        if (container) container.dataset.state = 'error';
        if (text) text.textContent = `${finalizationReportLabel(reportType)}定稿状态加载失败`;
        if (finalizeButton) finalizeButton.disabled = true;
        showToast(error.message || '定稿状态加载失败', 'error');
    }
}

async function finalizeCurrentDutyReview() {
    if (!IS_DUTY_WORKSPACE || state.reviewView !== 'selected') return;
    const items = state.reviewData.selected || [];
    if (!items.length) {
        showToast('当前采纳列表为空', 'error');
        return;
    }
    if (!window.confirm(
        `确定将当前 ${items.length} 条${finalizationReportLabel()}采纳新闻定稿并清空列表吗？`
    )) {
        return;
    }

    const button = document.getElementById('btn-finalize-review');
    if (button) button.disabled = true;
    try {
        const activeElement = document.activeElement;
        if (activeElement?.matches('.summary-box, .source-box')) {
            activeElement.blur();
        }
        await pendingReviewEditPromise;
        const orderSaved = await persistReviewOrder();
        if (!orderSaved) return;
        const result = await requestDutyFinalization(
            '/finalizations',
            '定稿失败',
            { report_type: state.reviewReportType }
        );
        clearDutyWorkspaceCache();
        renderDutyFinalizationStatus(result);
        await Promise.all([loadReviewData(), loadStats()]);
        showToast(`已定稿 ${result.item_count} 条新闻`);
    } catch (error) {
        showToast(error.message || '定稿失败', 'error');
    } finally {
        if (button) button.disabled = false;
    }
}

async function restoreDutyFinalization() {
    const button = document.getElementById('btn-restore-finalization');
    const batchId = button?.dataset.batchId;
    if (!batchId || !window.confirm(
        `确定撤回当前${finalizationReportLabel()}定稿吗？撤回后新闻将返回采纳列表。`
    )) {
        return;
    }

    button.disabled = true;
    try {
        const result = await requestDutyFinalization(
            `/finalizations/${encodeURIComponent(batchId)}/restore`,
            '撤回失败',
            {}
        );
        clearDutyWorkspaceCache();
        renderDutyFinalizationStatus(null);
        await Promise.all([loadReviewData(), loadStats()]);
        showToast(`已撤回 ${result.restored} 条新闻`);
    } catch (error) {
        button.disabled = false;
        showToast(error.message || '撤回失败', 'error');
    }
}
