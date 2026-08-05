// Duty Summary JS - Init
async function importSelectedItems() {
    const target = selectedImportTarget();
    elements.importTarget.value = '';
    if (!state.shiftId || !state.selected.size) {
        showToast('请先选择要送入汇总审阅的新闻', 'error');
        return;
    }
    if (!target.reportType || !target.targetStatus) {
        return;
    }
    const payload = {
        shift_id: state.shiftId,
        article_ids: [...state.selected],
        target_status: target.targetStatus,
        report_type: target.reportType
    };
    const undoTargets = captureImportUndoTargets(payload.article_ids);
    elements.importTarget.disabled = true;
    try {
        const preview = await request('/api/admin/duty-summary/import-preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (preview.conflicts && preview.conflicts.length) {
            openImportConflictModal(preview.conflicts, payload, undoTargets);
            return;
        }
        await submitDutyImport(payload, [], undoTargets);
    } catch (error) {
        showToast(error.message || '送入栏目失败', 'error');
    } finally {
        elements.importTarget.disabled = false;
        updateSelection();
    }
}

elements.importTarget.addEventListener('change', importSelectedItems);

document.getElementById('btn-close-import-conflict').addEventListener(
    'click',
    closeImportConflictModal
);
document.getElementById('btn-keep-existing').addEventListener('click', () => {
    chooseImportConflict('existing');
});
document.getElementById('btn-keep-duty').addEventListener('click', () => {
    chooseImportConflict('duty');
});
document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && elements.conflictModal.classList.contains('active')) {
        closeImportConflictModal();
    }
});

setShiftPanelOpen(false);
syncAdminProcessTabs();
syncSearchClearButton();
loadSummary()
    .then(() => {
        if (state.shiftId) return loadResults();
        return undefined;
    })
    .catch(error => {
        elements.items.innerHTML = `<div class="summary-empty empty-state">${escapeHtml(error.message)}</div>`;
    });
