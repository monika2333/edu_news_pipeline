(() => {
    const state = {
        shifts: [],
        shiftId: '',
        uncovered: false,
        items: [],
        selected: new Set()
    };
    const elements = {
        shiftList: document.getElementById('summary-shift-list'),
        items: document.getElementById('summary-items'),
        context: document.getElementById('summary-context'),
        title: document.getElementById('summary-title'),
        decision: document.getElementById('summary-decision'),
        reportType: document.getElementById('summary-report-type'),
        importBar: document.getElementById('summary-import-bar'),
        selectionCount: document.getElementById('summary-selection-count'),
        toast: document.getElementById('toast'),
        uncoveredButton: document.getElementById('btn-uncovered')
    };

    function escapeHtml(value) {
        const node = document.createElement('div');
        node.textContent = String(value ?? '');
        return node.innerHTML;
    }

    function formatDateTime(value) {
        if (!value) return '—';
        return new Intl.DateTimeFormat('zh-CN', {
            month: 'numeric',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        }).format(new Date(value));
    }

    function showToast(message) {
        elements.toast.textContent = message;
        elements.toast.classList.add('show');
        window.setTimeout(() => elements.toast.classList.remove('show'), 1800);
    }

    async function request(path, options) {
        const response = await fetch(path, options);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.detail || '请求失败');
        return payload;
    }

    function renderShifts() {
        elements.shiftList.innerHTML = state.shifts.map(shift => `
            <button class="summary-shift-card ${state.shiftId === shift.shift_id ? 'is-active' : ''}" data-shift-id="${escapeHtml(shift.shift_id)}">
                <strong>${escapeHtml(shift.display_name)} · ${escapeHtml(formatDateTime(shift.ends_at))}</strong>
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
                loadResults();
            });
        });
    }

    function updateSelection() {
        elements.selectionCount.textContent = `已选择 ${state.selected.size} 条`;
        elements.importBar.hidden = state.uncovered || !state.shiftId;
    }

    function renderItems() {
        updateSelection();
        if (!state.items.length) {
            elements.items.innerHTML = '<div class="summary-empty">当前筛选没有记录。</div>';
            return;
        }
        elements.items.innerHTML = state.items.map(item => `
            <article class="summary-item">
                ${state.uncovered ? '' : `<input type="checkbox" data-article-id="${escapeHtml(item.article_id)}" ${state.selected.has(item.article_id) ? 'checked' : ''}>`}
                <div>
                    <h3>${escapeHtml(item.title || '无标题')}</h3>
                    <div class="summary-item-meta">
                        <span>${escapeHtml(item.decision || '未覆盖')}</span>
                        <span>${escapeHtml(item.report_type || '')}</span>
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
                updateSelection();
            });
        });
    }

    async function loadSummary() {
        const payload = await request('/api/admin/duty-summary?limit=60');
        state.shifts = payload.items || [];
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
        if (elements.decision.value) params.set('decision', elements.decision.value);
        if (elements.reportType.value) params.set('report_type', elements.reportType.value);
        const payload = await request(
            `/api/admin/duty-summary/${encodeURIComponent(state.shiftId)}/reviews?${params}`
        );
        state.items = payload.items || [];
        const shift = state.shifts.find(item => item.shift_id === state.shiftId);
        elements.context.textContent = shift
            ? `${shift.display_name} · ${formatDateTime(shift.starts_at)} – ${formatDateTime(shift.ends_at)}`
            : '';
        elements.title.textContent = `初审结果（${payload.total}）`;
        renderItems();
    }

    elements.uncoveredButton.addEventListener('click', () => {
        state.uncovered = true;
        state.shiftId = '';
        state.selected.clear();
        renderShifts();
        elements.uncoveredButton.classList.add('is-active');
        loadResults();
    });

    [elements.decision, elements.reportType].forEach(select => {
        select.addEventListener('change', () => {
            state.selected.clear();
            loadResults();
        });
    });

    document.getElementById('btn-import-results').addEventListener('click', async () => {
        if (!state.shiftId || !state.selected.size) {
            window.alert('请先选择要送入管理员工作区的新闻。');
            return;
        }
        const [reportType, targetStatus] = document.getElementById('summary-import-target').value.split(':');
        if (!window.confirm(`确定送入${reportType === 'zongbao' ? '综报' : '晚报'}${targetStatus === 'selected' ? '采纳' : '备选'}吗？`)) return;
        try {
            const result = await request('/api/admin/duty-summary/import', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    shift_id: state.shiftId,
                    article_ids: [...state.selected],
                    target_status: targetStatus,
                    report_type: reportType
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

    loadSummary().catch(error => {
        elements.items.innerHTML = `<div class="summary-empty">${escapeHtml(error.message)}</div>`;
    });
})();
