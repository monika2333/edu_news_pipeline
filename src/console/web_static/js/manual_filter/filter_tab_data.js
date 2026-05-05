// Manual Filter JS - Filter Tab Data

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
