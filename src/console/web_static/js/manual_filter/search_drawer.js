// Manual Filter JS - Search Drawer

const contentCache = new Map();

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
        console.error('Failed to load search filters', e);
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

async function fetchContent(articleId) {
    if (contentCache.has(articleId)) {
        return contentCache.get(articleId);
    }
    const res = await fetch(`/api/articles/${encodeURIComponent(articleId)}/content`);
    if (!res.ok) throw new Error('Content fetch failed');
    const data = await res.json();
    const content = data.content_markdown || '';
    contentCache.set(articleId, content);
    return content;
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
        const res = await fetch(`/api/articles/search?${params.toString()}`);
        if (!res.ok) throw new Error('Search failed');
        const data = await res.json();
        renderDrawerSearchResults(data);
    } catch (e) {
        container.innerHTML = `<div class="error">Search failed: ${e.message}</div>`;
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

    statsInfo.textContent = `Found ${total} results`;
    clearEl(container);

    if (!items.length) {
        container.appendChild(createEl('div', 'empty', '未找到结果'));
        return;
    }

    const fragment = document.createDocumentFragment();

    items.forEach(item => {
        const itemEl = createEl('div', 'search-item');

        const header = createEl('h4');
        const link = createEl('a', '', item.title || 'Untitled', {
            href: item.url || '#',
            target: '_blank',
            rel: 'noopener'
        });
        header.appendChild(link);
        itemEl.appendChild(header);

        const meta = createEl('div', 'search-meta');
        const sourceSpan = createEl('span', '', item.source || '-');
        const publishTime = item.publish_time_iso ? item.publish_time_iso.substring(0, 10) : (
            item.publish_time ? new Date(item.publish_time * 1000).toISOString().split('T')[0] : '-'
        );
        const timeSpan = createEl('span', '', publishTime);
        const sentimentSpan = createEl('span', `badge ${getSentimentClass(item.sentiment_label)}`, item.sentiment_label || '-');
        const statusSpan = createEl('span', '', `Status: ${item.status || '-'}`);

        meta.appendChild(sourceSpan);
        meta.appendChild(timeSpan);
        meta.appendChild(sentimentSpan);
        meta.appendChild(statusSpan);
        itemEl.appendChild(meta);

        const summary = createEl('div', 'search-summary', item.llm_summary || 'No summary available.');
        itemEl.appendChild(summary);

        const contentWrapper = createEl('div', 'search-content');
        const contentButton = createEl('button', 'btn btn-secondary btn-sm', 'Load content');
        const contentPre = createEl('pre', 'search-content-body');
        contentPre.style.display = 'none';

        contentButton.addEventListener('click', async () => {
            const isVisible = contentPre.style.display !== 'none';
            if (isVisible) {
                contentPre.style.display = 'none';
                contentButton.textContent = 'Load content';
                return;
            }
            contentButton.disabled = true;
            contentButton.textContent = 'Loading...';
            try {
                const content = await fetchContent(item.article_id);
                contentPre.textContent = content || 'No content available.';
                contentPre.style.display = 'block';
                contentButton.textContent = 'Hide content';
            } catch (err) {
                contentPre.textContent = 'Failed to load content.';
                contentPre.style.display = 'block';
                contentButton.textContent = 'Load content';
            } finally {
                contentButton.disabled = false;
            }
        });

        contentWrapper.appendChild(contentButton);
        contentWrapper.appendChild(contentPre);
        itemEl.appendChild(contentWrapper);

        fragment.appendChild(itemEl);
    });

    container.appendChild(fragment);
    clearEl(pagination);

    const prevBtn = createEl('button', 'btn btn-secondary btn-sm', 'Prev', {
        onclick: page > 1 ? () => changeSearchPage(page - 1) : undefined,
        disabled: page <= 1 ? 'disabled' : undefined
    });
    const pageInfo = createEl('span', '', `Page ${page} / ${pages}`, {
        style: { margin: '0 10px' }
    });
    const nextBtn = createEl('button', 'btn btn-secondary btn-sm', 'Next', {
        onclick: page < pages ? () => changeSearchPage(page + 1) : undefined,
        disabled: page >= pages ? 'disabled' : undefined
    });

    if (page <= 1) prevBtn.disabled = true;
    if (page >= pages) nextBtn.disabled = true;

    pagination.appendChild(prevBtn);
    pagination.appendChild(pageInfo);
    pagination.appendChild(nextBtn);
}

function changeSearchPage(newPage) {
    searchState.page = newPage;
    performDrawerSearch();
}
