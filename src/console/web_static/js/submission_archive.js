(() => {
    const body = document.body;
    const view = body.dataset.archiveView;
    const reportId = body.dataset.reportId;
    const isAdmin = body.dataset.userRole === 'admin';
    const typeLabels = {
        zongbao: '综报',
        wanbao: '晚报',
        feedback: '反馈'
    };
    let parsedState = null;

    const escapeHtml = value => String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');

    const dateValue = value => String(value || '').slice(0, 10);
    const scoreValue = value => {
        const score = Number(value);
        return Number.isFinite(score) ? score.toFixed(2) : '-';
    };

    function toast(message, type = 'success') {
        const target = document.getElementById('archive-toast');
        if (!target) return;
        target.textContent = message;
        target.className = `toast show ${type}`;
        window.setTimeout(() => {
            target.classList.remove('show');
            target.textContent = '';
        }, 3500);
    }

    async function api(path, options = {}) {
        const response = await window.fetch(`/api/submission-archive${path}`, options);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(
                typeof payload.detail === 'string'
                    ? payload.detail
                    : payload.detail?.message || '操作失败'
            );
            error.status = response.status;
            error.payload = payload;
            throw error;
        }
        return payload;
    }

    function itemEditor(item = {}) {
        const urls = Array.isArray(item.urls) ? item.urls.join('\n') : '';
        return `
            <article class="archive-item-editor">
                <div class="archive-item-editor-grid">
                    <label class="archive-field">
                        <span>章节</span>
                        <input data-field="section" value="${escapeHtml(item.section || '')}">
                    </label>
                    <label class="archive-field">
                        <span>标记</span>
                        <input data-field="marker" value="${escapeHtml(item.marker || '')}">
                    </label>
                    <label class="archive-field">
                        <span>来源</span>
                        <input data-field="source" value="${escapeHtml(item.source || '')}">
                    </label>
                    <label class="archive-field archive-item-body">
                        <span>标题</span>
                        <input data-field="title" value="${escapeHtml(item.title || '')}">
                    </label>
                    <label class="archive-field archive-item-body">
                        <span>正文</span>
                        <textarea data-field="body">${escapeHtml(item.body || '')}</textarea>
                    </label>
                    <label class="archive-field archive-item-body">
                        <span>URL（每行一个）</span>
                        <textarea data-field="urls">${escapeHtml(urls)}</textarea>
                    </label>
                </div>
                <div class="archive-item-editor-actions">
                    <button class="btn btn-secondary archive-remove-item" type="button">删除此条</button>
                </div>
            </article>
        `;
    }

    function collectPreviewItems() {
        return [...document.querySelectorAll('.archive-item-editor')].map((card, index) => ({
            section: card.querySelector('[data-field="section"]').value.trim() || null,
            marker: card.querySelector('[data-field="marker"]').value.trim() || null,
            order_index: index,
            title: card.querySelector('[data-field="title"]').value.trim(),
            body: card.querySelector('[data-field="body"]').value.trim(),
            source: card.querySelector('[data-field="source"]').value.trim() || null,
            urls: card.querySelector('[data-field="urls"]').value
                .split(/\r?\n/)
                .map(value => value.trim())
                .filter(Boolean)
        }));
    }

    function renderWarnings(warnings) {
        const target = document.getElementById('archive-warnings');
        const selectedType = document.getElementById('archive-report-type')?.value;
        const messages = [...(warnings || [])];
        if (parsedState && selectedType !== parsedState.detected_report_type) {
            messages.unshift(
                `当前选择“${typeLabels[selectedType]}”，文本格式更像“${typeLabels[parsedState.detected_report_type]}”；仍可继续保存。`
            );
        }
        target.hidden = !messages.length;
        target.innerHTML = messages.map(message => `<div>${escapeHtml(message)}</div>`).join('');
    }

    async function parsePastedReport() {
        const pastedText = document.getElementById('archive-pasted-text').value.trim();
        if (!pastedText) {
            toast('请先粘贴报告全文', 'error');
            return;
        }
        try {
            parsedState = await api('/parse', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pasted_text: pastedText })
            });
            document.getElementById('archive-report-type').value = parsedState.detected_report_type;
            document.getElementById('archive-report-date').value = dateValue(parsedState.report_date);
            document.getElementById('archive-compiled-date').value = dateValue(parsedState.compiled_date);
            document.getElementById('archive-issue-no').value = parsedState.issue_no || '';
            document.getElementById('archive-preview-items').innerHTML =
                parsedState.items.map(itemEditor).join('');
            document.getElementById('archive-preview').hidden = false;
            renderWarnings(parsedState.warnings);
            document.getElementById('archive-preview').scrollIntoView({ behavior: 'smooth' });
        } catch (error) {
            toast(error.message, 'error');
        }
    }

    async function saveReport(overwrite = false) {
        const items = collectPreviewItems();
        if (!items.length || items.some(item => !item.title)) {
            toast('每条存档都必须有标题', 'error');
            return;
        }
        const payload = {
            report_type: document.getElementById('archive-report-type').value,
            report_date: document.getElementById('archive-report-date').value,
            compiled_date: document.getElementById('archive-compiled-date').value,
            issue_no: document.getElementById('archive-issue-no').value.trim() || null,
            title_line: parsedState.title_line,
            pasted_text: document.getElementById('archive-pasted-text').value,
            items,
            overwrite
        };
        try {
            const result = await api('/reports', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const summary = result.link_summary || {};
            sessionStorage.setItem(
                'archiveNotice',
                `保存成功：精确 ${summary.exact || 0}，模糊 ${summary.fuzzy || 0}，待确认 ${summary.pending || 0}，未覆盖 ${summary.unmatched || 0}`
            );
            window.location.assign(`/submission-archive/${result.report.id}`);
        } catch (error) {
            if (error.status === 409 && !overwrite) {
                const confirmed = window.confirm(
                    '同一类型和日期的报告已存在。覆盖会删除旧条目及其人工确认结果，是否继续？'
                );
                if (confirmed) await saveReport(true);
                return;
            }
            toast(error.message, 'error');
        }
    }

    async function loadReportList() {
        const params = new URLSearchParams();
        const type = document.getElementById('archive-filter-type').value;
        const dateFrom = document.getElementById('archive-date-from').value;
        const dateTo = document.getElementById('archive-date-to').value;
        if (type) params.set('report_type', type);
        if (dateFrom) params.set('date_from', dateFrom);
        if (dateTo) params.set('date_to', dateTo);
        const target = document.getElementById('archive-report-list');
        target.innerHTML = '<tr><td colspan="6">正在加载…</td></tr>';
        try {
            const data = await api(`/reports?${params.toString()}`);
            target.innerHTML = data.items.length ? data.items.map(report => `
                <tr>
                    <td><a href="/submission-archive/${report.id}">${dateValue(report.report_date)}</a></td>
                    <td>${typeLabels[report.report_type] || report.report_type}<br>
                        <small>${escapeHtml(report.issue_no || '无期号')}</small></td>
                    <td>${report.item_count || 0}</td>
                    <td>${Number(report.exact_count || 0) + Number(report.fuzzy_count || 0)}</td>
                    <td>${report.pending_count || 0}</td>
                    <td>${report.unmatched_count || 0}</td>
                </tr>
            `).join('') : '<tr><td colspan="6" class="archive-empty">暂无存档</td></tr>';
        } catch (error) {
            target.innerHTML = `<tr><td colspan="6">${escapeHtml(error.message)}</td></tr>`;
        }
    }

    function reportItemHtml(item) {
        const urls = (item.urls || []).map(url =>
            `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>`
        ).join('<br>');
        return `
            <article class="archive-item">
                <strong>${item.order_index + 1}. ${escapeHtml(item.title)}</strong>
                <div class="archive-score-row">
                    <span>${escapeHtml(item.section || '无章节')} · ${escapeHtml(item.marker || '无标记')}</span>
                    <span>回链：${escapeHtml(item.link_status)}</span>
                    <span>综合分：${scoreValue(item.link_combined_score)}</span>
                </div>
                <p>${escapeHtml(item.body || '')}</p>
                <small>来源：${escapeHtml(item.source || '-')}</small>
                ${urls ? `<div>${urls}</div>` : ''}
            </article>
        `;
    }

    async function loadReportDetail() {
        const target = document.getElementById('archive-detail');
        target.innerHTML = '<div class="archive-empty">正在加载…</div>';
        try {
            const report = await api(`/reports/${encodeURIComponent(reportId)}`);
            target.innerHTML = `
                <div class="archive-detail-actions">
                    <div>
                        <h2>${dateValue(report.report_date)} ${typeLabels[report.report_type] || report.report_type}</h2>
                        <p>${escapeHtml(report.issue_no || '无期号')} · 实际整理日期 ${dateValue(report.compiled_date)}</p>
                    </div>
                    ${isAdmin ? `
                        <div class="archive-actions">
                            <button class="btn btn-secondary" id="archive-reparse" type="button">重新解析</button>
                            <button class="btn btn-primary" id="archive-delete" type="button">删除报告</button>
                        </div>
                    ` : ''}
                </div>
                <div>${(report.items || []).map(reportItemHtml).join('')}</div>
            `;
            document.getElementById('archive-reparse')?.addEventListener('click', async () => {
                if (!window.confirm('重新解析会删除旧条目，已人工确认的回链结果也会丢失。确定继续？')) return;
                try {
                    await api(`/reports/${encodeURIComponent(reportId)}/reparse`, { method: 'POST' });
                    toast('已重新解析并回链');
                    await loadReportDetail();
                } catch (error) {
                    toast(error.message, 'error');
                }
            });
            document.getElementById('archive-delete')?.addEventListener('click', async () => {
                if (!window.confirm('确定删除整份报告及其条目和查重结果吗？')) return;
                try {
                    await api(`/reports/${encodeURIComponent(reportId)}`, { method: 'DELETE' });
                    window.location.assign('/submission-archive');
                } catch (error) {
                    toast(error.message, 'error');
                }
            });
            const notice = sessionStorage.getItem('archiveNotice');
            if (notice) {
                sessionStorage.removeItem('archiveNotice');
                toast(notice);
            }
        } catch (error) {
            target.innerHTML = `<div class="archive-empty">${escapeHtml(error.message)}</div>`;
        }
    }

    function linkCard(item) {
        return `
            <article class="archive-link-card" data-item-id="${item.id}">
                <div class="archive-link-columns">
                    <div class="archive-link-column">
                        <small>${dateValue(item.report_date)} ${typeLabels[item.report_type] || item.report_type}</small>
                        <h3>${escapeHtml(item.title)}</h3>
                        <p>${escapeHtml(item.body || '')}</p>
                    </div>
                    <div class="archive-link-column">
                        <small>最佳候选</small>
                        <h3>${escapeHtml(item.candidate_title || '候选已不存在')}</h3>
                        <p>${escapeHtml(item.candidate_body || '')}</p>
                        <small>来源：${escapeHtml(item.candidate_source || '-')}</small>
                        ${item.candidate_url ? `<a href="${escapeHtml(item.candidate_url)}" target="_blank" rel="noopener noreferrer">打开原文</a>` : ''}
                    </div>
                </div>
                <div class="archive-score-row">
                    <span>标题 ${scoreValue(item.link_title_score)}</span>
                    <span>正文 ${scoreValue(item.link_body_score)}</span>
                    <span>综合 ${scoreValue(item.link_combined_score)}</span>
                </div>
                <div class="archive-actions">
                    <button class="btn btn-primary archive-link-accept" type="button">确认绑定</button>
                    <button class="btn btn-secondary archive-link-reject" type="button">不是同一条</button>
                </div>
            </article>
        `;
    }

    async function loadLinkQueue() {
        const target = document.getElementById('archive-link-queue');
        target.innerHTML = '<div class="archive-empty">正在加载…</div>';
        try {
            const data = await api('/link-queue?limit=100');
            target.innerHTML = data.items.length
                ? data.items.map(linkCard).join('')
                : '<div class="archive-empty">当前没有待确认条目</div>';
            target.addEventListener('click', async event => {
                const button = event.target.closest('.archive-link-accept, .archive-link-reject');
                if (!button) return;
                const card = button.closest('.archive-link-card');
                button.disabled = true;
                try {
                    await api(`/items/${encodeURIComponent(card.dataset.itemId)}/link-decision`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ accepted: button.classList.contains('archive-link-accept') })
                    });
                    card.remove();
                    if (!target.querySelector('.archive-link-card')) {
                        target.innerHTML = '<div class="archive-empty">当前没有待确认条目</div>';
                    }
                } catch (error) {
                    button.disabled = false;
                    toast(error.message, 'error');
                }
            });
        } catch (error) {
            target.innerHTML = `<div class="archive-empty">${escapeHtml(error.message)}</div>`;
        }
    }

    async function searchArchive(event) {
        event?.preventDefault();
        const query = document.getElementById('archive-search-query').value.trim();
        const target = document.getElementById('archive-search-results');
        if (!query) {
            target.innerHTML = '<div class="archive-empty">请输入关键词</div>';
            return;
        }
        target.innerHTML = '<div class="archive-empty">正在搜索…</div>';
        try {
            const data = await api(`/search?q=${encodeURIComponent(query)}&limit=50`);
            target.innerHTML = data.items.length ? data.items.map(item => `
                <article class="archive-search-item">
                    <small>${dateValue(item.report_date)} ${typeLabels[item.report_type] || item.report_type}</small>
                    <h3>${escapeHtml(item.title)}</h3>
                    <p>${escapeHtml(item.body || '')}</p>
                    <span>来源：${escapeHtml(item.source || '-')}</span>
                </article>
            `).join('') : '<div class="archive-empty">没有匹配结果</div>';
        } catch (error) {
            target.innerHTML = `<div class="archive-empty">${escapeHtml(error.message)}</div>`;
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        if (view === 'list') {
            document.getElementById('archive-list-filters').addEventListener('submit', event => {
                event.preventDefault();
                loadReportList();
            });
            loadReportList();
        } else if (view === 'new') {
            document.getElementById('archive-parse').addEventListener('click', parsePastedReport);
            document.getElementById('archive-report-type').addEventListener('change', () => {
                renderWarnings(parsedState?.warnings || []);
            });
            document.getElementById('archive-add-item').addEventListener('click', () => {
                document.getElementById('archive-preview-items').insertAdjacentHTML('beforeend', itemEditor());
            });
            document.getElementById('archive-preview-items').addEventListener('click', event => {
                event.target.closest('.archive-remove-item')?.closest('.archive-item-editor')?.remove();
            });
            document.getElementById('archive-save').addEventListener('click', () => saveReport(false));
        } else if (view === 'detail') {
            loadReportDetail();
        } else if (view === 'link-queue') {
            loadLinkQueue();
        } else if (view === 'search') {
            document.getElementById('archive-search-form').addEventListener('submit', searchArchive);
        }
    });
})();
