const API_BASE = '/api/manual_filter';

// State
let state = {
    filterPage: 1,
    reviewPage: 1,
    discardPage: 1,
    actor: localStorage.getItem('actor') || '',
    currentTab: 'filter',
    filterCategory: 'internal_positive'
};

// UI mode
let isSortMode = false;

// DOM Elements
const elements = {
    tabs: document.querySelectorAll('.tab-btn'),
    contents: document.querySelectorAll('.tab-content'),
    filterList: document.getElementById('filter-list'),
    filterTabButtons: document.querySelectorAll('.filter-tab-btn'),
    reviewList: document.getElementById('review-list'),
    discardList: document.getElementById('discard-list'),
    actorInput: document.getElementById('actor-input'),
    sortToggleBtn: document.getElementById('btn-toggle-sort'),
    exportTemplate: document.getElementById('export-template'),
    exportPeriod: document.getElementById('export-period'),
    exportTotal: document.getElementById('export-total'),
    exportPreviewToggle: document.getElementById('export-preview'),
    exportMarkToggle: document.getElementById('export-mark'),
    exportPreviewBtn: document.getElementById('btn-export-preview'),
    exportConfirmBtn: document.getElementById('btn-export-confirm'),
    stats: {
        pending: document.getElementById('stat-pending'),
        selected: document.getElementById('stat-selected'),
        backup: document.getElementById('stat-backup'),
        discarded: document.getElementById('stat-discarded'),
        exported: document.getElementById('stat-exported')
    },
    modal: document.getElementById('export-modal'),
    modalText: document.getElementById('export-text'),
    toast: document.getElementById('toast')
};

// Init
document.addEventListener('DOMContentLoaded', () => {
    setupTabs();
    setupActor();
    loadStats();
    loadFilterData();

    // Global event listeners
    document.getElementById('btn-refresh').addEventListener('click', () => {
        loadStats();
        reloadCurrentTab();
    });

    document.getElementById('btn-submit-filter').addEventListener('click', submitFilter);
    document.getElementById('btn-save-review').addEventListener('click', saveReview);
    document.getElementById('btn-export').addEventListener('click', openExportModal);
    document.getElementById('btn-copy').addEventListener('click', copyExportText);
    document.getElementById('btn-close-modal').addEventListener('click', closeModal);
    if (elements.sortToggleBtn) {
        elements.sortToggleBtn.addEventListener('click', toggleSortMode);
    }
    if (elements.filterTabButtons && elements.filterTabButtons.length) {
        elements.filterTabButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                elements.filterTabButtons.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                state.filterCategory = btn.dataset.category || 'all';
                state.filterPage = 1;
                loadFilterData();
            });
        });
    }
    if (elements.exportPreviewToggle) {
        elements.exportPreviewToggle.addEventListener('change', syncPreviewToggleState);
    }
    if (elements.exportPreviewBtn) {
        elements.exportPreviewBtn.addEventListener('click', () => triggerExport(true));
    }
    if (elements.exportConfirmBtn) {
        elements.exportConfirmBtn.addEventListener('click', () => triggerExport(false));
    }

    // Pagination listeners (delegated or specific)
    setupPagination();
});

function setupTabs() {
    elements.tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            elements.tabs.forEach(t => t.classList.remove('active'));
            elements.contents.forEach(c => c.classList.remove('active'));

            tab.classList.add('active');
            document.getElementById(`${tab.dataset.tab}-tab`).classList.add('active');
            state.currentTab = tab.dataset.tab;

            reloadCurrentTab();
        });
    });
}

function setupActor() {
    elements.actorInput.value = state.actor;
    elements.actorInput.addEventListener('change', (e) => {
        state.actor = e.target.value.trim();
        localStorage.setItem('actor', state.actor);
    });
}

function reloadCurrentTab() {
    if (state.currentTab === 'filter') loadFilterData();
    else if (state.currentTab === 'review') loadReviewData();
    else if (state.currentTab === 'discard') loadDiscardData();
}

