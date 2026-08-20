// Manual Filter JS - Filter Tab Data

// 全库最新收录时间，与筛选列表无关，单独拉取；失败时静默保留旧值。
// 走 /api/articles（登录即可访问），管理员页与值班页共用同一接口。
async function loadLatestIngestStatus() {
    try {
        const res = await fetch('/api/articles/ingest-status');
        if (!res.ok) return;
        const data = await res.json();
        state.latestIngestedAt = data.latest_created_at || null;
        syncFilterToolbarState();
    } catch (error) {
        // Keep previous value on failure.
    }
}

async function loadFilterData(options = {}) {
    const forceClusterRefresh = Boolean(options.forceClusterRefresh) || shouldForceClusterRefresh;
    shouldForceClusterRefresh = false;
    syncFilterToolbarState();
    elements.filterList.innerHTML = renderSkeleton(3);
    loadLatestIngestStatus();

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
        }
        if (forceClusterRefresh) params.set('force_refresh', 'true');

        const res = await workspaceFetch(`${API_BASE}/candidates?${params.toString()}`);
        if (!res.ok) throw new Error('failed to load candidates');
        const data = await res.json();

        state.filterViewMode = data.view_mode || (searchMode ? 'search' : 'browse');
        state.filterSearchTotal = searchMode ? (data.total || 0) : 0;

        renderFilterList(data);
        updatePagination('filter', data.total || 0, state.filterPage, data.limit);
        if (!searchMode) {
            const bucketTotal = typeof data.item_total === 'number' ? data.item_total : data.total;
            state.filterCounts[cat] = bucketTotal || 0;
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

                const res = await workspaceFetch(`${API_BASE}/candidates?${params.toString()}`);
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
    const articleIds = Object.keys(edits);
    const res = await workspaceFetch(`${API_BASE}/edit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            edits,
            versions: collectManualReviewVersions(articleIds)
        })
    });
    await requireManualMutationSuccess(res, '编辑保存失败，请重试');
}

// reportType 缺省用当前页面报别；右键快捷菜单可覆盖为另一报别，不改变页面报别状态。
async function submitDecisions(ids, status, versions = null, reportType = null) {
    const payload = {
        selected_ids: status === 'selected' ? ids : [],
        backup_ids: status === 'backup' ? ids : [],
        discarded_ids: status === 'discarded' ? ids : [],
        pending_ids: status === 'pending' ? ids : [],
        versions: versions || collectManualReviewVersions(ids),
        report_type: reportType || state.reviewReportType
    };

    const res = await workspaceFetch(`${API_BASE}/decide`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    return requireManualMutationSuccess(res, '状态保存失败，请重试');
}

async function applyFilterSearch() {
    state.filterQuery = elements.filterSearchInput ? elements.filterSearchInput.value.trim() : '';
    state.filterViewMode = state.filterQuery ? 'search' : 'browse';
    state.filterSearchTotal = 0;
    state.filterPage = 1;
    syncFilterToolbarState();
    await loadFilterData();
}

async function clearFilterSearch() {
    state.filterQuery = '';
    state.filterViewMode = 'browse';
    state.filterSearchTotal = 0;
    state.filterPage = 1;
    syncFilterToolbarState();
    await loadFilterData();
}
