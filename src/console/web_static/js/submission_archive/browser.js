// Submission Archive JS - Browser
/* ---------- 存档库（列表 + 详情双栏） ---------- */

const listState = { offset: 0, total: 0, loading: false, type: '' };
let activeReportId = initialReportId;
let reportRefreshTimer = null;
let activeReportStatusSignature = '';
// 详情区按回链状态筛选：'' 表示不过滤，其余取值 linked/pending/uncovered
let detailStatusFilter = '';

function linkStatusGroup(status) {
    if (status === 'matched') return 'linked';
    if (status === 'pending') return 'pending';
    if (status === 'unmatched' || status === 'rejected') return 'uncovered';
    return 'processing';
}

function reportCardStatsHtml(report) {
    const linked = Number(report.matched_count || 0);
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
        ${bar}
        <div class="archive-report-card-counts">
            <span><strong>${report.item_count || 0}</strong> 条</span>
            ${processing ? `<span>正在判断 ${processing}</span>` : ''}
            ${pending ? `<span class="has-pending">待确认 ${pending}</span>` : ''}
        </div>
    `;
}

function reportCard(report) {
    return `
        <button class="archive-report-card${report.id === activeReportId ? ' is-active' : ''}"
            data-report-id="${report.id}" type="button">
            <div class="archive-report-card-top">
                ${typePill(report.report_type)}
                <span class="archive-report-card-date">${dateValue(report.report_date)}</span>
            </div>
            <div class="archive-report-card-issue">${escapeHtml(report.issue_no || '无期号')}</div>
            <div class="archive-report-card-stats">
                ${reportCardStatsHtml(report)}
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
        matched: 0,
        pending: 0,
        unmatched: 0,
        rejected: 0
    };
    items.forEach(item => {
        if (item.link_status in stats) stats[item.link_status] += 1;
    });
    const matched = stats.matched;
    const uncovered = stats.unmatched + stats.rejected;
    // 已匹配/待确认/未覆盖渲染为可点击按钮，点击后仅展示该分类条目，再次点击取消筛选
    const filterChip = (filter, label, count, extraClass = '') => {
        const active = detailStatusFilter === filter;
        return `<button class="archive-stat-chip${extraClass}${active ? ' is-active' : ''}" type="button"`
            + ` data-status-filter="${filter}" aria-pressed="${active}">${label} <strong>${count}</strong></button>`;
    };
    return `
        <div class="archive-stat-chips" id="archive-detail-stats">
            <span class="archive-stat-chip">共 <strong>${items.length}</strong> 条</span>
            ${stats.processing ? `<span class="archive-stat-chip is-processing">正在判断 <strong>${stats.processing}</strong></span>` : ''}
            ${filterChip('linked', '已匹配', matched, ' is-linked')}
            ${filterChip('pending', '待确认', stats.pending, stats.pending ? ' is-pending' : '')}
            ${filterChip('uncovered', '未覆盖', uncovered)}
        </div>
    `;
}

function detailItemMetaHtml(item) {
    const meta = [];
    meta.push(`来源：${escapeHtml(item.source || '-')}`);
    (item.urls || []).forEach(url => {
        meta.push(`<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>`);
    });
    const score = Number(item.link_combined_score);
    if (Number.isFinite(score) && item.link_status !== 'matched') {
        meta.push(`综合分 ${scoreValue(score)}`);
    }
    if (item.link_status === 'pending') {
        meta.push('<a href="/submission-archive/link-queue">去确认</a>');
    }
    return meta.map(part => `<span>${part}</span>`).join('');
}