async function loadStats() {
    try {
        const res = await fetch(`${API_BASE}/stats`);
        const data = await res.json();
        Object.keys(data).forEach(key => {
            if (elements.stats[key]) elements.stats[key].textContent = data[key];
        });
    } catch (e) {
        showToast('Failed to load stats', 'error');
    }
}

// --- Filter Tab Logic ---

async function loadFilterData() {
    elements.filterList.innerHTML = '<div class="loading">Loading...</div>';
    try {
        const params = new URLSearchParams({
            limit: '10',
            offset: `${(state.filterPage - 1) * 10}`,
            cluster: 'true',
        });
        const cat = state.filterCategory || 'internal_positive';
        if (cat) {
            if (cat.startsWith('internal')) params.set('region', 'internal');
            if (cat.startsWith('external')) params.set('region', 'external');
            if (cat.endsWith('positive')) params.set('sentiment', 'positive');
            if (cat.endsWith('negative')) params.set('sentiment', 'negative');
        }
        const res = await fetch(`${API_BASE}/candidates?${params.toString()}`);
        const data = await res.json();
        renderFilterList(data);
        updatePagination('filter', data.total || 0, state.filterPage);
    } catch (e) {
        elements.filterList.innerHTML = '<div class="error">Failed to load data</div>';
    }
}

function renderFilterList(data) {
    const items = data.items || [];
    if (data.clusters && Array.isArray(data.clusters) && data.clusters.length) {
        renderClusteredList(data.clusters);
        return;
    }
    if (!items.length) {
        elements.filterList.innerHTML = '<div class="empty">No pending articles</div>';
        return;
    }

    const buckets = {
        internalPositive: [],
        internalNegative: [],
        externalPositive: [],
        externalNegative: [],
    };

    const renderCard = (item) => `
        <div class="article-card" data-id="${item.article_id}">
            <div class="card-header">
                <h3 class="article-title">
                    ${item.title || '(No Title)'}
                    ${item.url ? `<a href="${item.url}" target="_blank" rel="noopener noreferrer">🔗</a>` : ''}
                </h3>
                <div class="radio-group" role="radiogroup">
                    <div class="radio-option">
                        <input type="radio" name="status-${item.article_id}" value="selected" id="sel-${item.article_id}">
                        <label for="sel-${item.article_id}" class="radio-label">采纳</label>
                    </div>
                    <div class="radio-option">
                        <input type="radio" name="status-${item.article_id}" value="backup" id="bak-${item.article_id}">
                        <label for="bak-${item.article_id}" class="radio-label">备选</label>
                    </div>
                    <div class="radio-option">
                        <input type="radio" name="status-${item.article_id}" value="discarded" id="dis-${item.article_id}" checked>
                        <label for="dis-${item.article_id}" class="radio-label">放弃</label>
                    </div>
                </div>
            </div>
            
            <div class="meta-row">
                <div class="meta-item">来源: ${item.source || '-'}</div>
                <div class="meta-item">分数: ${item.score || '-'}</div>
                <div class="meta-item">
                    <span class="badge ${getSentimentClass(item.sentiment_label)}">${item.sentiment_label || '-'}</span>
                </div>
                <div class="meta-item">京内: ${item.is_beijing_related ? '是' : '否'}</div>
                ${item.bonus_keywords && item.bonus_keywords.length ?
            `<div class="meta-item">Bonus: ${item.bonus_keywords.join(', ')}</div>` : ''}
            </div>
            
            <textarea class="summary-box" id="summary-${item.article_id}">${item.summary || ''}</textarea>
        </div>
    `;

    items.forEach(item => {
        const isInternal = !!item.is_beijing_related;
        const sentiment = (item.sentiment_label || '').toLowerCase() === 'negative' ? 'negative' : 'positive';
        if (isInternal && sentiment === 'positive') buckets.internalPositive.push(item);
        else if (isInternal && sentiment === 'negative') buckets.internalNegative.push(item);
        else if (!isInternal && sentiment === 'positive') buckets.externalPositive.push(item);
        else buckets.externalNegative.push(item);
    });

    const sections = [
        { key: 'internalPositive', label: '' },
        { key: 'internalNegative', label: '' },
        { key: 'externalPositive', label: '' },
        { key: 'externalNegative', label: '' },
    ];

    elements.filterList.innerHTML = sections.map(sec => {
        const list = buckets[sec.key] || [];
        if (!list.length) return '';
        return `
            <div class="filter-section">
                ${list.map(renderCard).join('')}
            </div>
        `;
    }).filter(Boolean).join('') || '<div class="empty">No pending articles</div>';
}

