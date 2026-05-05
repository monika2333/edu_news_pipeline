// Manual Filter JS - Filter Tab

function getCurrentFilterBucket() {
    const cat = state.filterCategory || 'internal_positive';
    const region = cat.startsWith('external') ? 'external' : 'internal';
    const sentiment = cat.endsWith('negative') ? 'negative' : 'positive';
    return { cat, region, sentiment };
}

function isFilterSearchMode() {
    return Boolean(state.filterQuery || state.filterPublishedBefore || state.filterViewMode === 'search');
}

function syncFilterToolbarState() {
    if (elements.filterSearchInput) elements.filterSearchInput.value = state.filterQuery || '';
    if (elements.filterDateBefore) elements.filterDateBefore.value = state.filterPublishedBefore || '';
    if (!elements.filterSearchMeta) return;

    const bucketTotal = state.filterCounts[state.filterCategory || 'internal_positive'] || 0;
    if (isFilterSearchMode()) {
        elements.filterSearchMeta.textContent = `Matched ${state.filterSearchTotal} items in current bucket. Bucket total: ${bucketTotal}.`;
        return;
    }
    elements.filterSearchMeta.textContent = `Bucket total: ${bucketTotal}.`;
}

async function loadFilterData(options = {}) {
    const forceClusterRefresh = Boolean(options.forceClusterRefresh) || shouldForceClusterRefresh;
    shouldForceClusterRefresh = false;
    syncFilterToolbarState();
    elements.filterList.innerHTML = renderSkeleton(3);

    try {
        const searchMode = isFilterSearchMode();
        const { cat, region, sentiment } = getCurrentFilterBucket();
        const params = new URLSearchParams({
            limit: '10',
            offset: `${(state.filterPage - 1) * 10}`,
            cluster: searchMode ? 'false' : 'true',
            region,
            sentiment,
        });
        if (searchMode) {
            params.set('view_mode', 'search');
            if (state.filterQuery) params.set('q', state.filterQuery);
            if (state.filterPublishedBefore) params.set('published_before', state.filterPublishedBefore);
        }
        if (forceClusterRefresh) params.set('force_refresh', 'true');

        const res = await fetch(`${API_BASE}/candidates?${params.toString()}`);
        if (!res.ok) throw new Error('failed to load candidates');
        const data = await res.json();

        state.filterViewMode = data.view_mode || (searchMode ? 'search' : 'browse');
        state.filterSearchTotal = searchMode ? (data.total || 0) : 0;

        renderFilterList(data);
        updatePagination('filter', data.total || 0, state.filterPage);
        if (!searchMode) {
            state.filterCounts[cat] = data.total || 0;
            updateFilterCountsUI();
        }
        syncFilterToolbarState();
    } catch (error) {
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
                    cluster: 'false',
                });
                if (cat.startsWith('internal')) params.set('region', 'internal');
                if (cat.startsWith('external')) params.set('region', 'external');
                if (cat.endsWith('positive')) params.set('sentiment', 'positive');
                if (cat.endsWith('negative')) params.set('sentiment', 'negative');

                const res = await fetch(`${API_BASE}/candidates?${params.toString()}`);
                if (!res.ok) throw new Error('failed to load counts');
                const data = await res.json();
                state.filterCounts[cat] = data.total || 0;
            })
        );
        updateFilterCountsUI();
        syncFilterToolbarState();
    } catch (error) {
        // Keep previous counts on failure.
    }
}

function renderFilterList(data) {
    const items = data.items || [];
    if (data.clusters && Array.isArray(data.clusters) && data.clusters.length) {
        renderClusteredList(data.clusters);
        return;
    }
    if (!items.length) {
        const message = isFilterSearchMode() ? 'No matched articles in current bucket' : 'No pending articles';
        elements.filterList.innerHTML = `<div class="empty">${message}</div>`;
        return;
    }

    elements.filterList.innerHTML = items
        .map((item) => renderArticleCard(item, { showStatus: true, collapsed: false }))
        .join('');
}

