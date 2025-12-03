const API_BASE = '/api/manual_filter';

// State
let state = {
    filterPage: 1,
    reviewPage: 1,
    discardPage: 1,
    actor: localStorage.getItem('actor') || '',
    currentTab: 'filter',
    filterCategory: 'internal_positive',
    reviewView: 'selected'
};

let shouldForceClusterRefresh = false;
let emptyFilterPageReloadTimer = null;

// UI mode
let isSortMode = false;
const MOBILE_REVIEW_BREAKPOINT = 768;

// DOM Elements
const elements = {
    tabs: document.querySelectorAll('.tab-btn'),
    contents: document.querySelectorAll('.tab-content'),
    filterList: document.getElementById('filter-list'),
    filterTabButtons: document.querySelectorAll('.filter-tab-btn'),
    reviewList: document.getElementById('review-list'),
    reviewSelectAll: document.getElementById('review-select-all'),
    reviewBulkStatus: document.getElementById('review-bulk-status'),
    discardList: document.getElementById('discard-list'),
    actorInput: document.getElementById('actor-input'),
    sortToggleBtn: document.getElementById('btn-toggle-sort'),
    exportTemplate: document.getElementById('export-template'),
    exportPeriod: document.getElementById('export-period'),
    exportTotal: document.getElementById('export-total'),
    exportPreviewBtn: document.getElementById('btn-export-preview'),
    exportConfirmBtn: document.getElementById('btn-export-confirm'),
    reviewViewSelect: document.getElementById('review-view-select'),
    stats: {
        pending: document.getElementById('stat-pending'),
        selected: document.getElementById('stat-selected'),
        backup: document.getElementById('stat-backup'),
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
    setupFilterRealtimeDecisionHandlers();

    // Global event listeners
    document.getElementById('btn-refresh').addEventListener('click', () => {
        loadStats();
        shouldForceClusterRefresh = true;
        reloadCurrentTab({ forceClusterRefresh: true });
    });

    document.getElementById('btn-submit-filter').addEventListener('click', discardRemainingItems);
    document.getElementById('btn-export').addEventListener('click', openExportModal);
    document.getElementById('btn-close-modal').addEventListener('click', closeModal);
    if (elements.sortToggleBtn) {
        elements.sortToggleBtn.addEventListener('click', toggleSortMode);
    }
    if (elements.reviewSelectAll) {
        elements.reviewSelectAll.addEventListener('change', (e) => {
            toggleReviewSelectAll(Boolean(e.target.checked));
        });
    }
    if (elements.reviewBulkStatus) {
        elements.reviewBulkStatus.addEventListener('change', applyReviewBulkStatus);
    }
    if (elements.reviewViewSelect) {
        elements.reviewViewSelect.addEventListener('change', handleReviewViewChange);
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
    if (elements.exportPreviewBtn) {
        elements.exportPreviewBtn.addEventListener('click', refreshPreviewAndCopy);
    }
    if (elements.exportConfirmBtn) {
        elements.exportConfirmBtn.addEventListener('click', confirmExportAndCopy);
    }

    // Pagination listeners (delegated or specific)
    setupPagination();
    window.addEventListener('resize', applyReviewViewMode);
});

function renderArticleCard(item, { showStatus = true, collapsed = false } = {}) {
    const safe = item || {};
    const currentStatus = safe.manual_status || safe.status || 'pending';
    const sourcePlaceholder = safe.llm_source_raw ? `(LLM: ${safe.llm_source_raw})` : '留空则回退抓取来源';
    const statusGroup = showStatus ? `
        <div class="radio-group" role="radiogroup">
            <div class="radio-option">
                <input type="radio" name="status-${safe.article_id}" value="selected" id="sel-${safe.article_id}" ${currentStatus === 'selected' ? 'checked' : ''}>
                <label for="sel-${safe.article_id}" class="radio-label">采纳</label>
            </div>
            <div class="radio-option">
                <input type="radio" name="status-${safe.article_id}" value="backup" id="bak-${safe.article_id}" ${currentStatus === 'backup' ? 'checked' : ''}>
                <label for="bak-${safe.article_id}" class="radio-label">备选</label>
            </div>
            <div class="radio-option">
                <input type="radio" name="status-${safe.article_id}" value="discarded" id="dis-${safe.article_id}" ${currentStatus === 'discarded' ? 'checked' : ''}>
                <label for="dis-${safe.article_id}" class="radio-label">放弃</label>
            </div>
        </div>
    ` : '';

    return `
        <div class="article-card${collapsed ? ' collapsed' : ''}" data-id="${safe.article_id || ''}" data-status="${currentStatus}" ${collapsed ? 'style="display:none;"' : ''}>
            <div class="card-header">
                <h3 class="article-title">
                    ${safe.title || '(No Title)'}
                    ${safe.url ? `<a href="${safe.url}" target="_blank" rel="noopener noreferrer">🔗</a>` : ''}
                </h3>
                ${statusGroup}
            </div>

            <div class="meta-row">
                <div class="meta-item">来源: ${safe.source || '-'}</div>
                <div class="meta-item">分数: ${safe.score || '-'}</div>
                <div class="meta-item">
                    <span class="badge ${getSentimentClass(safe.sentiment_label)}">${safe.sentiment_label || '-'}</span>
                </div>
                <div class="meta-item">京内: ${safe.is_beijing_related ? '是' : '否'
        }</div>
    ${safe.bonus_keywords && safe.bonus_keywords.length ?
            `<div class="meta-item">Bonus: ${safe.bonus_keywords.join(', ')}</div>` : ''
        }
            </div>

    <textarea class="summary-box" id="summary-${safe.article_id}">${safe.summary || ''}</textarea>
    <input class="source-box" id="source-${safe.article_id}" value="${safe.llm_source_display || ''}" placeholder="${sourcePlaceholder}">
        </div>
    `;
}

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

function reloadCurrentTab(options = {}) {
    if (state.currentTab === 'filter') loadFilterData(options);
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

async function loadFilterData(options = {}) {
    const forceClusterRefresh = Boolean(options.forceClusterRefresh) || shouldForceClusterRefresh;
    shouldForceClusterRefresh = false;
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
        if (forceClusterRefresh) params.set('force_refresh', 'true');
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
                <div class="meta-item">京内: ${item.is_beijing_related ? '是' : '否'
        }</div>
    ${item.bonus_keywords && item.bonus_keywords.length ?
            `<div class="meta-item">Bonus: ${item.bonus_keywords.join(', ')}</div>` : ''
        }
            </div>

    <textarea class="summary-box" id="summary-${item.article_id}">${item.summary || ''}</textarea>
    <input class="source-box" id="source-${item.article_id}" value="${item.llm_source_display || ''}" placeholder="${item.llm_source_raw ? `(LLM: ${item.llm_source_raw})` : '留空则回退抓取来源'}">
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
        ${list.map(item => renderArticleCard(item, { showStatus: true, collapsed: false })).join('')}
            </div>
    `;
    }).filter(Boolean).join('') || '<div class="empty">No pending articles</div>';
}

function renderClusteredList(clusters) {
    if (!clusters.length) {
        elements.filterList.innerHTML = '<div class="empty">No pending articles</div>';
        return;
    }

    elements.filterList.innerHTML = clusters.map(cluster => {
        const items = cluster.items || [];
        const size = items.length;
        const clusterStatus = cluster.status || 'pending';

        // Single-item cluster: render as a plain article card (no cluster frame).
        if (size <= 1) {
            return renderArticleCard(items[0], { showStatus: true, collapsed: false });
        }

        const [first, ...rest] = items;
        const hiddenCount = rest.length;

        return `
    <div class="filter-cluster" data-cluster-id="${cluster.cluster_id}" data-size="${size}" data-status="${clusterStatus}">
        <div class="cluster-header">
            <div class="radio-group cluster-radio" data-cluster="${cluster.cluster_id}">
                <div class="radio-option">
                    <input type="radio" name="cluster-${cluster.cluster_id}" value="selected" id="cluster-sel-${cluster.cluster_id}" ${clusterStatus === 'selected' ? 'checked' : ''}>
                    <label for="cluster-sel-${cluster.cluster_id}" class="radio-label">采纳</label>
                </div>
                <div class="radio-option">
                    <input type="radio" name="cluster-${cluster.cluster_id}" value="backup" id="cluster-bak-${cluster.cluster_id}" ${clusterStatus === 'backup' ? 'checked' : ''}>
                    <label for="cluster-bak-${cluster.cluster_id}" class="radio-label">备选</label>
                </div>
                <div class="radio-option">
                    <input type="radio" name="cluster-${cluster.cluster_id}" value="discarded" id="cluster-dis-${cluster.cluster_id}" ${clusterStatus === 'discarded' ? 'checked' : ''}>
                    <label for="cluster-dis-${cluster.cluster_id}" class="radio-label">放弃</label>
                </div>
            </div>
        </div>
        <div class="filter-section">
            ${renderArticleCard(first, { showStatus: false, collapsed: false })}
            ${rest.map(item => renderArticleCard(item, { showStatus: false, collapsed: true })).join('')}
        </div>
        ${hiddenCount ? `<div class="cluster-toggle-row"><button type="button" class="btn btn-link cluster-toggle" data-target="${cluster.cluster_id}">展开其余${hiddenCount}条</button></div>` : ''}
    </div>
`;
    }).join('');

    elements.filterList.querySelectorAll('.cluster-toggle').forEach(btn => {
        btn.addEventListener('click', () => {
            const target = btn.dataset.target;
            const container = elements.filterList.querySelector(`[data-cluster-id="${target}"]`);
            if (!container) return;
            const hiddenCards = container.querySelectorAll('.article-card.collapsed');
            const isHidden = hiddenCards.length ? hiddenCards[0].style.display === 'none' : true;
            hiddenCards.forEach(card => {
                card.style.display = isHidden ? '' : 'none';
            });
            const count = hiddenCards.length;
            btn.textContent = isHidden ? '收起其余' + count + '条' : '展开其余' + count + '条';
        });
    });
}

function setupFilterRealtimeDecisionHandlers() {
    if (!elements.filterList) return;
    elements.filterList.addEventListener('change', (e) => {
        const target = e.target;
        if (!(target instanceof HTMLInputElement) || target.type !== 'radio') return;

        if (target.name.startsWith('cluster-')) {
            handleClusterDecisionChange(target);
        } else if (target.name.startsWith('status-')) {
            handleCardDecisionChange(target);
        }
    });
}

async function handleCardDecisionChange(input) {
    const card = input.closest('.article-card');
    if (!card) return;

    const id = card.dataset.id;
    const status = input.value;
    const previousStatus = card.dataset.status || 'pending';
    if (!id || status === previousStatus) return;

    const radios = card.querySelectorAll('input[type="radio"][name^="status-"]');
    setInputsDisabled(radios, true);

    const edits = {};
    collectCardEdits(card, edits);

    try {
        await persistEdits(edits);
        await submitDecisions([id], status);
        removeCardAndMaybeCluster(card);
        loadStats();
        showToast('已更新并移除');
    } catch (err) {
        revertRadioSelection(radios, previousStatus);
        card.dataset.status = previousStatus;
        showToast('更新失败，请重试', 'error');
    } finally {
        if (card.isConnected) {
            setInputsDisabled(radios, false);
        }
    }
}

async function handleClusterDecisionChange(input) {
    const cluster = input.closest('.filter-cluster');
    if (!cluster) return;

    const status = input.value;
    const previousStatus = cluster.dataset.status || 'pending';
    if (status === previousStatus) return;

    const cards = cluster.querySelectorAll('.article-card');
    if (!cards.length) return;

    const radios = cluster.querySelectorAll('.cluster-radio input[type="radio"]');
    setInputsDisabled(radios, true);

    const edits = {};
    const ids = [];
    cards.forEach(card => {
        const id = card.dataset.id;
        if (!id) return;
        ids.push(id);
        collectCardEdits(card, edits);
    });

    if (!ids.length) {
        setInputsDisabled(radios, false);
        return;
    }

    try {
        await persistEdits(edits);
        await submitDecisions(ids, status);
        cluster.remove();
        loadStats();
        showToast('已更新并移除');
    } catch (err) {
        revertRadioSelection(radios, previousStatus);
        cluster.dataset.status = previousStatus;
        showToast('更新失败，请重试', 'error');
    } finally {
        if (cluster.isConnected) {
            setInputsDisabled(radios, false);
        }
    }
}

function collectCardEdits(card, edits) {
    const id = card.dataset.id;
    if (!id) return;
    const summaryBox = card.querySelector('.summary-box');
    const sourceBox = card.querySelector('.source-box');
    const summary = summaryBox ? summaryBox.value : '';
    const llm_source = sourceBox ? sourceBox.value : '';
    edits[id] = { summary, llm_source };
}

async function persistEdits(edits) {
    if (!Object.keys(edits || {}).length) return;
    const res = await fetch(`${API_BASE}/edit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ edits, actor: state.actor })
    });
    if (!res.ok) throw new Error('failed to save edits');
}