function renderClusteredList(clusters) {
    const renderCard = (item) => `
        <div class="article-card" data-id="${item.article_id}">
            <div class="card-header">
                <h3 class="article-title">
                    ${item.title || '(No Title)'}
                    ${item.url ? `<a href="${item.url}" target="_blank" rel="noopener noreferrer">🔗</a>` : ''}
                </h3>
                <div class="radio-group" role="radiogroup">
                    <div class="radio-option">
                        <input type="radio" name="status-${item.article_id}" value="selected" id="sel-${item.article_id}">
                        <label for="sel-${item.article_id}" class="radio-label">采纳</label>
                    </div>
                    <div class="radio-option">
                        <input type="radio" name="status-${item.article_id}" value="backup" id="bak-${item.article_id}">
                        <label for="bak-${item.article_id}" class="radio-label">备选</label>
                    </div>
                    <div class="radio-option">
                        <input type="radio" name="status-${item.article_id}" value="discarded" id="dis-${item.article_id}" checked>
                        <label for="dis-${item.article_id}" class="radio-label">放弃</label>
                    </div>
                </div>
            </div>
            
            <div class="meta-row">
                <div class="meta-item">来源: ${item.source || '-'}</div>
                <div class="meta-item">分数: ${item.score || '-'}</div>
                <div class="meta-item">
                    <span class="badge ${getSentimentClass(item.sentiment_label)}">${item.sentiment_label || '-'}</span>
                </div>
                <div class="meta-item">京内: ${item.is_beijing_related ? '是' : '否'}</div>
                ${item.bonus_keywords && item.bonus_keywords.length ?
            `<div class="meta-item">Bonus: ${item.bonus_keywords.join(', ')}</div>` : ''}
            </div>
            
            <textarea class="summary-box" id="summary-${item.article_id}">${item.summary || ''}</textarea>
        </div>
    `;

    if (!clusters.length) {
        elements.filterList.innerHTML = '<div class="empty">No pending articles</div>';
        return;
    }

    elements.filterList.innerHTML = clusters.map(cluster => `
        <div class="filter-cluster" data-cluster-id="${cluster.cluster_id}">
            <div class="cluster-header">
                <div class="cluster-title">
                    ${cluster.representative || '(聚类)'} ${cluster.size ? `(${cluster.size})` : ''}
                </div>
                <div class="radio-group cluster-radio" data-cluster="${cluster.cluster_id}">
                    <div class="radio-option">
                        <input type="radio" name="cluster-${cluster.cluster_id}" value="selected" id="cluster-sel-${cluster.cluster_id}">
                        <label for="cluster-sel-${cluster.cluster_id}" class="radio-label">采纳</label>
                    </div>
                    <div class="radio-option">
                        <input type="radio" name="cluster-${cluster.cluster_id}" value="backup" id="cluster-bak-${cluster.cluster_id}">
                        <label for="cluster-bak-${cluster.cluster_id}" class="radio-label">备选</label>
                    </div>
                    <div class="radio-option">
                        <input type="radio" name="cluster-${cluster.cluster_id}" value="discarded" id="cluster-dis-${cluster.cluster_id}">
                        <label for="cluster-dis-${cluster.cluster_id}" class="radio-label">放弃</label>
                    </div>
                </div>
            </div>
            <div class="filter-section">
                ${cluster.items.map(renderCard).join('')}
            </div>
        </div>
    `).join('');

    // Cluster-level selection
    elements.filterList.querySelectorAll('.cluster-radio input').forEach(input => {
        input.addEventListener('change', () => {
            const clusterId = input.name.replace('cluster-', '');
            const status = input.value;
            const container = elements.filterList.querySelector(`[data-cluster-id="${clusterId}"]`);
            if (!container) return;
            container.querySelectorAll(`input[type="radio"][value="${status}"]`).forEach(r => {
                r.checked = true;
            });
        });
    });
}

