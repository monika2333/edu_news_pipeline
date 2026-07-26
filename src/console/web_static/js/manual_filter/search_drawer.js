// Manual Filter JS - Search Drawer

let searchState = {
    page: 1,
    limit: 20,
    loading: false
};

function setupSearchDrawer() {
    const toggleBtn = document.getElementById('search-drawer-toggle');
    const closeBtn = document.getElementById('search-drawer-close');
    const overlay = document.getElementById('search-overlay');
    const drawer = document.getElementById('search-drawer');
    const searchBtn = document.getElementById('btn-drawer-search');
    const limitSelect = document.getElementById('search-limit');
    const inputs = document.querySelectorAll('.search-form-container input, .search-form-container select');

    if (!drawer) return;
    if (drawer.dataset.searchDrawerReady === 'true') return;
    drawer.dataset.searchDrawerReady = 'true';

    function toggleDrawer(show) {
        drawer.classList.toggle('active', show);
        overlay.classList.toggle('active', show);
        if (toggleBtn) {
            toggleBtn.style.display = show ? 'none' : 'flex';
        }
        localStorage.setItem('search_drawer_open', show);
    }

    if (toggleBtn) toggleBtn.addEventListener('click', () => toggleDrawer(true));
    if (closeBtn) closeBtn.addEventListener('click', () => toggleDrawer(false));
    if (overlay) overlay.addEventListener('click', () => toggleDrawer(false));
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && drawer.classList.contains('active')) {
            toggleDrawer(false);
        }
    });

    if (localStorage.getItem('search_drawer_open') === 'true') {
        toggleDrawer(true);
    }

    if (searchBtn) {
        searchBtn.addEventListener('click', () => {
            searchState.page = 1;
            performDrawerSearch();
        });
    }

    if (limitSelect) {
        limitSelect.addEventListener('change', () => {
            searchState.page = 1;
            performDrawerSearch();
        });
    }

    inputs.forEach(input => {
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                searchState.page = 1;
                performDrawerSearch();
            }
        });
    });

    loadSearchFilters();
}

function loadSearchFilters() {
    try {
        const saved = JSON.parse(localStorage.getItem('search_filters') || '{}');
        if (saved && saved.q) {
            const qInput = document.getElementById('search-q');
            if (qInput) qInput.value = saved.q;
        }
        if (saved && saved.limit) {
            const limitSelect = document.getElementById('search-limit');
            if (limitSelect) {
                limitSelect.value = String(saved.limit);
                searchState.limit = parseInt(limitSelect.value, 10) || 20;
            }
        }
    } catch (e) {
        console.error('加载搜索筛选条件失败', e);
    }
}

function saveSearchFilters() {
    const qInput = document.getElementById('search-q');
    const limitSelect = document.getElementById('search-limit');
    const filters = {
        q: qInput ? qInput.value : '',
        limit: limitSelect ? limitSelect.value : '20'
    };
    localStorage.setItem('search_filters', JSON.stringify(filters));
    return filters;
}

async function performDrawerSearch() {
    const container = document.getElementById('search-results-list');
    const statsInfo = document.getElementById('search-results-stats');
    const pagination = document.getElementById('search-pagination');

    if (!container || !statsInfo || !pagination) return;

    container.innerHTML = renderSkeleton(3);
    statsInfo.textContent = '';
    pagination.innerHTML = '';

    const filters = saveSearchFilters();
    searchState.limit = parseInt(filters.limit, 10) || 20;

    const params = new URLSearchParams({
        page: searchState.page.toString(),
        limit: searchState.limit.toString()
    });

    if (filters.q) params.set('q', filters.q);

    try {
        const searchFetch = typeof workspaceFetch === 'function'
            ? workspaceFetch
            : window.fetch.bind(window);
        const res = await searchFetch(`/api/articles/search?${params.toString()}`);
        if (!res.ok) throw new Error('检索失败');
        const data = await res.json();
        renderDrawerSearchResults(data);
    } catch (e) {
        container.innerHTML = `<div class="error">检索失败：${e.message}</div>`;
    }
}

