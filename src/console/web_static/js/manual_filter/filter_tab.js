// Manual Filter JS - Filter Tab

// --- Filter Tab Logic ---

async function loadFilterData(options = {}) {
    const forceClusterRefresh = Boolean(options.forceClusterRefresh) || shouldForceClusterRefresh;
    shouldForceClusterRefresh = false;
    elements.filterList.innerHTML = renderSkeleton(3);
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
        elements.filterList.innerHTML = '<div class="error">加载数据失败</div>';
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
        elements.filterList.innerHTML = '<div class="empty">无待处理文章</div>';
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
        { key: 'internalPositive', label: '京内正面', category: 'internal_positive' },
        { key: 'internalNegative', label: '京内负面', category: 'internal_negative' },
        { key: 'externalPositive', label: '京外正面', category: 'external_positive' },
        { key: 'externalNegative', label: '京外负面', category: 'external_negative' },
    ];

    elements.filterList.innerHTML = sections.map(sec => {
        const list = buckets[sec.key] || [];
        if (!list.length) return '';
        return `
    <div class="filter-section">
        ${list.map(item => renderArticleCard(item, { showStatus: true, collapsed: false })).join('')}
            </div>
    `;
    }).filter(Boolean).join('') || '<div class="empty">无待处理文章</div>';
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

    elements.filterList.innerHTML = sections.map(sec => {
        const clusterList = buckets[sec.key] || [];
        if (!clusterList.length) return '';
        const count = state.filterCounts[sec.category] || 0;

        const clustersHtml = clusterList.map(cluster => {
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
        <div class="cluster-items">
            ${renderArticleCard(first, { showStatus: false, collapsed: false })}
            ${rest.map(item => renderArticleCard(item, { showStatus: false, collapsed: true })).join('')}
        </div>
        ${hiddenCount ? `<div class="cluster-toggle-row"><button type="button" class="btn btn-link cluster-toggle" data-target="${cluster.cluster_id}">展开其余${hiddenCount}条</button></div>` : ''}
    </div>
`;
        }).join('');

        return `
    <div class="filter-section">
        ${clustersHtml}
    </div>
        `;
    }).join('') || '<div class="empty">无待处理文章</div>';

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