async function submitDecisions(ids, status) {
    const payload = {
        selected_ids: status === 'selected' ? ids : [],
        backup_ids: status === 'backup' ? ids : [],
        discarded_ids: status === 'discarded' ? ids : [],
        pending_ids: [],
        actor: state.actor
    };

    const res = await fetch(`${API_BASE}/decide`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error('failed to update status');
}

function setInputsDisabled(nodes, disabled) {
    nodes.forEach(node => {
        node.disabled = disabled;
    });
}

function revertRadioSelection(radios, status) {
    radios.forEach(r => {
        r.checked = r.value === status;
    });
}

function removeCardAndMaybeCluster(card) {
    const cluster = card.closest('.filter-cluster');
    card.remove();
    if (cluster && !cluster.querySelector('.article-card')) {
        cluster.remove();
    }
    scheduleReloadIfFilterPageEmpty();
}

function removeCardsAndClusters(cards) {
    const clusters = new Set();
    cards.forEach(card => {
        const cluster = card.closest('.filter-cluster');
        if (cluster) clusters.add(cluster);
        card.remove();
    });
    clusters.forEach(cluster => {
        if (!cluster.querySelector('.article-card')) cluster.remove();
    });
    scheduleReloadIfFilterPageEmpty();
}

function scheduleReloadIfFilterPageEmpty() {
    if (emptyFilterPageReloadTimer) clearTimeout(emptyFilterPageReloadTimer);
    emptyFilterPageReloadTimer = setTimeout(async () => {
        emptyFilterPageReloadTimer = null;
        if (!elements.filterList) return;
        const remaining = elements.filterList.querySelectorAll('.article-card');
        if (remaining && remaining.length) return;

        const currentPage = state.filterPage;
        await loadFilterData();
        const afterReload = elements.filterList.querySelectorAll('.article-card');
        if ((!afterReload || !afterReload.length) && currentPage > 1) {
            state.filterPage = currentPage - 1;
            await loadFilterData();
        }
    }, 120);
}

async function discardRemainingItems() {
    const cards = elements.filterList ? elements.filterList.querySelectorAll('.article-card') : [];
    if (!cards || !cards.length) {
        showToast('当前无可放弃内容');
        return;
    }

    const edits = {};
    const ids = [];
    cards.forEach(card => {
        const id = card.dataset.id;
        if (!id) return;
        ids.push(id);
        collectCardEdits(card, edits);
    });

    if (!ids.length) {
        showToast('当前无可放弃内容');
        return;
    }

    try {
        await persistEdits(edits);
        await submitDecisions(ids, 'discarded');
        removeCardsAndClusters(cards);
        loadStats();
        showToast(`已放弃${ids.length}条`);
    } catch (e) {
        showToast('批量放弃失败', 'error');
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
                <h3>备选(${backupItems.length})</h3>
                <div class="review-items">
                    ${renderReviewItems(backupItems, 'backup')}
                </div>
            </div>
        </div>
    `;
    initReviewSortable();
    applySortModeState();
    bindReviewSelectionControls();
    applyReviewViewMode();
}

function applySortModeState() {
    const container = document.querySelector('#review-list .review-grid');
    const toggleBtn = elements.sortToggleBtn;
    if (container) {
        container.classList.toggle('compact-mode', isSortMode);
    }
    if (toggleBtn) {
        toggleBtn.classList.toggle('active', isSortMode);
        toggleBtn.innerHTML = `<span class="icon">⇅</span> ${isSortMode ? '退出排序' : '排序模式'}`;
    }
    applyReviewViewMode();
}

function toggleSortMode() {
    isSortMode = !isSortMode;
    applySortModeState();
}

function renderReviewItems(items, currentStatus) {
    return items.map(item => `
        <div class="article-card" data-id="${item.article_id}">
            <div class="card-header">
                <label class="review-select-wrap" title="选择">
                    <input type="checkbox" class="review-select">
                </label>
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
            <input class="source-box" data-id="${item.article_id}" value="${item.llm_source_display || ''}" placeholder="${item.llm_source_raw ? `(LLM: ${item.llm_source_raw})` : '留空则回退抓取来源'}">
        </div>
    `).join('');
}

function initReviewSortable() {
    if (typeof Sortable === 'undefined') return;
    const selectedList = document.querySelector('.review-col.selected-col .review-items');
    const backupList = document.querySelector('.review-col.backup-col .review-items');
    if (!selectedList || !backupList) return;

    const isMobileSort = window.innerWidth <= MOBILE_REVIEW_BREAKPOINT;
    const options = {
        group: 'review-order',
        animation: 150,
        handle: isMobileSort ? undefined : '.drag-handle',
        ghostClass: 'review-ghost',
        forceFallback: true,
        fallbackOnBody: true,
        onEnd: persistReviewOrder,
    };

    // Destroy previous instances by replacing containers (renderReviewGrid already re-rendered DOM),
    // so just create new Sortable instances.
    new Sortable(selectedList, options);
    new Sortable(backupList, options);
}

function bindReviewSelectionControls() {
    const checkboxes = elements.reviewList.querySelectorAll('.review-select');
    checkboxes.forEach(cb => {
        cb.addEventListener('change', updateReviewSelectAllState);
    });

    const statusSelects = elements.reviewList.querySelectorAll('.status-select');
    statusSelects.forEach(sel => {
        sel.addEventListener('change', handleReviewStatusChange);
    });

    const summaries = elements.reviewList.querySelectorAll('.summary-box');
    summaries.forEach(box => {
        box.addEventListener('change', handleSummaryUpdate);
    });
    const sources = elements.reviewList.querySelectorAll('.source-box');
    sources.forEach(input => {
        input.addEventListener('change', handleSourceUpdate);
    });
    updateReviewSelectAllState();
}

function updateReviewSelectAllState() {
    const selectAll = elements.reviewSelectAll;
    if (!selectAll) return;
    const scope = getActiveReviewContainer();
    const checkboxes = scope.querySelectorAll('.review-select');
    const total = checkboxes.length;
    const checkedCount = Array.from(checkboxes).filter(cb => cb.checked).length;
    selectAll.indeterminate = checkedCount > 0 && checkedCount < total;
    selectAll.checked = total > 0 && checkedCount === total;
}

function toggleReviewSelectAll(checked) {
    const scope = getActiveReviewContainer();
    const checkboxes = scope.querySelectorAll('.review-select');
    checkboxes.forEach(cb => {
        cb.checked = checked;
    });
    updateReviewSelectAllState();
}

function applyReviewBulkStatus() {
    if (!elements.reviewBulkStatus) return;
    const value = elements.reviewBulkStatus.value;
    if (!value) return;
    const scope = getActiveReviewContainer();
    const targets = scope.querySelectorAll('.review-select:checked');
    targets.forEach(cb => {
        const card = cb.closest('.article-card');
        if (!card) return;
        const select = card.querySelector('.status-select');
        if (select && select.value !== value) {
            select.value = value;
            handleReviewStatusChange({ target: select });
        }
    });
    elements.reviewBulkStatus.value = '';
    updateReviewSelectAllState();
}

function handleReviewViewChange(e) {
    const value = e.target.value || 'selected';
    state.reviewView = value;
    applyReviewViewMode();
}

function applyReviewViewMode() {
    const grid = document.querySelector('#review-list .review-grid');
    const wrapper = document.querySelector('.review-view-toggle');
    const select = elements.reviewViewSelect;
    if (!grid) return;
    const isMobile = window.innerWidth <= MOBILE_REVIEW_BREAKPOINT;
    if (select) {
        select.value = state.reviewView;
    }
    if (isMobile) {
        grid.classList.add('single-view');
        grid.dataset.view = state.reviewView || 'selected';
        if (wrapper) wrapper.style.display = '';
    } else {
        grid.classList.remove('single-view');
        grid.removeAttribute('data-view');
        if (wrapper) wrapper.style.display = 'none';
    }
}

function getActiveReviewContainer() {
    const grid = document.querySelector('#review-list .review-grid');
    if (!grid) return elements.reviewList;
    const isSingle = grid.classList.contains('single-view');
    if (!isSingle) return elements.reviewList;
    const view = grid.dataset.view === 'backup' ? 'backup' : 'selected';
    const selector = view === 'backup' ? '.review-col.backup-col' : '.review-col.selected-col';
    const col = grid.querySelector(selector);
    return col || elements.reviewList;
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
        showToast('Order saved');
    } catch (e) {
        showToast('Failed to save order', 'error');
    }
}

async function handleReviewStatusChange(e) {
    const select = e.target;
    const card = select.closest('.article-card');
    if (!card) return;
    const id = card.dataset.id;
    const status = select.value;
    const summaryBox = card.querySelector('.summary-box');
    const summary = summaryBox ? summaryBox.value : '';
    const sourceBox = card.querySelector('.source-box');
    const llm_source = sourceBox ? sourceBox.value : '';

    select.disabled = true;
    try {
        // Persist summary edits along with status change
        await fetch(`${API_BASE}/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ edits: { [id]: { summary, llm_source } }, actor: state.actor })
        });

        await fetch(`${API_BASE}/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                selected_ids: status === 'selected' ? [id] : [],
                backup_ids: status === 'backup' ? [id] : [],
                discarded_ids: status === 'discarded' ? [id] : [],
                pending_ids: status === 'pending' ? [id] : [],
                actor: state.actor
            })
        });

        moveReviewCard(card, status);
        updateReviewCounters();
        updateReviewSelectAllState();
        loadStats();
        showToast('已更新状态');
    } catch (err) {
        showToast('更新失败，请重试', 'error');
    } finally {
        select.disabled = false;
    }
}

async function handleSummaryUpdate(e) {
    const box = e.target;
    const card = box.closest('.article-card');
    if (!card) return;
    const id = card.dataset.id;
    const summary = box.value;
    const sourceBox = card.querySelector('.source-box');
    const llm_source = sourceBox ? sourceBox.value : '';
    try {
        await fetch(`${API_BASE}/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ edits: { [id]: { summary, llm_source } }, actor: state.actor })
        });
        showToast('摘要已保存');
    } catch (err) {
        showToast('摘要保存失败', 'error');
    }
}