async function submitFilter() {
    const cards = document.querySelectorAll('#filter-list .article-card');
    const selected = [];
    const backup = [];
    const discarded = [];
    const edits = {};

    cards.forEach(card => {
        const id = card.dataset.id;
        // Avoid invalid selectors when article_id contains ':' or '/'
        const statusInput = card.querySelector('input[type="radio"]:checked');
        const status = statusInput ? statusInput.value : 'discarded';
        const summaryBox = card.querySelector('.summary-box');
        const summary = summaryBox ? summaryBox.value : '';

        // Save edit if summary changed (we assume it might have, simplest to just send all for now or check dataset)
        // For simplicity, we'll send edits for all items in this batch to ensure latest summary is saved
        edits[id] = { summary };

        if (status === 'selected') selected.push(id);
        else if (status === 'backup') backup.push(id);
        else discarded.push(id);
    });

    try {
        // First save edits
        await fetch(`${API_BASE}/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ edits, actor: state.actor })
        });

        // Then update status
        const res = await fetch(`${API_BASE}/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                selected_ids: selected,
                backup_ids: backup,
                discarded_ids: discarded,
                actor: state.actor
            })
        });

        const result = await res.json();
        showToast(`Updated: ${result.selected} selected, ${result.backup} backup, ${result.discarded} discarded`);
        loadStats();
        loadFilterData(); // Reload to get next page
    } catch (e) {
        showToast('Failed to submit', 'error');
    }
}

// --- Review Tab Logic ---

async function loadReviewData() {
    elements.reviewList.innerHTML = '<div class="loading">Loading...</div>';
    try {
        // Load both selected and backup
        const [selRes, bakRes] = await Promise.all([
            fetch(`${API_BASE}/review?decision=selected&limit=50`), // Load more for review
            fetch(`${API_BASE}/review?decision=backup&limit=50`)
        ]);

        const selData = await selRes.json();
        const bakData = await bakRes.json();

        renderReviewGrid(selData.items, bakData.items);
    } catch (e) {
        elements.reviewList.innerHTML = '<div class="error">Failed to load review data</div>';
    }
}

function renderReviewGrid(selectedItems, backupItems) {
    elements.reviewList.innerHTML = `
        <div class="review-grid">
            <div class="review-col selected-col" data-status="selected">
                <h3>采纳 (${selectedItems.length})</h3>
                <div class="review-items">
                    ${renderReviewItems(selectedItems, 'selected')}
                </div>
            </div>
            <div class="review-col backup-col" data-status="backup">
                <h3>备选 (${backupItems.length})</h3>
                <div class="review-items">
                    ${renderReviewItems(backupItems, 'backup')}
                </div>
            </div>
        </div>
    `;
    initReviewSortable();
    applySortModeState();
}

function applySortModeState() {
    const container = document.querySelector('#review-list .review-grid');
    const toggleBtn = elements.sortToggleBtn;
    if (container) {
        container.classList.toggle('compact-mode', isSortMode);
    }
    if (toggleBtn) {
        toggleBtn.classList.toggle('active', isSortMode);
        toggleBtn.innerHTML = `<span class="icon">🔃</span> ${isSortMode ? '退出排序' : '排序模式'}`;
    }
}

function toggleSortMode() {
    isSortMode = !isSortMode;
    applySortModeState();
}

