// Duty Summary JS - Admin Actions
async function quickDecideItem(button) {
    if (button.disabled || !state.shiftId) return;
    const item = findAdminItem(button.dataset.articleId);
    if (!item) return;
    if (button.dataset.cancelSelected === 'true') {
        await resetAdminDecision(button, item);
        return;
    }
    if (button.dataset.quickStatus === 'discarded') {
        if (
            button.dataset.cancelDiscarded === 'true'
            && !isRecoveredManualDiscard(item)
            && !item.admin_discarded_at
            && item.admin_status === 'discarded'
        ) {
            await resetAdminDecision(button, item);
            return;
        }
        await setAdminDiscarded(button, true);
        return;
    }
    const payload = {
        shift_id: state.shiftId,
        article_ids: [button.dataset.articleId],
        target_status: button.dataset.quickStatus,
        report_type: state.targetReportType
    };
    const undoTargets = captureImportUndoTargets(payload.article_ids);
    button.disabled = true;
    try {
        const preview = await request('/api/admin/duty-summary/import-preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (preview.conflicts && preview.conflicts.length) {
            button.disabled = false;
            openImportConflictModal(preview.conflicts, payload, undoTargets);
            return;
        }
        await submitDutyImport(payload, [], undoTargets);
    } catch (error) {
        button.disabled = false;
        window.alert(error.message);
    }
}

async function resetAdminDecision(button, item) {
    if (button.disabled) return;
    button.disabled = true;
    try {
        const previousStatus = item.admin_status;
        const result = await setManualReviewStatus(item, 'pending');
        const nextVersion = result.versions?.[item.article_id];
        const undoAction = buildUndoToastAction(async () => {
            try {
                await setManualReviewStatus(item, previousStatus, nextVersion);
                showToast('已撤销操作');
                await Promise.all([loadSummary(), loadResults()]);
            } catch (error) {
                showToast(error.message || '撤销失败', 'error');
            }
        });
        state.selected.delete(item.article_id);
        showToast('已撤回到未处理', 'success', undoAction);
        await Promise.all([loadSummary(), loadResults()]);
    } catch (error) {
        button.disabled = false;
        window.alert(error.message);
    }
}

async function setAdminDiscarded(button, discarded) {
    if (button.disabled || !state.shiftId) return;
    const item = findAdminItem(button.dataset.articleId);
    if (!item) return;
    const nextDiscarded = isRecoveredManualDiscard(item)
        ? true
        : button.dataset.cancelDiscarded === 'true'
            ? false
            : discarded;
    button.disabled = true;
    try {
        await patchAdminDiscard(item.article_id, nextDiscarded);
        const undoAction = buildUndoToastAction(async () => {
            try {
                await patchAdminDiscard(item.article_id, !nextDiscarded);
                showToast('已撤销操作');
                await Promise.all([loadSummary(), loadResults()]);
            } catch (error) {
                showToast(error.message || '撤销失败', 'error');
            }
        });
        state.selected.delete(item.article_id);
        showToast(
            nextDiscarded ? '已移入放弃栏目' : '已恢复到原栏目',
            'success',
            undoAction
        );
        await Promise.all([loadSummary(), loadResults()]);
    } catch (error) {
        button.disabled = false;
        window.alert(error.message);
    }
}

async function undoAdminProcessing(button) {
    if (button.disabled || !state.shiftId) return;
    const item = findAdminItem(button.dataset.articleId);
    if (!item) return;
    button.disabled = true;
    try {
        const previousStatus = ['selected', 'backup', 'discarded'].includes(item.admin_status)
            ? item.admin_status
            : null;
        let nextVersion = item.admin_version;
        if (previousStatus) {
            const result = await setManualReviewStatus(item, 'pending');
            nextVersion = result.versions?.[item.article_id];
        }
        if (item.admin_discarded_at) {
            await patchAdminDiscard(item.article_id, false);
        }
        const undoAction = buildUndoToastAction(async () => {
            try {
                if (previousStatus) {
                    await setManualReviewStatus(item, previousStatus, nextVersion);
                }
                if (item.admin_discarded_at) {
                    await patchAdminDiscard(item.article_id, true);
                }
                showToast('已撤销操作');
                await Promise.all([loadSummary(), loadResults()]);
            } catch (error) {
                showToast(error.message || '撤销失败', 'error');
            }
        });
        state.selected.delete(item.article_id);
        showToast('已撤回到未处理', 'success', undoAction);
        await Promise.all([loadSummary(), loadResults()]);
    } catch (error) {
        button.disabled = false;
        window.alert(error.message);
    }
}

async function discardSelectedItems() {
    if (!state.shiftId || !state.selected.size) {
        window.alert('请先选择要放弃的新闻。');
        return;
    }
    const articleIds = [...state.selected];
    elements.discardButton.disabled = true;
    try {
        const result = await request('/api/admin/duty-summary/discard-bulk', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                shift_id: state.shiftId,
                article_ids: articleIds,
                discarded: true
            })
        });
        state.selected.clear();
        showToast(`已放弃 ${result.updated} 条新闻`);
        await Promise.all([loadSummary(), loadResults()]);
    } catch (error) {
        window.alert(error.message);
    } finally {
        updateSelection();
    }
}