async function handleSourceUpdate(e) {
    const input = e.target;
    const card = input.closest('.article-card');
    if (!card) return;
    const id = card.dataset.id;
    const llm_source = input.value;
    const summaryBox = card.querySelector('.summary-box');
    const summary = summaryBox ? summaryBox.value : '';
    try {
        await fetch(`${API_BASE}/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ edits: { [id]: { summary, llm_source } }, actor: state.actor })
        });
        showToast('来源已保存');
    } catch (err) {
        showToast('来源保存失败', 'error');
    }
}

function moveReviewCard(card, status) {
    const selectedList = document.querySelector('.review-col.selected-col .review-items');
    const backupList = document.querySelector('.review-col.backup-col .review-items');
    if (!selectedList || !backupList) return;

    if (status === 'selected') {
        selectedList.prepend(card);
    } else if (status === 'backup') {
        backupList.prepend(card);
    } else {
        card.remove();
    }
}

function updateReviewCounters() {
    const selectedList = document.querySelector('.review-col.selected-col .review-items');
    const backupList = document.querySelector('.review-col.backup-col .review-items');
    const selectedCount = selectedList ? selectedList.children.length : 0;
    const backupCount = backupList ? backupList.children.length : 0;

    const selHeader = document.querySelector('.review-col.selected-col h3');
    const bakHeader = document.querySelector('.review-col.backup-col h3');
    if (selHeader) selHeader.textContent = `采纳 (${selectedCount})`;
    if (bakHeader) bakHeader.textContent = `备选(${backupCount})`;
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
    elements.modal.classList.add('active');
    await triggerExport(true);
}

function closeModal() {
    elements.modal.classList.remove('active');
}

function buildExportPayload(dryRun) {
    const tag = new Date().toISOString().split('T')[0];
    const payload = {
        report_tag: tag,
        template: elements.exportTemplate ? elements.exportTemplate.value : 'zongbao',
        period: undefined,
        total_period: undefined,
        dry_run: dryRun,
        mark_exported: !dryRun,
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
        const toastMsg = dryRun ? '已生成预览' : `已导出${result.count || 0} 条`;
        showToast(toastMsg);
    } catch (e) {
        showToast(dryRun ? '预览失败' : '导出失败', 'error');
    }
}

async function copyPreviewText() {
    if (!elements.modalText) return;
    const text = elements.modalText.value || '';
    if (!text) return;
    try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
        } else {
            elements.modalText.select();
            document.execCommand('copy');
        }
        showToast('已复制到剪贴板');
    } catch (err) {
        showToast('复制失败，请手动复制', 'error');
    }
}

async function refreshPreviewAndCopy() {
    await triggerExport(true);
    await copyPreviewText();
}

async function confirmExportAndCopy() {
    await triggerExport(false);
    await copyPreviewText();
    if (state.currentTab === 'review') {
        loadReviewData();
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
