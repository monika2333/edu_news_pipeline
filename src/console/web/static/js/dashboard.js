const API_BASE = '/api/manual_filter';
const GROUP_ORDER = [
    { key: 'internal_negative', label: '京内负面' },
    { key: 'internal_positive', label: '京内正面' },
    { key: 'external_positive', label: '京外正面' },
    { key: 'external_negative', label: '京外负面' }
];
const FILTER_CATEGORIES = ['internal_positive', 'internal_negative', 'external_positive', 'external_negative'];

// State
let state = {
    filterPage: 1,
    reviewPage: 1,
    discardPage: 1,
    actor: localStorage.getItem('actor') || '',
    currentTab: 'filter',
    filterCategory: 'internal_positive',
    reviewView: 'selected',
    reviewReportType: 'zongbao',
    showGroups: true,
    reviewData: {
        selected: [],
        backup: []
    },
    filterCounts: {
        internal_positive: 0,
        internal_negative: 0,
        external_positive: 0,
        external_negative: 0
    },
    reviewCounts: {
        zongbao: { selected: 0, backup: 0 },
        wanbao: { selected: 0, backup: 0 }
    }
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
    reportTypeButtons: document.querySelectorAll('.report-type-btn'),
    stats: {
        pending: document.getElementById('stat-pending'),
        selected: document.getElementById('stat-selected'),
        backup: document.getElementById('stat-backup'),
        exported: document.getElementById('stat-exported')
    },
    reviewRailButtons: document.querySelectorAll('.review-category-btn'),
    modal: document.getElementById('export-modal'),
    modalText: document.getElementById('export-text'),
    toast: document.getElementById('toast')
};

let isBulkUpdatingReview = false;

// Init
document.addEventListener('DOMContentLoaded', () => {
    setupTabs();
    setupActor();
    loadStats();
    loadFilterData();
    loadFilterCounts();
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
    if (elements.reviewRailButtons && elements.reviewRailButtons.length) {
        elements.reviewRailButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                const targetType = btn.dataset.reportType || 'zongbao';
                const targetView = btn.dataset.view || 'selected';
                setReviewReportType(targetType);
                setReviewView(targetView);
            });
        });
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
        updateFilterCountsUI();
    }
    if (elements.exportPreviewBtn) {
        elements.exportPreviewBtn.addEventListener('click', refreshPreviewAndCopy);
    }
    if (elements.exportConfirmBtn) {
        elements.exportConfirmBtn.addEventListener('click', confirmExportAndCopy);
    }
    if (elements.reportTypeButtons && elements.reportTypeButtons.length) {
        elements.reportTypeButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                const val = btn.dataset.type || 'zongbao';
                setReviewReportType(val);
            });
        });
        elements.reportTypeButtons.forEach(btn => {
            btn.classList.toggle('active', btn.dataset.type === state.reviewReportType);
        });
    }

    // Pagination listeners (delegated or specific)
    setupPagination();
    window.addEventListener('resize', applyReviewViewMode);
});