function renderClusteredList(clusters) {
    if (!clusters.length) {
        elements.filterList.innerHTML = '<div class="empty">No pending articles</div>';
        return;
    }

    const clustersHtml = clusters
        .map((cluster) => {
            const items = cluster.items || [];
            if (!items.length) return '';
            const size = items.length;
            const clusterStatus = cluster.status || 'pending';

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
                    <label for="cluster-sel-${cluster.cluster_id}" class="radio-label">Adopt</label>
                </div>
                <div class="radio-option">
                    <input type="radio" name="cluster-${cluster.cluster_id}" value="backup" id="cluster-bak-${cluster.cluster_id}" ${clusterStatus === 'backup' ? 'checked' : ''}>
                    <label for="cluster-bak-${cluster.cluster_id}" class="radio-label">Backup</label>
                </div>
                <div class="radio-option">
                    <input type="radio" name="cluster-${cluster.cluster_id}" value="discarded" id="cluster-dis-${cluster.cluster_id}" ${clusterStatus === 'discarded' ? 'checked' : ''}>
                    <label for="cluster-dis-${cluster.cluster_id}" class="radio-label">Discard</label>
                </div>
            </div>
        </div>
        <div class="cluster-items">
            ${renderArticleCard(first, { showStatus: false, collapsed: false })}
            ${rest.map((item) => renderArticleCard(item, { showStatus: false, collapsed: true })).join('')}
        </div>
        ${hiddenCount ? `<div class="cluster-toggle-row"><button type="button" class="btn btn-link cluster-toggle" data-target="${cluster.cluster_id}">Show ${hiddenCount} more</button></div>` : ''}
    </div>
`;
        })
        .filter(Boolean)
        .join('');

    elements.filterList.innerHTML = clustersHtml || '<div class="empty">No pending articles</div>';

    elements.filterList.querySelectorAll('.cluster-toggle').forEach((btn) => {
        btn.addEventListener('click', () => {
            const target = btn.dataset.target;
            const container = elements.filterList.querySelector(`[data-cluster-id="${target}"]`);
            if (!container) return;
            const hiddenCards = container.querySelectorAll('.article-card.collapsed');
            const isHidden = hiddenCards.length ? hiddenCards[0].style.display === 'none' : true;
            hiddenCards.forEach((card) => {
                card.style.display = isHidden ? '' : 'none';
            });
            btn.textContent = isHidden ? `Hide ${hiddenCards.length}` : `Show ${hiddenCards.length} more`;
        });
    });
}

function setupFilterRealtimeDecisionHandlers() {
    if (!elements.filterList) return;
    elements.filterList.addEventListener('change', (event) => {
        const target = event.target;
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
        showToast('Saved');
    } catch (error) {
        showToast('Save failed', 'error');
    }
}

async function handleCardDecisionChange(input) {
    const card = input.closest('.article-card');
    if (!card) return;

    const articleId = card.dataset.id;
    const status = input.value;
    const previousStatus = card.dataset.status || 'pending';
    if (!articleId || status === previousStatus) return;

    const radios = card.querySelectorAll('input[type="radio"][name^="status-"]');
    setInputsDisabled(radios, true);

    const edits = {};
    collectCardEdits(card, edits);

    try {
        await persistEdits(edits);
        await submitDecisions([articleId], status);
        removeCardAndMaybeCluster(card);
        loadStats();

        const undoAction = {
            text: 'Undo',
            callback: async () => {
                try {
                    await submitDecisions([articleId], 'pending');
                    showToast('Undone');
                    await loadFilterData();
                    loadStats();
                } catch (error) {
                    showToast('Undo failed', 'error');
                }
            }
        };

        showToast('Updated', 'success', undoAction);
    } catch (error) {
        revertRadioSelection(radios, previousStatus);
        card.dataset.status = previousStatus;
        showToast('Update failed', 'error');
    } finally {
        if (card.isConnected) setInputsDisabled(radios, false);
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
    cards.forEach((card) => {
        const articleId = card.dataset.id;
        if (!articleId) return;
        ids.push(articleId);
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

        const undoAction = {
            text: 'Undo',
            callback: async () => {
                try {
                    await submitDecisions(ids, 'pending');
                    showToast('Undone');
                    await loadFilterData();
                    loadStats();
                } catch (error) {
                    showToast('Undo failed', 'error');
                }
            }
        };

        showToast('Updated', 'success', undoAction);
    } catch (error) {
        revertRadioSelection(radios, previousStatus);
        cluster.dataset.status = previousStatus;
        showToast('Update failed', 'error');
    } finally {
        if (cluster.isConnected) setInputsDisabled(radios, false);
    }
}

function collectCardEdits(card, edits) {
    const articleId = card.dataset.id;
    if (!articleId) return;
    const summaryBox = card.querySelector('.summary-box');
    const sourceBox = card.querySelector('.source-box');
    const summary = summaryBox ? summaryBox.value : '';
    const llm_source = sourceBox ? sourceBox.value : '';
    edits[articleId] = { summary, llm_source };
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
        pending_ids: status === 'pending' ? ids : [],
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
    nodes.forEach((node) => {
        node.disabled = disabled;
    });
}

function revertRadioSelection(radios, status) {
    radios.forEach((radio) => {
        radio.checked = radio.value === status;
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
    cards.forEach((card) => {
        const cluster = card.closest('.filter-cluster');
        if (cluster) clusters.add(cluster);
        card.remove();
    });
    clusters.forEach((cluster) => {
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
        showToast('No visible articles to discard');
        return;
    }

    const edits = {};
    const ids = [];
    cards.forEach((card) => {
        const articleId = card.dataset.id;
        if (!articleId) return;
        ids.push(articleId);
        collectCardEdits(card, edits);
    });

    if (!ids.length) {
        showToast('No visible articles to discard');
        return;
    }

    try {
        await persistEdits(edits);
        await submitDecisions(ids, 'discarded');
        removeCardsAndClusters(cards);
        loadStats();

        const undoAction = {
            text: 'Undo',
            callback: async () => {
                try {
                    await submitDecisions(ids, 'pending');
                    showToast('Undone');
                    await loadFilterData();
                    loadStats();
                } catch (error) {
                    showToast('Undo failed', 'error');
                }
            }
        };

        showToast(`Discarded ${ids.length} articles`, 'success', undoAction);
    } catch (error) {
        showToast('Bulk discard failed', 'error');
    }
}

async function applyFilterSearch() {
    state.filterQuery = elements.filterSearchInput ? elements.filterSearchInput.value.trim() : '';
    state.filterPublishedBefore = elements.filterDateBefore ? elements.filterDateBefore.value : '';
    state.filterViewMode = (state.filterQuery || state.filterPublishedBefore) ? 'search' : 'browse';
    state.filterSearchTotal = 0;
    state.filterPage = 1;
    syncFilterToolbarState();
    await loadFilterData();
}

async function clearFilterSearch() {
    state.filterQuery = '';
    state.filterPublishedBefore = '';
    state.filterViewMode = 'browse';
    state.filterSearchTotal = 0;
    state.filterPage = 1;
    syncFilterToolbarState();
    await loadFilterData();
}

async function discardBeforeDate() {
    const publishedBefore = elements.filterDateBefore ? elements.filterDateBefore.value : '';
    const { region, sentiment } = getCurrentFilterBucket();
    const query = state.filterQuery || (elements.filterSearchInput ? elements.filterSearchInput.value.trim() : '');
    try {
        const previewRes = await fetch(`${API_BASE}/discard_before_date`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                region,
                sentiment,
                q: query || null,
                published_before: publishedBefore || null,
                actor: state.actor,
                dry_run: true
            })
        });
        if (!previewRes.ok) throw new Error('failed preview');
        const preview = await previewRes.json();
        if (!preview.matched) {
            showToast('No matched articles for current filters');
            return;
        }

        const filterSummary = [];
        if (query) filterSummary.push(`keyword "${query}"`);
        if (publishedBefore) filterSummary.push(`published before ${publishedBefore}`);
        const summaryText = filterSummary.length ? filterSummary.join(' and ') : 'the current bucket';
        const confirmed = window.confirm(`Discard ${preview.matched} pending articles matching ${summaryText}?`);
        if (!confirmed) return;

        const applyRes = await fetch(`${API_BASE}/discard_before_date`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                region,
                sentiment,
                q: query || null,
                published_before: publishedBefore || null,
                actor: state.actor,
                dry_run: false
            })
        });
        if (!applyRes.ok) throw new Error('failed apply');
        const result = await applyRes.json();
        showToast(`Discarded ${result.updated} articles`);
        state.filterPage = 1;
        await loadFilterCounts();
        await loadFilterData({ forceClusterRefresh: true });
        loadStats();
    } catch (error) {
        showToast('Discard before date failed', 'error');
    }
}