function renderDrawerSearchResults(data) {
    const container = document.getElementById('search-results-list');
    const statsInfo = document.getElementById('search-results-stats');
    const pagination = document.getElementById('search-pagination');

    if (!container || !statsInfo || !pagination) return;

    const items = data.items || [];
    const total = data.total || 0;
    const page = data.page || 1;
    const pages = data.pages || 1;

    statsInfo.textContent = `共找到 ${total} 条结果`;
    clearEl(container);

    if (!items.length) {
        container.appendChild(createEl('div', 'empty', '未找到结果'));
        return;
    }

    const fragment = document.createDocumentFragment();
    const summaryToggles = [];

    items.forEach(item => {
        const itemEl = createEl('div', 'search-item');

        const header = createEl('h4');
        const title = item.title || '未命名标题';
        header.appendChild(document.createTextNode(title));
        if (item.url) {
            const link = createEl('a', 'search-source-link', '🔗', {
                href: item.url,
                target: '_blank',
                rel: 'noopener noreferrer',
                'aria-label': `打开《${title}》的原始链接`,
                title: '打开原始链接'
            });
            header.appendChild(link);
        }
        itemEl.appendChild(header);

        const meta = createEl('div', 'search-meta');
        const sourceSpan = createEl('span', '', item.source || '-');
        const publishTime = item.publish_time_iso ? item.publish_time_iso.substring(0, 10) : (
            item.publish_time ? new Date(item.publish_time * 1000).toISOString().split('T')[0] : '-'
        );
        const timeSpan = createEl('span', '', publishTime);
        const scoreSpan = createEl('span', '', `分数：${formatScore(item.external_importance_score)}`);
        const sentimentSpan = createEl('span', `badge ${getSentimentClass(item.sentiment_label)}`, item.sentiment_label || '-');
        const statusSpan = createEl('span', '', `状态：${item.status || '-'}`);

        meta.appendChild(sourceSpan);
        meta.appendChild(timeSpan);
        meta.appendChild(scoreSpan);
        meta.appendChild(sentimentSpan);
        meta.appendChild(statusSpan);
        itemEl.appendChild(meta);

        const details = createEl('div', 'search-details');
        const summaryText = String(item.llm_summary || '').trim();
        const summary = createEl(
            'div',
            'search-summary',
            summaryText || '暂无摘要。'
        );
        details.appendChild(summary);
        if (summaryText) {
            const toggle = createEl('button', 'search-summary-toggle', '▾', {
                type: 'button',
                'aria-expanded': 'false',
                'aria-label': '展开摘要',
                title: '展开摘要'
            });
            toggle.hidden = true;
            toggle.addEventListener('click', () => {
                const expanded = summary.classList.toggle('expanded');
                const actionLabel = expanded ? '收起摘要' : '展开摘要';
                toggle.textContent = expanded ? '▴' : '▾';
                toggle.setAttribute('aria-expanded', String(expanded));
                toggle.setAttribute('aria-label', actionLabel);
                toggle.setAttribute('title', actionLabel);
            });
            details.appendChild(toggle);
            summaryToggles.push({ summary, toggle });
        }
        itemEl.appendChild(details);

        fragment.appendChild(itemEl);
    });

    container.appendChild(fragment);
    requestAnimationFrame(() => {
        summaryToggles.forEach(({ summary, toggle }) => {
            const truncated = summary.scrollHeight > summary.clientHeight + 1;
            toggle.hidden = !truncated;
        });
    });
    clearEl(pagination);

    const prevBtn = createEl('button', 'btn btn-secondary btn-sm', '上一页');
    if (page > 1) {
        prevBtn.addEventListener('click', () => changeSearchPage(page - 1));
    } else {
        prevBtn.disabled = true;
    }
    const pageInfo = createEl('span', '', `第 ${page} 页 / 共 ${pages} 页`, {
        style: { margin: '0 10px' }
    });
    const nextBtn = createEl('button', 'btn btn-secondary btn-sm', '下一页');
    if (page < pages) {
        nextBtn.addEventListener('click', () => changeSearchPage(page + 1));
    } else {
        nextBtn.disabled = true;
    }

    pagination.appendChild(prevBtn);
    pagination.appendChild(pageInfo);
    pagination.appendChild(nextBtn);
}

function changeSearchPage(newPage) {
    searchState.page = newPage;
    performDrawerSearch();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupSearchDrawer, { once: true });
} else {
    setupSearchDrawer();
}
