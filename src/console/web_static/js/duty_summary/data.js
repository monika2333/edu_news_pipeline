// Duty Summary JS - Data
async function loadSummary() {
    const payload = await request('/api/admin/duty-summary?limit=60');
    state.shifts = normalizeShifts(payload.items);
    if (!state.shifts.some(shift => shift.shift_id === state.shiftId)) {
        const initial = chooseInitialShift();
        state.shiftId = initial ? initial.shift_id : '';
        state.selected.clear();
    }
    renderShifts();
}

async function loadResults() {
    if (!state.shiftId) return;
    const params = new URLSearchParams({ limit: '200' });
    if (state.adminDiscarded) {
        params.set('admin_discarded_only', 'true');
    } else {
        params.set('decision', state.targetStatus);
        if (state.adminProcessScope === 'unprocessed') {
            params.set('admin_unprocessed_only', 'true');
        } else {
            params.set('include_admin_discarded', 'true');
        }
        if (elements.comparison.value === 'mismatch') {
            params.set('mismatch_only', 'true');
        }
        params.set('report_type', state.targetReportType);
    }
    const payload = await request(
        `/api/admin/duty-summary/${encodeURIComponent(state.shiftId)}/reviews?${params}`
    );
    state.items = payload.items || [];
    const shift = state.shifts.find(item => item.shift_id === state.shiftId);
    elements.context.textContent = shift
        ? `${shift.display_name} · ${window.formatDutyShiftDate(shift.ends_at)}`
        : '';
    renderItems();
}

elements.shiftsToggle.addEventListener('click', () => {
    setShiftPanelOpen(!state.shiftsOpen);
});

elements.shiftsClose.addEventListener('click', () => {
    setShiftPanelOpen(false);
});

elements.comparison.addEventListener('change', () => {
    state.selected.clear();
    loadResults();
});

elements.processTabs.forEach(tab => {
    tab.addEventListener('click', () => {
        state.adminProcessScope = tab.dataset.adminProcessScope === 'all'
            ? 'all'
            : 'unprocessed';
        state.selected.clear();
        syncAdminProcessTabs();
        renderColumnCounts();
        loadResults();
    });
});

elements.searchInput.addEventListener('input', () => {
    state.searchQuery = elements.searchInput.value;
    syncSearchClearButton();
    renderItems();
});

elements.searchClear.addEventListener('click', () => {
    elements.searchInput.value = '';
    state.searchQuery = '';
    syncSearchClearButton();
    renderItems();
    elements.searchInput.focus();
});

elements.selectAll.addEventListener('change', () => {
    getVisibleItems().forEach(item => {
        if (elements.selectAll.checked) state.selected.add(item.article_id);
        else state.selected.delete(item.article_id);
    });
    renderItems();
});

elements.columnTabs.forEach(tab => {
    tab.addEventListener('click', () => {
        state.targetReportType = tab.dataset.reportType;
        state.targetStatus = tab.dataset.targetStatus;
        state.selected.clear();
        elements.columnTabs.forEach(item => {
            const active = item === tab;
            item.classList.toggle('active', active);
            item.setAttribute('aria-selected', String(active));
        });
        loadResults();
    });
});

elements.viewTabs.forEach(tab => {
    tab.addEventListener('click', () => {
        state.adminDiscarded = tab.dataset.summaryView === 'discarded';
        state.searchQuery = '';
        state.selected.clear();
        elements.searchInput.value = '';
        syncSearchClearButton();
        elements.comparison.value = '';
        elements.comparison.disabled = state.adminDiscarded;
        elements.filterLayout.classList.toggle('is-discarded', state.adminDiscarded);
        syncAdminProcessTabs();
        elements.viewTabs.forEach(item => {
            const active = item === tab;
            item.classList.toggle('active', active);
            item.setAttribute('aria-selected', String(active));
        });
        loadResults();
    });
});

elements.discardButton.addEventListener('click', discardSelectedItems);
