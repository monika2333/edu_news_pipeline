// Duty Summary JS - Import Conflict
function setConflictModalOpen(open) {
    elements.conflictModal.classList.toggle('active', open);
    elements.conflictModal.setAttribute('aria-hidden', String(!open));
    if (open) elements.existingSummary.focus();
}

function renderImportConflict() {
    const conflict = state.importConflicts[state.importConflictIndex];
    if (!conflict) return;
    elements.conflictProgress.textContent = (
        `重复新闻 ${state.importConflictIndex + 1} / ${state.importConflicts.length}`
    );
    elements.conflictTitle.textContent = conflict.title || '无标题';
    elements.existingColumn.textContent = reviewColumnLabel(
        conflict.existing.report_type,
        conflict.existing.status
    );
    elements.existingSummary.value = conflict.existing.summary || '';
    elements.existingSource.value = conflict.existing.manual_llm_source || '';
    elements.dutyColumn.textContent = reviewColumnLabel(
        conflict.duty.report_type,
        state.pendingImport?.target_status || conflict.duty.decision
    );
    elements.dutySummary.value = conflict.duty.summary || '';
    elements.dutySource.value = conflict.duty.manual_llm_source || '';
}

function closeImportConflictModal() {
    setConflictModalOpen(false);
    state.pendingImport = null;
    state.pendingUndoTargets = [];
    state.importConflicts = [];
    state.importConflictIndex = 0;
    state.conflictResolutions = [];
}

async function submitDutyImport(
    payload,
    conflictResolutions = [],
    undoTargets = captureImportUndoTargets(payload.article_ids)
) {
    const result = await request('/api/admin/duty-summary/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            ...payload,
            conflict_resolutions: conflictResolutions
        })
    });
    state.selected.clear();
    elements.importTarget.value = '';
    const actionLabel = `已送入${reviewColumnLabel(
        payload.report_type,
        payload.target_status
    )}`;
    const undoAction = buildUndoToastAction(async () => {
        try {
            await undoDutyImport(result.items, undoTargets);
        } catch (error) {
            showToast(error.message || '撤销失败', 'error');
        }
    });
    showToast(`${actionLabel} ${result.imported} 条`, 'success', undoAction);
    await Promise.all([loadSummary(), loadResults()]);
}

async function chooseImportConflict(choice) {
    const conflict = state.importConflicts[state.importConflictIndex];
    if (!conflict || !state.pendingImport) return;
    const keepExisting = choice === 'existing';
    state.conflictResolutions.push({
        article_id: conflict.article_id,
        choice,
        summary: keepExisting
            ? elements.existingSummary.value
            : elements.dutySummary.value,
        manual_llm_source: keepExisting
            ? elements.existingSource.value
            : elements.dutySource.value,
        existing_version: conflict.existing.version
    });
    state.importConflictIndex += 1;
    if (state.importConflictIndex < state.importConflicts.length) {
        renderImportConflict();
        return;
    }
    const payload = state.pendingImport;
    const resolutions = [...state.conflictResolutions];
    const undoTargets = [...state.pendingUndoTargets];
    closeImportConflictModal();
    try {
        await submitDutyImport(payload, resolutions, undoTargets);
    } catch (error) {
        window.alert(`${error.message}。汇总审阅内容可能已经变化，请重新操作。`);
    }
}

function openImportConflictModal(conflicts, payload, undoTargets) {
    state.pendingImport = payload;
    state.pendingUndoTargets = undoTargets;
    state.importConflicts = conflicts;
    state.importConflictIndex = 0;
    state.conflictResolutions = [];
    renderImportConflict();
    setConflictModalOpen(true);
}