function renderArticleCard(item, { showStatus = true, collapsed = false } = {}) {
    const safe = item || {};
    const currentStatus = safe.manual_status || safe.status || 'pending';
    const importanceScoreRaw = safe.external_importance_score ?? safe.score;
    const importanceScore = (importanceScoreRaw === undefined || importanceScoreRaw === null) ? '-' : importanceScoreRaw;
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
                <div class="meta-item">分数: ${importanceScore}</div>
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

function updateReviewRailCounts() {
    if (!elements.reviewRailButtons || !elements.reviewRailButtons.length) return;
    elements.reviewRailButtons.forEach(btn => {
        const baseLabel = btn.dataset.label || btn.textContent.trim();
        btn.dataset.label = baseLabel;
        const rt = btn.dataset.reportType === 'wanbao' ? 'wanbao' : 'zongbao';
        const view = btn.dataset.view === 'backup' ? 'backup' : 'selected';
        const count = (state.reviewCounts[rt] && state.reviewCounts[rt][view]) || 0;
        btn.textContent = `${baseLabel} (${count})`;
    });
}

function updateFilterCountsUI() {
    if (!elements.filterTabButtons || !elements.filterTabButtons.length) return;
    elements.filterTabButtons.forEach(btn => {
        const baseLabel = btn.dataset.label || btn.textContent.trim();
        btn.dataset.label = baseLabel;
        const key = btn.dataset.category || '';
        const count = state.filterCounts[key] || 0;
        btn.textContent = `${baseLabel} (${count})`;
    });
}

function setReviewReportType(value) {
    const normalized = value === 'wanbao' ? 'wanbao' : 'zongbao';
    state.reviewReportType = normalized;
    if (elements.reportTypeButtons && elements.reportTypeButtons.length) {
        elements.reportTypeButtons.forEach(btn => {
            btn.classList.toggle('active', btn.dataset.type === normalized);
        });
    }
    if (elements.reviewRailButtons && elements.reviewRailButtons.length) {
        elements.reviewRailButtons.forEach(btn => {
            btn.classList.toggle(
                'active',
                btn.dataset.reportType === normalized && btn.dataset.view === state.reviewView
            );
        });
    }
    updateReviewRailCounts();
    loadReviewData();
}

function setReviewView(view) {
    const normalized = view === 'backup' ? 'backup' : 'selected';
    state.reviewView = normalized;
    if (elements.reviewRailButtons && elements.reviewRailButtons.length) {
        elements.reviewRailButtons.forEach(btn => {
            btn.classList.toggle(
                'active',
                btn.dataset.reportType === state.reviewReportType && btn.dataset.view === normalized
            );
        });
    }
    updateReviewRailCounts();
    renderReviewView();
}

async function loadStats() {
    try {
        const [allRes, zbRes, wbRes] = await Promise.all([
            fetch(`${API_BASE}/stats`),
            fetch(`${API_BASE}/stats?report_type=zongbao`),
            fetch(`${API_BASE}/stats?report_type=wanbao`)
        ]);
        const allData = await allRes.json();
        const zbData = await zbRes.json();
        const wbData = await wbRes.json();
        Object.keys(allData).forEach(key => {
            if (elements.stats[key]) elements.stats[key].textContent = allData[key];
        });
        state.reviewCounts = {
            zongbao: { selected: zbData.selected || 0, backup: zbData.backup || 0 },
            wanbao: { selected: wbData.selected || 0, backup: wbData.backup || 0 }
        };
        updateReviewRailCounts();
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
        state.filterCounts[cat] = data.total || 0;
        updateFilterCountsUI();
    } catch (e) {
        elements.filterList.innerHTML = '<div class="error">Failed to load data</div>';
    }
}

async function loadFilterCounts() {
    try {
        await Promise.all(
            FILTER_CATEGORIES.map(async (cat) => {
                const params = new URLSearchParams({
                    limit: '1',
                    offset: '0',
                    cluster: 'false'
                });
                if (cat.startsWith('internal')) params.set('region', 'internal');
                if (cat.startsWith('external')) params.set('region', 'external');
                if (cat.endsWith('positive')) params.set('sentiment', 'positive');
                if (cat.endsWith('negative')) params.set('sentiment', 'negative');
                const res = await fetch(`${API_BASE}/candidates?${params.toString()}`);
                const data = await res.json();
                state.filterCounts[cat] = data.total || 0;
            })
        );
        updateFilterCountsUI();
    } catch (e) {
        // Silent fail; counts remain previous
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
        const paramsSelected = new URLSearchParams({
            decision: 'selected',
            limit: '200',
            report_type: state.reviewReportType
        });
        const paramsBackup = new URLSearchParams({
            decision: 'backup',
            limit: '200',
            report_type: state.reviewReportType
        });

        const [selRes, bakRes] = await Promise.all([
            fetch(`${API_BASE}/review?${paramsSelected.toString()}`),
            fetch(`${API_BASE}/review?${paramsBackup.toString()}`)
        ]);

        const selData = await selRes.json();
        const bakData = await bakRes.json();

        state.reviewData = {
            selected: selData.items || [],
            backup: bakData.items || []
        };
        renderReviewView();
    } catch (e) {
        elements.reviewList.innerHTML = '<div class="error">Failed to load review data</div>';
    }
}

function renderReviewView() {
    const currentView = state.reviewView === 'backup' ? 'backup' : 'selected';
    const items = state.reviewData[currentView] || [];
    const content = renderGroupedReviewItems(items);

    elements.reviewList.innerHTML = `
        <div class="review-grid single-view" data-view="${currentView}">
            <div class="review-items" id="review-items">
                ${content}
            </div>
        </div>
    `;
    applyReviewViewMode();
    bindReviewSelectionControls();
    initReviewSortable();
    applySortModeState();
}

function applySortModeState() {
    const container = document.querySelector('#review-items');
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

function resolveGroupKey(item) {
    if (item.group_key) return item.group_key;
    const region = item.is_beijing_related ? 'internal' : 'external';
    const sentiment = (item.sentiment_label || '').toLowerCase() === 'negative' ? 'negative' : 'positive';
    return `${region}_${sentiment}`;
}

function renderGroupedReviewItems(items) {
    if (!items || !items.length) {
        return '<div class="empty">当前列表为空</div>';
    }
    const buckets = {};
    items.forEach(item => {
        const key = resolveGroupKey(item);
        if (!buckets[key]) buckets[key] = [];
        buckets[key].push(item);
    });

    const renderCards = (list) => list.map(renderReviewCard).join('');
    let html = '';
    GROUP_ORDER.forEach(group => {
        const groupItems = buckets[group.key] || [];
        if (state.showGroups) {
            html += `
                <div class="review-group" data-group="${group.key}">
                    <div class="review-group-header">${group.label} (${groupItems.length})</div>
                    <div class="review-group-body">
                        ${renderCards(groupItems)}
                    </div>
                </div>
            `;
        } else {
            html += renderCards(groupItems);
        }
    });
    return html;
}

function renderReviewCard(item) {
    const currentStatus = item.manual_status || item.status || state.reviewView || 'selected';
    const placeholder = item.llm_source_raw ? `(LLM: ${item.llm_source_raw})` : '留空则回退抓取来源';
    return `
        <div class="article-card" data-id="${item.article_id || ''}" data-status="${currentStatus}">
            <div class="card-header">
                <label class="review-select-wrap" title="选择">
                    <input type="checkbox" class="review-select">
                </label>
                <span class="drag-handle" title="拖动排序">&#8942;</span>
                <h4 class="article-title">
                    ${item.title || '(No Title)'}
                    ${item.url ? `<a href="${item.url}" target="_blank" rel="noopener noreferrer">🔗</a>` : ''}
                </h4>
                <select class="status-select" data-id="${item.article_id || ''}">
                    <option value="selected" ${currentStatus === 'selected' ? 'selected' : ''}>采纳</option>
                    <option value="backup" ${currentStatus === 'backup' ? 'selected' : ''}>备选</option>
                    <option value="discarded">放弃</option>
                    <option value="pending">待处理</option>
                </select>
            </div>
            <textarea class="summary-box" data-id="${item.article_id || ''}">${item.summary || ''}</textarea>
            <input class="source-box" data-id="${item.article_id || ''}" value="${item.llm_source_display || ''}" placeholder="${placeholder}">
        </div>
    `;
}

function initReviewSortable() {
    if (typeof Sortable === 'undefined') return;
    const list = document.querySelector('#review-items');
    if (!list) return;

    const isMobileSort = window.innerWidth <= MOBILE_REVIEW_BREAKPOINT;
    new Sortable(list, {
        animation: 150,
        handle: isMobileSort ? undefined : '.drag-handle',
        ghostClass: 'review-ghost',
        forceFallback: true,
        fallbackOnBody: true,
        draggable: '.article-card',
        onEnd: persistReviewOrder,
    });
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

async function applyReviewBulkStatus() {
    if (!elements.reviewBulkStatus) return;
    const value = elements.reviewBulkStatus.value;
    if (!value) return;
    const scope = getActiveReviewContainer();
    const targets = scope.querySelectorAll('.review-select:checked');
    if (!targets.length) {
        elements.reviewBulkStatus.value = '';
        showToast('请先选择要移动的条目', 'error');
        return;
    }

    const selected_ids = [];
    const backup_ids = [];
    const discarded_ids = [];
    const pending_ids = [];

    let targetReportType = state.reviewReportType;
    if (value.includes(':')) {
        const [rt, st] = value.split(':');
        targetReportType = rt === 'wanbao' ? 'wanbao' : 'zongbao';
        targets.forEach(cb => {
            const card = cb.closest('.article-card');
            if (!card) return;
            const id = card.dataset.id;
            if (!id) return;
            if (st === 'selected') selected_ids.push(id);
            else if (st === 'backup') backup_ids.push(id);
        });
    } else if (value === 'discarded' || value === 'pending') {
        targets.forEach(cb => {
            const card = cb.closest('.article-card');
            if (!card) return;
            const id = card.dataset.id;
            if (!id) return;
            if (value === 'discarded') discarded_ids.push(id);
            else pending_ids.push(id);
        });
    }

    elements.reviewBulkStatus.value = '';
    if (!selected_ids.length && !backup_ids.length && !discarded_ids.length && !pending_ids.length) {
        return;
    }

    try {
        isBulkUpdatingReview = true;
        await fetch(`${API_BASE}/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                selected_ids,
                backup_ids,
                discarded_ids,
                pending_ids,
                actor: state.actor,
                report_type: targetReportType
            })
        });
        await loadReviewData();
        loadStats();
        showToast('批量移动完成');
    } catch (e) {
        showToast('批量移动失败', 'error');
    } finally {
        isBulkUpdatingReview = false;
        updateReviewSelectAllState();
    }
}

function applyReviewViewMode() {
    if (elements.reviewRailButtons && elements.reviewRailButtons.length) {
        elements.reviewRailButtons.forEach(btn => {
            btn.classList.toggle(
                'active',
                btn.dataset.reportType === state.reviewReportType && btn.dataset.view === state.reviewView
            );
        });
    }
    updateReviewRailCounts();
}

function getActiveReviewContainer() {
    const container = document.getElementById('review-items');
    return container || elements.reviewList;
}

function syncReviewStateOrder(status, orderedIds) {
    const lookup = {};
    [...(state.reviewData.selected || []), ...(state.reviewData.backup || [])].forEach(item => {
        if (item && item.article_id) lookup[item.article_id] = item;
    });
    const orderedItems = orderedIds.map(id => lookup[id]).filter(Boolean);
    state.reviewData[status] = orderedItems;
}

async function persistReviewOrder() {
    const list = document.querySelector('#review-items');
    if (!list) return;

    const orderedIds = Array.from(list.querySelectorAll('.article-card')).map(card => card.dataset.id);
    syncReviewStateOrder(state.reviewView, orderedIds);

    const payload = {
        selected_order: (state.reviewData.selected || []).map(item => item.article_id),
        backup_order: (state.reviewData.backup || []).map(item => item.article_id),
        actor: state.actor,
        report_type: state.reviewReportType
    };

    try {
        await fetch(`${API_BASE}/order`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
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
        await fetch(`${API_BASE}/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ edits: { [id]: { summary, llm_source } }, actor: state.actor, report_type: state.reviewReportType })
        });

        await fetch(`${API_BASE}/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                selected_ids: status === 'selected' ? [id] : [],
                backup_ids: status === 'backup' ? [id] : [],
                discarded_ids: status === 'discarded' ? [id] : [],
                pending_ids: status === 'pending' ? [id] : [],
                actor: state.actor,
                report_type: state.reviewReportType
            })
        });

        await loadReviewData();
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
            body: JSON.stringify({ edits: { [id]: { summary, llm_source } }, actor: state.actor, report_type: state.reviewReportType })
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
            body: JSON.stringify({ edits: { [id]: { summary, llm_source } }, actor: state.actor, report_type: state.reviewReportType })
        });
        showToast('来源已保存');
    } catch (err) {
        showToast('来源保存失败', 'error');
    }
}

// --- Discard Tab Logic ---

async function loadDiscardData() {
    elements.discardList.innerHTML = '<div class="loading">Loading...</div>';
    try {
        const params = new URLSearchParams({
            limit: '30',
            offset: `${(state.discardPage - 1) * 30}`,
            report_type: state.reviewReportType
        });
        const res = await fetch(`${API_BASE}/discarded?${params.toString()}`);
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
                actor: state.actor,
                report_type: state.reviewReportType
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
    const templateValue = elements.exportTemplate ? elements.exportTemplate.value : 'zongbao';
    const payload = {
        report_tag: tag,
        template: templateValue,
        period: undefined,
        total_period: undefined,
        dry_run: dryRun,
        mark_exported: !dryRun,
        report_type: templateValue === 'wanbao' ? 'wanbao' : 'zongbao',
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
