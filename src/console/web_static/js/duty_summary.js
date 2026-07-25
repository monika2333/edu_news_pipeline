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
        selected: new Set()
    };
    const elements = {
        shiftList: document.getElementById('summary-shift-list'),
        items: document.getElementById('summary-items'),
        context: document.getElementById('summary-context'),
        title: document.getElementById('summary-title'),
        decision: document.getElementById('summary-decision'),
        importBar: document.getElementById('summary-import-bar'),
        searchInput: document.getElementById('summary-search-input'),
        selectAll: document.getElementById('summary-select-all'),
        columnTabs: [...document.querySelectorAll('.summary-column-tab')],
        selectionCount: document.getElementById('summary-selection-count'),
        toast: document.getElementById('toast'),
        uncoveredButton: document.getElementById('btn-uncovered'),
        layout: document.getElementById('summary-layout'),
        shiftsPanel: document.getElementById('summary-shifts-panel'),
        shiftsToggle: document.getElementById('btn-toggle-shifts')
    };
    const businessTimeZone = 'Asia/Shanghai';

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

    function formatShiftDate(value) {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '日期未知';
        const today = new Date();
        const yesterday = new Date(today.getTime() - 24 * 60 * 60 * 1000);
        const key = businessDateKey(date);
        const calendarLabel = new Intl.DateTimeFormat('zh-CN', {
            timeZone: businessTimeZone,
            month: 'long',
            day: 'numeric',
            weekday: 'short'
        }).format(date);
        if (key === businessDateKey(today)) return `今天 · ${calendarLabel}`;
        if (key === businessDateKey(yesterday)) return `昨天 · ${calendarLabel}`;
        return calendarLabel;
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
        elements.shiftsToggle.textContent = state.shiftsOpen ? '收起班次' : '查看班次';
    }

    function showToast(message) {
        elements.toast.textContent = message;
        elements.toast.classList.add('show');
        window.setTimeout(() => elements.toast.classList.remove('show'), 1800);
    }

    function activeColumnLabel() {
        const reportLabel = state.targetReportType === 'zongbao' ? '综报' : '晚报';
        const statusLabel = state.targetStatus === 'selected' ? '采纳' : '备选';
        return `${reportLabel}${statusLabel}`;
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
            item.decision
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
            elements.shiftList.innerHTML = '<div class="summary-empty">暂无当前或历史班次。</div>';
            return;
        }
        elements.shiftList.innerHTML = state.shifts.map(shift => `
            <button class="summary-shift-card ${state.shiftId === shift.shift_id ? 'is-active' : ''}" data-shift-id="${escapeHtml(shift.shift_id)}">
                <strong>
                    <span class="summary-shift-date">${escapeHtml(formatShiftDate(shift.ends_at))}</span>
                    <span class="summary-shift-owner">${escapeHtml(shift.display_name)}</span>
                </strong>
                <div class="summary-shift-coverage">${escapeHtml(formatDateTime(shift.starts_at))} – ${escapeHtml(formatDateTime(shift.ends_at))}</div>
                <div class="summary-shift-counts">
                    <span>总 ${shift.total}</span><span>待 ${shift.pending}</span>
                    <span>采 ${shift.selected}</span><span>备 ${shift.backup}</span>
                </div>
            </button>
        `).join('');
        elements.shiftList.querySelectorAll('[data-shift-id]').forEach(button => {
            button.addEventListener('click', () => {
                state.shiftId = button.dataset.shiftId;
                state.uncovered = false;
                state.selected.clear();
                renderShifts();
                elements.uncoveredButton.classList.remove('is-active');
                setShiftPanelOpen(false);
                loadResults();
            });
        });
    }

    function updateSelection(visibleItems = getVisibleItems()) {
        elements.selectionCount.textContent = `已选择 ${state.selected.size} 条`;
        elements.importBar.hidden = state.uncovered || !state.shiftId;
        const visibleIds = visibleItems.map(item => item.article_id);
        const selectedVisibleCount = visibleIds.filter(id => state.selected.has(id)).length;
        elements.selectAll.disabled = !visibleIds.length || state.uncovered;
        elements.selectAll.checked = Boolean(visibleIds.length)
            && selectedVisibleCount === visibleIds.length;
        elements.selectAll.indeterminate = selectedVisibleCount > 0
            && selectedVisibleCount < visibleIds.length;
    }

    function renderItems() {
        const visibleItems = getVisibleItems();
        updateSelection(visibleItems);
        if (!state.items.length) {
            elements.items.innerHTML = '<div class="summary-empty">当前筛选没有记录。</div>';
            return;
        }
        if (!visibleItems.length) {
            elements.items.innerHTML = '<div class="summary-empty">没有找到匹配的新闻。</div>';
            return;
        }
        elements.items.innerHTML = visibleItems.map(item => `
            <article class="summary-item">
                ${state.uncovered ? '' : `<input type="checkbox" data-article-id="${escapeHtml(item.article_id)}" ${state.selected.has(item.article_id) ? 'checked' : ''}>`}
                <div>
                    <h3>${escapeHtml(item.title || '无标题')}</h3>
                    <div class="summary-item-meta">
                        <span>值班：${escapeHtml(item.decision || '未覆盖')}</span>
                        <span>${escapeHtml(item.report_type || '')}</span>
                        ${state.uncovered ? '' : `<span>管理员：${escapeHtml(item.admin_status || 'pending')}</span>`}
                        ${state.uncovered ? '' : `<span>${escapeHtml(item.admin_report_type || 'zongbao')}</span>`}
                        <span>${escapeHtml(item.source || item.llm_source || '未知来源')}</span>
                        <span>${escapeHtml(formatDateTime(item.publish_time_iso || item.created_at))}</span>
                    </div>
                    <p>${escapeHtml(item.edited_summary || item.summary || item.llm_summary || '')}</p>
                </div>
            </article>
        `).join('');
        elements.items.querySelectorAll('[data-article-id]').forEach(checkbox => {
            checkbox.addEventListener('change', () => {
                if (checkbox.checked) state.selected.add(checkbox.dataset.articleId);
                else state.selected.delete(checkbox.dataset.articleId);
                updateSelection(visibleItems);
            });
        });
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
            elements.title.textContent = '无有效班次覆盖';
            renderItems();
            return;
        }
        if (!state.shiftId) return;
        const params = new URLSearchParams({ limit: '200' });
        if (elements.decision.value === 'mismatch') {
            params.set('mismatch_only', 'true');
        } else if (elements.decision.value) {
            params.set('decision', elements.decision.value);
        }
        params.set('report_type', state.targetReportType);
        const payload = await request(
            `/api/admin/duty-summary/${encodeURIComponent(state.shiftId)}/reviews?${params}`
        );
        state.items = payload.items || [];
        const shift = state.shifts.find(item => item.shift_id === state.shiftId);
        elements.context.textContent = shift
            ? `${shift.display_name} · ${formatDateTime(shift.starts_at)} – ${formatDateTime(shift.ends_at)}`
            : '';
        elements.title.textContent = `${activeColumnLabel()}（${payload.total}）`;
        renderItems();
    }

    elements.uncoveredButton.addEventListener('click', () => {
        state.uncovered = true;
        state.shiftId = '';
        state.selected.clear();
        renderShifts();
        elements.uncoveredButton.classList.add('is-active');
        setShiftPanelOpen(false);
        loadResults();
    });

    elements.shiftsToggle.addEventListener('click', () => {
        setShiftPanelOpen(!state.shiftsOpen);
    });

    elements.decision.addEventListener('change', () => {
        state.selected.clear();
        loadResults();
    });

    elements.searchInput.addEventListener('input', () => {
        state.searchQuery = elements.searchInput.value;
        renderItems();
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
                item.classList.toggle('is-active', active);
                item.setAttribute('aria-selected', String(active));
            });
            loadResults();
        });
    });

    document.getElementById('btn-import-results').addEventListener('click', async () => {
        if (!state.shiftId || !state.selected.size) {
            window.alert('请先选择要送入管理员工作区的新闻。');
            return;
        }
        if (!window.confirm(`确定送入${activeColumnLabel()}吗？`)) return;
        try {
            const result = await request('/api/admin/duty-summary/import', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    shift_id: state.shiftId,
                    article_ids: [...state.selected],
                    target_status: state.targetStatus,
                    report_type: state.targetReportType
                })
            });
            state.selected.clear();
            showToast(`已送入 ${result.imported} 条`);
            await Promise.all([loadSummary(), loadResults()]);
        } catch (error) {
            window.alert(error.message);
        }
    });

    document.getElementById('btn-summary-refresh').addEventListener('click', async () => {
        await loadSummary();
        if (state.shiftId || state.uncovered) await loadResults();
    });

    setShiftPanelOpen(false);
    loadSummary()
        .then(() => {
            if (state.shiftId) return loadResults();
            return undefined;
        })
        .catch(error => {
            elements.items.innerHTML = `<div class="summary-empty">${escapeHtml(error.message)}</div>`;
        });
})();
