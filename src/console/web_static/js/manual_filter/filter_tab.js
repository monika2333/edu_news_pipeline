// Manual Filter JS - Filter Tab
// Depends on: api.js, ui_templates.js, core.js, utils.js

// --- Filter Tab Logic ---

async function loadFilterData(options = {}) {
    const forceClusterRefresh = Boolean(options.forceClusterRefresh) || shouldForceClusterRefresh;
    shouldForceClusterRefresh = false;
    elements.filterList.innerHTML = renderSkeleton(3);
    try {
        // Prepare params
        const params = {
            limit: '10',
            offset: `${(state.filterPage - 1) * 10}`,
            cluster: 'true',
        };
        const cat = state.filterCategory || 'internal_positive';
        if (cat) {
            if (cat.startsWith('internal')) params.region = 'internal';
            if (cat.startsWith('external')) params.region = 'external';
            if (cat.endsWith('positive')) params.sentiment = 'positive';
            if (cat.endsWith('negative')) params.sentiment = 'negative';
        }
        if (forceClusterRefresh) params.force_refresh = 'true';

        // Call API
        const data = await manualFilterApi.fetchCandidates(params);

        renderFilterList(data);
        updatePagination('filter', data.total || 0, state.filterPage);
        state.filterCounts[cat] = data.total || 0;
        updateFilterCountsUI();
    } catch (e) {
        console.error(e);
        elements.filterList.innerHTML = '<div class="error">加载数据失败</div>';
    }
}

async function loadFilterCounts() {
    try {
        const counts = await manualFilterApi.fetchFilterCounts(FILTER_CATEGORIES);
        counts.forEach(c => {
            state.filterCounts[c.category] = c.total;
        });
        updateFilterCountsUI();
    } catch (e) {
        // Silent fail; counts remain previous
        console.error('Failed to load filter counts', e);
    }
}

function renderFilterList(data) {
    const items = data.items || [];

    // Cluster View
    if (data.clusters && Array.isArray(data.clusters) && data.clusters.length) {
        renderClusteredList(data.clusters);
        return;
    }

    // Flat List View
    if (!items.length) {
        elements.filterList.innerHTML = '<div class="empty">无待处理文章</div>';
        return;
    }

    // Bucketing
    const buckets = {
        internalPositive: [],
        internalNegative: [],
        externalPositive: [],
        externalNegative: [],
    };

    items.forEach(item => {
        const isInternal = !!item.is_beijing_related;
        const sentiment = (item.sentiment_label || '').toLowerCase() === 'negative' ? 'negative' : 'positive';
        if (isInternal && sentiment === 'positive') buckets.internalPositive.push(item);
        else if (isInternal && sentiment === 'negative') buckets.internalNegative.push(item);
        else if (!isInternal && sentiment === 'positive') buckets.externalPositive.push(item);
        else buckets.externalNegative.push(item);
    });

    const sections = [
        { key: 'internalPositive', label: '京内正面', category: 'internal_positive' },
        { key: 'internalNegative', label: '京内负面', category: 'internal_negative' },
        { key: 'externalPositive', label: '京外正面', category: 'external_positive' },
        { key: 'externalNegative', label: '京外负面', category: 'external_negative' },
    ];

    const html = sections.map(sec => {
        const list = buckets[sec.key] || [];
        if (!list.length) return '';
        return `
            <div class="filter-section">
                ${list.map(item => manualFilterUi.renderFilterArticleCard(item, { showStatus: true, collapsed: false })).join('')}
            </div>
        `;
    }).filter(Boolean).join('');

    elements.filterList.innerHTML = html || '<div class="empty">无待处理文章</div>';
}