function renderReviewItems(items, currentStatus) {
    return items.map(item => `
        <div class="article-card" data-id="${item.article_id}">
            <div class="card-header">
                <span class="drag-handle" title="拖动排序">&#8942;</span>
                <h4 class="article-title">
                    ${item.title}
                    ${item.url ? `<a href="${item.url}" target="_blank" rel="noopener noreferrer">🔗</a>` : ''}
                </h4>
                <select class="status-select" data-id="${item.article_id}">
                    <option value="selected" ${currentStatus === 'selected' ? 'selected' : ''}>采纳</option>
                    <option value="backup" ${currentStatus === 'backup' ? 'selected' : ''}>备选</option>
                    <option value="discarded">放弃</option>
                    <option value="pending">待处理</option>
                </select>
            </div>
            <textarea class="summary-box" data-id="${item.article_id}">${item.summary || ''}</textarea>
        </div>
    `).join('');
}

function initReviewSortable() {
    if (typeof Sortable === 'undefined') return;
    const selectedList = document.querySelector('.review-col.selected-col .review-items');
    const backupList = document.querySelector('.review-col.backup-col .review-items');
    if (!selectedList || !backupList) return;

    const options = {
        group: 'review-order',
        animation: 150,
        handle: '.drag-handle',
        forceFallback: true,
        fallbackOnBody: true,
        onEnd: persistReviewOrder,
    };

    // Destroy previous instances by replacing containers (renderReviewGrid already re-rendered DOM),
    // so just create new Sortable instances.
    new Sortable(selectedList, options);
    new Sortable(backupList, options);
}

async function persistReviewOrder() {
    const selectedList = document.querySelector('.review-col.selected-col .review-items');
    const backupList = document.querySelector('.review-col.backup-col .review-items');
    if (!selectedList || !backupList) return;

    const selected_order = Array.from(selectedList.querySelectorAll('.article-card')).map(card => card.dataset.id);
    const backup_order = Array.from(backupList.querySelectorAll('.article-card')).map(card => card.dataset.id);

    try {
        await fetch(`${API_BASE}/order`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ selected_order, backup_order, actor: state.actor })
        });
        showToast('排序已保存');
    } catch (e) {
        showToast('排序保存失败', 'error');
    }
}

async function saveReview() {
    const cards = document.querySelectorAll('#review-list .article-card');
    const edits = {};
    const statusChanges = { selected: [], backup: [], discarded: [], pending: [] };
    let hasChanges = false;

    cards.forEach(card => {
        const id = card.dataset.id;
        const summary = card.querySelector('textarea').value;
        const status = card.querySelector('select').value;

        edits[id] = { summary };
        statusChanges[status].push(id);
        hasChanges = true;
    });

    if (!hasChanges) return;

    try {
        await fetch(`${API_BASE}/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ edits, actor: state.actor })
        });

        await fetch(`${API_BASE}/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                selected_ids: statusChanges.selected,
                backup_ids: statusChanges.backup,
                discarded_ids: statusChanges.discarded,
                pending_ids: statusChanges.pending,
                actor: state.actor
            })
        });

        showToast('Review saved');
        loadStats();
        loadReviewData();
    } catch (e) {
        showToast('Failed to save review', 'error');
    }
}

// --- Discard Tab Logic ---

async function loadDiscardData() {
    elements.discardList.innerHTML = '<div class="loading">Loading...</div>';
    try {
        const res = await fetch(`${API_BASE}/discarded?limit=30&offset=${(state.discardPage - 1) * 30}`);
        const data = await res.json();
        renderDiscardList(data.items);
        updatePagination('discard', data.total, state.discardPage);
    } catch (e) {
        elements.discardList.innerHTML = '<div class="error">Failed to load data</div>';
    }
}

function renderDiscardList(items) {
    if (!items.length) {
        elements.discardList.innerHTML = '<div class="empty">No discarded articles</div>';
        return;
    }

    elements.discardList.innerHTML = items.map(item => `
        <div class="article-card">
            <div class="card-header">
                <h4 class="article-title">${item.title}</h4>
                <button class="btn btn-secondary btn-sm" onclick="restoreToBackup('${item.article_id}')">恢复至备选</button>
            </div>
            <div class="meta-row">
                <div class="meta-item">来源: ${item.source}</div>
                <div class="meta-item">分数: ${item.score}</div>
            </div>
        </div>
    `).join('');
}