function detailItemCard(item) {
    return `
        <article class="archive-item${item.link_status === 'pending' ? ' is-pending' : ''}${item.link_status === 'processing' ? ' is-processing' : ''}"
            data-item-id="${escapeHtml(item.id)}" data-link-group="${linkStatusGroup(item.link_status)}">
            <div class="archive-item-head">
                <span class="archive-item-order">${item.order_index + 1}</span>
                <h4 class="archive-item-title">${escapeHtml(item.title)}</h4>
                ${linkPill(item.link_status, item.article_id)}
            </div>
            ${item.body ? `<p class="archive-item-body">${escapeHtml(item.body)}</p>` : ''}
            <div class="archive-item-meta">${detailItemMetaHtml(item)}</div>
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

// 按 detailStatusFilter 隐藏不匹配的条目卡片；所在章节全部不可见时一并隐藏章节标题
function applyDetailFilter() {
    const container = document.querySelector('#archive-detail .archive-detail-items');
    if (!container) return;
    const filtering = Boolean(detailStatusFilter);
    container.querySelectorAll('.archive-item[data-item-id]').forEach(card => {
        card.hidden = filtering && card.dataset.linkGroup !== detailStatusFilter;
    });
    let heading = null;
    let anyVisible = false;
    const flushHeading = () => {
        if (heading) heading.hidden = filtering && !anyVisible;
    };
    container.childNodes.forEach(node => {
        if (!(node instanceof HTMLElement)) return;
        if (node.classList.contains('archive-section-heading')) {
            flushHeading();
            heading = node;
            anyVisible = false;
        } else if (node.classList.contains('archive-item') && !node.hidden) {
            anyVisible = true;
        }
    });
    flushHeading();
}

function reportStatusSignature(items) {
    return items.map(item => [
        item.id,
        item.link_status,
        item.link_combined_score ?? '',
        item.article_id ?? '',
        item.best_candidate_article_id ?? ''
    ].join(':')).join('|');
}

function reportCountsFromItems(items) {
    const counts = {
        item_count: items.length,
        processing_count: 0,
        matched_count: 0,
        pending_count: 0,
        unmatched_count: 0
    };
    items.forEach(item => {
        const key = `${item.link_status}_count`;
        if (key in counts) {
            counts[key] += 1;
        } else if (item.link_status === 'rejected') {
            counts.unmatched_count += 1;
        }
    });
    return counts;
}

function updateReportStatusComponents(id, items) {
    const statsTarget = document.getElementById('archive-detail-stats');
    if (statsTarget) {
        statsTarget.outerHTML = detailStats(items);
    }
    const itemsById = new Map(items.map(item => [String(item.id), item]));
    document.querySelectorAll('#archive-detail .archive-item[data-item-id]').forEach(card => {
        const item = itemsById.get(card.dataset.itemId);
        if (!item) return;
        card.classList.toggle('is-processing', item.link_status === 'processing');
        card.classList.toggle('is-pending', item.link_status === 'pending');
        card.dataset.linkGroup = linkStatusGroup(item.link_status);
        const pill = card.querySelector('.archive-link-pill');
        if (pill) pill.outerHTML = linkPill(item.link_status, item.article_id);
        const meta = card.querySelector('.archive-item-meta');
        if (meta) meta.innerHTML = detailItemMetaHtml(item);
    });
    applyDetailFilter();
    const activeCard = Array.from(
        document.querySelectorAll('.archive-report-card')
    ).find(card => card.dataset.reportId === id);
    const cardStats = activeCard?.querySelector('.archive-report-card-stats');
    if (cardStats) {
        cardStats.innerHTML = reportCardStatsHtml(
            reportCountsFromItems(items)
        );
    }
}

function scheduleReportStatusPoll(id, delay = 1500) {
    reportRefreshTimer = window.setTimeout(
        () => pollReportStatus(id),
        delay
    );
}

async function pollReportStatus(id) {
    reportRefreshTimer = null;
    if (activeReportId !== id) return;
    try {
        const report = await api(`/reports/${encodeURIComponent(id)}`);
        if (activeReportId !== id) return;
        const items = report.items || [];
        const signature = reportStatusSignature(items);
        if (signature !== activeReportStatusSignature) {
            updateReportStatusComponents(id, items);
            activeReportStatusSignature = signature;
        }
        if (items.some(item => item.link_status === 'processing')) {
            scheduleReportStatusPoll(id);
        } else {
            loadNavPending();
        }
    } catch (error) {
        if (activeReportId === id) {
            scheduleReportStatusPoll(id, 3000);
        }
    }
}

async function selectReport(id, pushUrl = true) {
    if (!id) return;
    if (reportRefreshTimer) {
        window.clearTimeout(reportRefreshTimer);
        reportRefreshTimer = null;
    }
    activeReportId = id;
    activeReportStatusSignature = '';
    detailStatusFilter = '';
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
            </div>
            <div class="archive-detail-items">
                ${items.length ? detailItemsHtml(items) : '<div class="archive-empty">这份报告没有条目。</div>'}
            </div>
        `;
        activeReportStatusSignature = reportStatusSignature(items);
        if (items.some(item => item.link_status === 'processing')) {
            scheduleReportStatusPoll(id);
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
    // 统计 chip 点击筛选：再次点击同一 chip 取消筛选（事件委托，轮询重渲染后仍生效）
    document.getElementById('archive-detail').addEventListener('click', event => {
        const chip = event.target.closest('[data-status-filter]');
        if (!chip) return;
        const value = chip.dataset.statusFilter || '';
        detailStatusFilter = detailStatusFilter === value ? '' : value;
        document.querySelectorAll('#archive-detail [data-status-filter]').forEach(btn => {
            const active = btn.dataset.statusFilter === detailStatusFilter;
            btn.classList.toggle('is-active', active);
            btn.setAttribute('aria-pressed', String(active));
        });
        applyDetailFilter();
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

