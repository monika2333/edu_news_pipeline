(() => {
    const state = {
        shifts: [],
        shiftId: '',
        shiftsOpen: false,
        uncovered: false,
        items: [],
        searchQuery: '',
        targetReportType: 'zongbao',
        targetStatus: 'selected',
        adminDiscarded: false,
        selected: new Set(),
        pendingImport: null,
        importConflicts: [],
        importConflictIndex: 0,
        conflictResolutions: []
    };
    const elements = {
        shiftList: document.getElementById('summary-shift-list'),
        items: document.getElementById('summary-items'),
        context: document.getElementById('summary-context'),
        comparison: document.getElementById('summary-comparison'),
        importButton: document.getElementById('btn-import-results'),
        importTarget: document.getElementById('summary-import-target'),
        discardButton: document.getElementById('btn-discard-selected'),
        importBar: document.getElementById('summary-import-bar'),
        searchInput: document.getElementById('summary-search-input'),
        searchClear: document.getElementById('summary-search-clear'),
        selectAll: document.getElementById('summary-select-all'),
        viewTabs: [...document.querySelectorAll('[data-summary-view]')],
        columnTabs: [...document.querySelectorAll('.summary-column-tab')],
        filterLayout: document.getElementById('summary-filter-layout'),
        selectionCount: document.getElementById('summary-selection-count'),
        toast: document.getElementById('toast'),
        uncoveredButton: document.getElementById('btn-uncovered'),
        layout: document.getElementById('summary-layout'),
        shiftsPanel: document.getElementById('summary-shifts-panel'),
        shiftsToggle: document.getElementById('btn-toggle-shifts'),
        conflictModal: document.getElementById('summary-import-conflict-modal'),
        conflictProgress: document.getElementById('summary-conflict-progress'),
        conflictTitle: document.getElementById('summary-conflict-article-title'),
        existingColumn: document.getElementById('summary-existing-column'),
        existingSummary: document.getElementById('summary-existing-summary'),
        existingSource: document.getElementById('summary-existing-source'),
        dutyColumn: document.getElementById('summary-duty-column'),
        dutySummary: document.getElementById('summary-duty-summary'),
        dutySource: document.getElementById('summary-duty-source')
    };
    const businessTimeZone = 'Asia/Shanghai';
    const reviewColumns = [
        ['zongbao', 'selected'],
        ['zongbao', 'backup'],
        ['wanbao', 'selected'],
        ['wanbao', 'backup']
    ];

    function escapeHtml(value) {
        const node = document.createElement('div');
        node.textContent = String(value ?? '');
        return node.innerHTML;
    }

    function formatDateTime(value) {
        if (!value) return '—';
        return new Intl.DateTimeFormat('zh-CN', {
            timeZone: businessTimeZone,
            month: 'numeric',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        }).format(new Date(value));
    }

    function businessDateKey(value) {
        const date = value instanceof Date ? value : new Date(value);
        if (Number.isNaN(date.getTime())) return '';
        const parts = new Intl.DateTimeFormat('en', {
            timeZone: businessTimeZone,
            year: 'numeric',
            month: '2-digit',
            day: '2-digit'
        }).formatToParts(date);
        const fields = Object.fromEntries(parts.map(part => [part.type, part.value]));
        return `${fields.year}-${fields.month}-${fields.day}`;
    }

    function normalizeShifts(items) {
        const now = Date.now();
        return (items || [])
            .filter(shift => new Date(shift.starts_at).getTime() <= now)
            .sort((left, right) => (
                new Date(right.ends_at).getTime() - new Date(left.ends_at).getTime()
            ));
    }

    function chooseInitialShift() {
        const todayKey = businessDateKey(new Date());
        return state.shifts.find(shift => (
            businessDateKey(shift.ends_at) === todayKey
        )) || state.shifts[0] || null;
    }

    function setShiftPanelOpen(open) {
        state.shiftsOpen = Boolean(open);
        elements.shiftsPanel.hidden = !state.shiftsOpen;
        elements.layout.classList.toggle('is-shifts-collapsed', !state.shiftsOpen);
        elements.shiftsToggle.setAttribute('aria-expanded', String(state.shiftsOpen));
        elements.shiftsToggle.textContent = state.shiftsOpen
            ? '收起历史班次'
            : '查看历史班次';
    }

    function showToast(message) {
        elements.toast.textContent = message;
        elements.toast.classList.add('show');
        window.setTimeout(() => elements.toast.classList.remove('show'), 1800);
    }

    function reviewColumnLabel(reportType, status) {
        const reportLabel = reportType === 'wanbao' ? '晚报' : '综报';
        const statusLabels = {
            selected: '采纳',
            backup: '备选',
            pending: '待处理',
            discarded: '放弃',
            exported: '已归档'
        };
        const statusLabel = statusLabels[status] || '待处理';
        return `${reportLabel}${statusLabel}`;
    }

    function renderImportTargets() {
        const current = `${state.targetReportType}:${state.targetStatus}`;
        const alternatives = reviewColumns
            .filter(([reportType, status]) => `${reportType}:${status}` !== current)
            .map(([reportType, status]) => (
                `<option value="${reportType}:${status}">${reviewColumnLabel(reportType, status)}</option>`
            ));
        elements.importTarget.innerHTML = [
            '<option value="">送入当前栏目</option>',
            ...alternatives
        ].join('');
    }

    function selectedImportTarget() {
        const [reportType, targetStatus] = elements.importTarget.value.split(':');
        return {
            reportType: reportType || state.targetReportType,
            targetStatus: targetStatus || state.targetStatus
        };
    }

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
        state.importConflicts = [];
        state.importConflictIndex = 0;
        state.conflictResolutions = [];
    }

    async function submitDutyImport(payload, conflictResolutions = []) {
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
        showToast(`${actionLabel} ${result.imported} 条`);
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
        closeImportConflictModal();
        try {
            await submitDutyImport(payload, resolutions);
        } catch (error) {
            window.alert(`${error.message}。汇总审阅内容可能已经变化，请重新操作。`);
        }
    }

    function openImportConflictModal(conflicts, payload) {
        state.pendingImport = payload;
        state.importConflicts = conflicts;
        state.importConflictIndex = 0;
        state.conflictResolutions = [];
        renderImportConflict();
        setConflictModalOpen(true);
    }

    function getVisibleItems() {
        const query = state.searchQuery.trim().toLocaleLowerCase('zh-CN');
        if (!query) return state.items;
        return state.items.filter(item => [
            item.title,
            item.edited_summary,
            item.summary,
            item.llm_summary,
            item.source,
            item.llm_source,
            item.decision,
            item.admin_discarded_by_display_name
        ].some(value => String(value ?? '').toLocaleLowerCase('zh-CN').includes(query)));
    }

    async function request(path, options) {
        const response = await fetch(path, options);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.detail || '请求失败');
        return payload;
    }

    function renderShifts() {
        if (!state.shifts.length) {
            elements.shiftList.innerHTML = '<div class="summary-empty empty-state">暂无当前或历史班次。</div>';
            renderColumnCounts();
            return;
        }
        elements.shiftList.innerHTML = state.shifts.map(shift => `
            <button class="filter-tab-btn summary-shift-card ${state.shiftId === shift.shift_id ? 'active' : ''}" data-shift-id="${escapeHtml(shift.shift_id)}">
                <span class="summary-shift-date">${escapeHtml(window.formatDutyShiftDate(shift.ends_at))}</span>
            </button>
        `).join('');
        elements.shiftList.querySelectorAll('[data-shift-id]').forEach(button => {
            button.addEventListener('click', () => {
                state.shiftId = button.dataset.shiftId;
                state.uncovered = false;
                state.selected.clear();
                renderShifts();
                elements.uncoveredButton.classList.remove('active');
                setShiftPanelOpen(false);
                loadResults();
            });
        });
        renderColumnCounts();
    }

    function renderColumnCounts() {
        const shift = state.shifts.find(item => item.shift_id === state.shiftId);
        elements.columnTabs.forEach(tab => {
            const reportType = tab.dataset.reportType;
            const status = tab.dataset.targetStatus;
            const count = Number(shift?.[`${reportType}_${status}`]) || 0;
            tab.textContent = `${reviewColumnLabel(reportType, status)}（${count}）`;
        });
    }

    function updateSelection(visibleItems = getVisibleItems()) {
        const canImport = !state.adminDiscarded && !state.uncovered && Boolean(state.shiftId);
        elements.selectAll.closest('.summary-select-all').hidden = !canImport;
        elements.selectionCount.hidden = !canImport;
        elements.discardButton.hidden = !canImport;
        elements.importButton.hidden = !canImport;
        elements.importTarget.disabled = !canImport;
        elements.discardButton.disabled = !canImport || !state.selected.size;
        elements.importButton.disabled = !canImport || !state.selected.size;
        elements.selectionCount.textContent = `已选择 ${state.selected.size} 条`;
        elements.importBar.hidden = state.uncovered || !state.shiftId;
        const visibleIds = visibleItems.map(item => item.article_id);
        const selectedVisibleCount = visibleIds.filter(id => state.selected.has(id)).length;
        elements.selectAll.disabled = !visibleIds.length || !canImport;
        elements.selectAll.checked = Boolean(visibleIds.length)
            && selectedVisibleCount === visibleIds.length;
        elements.selectAll.indeterminate = selectedVisibleCount > 0
            && selectedVisibleCount < visibleIds.length;
    }

    function renderItems() {
        const visibleItems = getVisibleItems();
        updateSelection(visibleItems);
        if (!state.items.length) {
            elements.items.innerHTML = '<div class="summary-empty empty-state">当前没有待处理新闻</div>';
            return;
        }
        if (!visibleItems.length) {
            elements.items.innerHTML = '<div class="summary-empty empty-state">没有找到匹配的新闻。</div>';
            return;
        }
        elements.items.innerHTML = visibleItems.map(item => {
            const adminReportType = item.admin_report_type || 'zongbao';
            const selectedActive = item.admin_status === 'selected'
                && adminReportType === state.targetReportType;
            return `
            <article class="article-card summary-item">
                <div class="card-header">
                    ${state.uncovered || state.adminDiscarded ? '' : `<input type="checkbox" data-article-id="${escapeHtml(item.article_id)}" ${state.selected.has(item.article_id) ? 'checked' : ''}>`}
                    <h3 class="article-title">${escapeHtml(item.title || '无标题')}</h3>
                    ${state.uncovered ? '' : `
                        ${state.adminDiscarded ? `
                            <div class="review-card-actions">
                                <button class="btn btn-secondary summary-restore-action" type="button"
                                    data-admin-discard-action="restore"
                                    data-article-id="${escapeHtml(item.article_id)}">
                                    恢复
                                </button>
                            </div>
                        ` : `
                            <div class="review-card-actions" role="group" aria-label="单条新闻操作">
                                <button class="summary-quick-action summary-quick-accept${selectedActive ? ' is-active' : ''}"
                                    type="button" data-quick-status="selected"
                                    data-article-id="${escapeHtml(item.article_id)}"
                                    aria-label="采纳这条新闻" title="采纳"
                                    aria-pressed="${String(selectedActive)}" ${selectedActive ? 'disabled' : ''}>
                                    ✅
                                </button>
                                <button class="summary-quick-action summary-quick-discard"
                                    type="button" data-quick-status="discarded"
                                    data-article-id="${escapeHtml(item.article_id)}"
                                    aria-label="放弃这条新闻" title="放弃">
                                    ❌
                                </button>
                            </div>
                        `}
                    `}
                </div>
                <div class="meta-row">
                        <span>值班：${escapeHtml(item.decision || '未覆盖')}</span>
                        <span>${escapeHtml(item.report_type || '')}</span>
                        ${state.uncovered ? '' : `<span>管理员：${escapeHtml(item.admin_status || 'pending')}</span>`}
                        ${state.uncovered ? '' : `<span>${escapeHtml(item.admin_report_type || 'zongbao')}</span>`}
                        ${state.adminDiscarded ? `<span>放弃人：${escapeHtml(item.admin_discarded_by_display_name || '管理员')}</span>` : ''}
                        <span>${escapeHtml(item.source || item.llm_source || '未知来源')}</span>
                        <span>${escapeHtml(formatDateTime(item.publish_time_iso || item.created_at))}</span>
                </div>
                <p class="summary-box">${escapeHtml(item.edited_summary || item.summary || item.llm_summary || '')}</p>
            </article>
        `;
        }).join('');
        elements.items.querySelectorAll('[data-article-id]').forEach(checkbox => {
            if (checkbox.matches('[data-quick-status]')) return;
            checkbox.addEventListener('change', () => {
                if (checkbox.checked) state.selected.add(checkbox.dataset.articleId);
                else state.selected.delete(checkbox.dataset.articleId);
                updateSelection(visibleItems);
            });
        });
        elements.items.querySelectorAll('[data-quick-status]').forEach(button => {
            button.addEventListener('click', () => {
                quickDecideItem(button);
            });
        });
        elements.items.querySelectorAll('[data-admin-discard-action]').forEach(button => {
            button.addEventListener('click', () => {
                setAdminDiscarded(button, false);
            });
        });
    }

    function syncSearchClearButton() {
        elements.searchClear.hidden = !elements.searchInput.value;
    }

    async function quickDecideItem(button) {
        if (button.disabled || !state.shiftId) return;
        if (button.dataset.quickStatus === 'discarded') {
            await setAdminDiscarded(button, true);
            return;
        }
        const payload = {
            shift_id: state.shiftId,
            article_ids: [button.dataset.articleId],
            target_status: button.dataset.quickStatus,
            report_type: state.targetReportType
        };
        button.disabled = true;
        try {
            const preview = await request('/api/admin/duty-summary/import-preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (preview.conflicts && preview.conflicts.length) {
                button.disabled = false;
                openImportConflictModal(preview.conflicts, payload);
                return;
            }
            await submitDutyImport(payload);
        } catch (error) {
            button.disabled = false;
            window.alert(error.message);
        }
    }

    async function setAdminDiscarded(button, discarded) {
        if (button.disabled || !state.shiftId) return;
        button.disabled = true;
        try {
            await request('/api/admin/duty-summary/discard', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    shift_id: state.shiftId,
                    article_id: button.dataset.articleId,
                    discarded
                })
            });
            state.selected.delete(button.dataset.articleId);
            showToast(discarded ? '已移入放弃栏目' : '已恢复到原栏目');
            await loadSummary();
            await loadResults();
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

    async function loadSummary() {
        const payload = await request('/api/admin/duty-summary?limit=60');
        state.shifts = normalizeShifts(payload.items);
        if (
            !state.uncovered
            && !state.shifts.some(shift => shift.shift_id === state.shiftId)
        ) {
            const initial = chooseInitialShift();
            state.shiftId = initial ? initial.shift_id : '';
            state.selected.clear();
        }
        renderShifts();
    }

    async function loadResults() {
        if (state.uncovered) {
            const payload = await request('/api/admin/duty-summary/uncovered?limit=200');
            state.items = payload.items || [];
            elements.context.textContent = `共 ${payload.total} 条`;
            renderItems();
            return;
        }
        if (!state.shiftId) return;
        const params = new URLSearchParams({ limit: '200' });
        if (state.adminDiscarded) {
            params.set('admin_discarded_only', 'true');
        } else {
            params.set('decision', state.targetStatus);
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

    elements.uncoveredButton.addEventListener('click', () => {
        state.uncovered = true;
        state.shiftId = '';
        state.selected.clear();
        renderShifts();
        elements.uncoveredButton.classList.add('active');
        setShiftPanelOpen(false);
        loadResults();
    });

    elements.shiftsToggle.addEventListener('click', () => {
        setShiftPanelOpen(!state.shiftsOpen);
    });

    elements.comparison.addEventListener('change', () => {
        state.selected.clear();
        loadResults();
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
            renderImportTargets();
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
            renderImportTargets();
            elements.comparison.value = '';
            elements.comparison.disabled = state.adminDiscarded;
            elements.filterLayout.classList.toggle('is-discarded', state.adminDiscarded);
            elements.viewTabs.forEach(item => {
                const active = item === tab;
                item.classList.toggle('active', active);
                item.setAttribute('aria-selected', String(active));
            });
            loadResults();
        });
    });

    elements.discardButton.addEventListener('click', discardSelectedItems);

    elements.importButton.addEventListener('click', async () => {
        if (!state.shiftId || !state.selected.size) {
            window.alert('请先选择要送入汇总审阅的新闻。');
            return;
        }
        const target = selectedImportTarget();
        const payload = {
            shift_id: state.shiftId,
            article_ids: [...state.selected],
            target_status: target.targetStatus,
            report_type: target.reportType
        };
        try {
            const preview = await request('/api/admin/duty-summary/import-preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (preview.conflicts && preview.conflicts.length) {
                openImportConflictModal(preview.conflicts, payload);
                return;
            }
            await submitDutyImport(payload);
        } catch (error) {
            window.alert(error.message);
        }
    });

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
    syncSearchClearButton();
    renderImportTargets();
    loadSummary()
        .then(() => {
            if (state.shiftId) return loadResults();
            return undefined;
        })
        .catch(error => {
            elements.items.innerHTML = `<div class="summary-empty empty-state">${escapeHtml(error.message)}</div>`;
        });
})();