window.restoreToBackup = async function (id) {
    try {
        await fetch(`${API_BASE}/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                selected_ids: [],
                backup_ids: [id],
                discarded_ids: [],
                actor: state.actor
            })
        });
        showToast('Restored to backup');
        loadStats();
        loadDiscardData();
    } catch (e) {
        showToast('Failed to restore', 'error');
    }
};

// --- Export Logic ---

async function openExportModal() {
    if (elements.exportPreviewToggle) {
        elements.exportPreviewToggle.checked = true;
        syncPreviewToggleState();
    }
    elements.modal.classList.add('active');
    await triggerExport(true);
}

function closeModal() {
    elements.modal.classList.remove('active');
}

function copyExportText() {
    elements.modalText.select();
    document.execCommand('copy');
    showToast('Copied to clipboard');
}

function syncPreviewToggleState() {
    const isPreview = elements.exportPreviewToggle && elements.exportPreviewToggle.checked;
    if (elements.exportMarkToggle) {
        elements.exportMarkToggle.disabled = isPreview;
        if (isPreview) {
            elements.exportMarkToggle.checked = false;
        } else if (!elements.exportMarkToggle.checked) {
            elements.exportMarkToggle.checked = true;
        }
    }
}

function buildExportPayload(dryRun) {
    const tag = new Date().toISOString().split('T')[0];
    const payload = {
        report_tag: tag,
        template: elements.exportTemplate ? elements.exportTemplate.value : 'zongbao',
        period: undefined,
        total_period: undefined,
        dry_run: dryRun,
        mark_exported: !dryRun && elements.exportMarkToggle ? elements.exportMarkToggle.checked : false,
    };
    if (elements.exportPeriod && elements.exportPeriod.value) {
        const val = Number(elements.exportPeriod.value);
        if (!Number.isNaN(val)) payload.period = val;
    }
    if (elements.exportTotal && elements.exportTotal.value) {
        const val = Number(elements.exportTotal.value);
        if (!Number.isNaN(val)) payload.total_period = val;
    }
    return payload;
}

async function triggerExport(dryRun = true) {
    const payload = buildExportPayload(dryRun);
    try {
        const res = await fetch(`${API_BASE}/export`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        if (elements.exportPeriod && result.period !== undefined) {
            elements.exportPeriod.value = result.period;
        }
        if (elements.exportTotal && result.total_period !== undefined) {
            elements.exportTotal.value = result.total_period;
        }
        if (elements.modalText) {
            elements.modalText.value = result.content || 'No content generated';
        }
        const toastMsg = dryRun ? '已生成预览' : `已导出 ${result.count || 0} 条`;
        showToast(toastMsg);
    } catch (e) {
        showToast(dryRun ? '预览失败' : '导出失败', 'error');
    }
}

// --- Helpers ---

function getSentimentClass(label) {
    if (!label) return 'neutral';
    label = label.toLowerCase();
    if (label === 'positive') return 'positive';
    if (label === 'negative') return 'negative';
    return 'neutral';
}

function showToast(msg, type = 'success') {
    elements.toast.textContent = msg;
    elements.toast.className = `toast show ${type}`;
    setTimeout(() => {
        elements.toast.classList.remove('show');
    }, 3000);
}

function updatePagination(tab, total, currentPage) {
    // Simple pagination implementation
    const totalPages = Math.ceil(total / 10);
    const container = document.getElementById(`${tab}-pagination`);
    if (!container) return;

    container.innerHTML = `
        <button class="btn btn-secondary" ${currentPage <= 1 ? 'disabled' : ''} onclick="changePage('${tab}', ${currentPage - 1})">Previous</button>
        <span>Page ${currentPage} of ${totalPages}</span>
        <button class="btn btn-secondary" ${currentPage >= totalPages ? 'disabled' : ''} onclick="changePage('${tab}', ${currentPage + 1})">Next</button>
    `;
}

window.changePage = function (tab, page) {
    if (tab === 'filter') state.filterPage = page;
    else if (tab === 'discard') state.discardPage = page;
    reloadCurrentTab();
};

function setupPagination() {
    // Handled dynamically
}
