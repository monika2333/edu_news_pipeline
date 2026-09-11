// Manual Filter JS - Filter Tab Data

// 最新收录时间与筛选列表无关，单独拉取；管理员看全库，值班编辑看当前班次。
// 失败时静默保留当前班次已有值，切换班次时由 workspace.js 主动清空。
async function loadLatestIngestStatus() {
    const requestBase = API_BASE;
    const endpoint = IS_DUTY_WORKSPACE
        ? `${requestBase}/ingest-status`
        : '/api/articles/ingest-status';
    try {
        const res = await fetch(endpoint);
        if (!res.ok) return;
        const data = await res.json();
        if (IS_DUTY_WORKSPACE && API_BASE !== requestBase) return;
        state.latestIngestedAt = data.latest_created_at || null;
        syncFilterToolbarState();
    } catch (error) {
        // Keep previous value on failure.
    }
}

// 最新请求获胜：只有最后发起的 loadFilterData 允许渲染，被取代的请求无论成败都静默丢弃
function isLatestFilterLoad(seq) {
    return seq === filterLoadSeq;
}

async function loadFilterData(options = {}) {
    const seq = ++filterLoadSeq;
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
        if (state.filterDutyScope === 'unprocessed') {
            params.set('duty_unprocessed_only', 'true');
        }

        const res = await workspaceFetch(`${API_BASE}/candidates?${params.toString()}`);
        if (!res.ok) throw new Error('failed to load candidates');
        const data = await res.json();
        if (!isLatestFilterLoad(seq)) return false;

        state.filterViewMode = data.view_mode || (searchMode ? 'search' : 'browse');
        state.filterSearchTotal = searchMode ? (data.total || 0) : 0;

        renderFilterList(data);
        captureFilterEditBaselines();
        updatePagination('filter', data.total || 0, state.filterPage, data.limit);
        if (!searchMode) {
            const bucketTotal = typeof data.item_total === 'number' ? data.item_total : data.total;
            state.filterCounts[cat] = bucketTotal || 0;
            updateFilterCountsUI();
        }
        syncFilterToolbarState();
        return true;
    } catch (error) {
        if (!isLatestFilterLoad(seq)) return false;
        elements.filterList.innerHTML = '<div class="error">加载数据失败</div>';
        return false;
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
                // 侧栏计数必须与列表同口径，否则「放弃全部 N 条」的 N 会大于实际弃用范围
                if (state.filterDutyScope === 'unprocessed') {
                    params.set('duty_unprocessed_only', 'true');
                }

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
    markFilterEditBaselinesSaved(edits);
}

// 渲染后把编辑框实际值记为基准：初始基准 = 最近一次被服务端确认的值
function captureFilterEditBaselines() {
    if (!elements.filterList) return;
    elements.filterList.querySelectorAll('.article-card[data-id]').forEach((card) => {
        filterEditBaselines.set(card.dataset.id, readCardEditValues(card));
    });
}

// 保存成功后推进基准；保存失败不调用，卡片保持「改过」等下一次决定流程重试
function markFilterEditBaselinesSaved(edits) {
    Object.entries(edits || {}).forEach(([articleId, value]) => {
        filterEditBaselines.set(articleId, {
            summary: value.summary,
            llm_source: value.llm_source
        });
    });
}

// reportType 缺省用「采纳/备选归入」报别（dock）；右键快捷菜单可覆盖为另一报别，不改变归入报别状态。
async function submitDecisions(ids, status, versions = null, reportType = null) {
    const payload = {
        selected_ids: status === 'selected' ? ids : [],
        backup_ids: status === 'backup' ? ids : [],
        discarded_ids: status === 'discarded' ? ids : [],
        pending_ids: status === 'pending' ? ids : [],
        versions: versions || collectManualReviewVersions(ids),
        report_type: reportType || state.filterAssignReportType
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
