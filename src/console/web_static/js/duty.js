(() => {
    const state = {
        shifts: [],
        shiftId: '',
        reportType: 'zongbao',
        decision: 'pending',
        items: [],
        clusters: [],
        stats: null
    };

    const elements = {
        heading: document.getElementById('shift-heading'),
        coverage: document.getElementById('shift-coverage'),
        shiftSelect: document.getElementById('shift-select'),
        list: document.getElementById('duty-list'),
        total: document.getElementById('duty-stat-total'),
        decided: document.getElementById('duty-stat-decided'),
        pending: document.getElementById('duty-stat-pending'),
        archive: document.getElementById('duty-archive-status'),
        warning: document.getElementById('duty-warning'),
        saveOrder: document.getElementById('btn-duty-save-order'),
        previewDialog: document.getElementById('duty-preview-dialog'),
        previewText: document.getElementById('duty-preview-text'),
        toast: document.getElementById('toast')
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
        if (!response.ok) {
            const error = new Error(payload.detail || '请求失败');
            error.status = response.status;
            throw error;
        }
        return payload;
    }

    function currentShift() {
        return state.shifts.find(item => item.id === state.shiftId);
    }

    function renderShiftHeader() {
        const shift = currentShift();
        if (!shift) {
            elements.heading.textContent = '暂无可用班次';
            elements.coverage.textContent = '请联系管理员检查排班。';
            return;
        }
        const coverageDate = new Date(shift.ends_at).toLocaleDateString('zh-CN', {
            month: 'numeric',
            day: 'numeric'
        });
        elements.heading.textContent = `${coverageDate}班次`;
        elements.coverage.textContent = `覆盖 ${formatDateTime(shift.starts_at)} – ${formatDateTime(shift.ends_at)}`;
    }

    function chooseInitialShift() {
        const active = state.shifts.find(item => item.status === 'active');
        return active || state.shifts[0] || null;
    }

    async function loadShifts() {
        const payload = await request('/api/duty/shifts');
        state.shifts = payload.items || [];
        const selected = chooseInitialShift();
        state.shiftId = selected?.id || '';
        elements.shiftSelect.innerHTML = state.shifts.map(shift => {
            const label = `${formatDateTime(shift.starts_at)} – ${formatDateTime(shift.ends_at)} · ${shift.status}`;
            return `<option value="${escapeHtml(shift.id)}">${escapeHtml(label)}</option>`;
        }).join('');
        elements.shiftSelect.value = state.shiftId;
        renderShiftHeader();
    }

    function archiveLabel(reportType, archivedAt) {
        const name = reportType === 'zongbao' ? '综报' : '晚报';
        return archivedAt ? `${name} 已于 ${formatDateTime(archivedAt)} 归档` : `${name} 未归档`;
    }

    function renderStats() {
        const stats = state.stats || {};
        elements.total.textContent = stats.total ?? '-';
        elements.decided.textContent = stats.decided ?? '-';
        elements.pending.textContent = stats.pending ?? '-';
        const archives = stats.archive_status || {};
        elements.archive.textContent = [
            archiveLabel('zongbao', archives.zongbao),
            archiveLabel('wanbao', archives.wanbao)
        ].join('　');
        const archivedAt = archives[state.reportType];
        elements.warning.hidden = !archivedAt;
        elements.warning.textContent = archivedAt
            ? `${state.reportType === 'zongbao' ? '综报' : '晚报'}已经归档；继续标记仅作为记录，不会自动进入已归档成稿。`
            : '';
    }

    function moveItem(index, delta) {
        const target = index + delta;
        if (target < 0 || target >= state.items.length) return;
        const [item] = state.items.splice(index, 1);
        state.items.splice(target, 0, item);
        renderItems();
    }

    function cardTemplate(item, index) {
        const decisionOptions = [
            ['pending', '待处理'],
            ['selected', '采纳'],
            ['backup', '备选'],
            ['discarded', '放弃']
        ].map(([value, label]) => (
            `<option value="${value}" ${item.decision === value ? 'selected' : ''}>${label}</option>`
        )).join('');
        const reportOptions = [
            ['zongbao', '综报'],
            ['wanbao', '晚报']
        ].map(([value, label]) => (
            `<option value="${value}" ${(item.report_type || 'zongbao') === value ? 'selected' : ''}>${label}</option>`
        )).join('');
        const orderControls = ['selected', 'backup'].includes(state.decision)
            ? `<div class="duty-order-controls">
                    <button class="btn btn-secondary" data-move="-1" ${index === 0 ? 'disabled' : ''}>上移</button>
                    <button class="btn btn-secondary" data-move="1" ${index === state.items.length - 1 ? 'disabled' : ''}>下移</button>
               </div>`
            : '';
        return `
            <article class="duty-card" data-article-id="${escapeHtml(item.article_id)}" data-version="${item.version || 0}">
                <section>
                    <h2>${escapeHtml(item.title || '无标题')}</h2>
                    <div class="duty-card-meta">
                        <span>${escapeHtml(item.source || item.llm_source || '未知来源')}</span>
                        <span>${escapeHtml(formatDateTime(item.publish_time_iso))}</span>
                        <span>重要性 ${escapeHtml(item.external_importance_score ?? '-')}</span>
                    </div>
                    <p class="duty-card-summary">${escapeHtml(item.llm_summary || item.content_markdown || '暂无摘要')}</p>
                    ${item.url ? `<p><a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">查看原文</a></p>` : ''}
                </section>
                <section class="duty-card-editor">
                    <div class="duty-card-controls">
                        <label>处理状态<select data-field="decision">${decisionOptions}</select></label>
                        <label>报告去向<select data-field="report_type">${reportOptions}</select></label>
                        ${orderControls}
                    </div>
                    <label>摘取内容<textarea data-field="excerpt_text">${escapeHtml(item.excerpt_text || '')}</textarea></label>
                    <label>编辑摘要<textarea data-field="edited_summary">${escapeHtml(item.edited_summary || '')}</textarea></label>
                    <label>来源修订<input data-field="manual_llm_source" value="${escapeHtml(item.manual_llm_source || '')}"></label>
                    <label>备注<textarea data-field="notes">${escapeHtml(item.notes || '')}</textarea></label>
                    <button class="btn btn-primary" data-action="save">保存本条</button>
                </section>
            </article>`;
    }

    function clusterLabel(bucketKey) {
        const labels = {
            internal_positive: '北京正面',
            internal_negative: '北京负面',
            external_positive: '外埠正面',
            external_negative: '外埠负面'
        };
        return labels[bucketKey] || '相似新闻';
    }

    function pendingItemsTemplate() {
        const itemById = new Map(state.items.map(item => [item.article_id, item]));
        const renderedIds = new Set();
        const groups = state.clusters.map(cluster => {
            const items = (cluster.item_ids || [])
                .map(articleId => itemById.get(articleId))
                .filter(Boolean);
            if (!items.length) return '';
            items.forEach(item => renderedIds.add(item.article_id));
            return `
                <section class="duty-cluster" data-cluster-id="${escapeHtml(cluster.cluster_id)}">
                    <header>
                        <strong>${escapeHtml(clusterLabel(cluster.bucket_key))} · 相似新闻 ${items.length} 条</strong>
                        <span>来自全量聚类缓存，已按当前班次过滤</span>
                    </header>
                    <div class="duty-cluster-items">
                        ${items.map((item, index) => cardTemplate(item, index)).join('')}
                    </div>
                </section>`;
        }).join('');
        const unclustered = state.items.filter(item => !renderedIds.has(item.article_id));
        const remainder = unclustered.length
            ? `<section class="duty-cluster duty-cluster-single">
                    <header><strong>其他新闻 · ${unclustered.length} 条</strong></header>
                    <div class="duty-cluster-items">
                        ${unclustered.map((item, index) => cardTemplate(item, index)).join('')}
                    </div>
               </section>`
            : '';
        return groups || remainder ? groups + remainder : state.items.map(cardTemplate).join('');
    }

    function renderItems() {
        elements.saveOrder.hidden = !['selected', 'backup'].includes(state.decision);
        if (!state.items.length) {
            elements.list.innerHTML = '<div class="duty-empty">当前范围没有新闻。</div>';
            return;
        }
        elements.list.innerHTML = state.decision === 'pending'
            ? pendingItemsTemplate()
            : state.items.map(cardTemplate).join('');
        elements.list.querySelectorAll('[data-move]').forEach(button => {
            button.addEventListener('click', () => {
                const card = button.closest('.duty-card');
                const index = [...elements.list.querySelectorAll('.duty-card')].indexOf(card);
                moveItem(index, Number(button.dataset.move));
            });
        });
        elements.list.querySelectorAll('[data-action="save"]').forEach(button => {
            button.addEventListener('click', () => saveCard(button.closest('.duty-card')));
        });
    }

    async function loadWorkspace() {
        if (!state.shiftId) {
            state.items = [];
            state.clusters = [];
            renderItems();
            return;
        }
        elements.list.innerHTML = '<div class="duty-empty">正在加载…</div>';
        const base = `/api/duty/shifts/${encodeURIComponent(state.shiftId)}`;
        try {
            const clustersRequest = state.decision === 'pending'
                ? request(`${base}/clusters?report_type=${encodeURIComponent(state.reportType)}`)
                : Promise.resolve({ clusters: [] });
            const [stats, items, clusters] = await Promise.all([
                request(`${base}/stats`),
                request(`${base}/${state.decision === 'pending' ? 'candidates' : 'reviews'}?${new URLSearchParams({
                    ...(state.decision === 'pending' ? {} : { decision: state.decision }),
                    report_type: state.reportType,
                    limit: '200'
                })}`),
                clustersRequest
            ]);
            state.stats = stats;
            state.items = items.items || [];
            state.clusters = clusters.clusters || [];
            renderStats();
            renderItems();
        } catch (error) {
            elements.list.innerHTML = `<div class="duty-empty">${escapeHtml(error.message)}</div>`;
        }
    }

    async function saveCard(card) {
        const patch = { version: Number(card.dataset.version || 0) };
        card.querySelectorAll('[data-field]').forEach(input => {
            patch[input.dataset.field] = input.value.trim();
        });
        const button = card.querySelector('[data-action="save"]');
        button.disabled = true;
        try {
            const payload = await request(
                `/api/duty/shifts/${encodeURIComponent(state.shiftId)}/reviews/${encodeURIComponent(card.dataset.articleId)}`,
                {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(patch)
                }
            );
            card.dataset.version = payload.item.version;
            showToast('已保存');
            await loadWorkspace();
        } catch (error) {
            if (error.status === 409) {
                window.alert('该记录已被其他操作更新，页面将刷新。');
                await loadWorkspace();
            } else {
                window.alert(error.message);
            }
        } finally {
            button.disabled = false;
        }
    }

    async function saveOrder() {
        const ids = state.items.map(item => item.article_id);
        const body = {
            selected_order: state.decision === 'selected' ? ids : [],
            backup_order: state.decision === 'backup' ? ids : []
        };
        await request(`/api/duty/shifts/${encodeURIComponent(state.shiftId)}/order`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        showToast('排序已保存');
        await loadWorkspace();
    }

    async function openPreview() {
        const payload = await request(
            `/api/duty/shifts/${encodeURIComponent(state.shiftId)}/preview?report_type=${state.reportType}`
        );
        elements.previewText.value = payload.text || '';
        elements.previewDialog.showModal();
    }

    function setActiveButton(container, selector, value) {
        container.querySelectorAll('button').forEach(button => {
            button.classList.toggle('is-active', button.dataset[selector] === value);
        });
    }

    document.getElementById('duty-report-types').addEventListener('click', event => {
        const button = event.target.closest('[data-report-type]');
        if (!button) return;
        state.reportType = button.dataset.reportType;
        setActiveButton(event.currentTarget, 'reportType', state.reportType);
        loadWorkspace();
    });

    document.getElementById('duty-decisions').addEventListener('click', event => {
        const button = event.target.closest('[data-decision]');
        if (!button) return;
        state.decision = button.dataset.decision;
        setActiveButton(event.currentTarget, 'decision', state.decision);
        loadWorkspace();
    });

    elements.shiftSelect.addEventListener('change', () => {
        state.shiftId = elements.shiftSelect.value;
        renderShiftHeader();
        loadWorkspace();
    });
    document.getElementById('btn-duty-refresh').addEventListener('click', loadWorkspace);
    elements.saveOrder.addEventListener('click', saveOrder);
    document.getElementById('btn-duty-preview').addEventListener('click', openPreview);
    document.getElementById('btn-duty-close-preview').addEventListener('click', () => elements.previewDialog.close());
    document.getElementById('btn-duty-copy').addEventListener('click', async () => {
        await navigator.clipboard.writeText(elements.previewText.value);
        showToast('已复制');
    });
    document.getElementById('btn-duty-download').addEventListener('click', () => {
        const blob = new Blob([elements.previewText.value], { type: 'text/plain;charset=utf-8' });
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = `${state.reportType}-${state.shiftId}.txt`;
        link.click();
        URL.revokeObjectURL(link.href);
    });

    loadShifts().then(loadWorkspace).catch(error => {
        elements.list.innerHTML = `<div class="duty-empty">${escapeHtml(error.message)}</div>`;
    });
})();