function renderClusteredList(clusters) {
    if (!clusters.length) {
        elements.filterList.innerHTML = '<div class="empty">无待处理文章</div>';
        return;
    }

    const buckets = {
        internalPositive: [],
        internalNegative: [],
        externalPositive: [],
        externalNegative: [],
    };

    clusters.forEach(cluster => {
        const items = cluster.items || [];
        if (!items.length) return;
        const first = items[0];
        const isInternal = !!first.is_beijing_related;
        const sentiment = (first.sentiment_label || '').toLowerCase() === 'negative' ? 'negative' : 'positive';

        if (isInternal && sentiment === 'positive') buckets.internalPositive.push(cluster);
        else if (isInternal && sentiment === 'negative') buckets.internalNegative.push(cluster);
        else if (!isInternal && sentiment === 'positive') buckets.externalPositive.push(cluster);
        else buckets.externalNegative.push(cluster);
    });

    const sections = [
        { key: 'internalPositive', label: '京内正面', category: 'internal_positive' },
        { key: 'internalNegative', label: '京内负面', category: 'internal_negative' },
        { key: 'externalPositive', label: '京外正面', category: 'external_positive' },
        { key: 'externalNegative', label: '京外负面', category: 'external_negative' },
    ];

    const html = sections.map(sec => {
        const clusterList = buckets[sec.key] || [];
        if (!clusterList.length) return '';

        const clustersHtml = clusterList.map(cluster => manualFilterUi.renderCluster(cluster)).join('');

        return `
            <div class="filter-section">
                ${clustersHtml}
            </div>
        `;
    }).join('');

    elements.filterList.innerHTML = html || '<div class="empty">无待处理文章</div>';

    // Bind Toggle Events
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
        if (target instanceof HTMLTextAreaElement && target.classList.contains('summary-box')) {
            handleFilterEditChange(target);
            return;
        }
        if (target instanceof HTMLInputElement && target.classList.contains('source-box')) {
            handleFilterEditChange(target);
            return;
        }
        if (!(target instanceof HTMLInputElement) || target.type !== 'radio') return;

        if (target.name.startsWith('cluster-')) {
            handleClusterDecisionChange(target);
        } else if (target.name.startsWith('status-')) {
            handleCardDecisionChange(target);
        }
    });
}

async function handleFilterEditChange(target) {
    const card = target.closest('.article-card');
    if (!card) return;
    const edits = {};
    collectCardEdits(card, edits);
    try {
        await persistEdits(edits);
        showToast('已保存');
    } catch (err) {
        showToast('保存失败', 'error');
    }
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

        // Undo Logic
        const undoAction = {
            icon: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
  <path d="M9 10h7a4 4 0 0 1 0 8h-1" />
  <path d="M12 7l-3 3 3 3" />
</svg>`,
            title: '撤销操作',
            callback: async () => {
                try {
                    // Revert to pending
                    await submitDecisions([id], 'pending');
                    showToast('已撤销操作');
                    // Reload data to show the item again
                    await loadFilterData();
                    loadStats();
                } catch (e) {
                    showToast('撤销失败', 'error');
                }
            }
        };

        showToast('已更新并移除', 'success', undoAction);
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

        // Undo Logic
        const undoAction = {
            icon: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
  <path d="M9 10h7a4 4 0 0 1 0 8h-1" />
  <path d="M12 7l-3 3 3 3" />
</svg>`,
            title: '撤销操作',
            callback: async () => {
                try {
                    await submitDecisions(ids, 'pending');
                    showToast('已撤销操作');
                    await loadFilterData();
                    loadStats();
                } catch (e) {
                    showToast('撤销失败', 'error');
                }
            }
        };

        showToast('已更新并移除', 'success', undoAction);
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
    await manualFilterApi.postEdits(edits, state.actor);
}

async function submitDecisions(ids, status) {
    await manualFilterApi.postDecisions(ids, status, state.actor);
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

        // Undo Logic
        const undoAction = {
            icon: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
  <path d="M9 10h7a4 4 0 0 1 0 8h-1" />
  <path d="M12 7l-3 3 3 3" />
</svg>`,
            title: '撤销操作',
            callback: async () => {
                try {
                    await submitDecisions(ids, 'pending');
                    showToast('已撤销操作');
                    await loadFilterData();
                    loadStats();
                } catch (e) {
                    showToast('撤销失败', 'error');
                }
            }
        };

        showToast(`已放弃${ids.length}条`, 'success', undoAction);
    } catch (e) {
        showToast('批量放弃失败', 'error');
    }
}