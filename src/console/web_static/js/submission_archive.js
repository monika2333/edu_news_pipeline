(() => {
    const body = document.body;
    const view = body.dataset.archiveView;
    const initialReportId = body.dataset.reportId || '';
    const isAdmin = body.dataset.userRole === 'admin';
    const typeLabels = { zongbao: '综报', wanbao: '晚报', feedback: '反馈' };
    const linkStatusMeta = {
        processing: { label: '正在判断中', className: 'is-processing' },
        exact: { label: '精确匹配', className: 'is-exact' },
        fuzzy: { label: '模糊匹配', className: 'is-fuzzy' },
        manual: { label: '人工确认', className: 'is-manual' },
        pending: { label: '待确认', className: 'is-pending' },
        unmatched: { label: '未覆盖', className: 'is-unmatched' },
        rejected: { label: '已否决', className: 'is-rejected' }
    };
    let parsedState = null;
    let previewItems = [];

    const escapeHtml = value => String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');

    const dateValue = value => String(value || '').slice(0, 10);
    const shortDate = value => {
        const text = dateValue(value);
        if (!text) return '';
        const [, month, day] = text.split('-');
        return `${Number(month)}月${Number(day)}日`;
    };
    const scoreValue = value => {
        const score = Number(value);
        return Number.isFinite(score) ? score.toFixed(2) : '-';
    };
    const typePill = type => `<span class="archive-type-pill is-${escapeHtml(type)}">${typeLabels[type] || escapeHtml(type)}</span>`;
    const linkPill = status => {
        const meta = linkStatusMeta[status] || { label: status || '未知', className: 'is-unmatched' };
        return `<span class="archive-link-pill ${meta.className}">${meta.label}</span>`;
    };
    const highlight = (text, query) => {
        const escaped = escapeHtml(text);
        if (!query) return escaped;
        const needle = escapeHtml(query).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        return escaped.replace(new RegExp(needle, 'gi'), match => `<mark>${match}</mark>`);
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

    function setNavPending(total) {
        const badge = document.getElementById('archive-nav-pending');
        if (!badge) return;
        const count = Number(total) || 0;
        badge.hidden = count <= 0;
        badge.textContent = count > 99 ? '99+' : String(count);
    }

    async function loadNavPending() {
        try {
            const data = await api('/link-queue?limit=1');
            setNavPending(data.total);
        } catch (error) {
            // 角标失败不影响主流程
        }
    }

    /* ---------- 存档库（列表 + 详情双栏） ---------- */

    const listState = { offset: 0, total: 0, loading: false, type: '' };
    let activeReportId = initialReportId;
    let reportRefreshTimer = null;

    function reportCard(report) {
        const linked = Number(report.exact_count || 0) + Number(report.fuzzy_count || 0)
            + Number(report.manual_count || 0);
        const processing = Number(report.processing_count || 0);
        const pending = Number(report.pending_count || 0);
        const unmatched = Number(report.unmatched_count || 0) + Number(report.rejected_count || 0);
        const total = linked + processing + pending + unmatched;
        const pct = value => total > 0 ? (value / total) * 100 : 0;
        const bar = total > 0 ? `
            <div class="archive-linkbar" aria-hidden="true">
                ${linked ? `<span class="seg-linked" style="width:${pct(linked)}%"></span>` : ''}
                ${processing ? `<span class="seg-processing" style="width:${pct(processing)}%"></span>` : ''}
                ${pending ? `<span class="seg-pending" style="width:${pct(pending)}%"></span>` : ''}
                ${unmatched ? `<span class="seg-unmatched" style="width:${pct(unmatched)}%"></span>` : ''}
            </div>` : '';
        return `
            <button class="archive-report-card${report.id === activeReportId ? ' is-active' : ''}"
                data-report-id="${report.id}" type="button">
                <div class="archive-report-card-top">
                    ${typePill(report.report_type)}
                    <span class="archive-report-card-date">${dateValue(report.report_date)}</span>
                </div>
                <div class="archive-report-card-issue">${escapeHtml(report.issue_no || '无期号')}</div>
                <div class="archive-report-card-stats">
                    ${bar}
                    <div class="archive-report-card-counts">
                        <span><strong>${report.item_count || 0}</strong> 条</span>
                        ${processing ? `<span>正在判断 ${processing}</span>` : ''}
                        ${pending ? `<span class="has-pending">待确认 ${pending}</span>` : ''}
                    </div>
                </div>
            </button>
        `;
    }

    function markActiveReport(id) {
        document.querySelectorAll('.archive-report-card').forEach(card => {
            card.classList.toggle('is-active', card.dataset.reportId === id);
        });
    }

    async function loadReportList(append = false) {
        const params = new URLSearchParams();
        const dateFrom = document.getElementById('archive-date-from').value;
        const dateTo = document.getElementById('archive-date-to').value;
        if (listState.type) params.set('report_type', listState.type);
        if (dateFrom) params.set('date_from', dateFrom);
        if (dateTo) params.set('date_to', dateTo);
        params.set('offset', String(append ? listState.offset : 0));
        params.set('limit', '30');
        const target = document.getElementById('archive-report-list');
        if (!append) {
            target.innerHTML = '<div class="archive-empty">正在加载…</div>';
            listState.offset = 0;
        }
        listState.loading = true;
        try {
            const data = await api(`/reports?${params.toString()}`);
            listState.total = data.total || 0;
            listState.offset = (append ? listState.offset : 0) + data.items.length;
            if (!append) {
                target.innerHTML = data.items.length
                    ? data.items.map(reportCard).join('')
                    : '<div class="archive-empty">还没有存档报告，点右上角「新增存档」录入第一份。</div>';
            } else {
                target.insertAdjacentHTML('beforeend', data.items.map(reportCard).join(''));
            }
            const footer = document.getElementById('archive-list-footer');
            const more = document.getElementById('archive-list-more');
            footer.hidden = listState.total === 0;
            document.getElementById('archive-list-total').textContent =
                `共 ${listState.total} 份 · 已显示 ${listState.offset} 份`;
            more.hidden = listState.offset >= listState.total;
            return data.items;
        } catch (error) {
            if (!append) {
                target.innerHTML = `<div class="archive-empty">${escapeHtml(error.message)}</div>`;
            } else {
                toast(error.message, 'error');
            }
            return [];
        } finally {
            listState.loading = false;
        }
    }

    function detailStats(items) {
        const stats = {
            processing: 0,
            exact: 0,
            fuzzy: 0,
            manual: 0,
            pending: 0,
            unmatched: 0,
            rejected: 0
        };
        items.forEach(item => {
            if (item.link_status in stats) stats[item.link_status] += 1;
        });
        const auto = stats.exact + stats.fuzzy + stats.manual;
        const uncovered = stats.unmatched + stats.rejected;
        return `
            <div class="archive-stat-chips">
                <span class="archive-stat-chip">共 <strong>${items.length}</strong> 条</span>
                ${stats.processing ? `<span class="archive-stat-chip is-processing">正在判断 <strong>${stats.processing}</strong></span>` : ''}
                <span class="archive-stat-chip is-linked">已回链 <strong>${auto}</strong></span>
                <span class="archive-stat-chip${stats.pending ? ' is-pending' : ''}">待确认 <strong>${stats.pending}</strong></span>
                <span class="archive-stat-chip">未覆盖 <strong>${uncovered}</strong></span>
            </div>
        `;
    }

    function detailItemCard(item) {
        const meta = [];
        meta.push(`来源：${escapeHtml(item.source || '-')}`);
        (item.urls || []).forEach(url => {
            meta.push(`<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>`);
        });
        const score = Number(item.link_combined_score);
        if (Number.isFinite(score) && item.link_status !== 'exact') {
            meta.push(`综合分 ${scoreValue(score)}`);
        }
        if (item.link_status === 'pending') {
            meta.push('<a href="/submission-archive/link-queue">去确认</a>');
        }
        return `
            <article class="archive-item${item.link_status === 'pending' ? ' is-pending' : ''}${item.link_status === 'processing' ? ' is-processing' : ''}">
                <div class="archive-item-head">
                    <span class="archive-item-order">${item.order_index + 1}</span>
                    <h4 class="archive-item-title">${escapeHtml(item.title)}</h4>
                    ${linkPill(item.link_status)}
                </div>
                ${item.body ? `<p class="archive-item-body">${escapeHtml(item.body)}</p>` : ''}
                <div class="archive-item-meta">${meta.map(part => `<span>${part}</span>`).join('')}</div>
            </article>
        `;
    }

    function detailItemsHtml(items) {
        const parts = [];
        let lastSection = null;
        items.forEach(item => {
            const section = item.section || '未分章节';
            if (section !== lastSection) {
                parts.push(`<h3 class="archive-section-heading">【${escapeHtml(section)}】</h3>`);
                lastSection = section;
            }
            parts.push(detailItemCard(item));
        });
        return parts.join('');
    }

    async function selectReport(id, pushUrl = true) {
        if (!id) return;
        if (reportRefreshTimer) {
            window.clearTimeout(reportRefreshTimer);
            reportRefreshTimer = null;
        }
        activeReportId = id;
        markActiveReport(id);
        if (pushUrl) {
            window.history.replaceState(null, '', `/submission-archive/${encodeURIComponent(id)}`);
        }
        const target = document.getElementById('archive-detail');
        target.innerHTML = '<div class="archive-empty">正在加载…</div>';
        try {
            const report = await api(`/reports/${encodeURIComponent(id)}`);
            const items = report.items || [];
            target.innerHTML = `
                <div class="archive-detail-head">
                    <div class="archive-detail-head-main">
                        <div class="archive-detail-title-row">
                            ${typePill(report.report_type)}
                            <h2>${dateValue(report.report_date)}</h2>
                        </div>
                        <p class="archive-detail-meta">
                            ${escapeHtml(report.issue_no || '无期号')} · 实际整理 ${dateValue(report.compiled_date)} · 录于 ${dateValue(report.imported_at)}
                        </p>
                        ${detailStats(items)}
                    </div>
                    ${isAdmin ? `
                        <div class="archive-detail-actions">
                            <button class="btn btn-secondary" id="archive-reparse" type="button">重新解析</button>
                            <button class="btn btn-danger" id="archive-delete" type="button">删除报告</button>
                        </div>
                    ` : ''}
                </div>
                <div class="archive-detail-items">
                    ${items.length ? detailItemsHtml(items) : '<div class="archive-empty">这份报告没有条目。</div>'}
                </div>
            `;
            document.getElementById('archive-reparse')?.addEventListener('click', async () => {
                if (!window.confirm('重新解析会删除旧条目，已人工确认的回链结果也会丢失。确定继续？')) return;
                try {
                    await api(`/reports/${encodeURIComponent(id)}/reparse`, { method: 'POST' });
                    toast('已重新解析，正在判断回链');
                    await selectReport(id, false);
                    loadReportList(false);
                } catch (error) {
                    toast(error.message, 'error');
                }
            });
            document.getElementById('archive-delete')?.addEventListener('click', async () => {
                if (!window.confirm('确定删除整份报告及其条目和查重结果吗？')) return;
                try {
                    await api(`/reports/${encodeURIComponent(id)}`, { method: 'DELETE' });
                    toast('报告已删除');
                    activeReportId = '';
                    window.history.replaceState(null, '', '/submission-archive');
                    target.innerHTML = '<div class="archive-empty">从左侧选择一份报告查看条目和回链情况。</div>';
                    loadReportList(false);
                } catch (error) {
                    toast(error.message, 'error');
                }
            });
            if (items.some(item => item.link_status === 'processing')) {
                reportRefreshTimer = window.setTimeout(async () => {
                    if (activeReportId !== id) return;
                    await selectReport(id, false);
                    loadReportList(false);
                    loadNavPending();
                }, 1500);
            }
        } catch (error) {
            target.innerHTML = `<div class="archive-empty">${escapeHtml(error.message)}</div>`;
        }
    }

    async function initBrowserView() {
        document.getElementById('archive-type-filter').addEventListener('click', event => {
            const option = event.target.closest('.archive-type-option');
            if (!option) return;
            document.querySelectorAll('.archive-type-option').forEach(node => {
                node.classList.toggle('active', node === option);
            });
            listState.type = option.dataset.type || '';
            loadReportList(false);
        });
        const onDateChange = () => loadReportList(false);
        document.getElementById('archive-date-from').addEventListener('change', onDateChange);
        document.getElementById('archive-date-to').addEventListener('change', onDateChange);
        document.getElementById('archive-list-more').addEventListener('click', () => {
            if (!listState.loading) loadReportList(true);
        });
        document.getElementById('archive-report-list').addEventListener('click', event => {
            const card = event.target.closest('.archive-report-card');
            if (card) selectReport(card.dataset.reportId);
        });
        const items = await loadReportList(false);
        if (initialReportId) {
            if (items.some(item => item.id === initialReportId)) {
                await selectReport(initialReportId, false);
            } else {
                // 详情页直达但报告不在当前筛选结果里：仍然加载详情
                await selectReport(initialReportId, false);
            }
        }
        const notice = sessionStorage.getItem('archiveNotice');
        if (notice) {
            sessionStorage.removeItem('archiveNotice');
            toast(notice);
        }
    }

    /* ---------- 新增存档（粘贴 → 核对保存） ---------- */

    function blankPreviewItem() {
        return { section: '', marker: '', title: '', body: '', source: '', urls: [], editing: true };
    }

    function itemCardView(item, index) {
        const meta = [];
        if (item.marker) meta.push(`标记 ${escapeHtml(item.marker)}`);
        meta.push(`来源：${escapeHtml(item.source || '未抽取')}`);
        if (item.urls?.length) meta.push(`${item.urls.length} 个链接`);
        return `
            <article class="archive-item" data-index="${index}">
                <div class="archive-item-head">
                    <span class="archive-item-order">${index + 1}</span>
                    <h4 class="archive-item-title">${escapeHtml(item.title || '（无标题）')}</h4>
                    <div class="archive-item-tools">
                        <button class="archive-btn-text" data-action="edit" type="button">编辑</button>
                        <button class="archive-btn-text is-danger" data-action="remove" type="button">删除</button>
                    </div>
                </div>
                ${item.body ? `<p class="archive-item-body">${escapeHtml(item.body)}</p>` : ''}
                <div class="archive-item-meta">${meta.map(part => `<span>${part}</span>`).join('')}</div>
            </article>
        `;
    }

    function itemCardEdit(item, index) {
        const urls = (item.urls || []).join('\n');
        return `
            <article class="archive-item-editor" data-index="${index}">
                <div class="archive-item-editor-grid">
                    <label class="archive-field is-wide">
                        <span>标题</span>
                        <input data-field="title" value="${escapeHtml(item.title)}">
                    </label>
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
                    <label class="archive-field is-wide">
                        <span>正文</span>
                        <textarea data-field="body" rows="4">${escapeHtml(item.body || '')}</textarea>
                    </label>
                    <label class="archive-field is-wide">
                        <span>URL（每行一个）</span>
                        <textarea data-field="urls" rows="2">${escapeHtml(urls)}</textarea>
                    </label>
                </div>
                <div class="archive-item-editor-actions">
                    <button class="archive-btn-text is-danger" data-action="remove" type="button">删除</button>
                    <button class="archive-btn-text" data-action="done" type="button">完成</button>
                </div>
            </article>
        `;
    }

    function renderPreviewItems() {
        const target = document.getElementById('archive-preview-items');
        const parts = [];
        let lastSection = null;
        let visibleIndex = 0;
        previewItems.forEach((item, index) => {
            const section = item.section || '未分章节';
            if (section !== lastSection) {
                parts.push(`<h3 class="archive-section-heading">【${escapeHtml(section)}】</h3>`);
                lastSection = section;
            }
            parts.push(item.editing ? itemCardEdit(item, index) : itemCardView(item, index));
            visibleIndex += 1;
        });
        target.innerHTML = parts.length
            ? parts.join('')
            : '<div class="archive-empty">还没有条目，点击下方「手工新增一条」。</div>';
        const count = document.getElementById('archive-save-count');
        if (count) count.textContent = `共 ${previewItems.length} 条`;
    }

    function bindPreviewItems() {
        const target = document.getElementById('archive-preview-items');
        target.addEventListener('click', event => {
            const button = event.target.closest('[data-action]');
            if (!button) return;
            const card = button.closest('[data-index]');
            const index = Number(card.dataset.index);
            const action = button.dataset.action;
            if (action === 'edit') {
                previewItems[index].editing = true;
                renderPreviewItems();
            } else if (action === 'done') {
                previewItems[index].editing = false;
                renderPreviewItems();
            } else if (action === 'remove') {
                previewItems.splice(index, 1);
                renderPreviewItems();
            }
        });
        target.addEventListener('input', event => {
            const field = event.target.dataset.field;
            if (!field) return;
            const card = event.target.closest('[data-index]');
            const index = Number(card.dataset.index);
            const value = event.target.value;
            if (field === 'urls') {
                previewItems[index].urls = value
                    .split(/\r?\n/)
                    .map(part => part.trim())
                    .filter(Boolean);
            } else {
                previewItems[index][field] = value;
            }
        });
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
        const button = document.getElementById('archive-parse');
        button.disabled = true;
        button.textContent = '解析中…';
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
            previewItems = parsedState.items.map(item => ({
                section: item.section || '',
                marker: item.marker || '',
                title: item.title || '',
                body: item.body || '',
                source: item.source || '',
                urls: Array.isArray(item.urls) ? item.urls : [],
                editing: false
            }));
            renderPreviewItems();
            document.getElementById('archive-preview').hidden = false;
            renderWarnings(parsedState.warnings);
            document.getElementById('archive-preview').scrollIntoView({ behavior: 'smooth' });
        } catch (error) {
            toast(error.message, 'error');
        } finally {
            button.disabled = false;
            button.textContent = '解析';
        }
    }

    async function saveReport(overwrite = false) {
        const items = previewItems.map((item, index) => ({
            section: (item.section || '').trim() || null,
            marker: (item.marker || '').trim() || null,
            order_index: index,
            title: (item.title || '').trim(),
            body: (item.body || '').trim(),
            source: (item.source || '').trim() || null,
            urls: item.urls || []
        }));
        if (!items.length) {
            toast('报告至少需要一个条目', 'error');
            return;
        }
        const missing = items.findIndex(item => !item.title);
        if (missing >= 0) {
            toast(`第 ${missing + 1} 条还没有标题`, 'error');
            previewItems[missing].editing = true;
            renderPreviewItems();
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
        const button = document.getElementById('archive-save');
        button.disabled = true;
        button.textContent = '保存中…';
        try {
            const result = await api('/reports', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            sessionStorage.setItem(
                'archiveNotice',
                '保存成功，系统正在后台判断回链'
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
        } finally {
            button.disabled = false;
            button.textContent = '确认保存';
        }
    }

    /* ---------- 回链确认队列 ---------- */

    function linkCard(item) {
        const combined = Number(item.link_combined_score);
        const percent = Number.isFinite(combined) ? Math.round(combined * 100) : 0;
        return `
            <article class="archive-link-card" data-item-id="${item.id}">
                <div class="archive-link-grid">
                    <section class="archive-link-col">
                        <p class="archive-link-col-label">
                            存档条目 ${typePill(item.report_type)} <span>${shortDate(item.report_date)}</span>
                        </p>
                        <h3>${escapeHtml(item.title)}</h3>
                        <p class="archive-link-body">${escapeHtml(item.body || '')}</p>
                        <footer><span>来源：${escapeHtml(item.source || '-')}</span></footer>
                    </section>
                    <section class="archive-link-col is-candidate">
                        <p class="archive-link-col-label">系统最佳候选</p>
                        <h3>${escapeHtml(item.candidate_title || '候选已不存在')}</h3>
                        <p class="archive-link-body">${escapeHtml(item.candidate_body || '')}</p>
                        <footer>
                            <span>来源：${escapeHtml(item.candidate_source || '-')}</span>
                            ${item.candidate_url ? `<a href="${escapeHtml(item.candidate_url)}" target="_blank" rel="noopener noreferrer">打开原文</a>` : ''}
                        </footer>
                    </section>
                </div>
                <div class="archive-link-score">
                    <div class="archive-score-bar" role="img" aria-label="综合相似度 ${scoreValue(item.link_combined_score)}">
                        <span style="width: ${percent}%"></span>
                    </div>
                    <div class="archive-score-nums">
                        <span>综合 <strong>${scoreValue(item.link_combined_score)}</strong></span>
                        <span>标题 <strong>${scoreValue(item.link_title_score)}</strong></span>
                        <span>正文 <strong>${scoreValue(item.link_body_score)}</strong></span>
                    </div>
                </div>
                <div class="archive-link-actions">
                    <button class="btn btn-secondary archive-link-reject" type="button">不是同一条</button>
                    <button class="btn btn-primary archive-link-accept" type="button">确认绑定</button>
                </div>
            </article>
        `;
    }

    async function loadLinkQueue() {
        const target = document.getElementById('archive-link-queue');
        const countTarget = document.getElementById('archive-queue-count');
        target.innerHTML = '<div class="archive-empty">正在加载…</div>';
        try {
            const data = await api('/link-queue?limit=100');
            let remaining = Number(data.total) || 0;
            const renderCount = () => {
                countTarget.textContent = remaining > 0 ? `剩余 ${remaining} 条` : '';
                setNavPending(remaining);
            };
            renderCount();
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
                    remaining = Math.max(remaining - 1, 0);
                    renderCount();
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

    /* ---------- 全库搜索 ---------- */

    async function searchArchive(event) {
        event?.preventDefault();
        const query = document.getElementById('archive-search-query').value.trim();
        const target = document.getElementById('archive-search-results');
        if (!query) {
            target.innerHTML = '<div class="archive-search-hint">输入关键词开始搜索全部存档条目。</div>';
            return;
        }
        target.innerHTML = '<div class="archive-empty">正在搜索…</div>';
        try {
            const data = await api(`/search?q=${encodeURIComponent(query)}&limit=50`);
            target.innerHTML = data.items.length ? data.items.map(item => `
                <article class="archive-search-item">
                    <div class="archive-search-item-head">
                        ${typePill(item.report_type)}
                        <span class="archive-issue">${dateValue(item.report_date)}</span>
                        ${item.section ? `<span class="archive-issue">【${escapeHtml(item.section)}】</span>` : ''}
                    </div>
                    <h3>${highlight(item.title, query)}</h3>
                    ${item.body ? `<p>${highlight(item.body, query)}</p>` : ''}
                    <footer>
                        <span>来源：${escapeHtml(item.source || '-')}</span>
                        <a href="/submission-archive/${item.report_id}">查看整份报告</a>
                    </footer>
                </article>
            `).join('') : '<div class="archive-empty">没有匹配结果</div>';
        } catch (error) {
            target.innerHTML = `<div class="archive-empty">${escapeHtml(error.message)}</div>`;
        }
    }

    /* ---------- 初始化 ---------- */

    document.addEventListener('DOMContentLoaded', () => {
        loadNavPending();
        if (view === 'list' || view === 'detail') {
            initBrowserView();
        } else if (view === 'new') {
            document.getElementById('archive-parse').addEventListener('click', parsePastedReport);
            document.getElementById('archive-report-type').addEventListener('change', () => {
                renderWarnings(parsedState?.warnings || []);
            });
            document.getElementById('archive-add-item').addEventListener('click', () => {
                previewItems.push(blankPreviewItem());
                renderPreviewItems();
                const cards = document.querySelectorAll('#archive-preview-items [data-index]');
                cards[cards.length - 1]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
            });
            document.getElementById('archive-save').addEventListener('click', () => saveReport(false));
            bindPreviewItems();
        } else if (view === 'link-queue') {
            loadLinkQueue();
        } else if (view === 'search') {
            document.getElementById('archive-search-form').addEventListener('submit', searchArchive);
            document.getElementById('archive-search-results').innerHTML =
                '<div class="archive-search-hint">输入关键词开始搜索全部存档条目。</div>';
        }
    });
})();
