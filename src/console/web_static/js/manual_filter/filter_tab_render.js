// Manual Filter JS - Filter Tab Render

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
        elements.filterSearchMeta.textContent = `当前桶命中 ${state.filterSearchTotal} 条，总数 ${bucketTotal} 条。`;
        return;
    }
    elements.filterSearchMeta.textContent = `当前桶共 ${bucketTotal} 条。`;
}

function renderFilterList(data) {
    const items = data.items || [];
    if (data.clusters && Array.isArray(data.clusters) && data.clusters.length) {
        renderClusteredList(data.clusters);
        return;
    }
    if (!items.length) {
        const message = isFilterSearchMode() ? '当前桶内没有匹配到新闻' : '当前没有待处理新闻';
        elements.filterList.innerHTML = `<div class="empty">${message}</div>`;
        return;
    }

    elements.filterList.innerHTML = items
        .map((item) => renderArticleCard(item, { showStatus: true, collapsed: false }))
        .join('');
}

function renderClusteredList(clusters) {
    if (!clusters.length) {
        elements.filterList.innerHTML = '<div class="empty">当前没有待处理新闻</div>';
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
            ${rest.map((item) => renderArticleCard(item, { showStatus: false, collapsed: true })).join('')}
        </div>
        ${hiddenCount ? `<div class="cluster-toggle-row"><button type="button" class="btn btn-link cluster-toggle" data-target="${cluster.cluster_id}">展开其余 ${hiddenCount} 条</button></div>` : ''}
    </div>
`;
        })
        .filter(Boolean)
        .join('');

    elements.filterList.innerHTML = clustersHtml || '<div class="empty">当前没有待处理新闻</div>';

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
            btn.textContent = isHidden ? `收起额外 ${hiddenCards.length} 条` : `展开其余 ${hiddenCards.length} 条`;
        });
    });
}
