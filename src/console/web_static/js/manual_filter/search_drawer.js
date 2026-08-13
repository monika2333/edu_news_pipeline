// Manual Filter JS - Search Drawer
// 抽屉本体、tab 切换、检索请求与结果卡片骨架。
// 抽屉分两个互斥 tab：「报送存档检索」（默认，更常用）与「全库文章检索」。
// A 块（链上归因）渲染在 search_drawer_attribution.js，
// B 块（报送存档命中）在 search_drawer_archive.js，两者均由 _search_drawer.html 引入。

// 与后端 articles_service 的默认/最大检索窗口保持一致，仅用于呈现与扩大范围。
const SEARCH_DEFAULT_LOOKBACK_DAYS = 30;
const SEARCH_MAX_LOOKBACK_DAYS = 3650;

// 全库检索每页条数固定，不再提供「每页条数」选项。
const SEARCH_PAGE_LIMIT = 10;

// 当前检索 tab 记入 localStorage，重开抽屉时恢复；首次默认报送存档检索。
const SEARCH_TAB_STORAGE_KEY = 'search_drawer_tab';
const SEARCH_TAB_DEFAULT = 'archive';

let searchState = {
    cursor: null,
    nextCursor: null,
    // null = 使用后端默认窗口；从空态「扩大时间范围重搜」后显式带上
    lookbackDays: null
};

function searchDrawerFetch(url) {
    const fetchImpl = typeof workspaceFetch === 'function'
        ? workspaceFetch
        : window.fetch.bind(window);
    return fetchImpl(url);
}

// tab 切换：报送存档检索（archive）/ 全库文章检索（articles）。
// search_drawer_archive.js 的「查报送存档」也会调用它切到存档 tab。
function switchSearchTab(tab) {
    const drawer = document.getElementById('search-drawer');
    if (!drawer) return;
    const target = tab === 'articles' ? 'articles' : 'archive';
    drawer.querySelectorAll('.search-tab').forEach(btn => {
        const active = btn.dataset.searchTab === target;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-selected', String(active));
    });
    drawer.querySelectorAll('.search-panel').forEach(panel => {
        panel.hidden = panel.dataset.searchPanel !== target;
    });
    localStorage.setItem(SEARCH_TAB_STORAGE_KEY, target);
}

function setupSearchDrawer() {
    const toggleBtn = document.getElementById('search-drawer-toggle');
    const closeBtn = document.getElementById('search-drawer-close');
    const overlay = document.getElementById('search-overlay');
    const drawer = document.getElementById('search-drawer');
    const searchBtn = document.getElementById('btn-drawer-search');
    const clearBtn = document.getElementById('btn-drawer-clear');
    const queryInput = document.getElementById('search-q');

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

    drawer.querySelectorAll('.search-tab').forEach(btn => {
        btn.addEventListener('click', () => switchSearchTab(btn.dataset.searchTab));
    });
    switchSearchTab(localStorage.getItem(SEARCH_TAB_STORAGE_KEY) || SEARCH_TAB_DEFAULT);

    // 新关键词 = 新一轮检索，窗口回到后端默认值；
    // 扩大窗口只对当前这轮检索（含翻页）生效。
    function startNewSearch() {
        searchState.cursor = null;
        searchState.nextCursor = null;
        searchState.lookbackDays = null;
        performDrawerSearch();
    }

    if (searchBtn) {
        searchBtn.addEventListener('click', startNewSearch);
    }

    if (clearBtn) {
        clearBtn.addEventListener('click', clearDrawerSearch);
    }

    if (queryInput) {
        queryInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') startNewSearch();
        });
    }

    if (typeof setupArchiveSearch === 'function') {
        setupArchiveSearch();
    }

    loadSearchFilters();
}

function loadSearchFilters() {
    try {
        const saved = JSON.parse(localStorage.getItem('search_filters') || '{}');
        if (saved && saved.q) {
            const qInput = document.getElementById('search-q');
            if (qInput) qInput.value = saved.q;
        }
    } catch (e) {
        console.error('加载搜索筛选条件失败', e);
    }
}

function saveSearchFilters() {
    const qInput = document.getElementById('search-q');
    const filters = {
        q: qInput ? qInput.value.trim() : ''
    };
    localStorage.setItem('search_filters', JSON.stringify(filters));
    return filters;
}

// 清空全库检索：输入框、结果、统计、分页与状态一并重置，
// 并清掉 localStorage 中保存的关键词，否则刷新后会被回填。
function clearDrawerSearch() {
    const qInput = document.getElementById('search-q');
    if (qInput) qInput.value = '';
    searchState.cursor = null;
    searchState.nextCursor = null;
    searchState.lookbackDays = null;
    localStorage.removeItem('search_filters');

    const container = document.getElementById('search-results-list');
    const statsInfo = document.getElementById('search-results-stats');
    const pagination = document.getElementById('search-pagination');
    if (container) clearEl(container);
    if (statsInfo) clearEl(statsInfo);
    if (pagination) clearEl(pagination);
}

async function performDrawerSearch() {
    const container = document.getElementById('search-results-list');
    const statsInfo = document.getElementById('search-results-stats');
    const pagination = document.getElementById('search-pagination');

    if (!container || !statsInfo || !pagination) return;

    container.innerHTML = renderSkeleton(3);
    clearEl(statsInfo);
    pagination.innerHTML = '';

    const filters = saveSearchFilters();

    if (!filters.q) {
        clearEl(statsInfo);
        clearEl(pagination);
        clearEl(container);
        container.appendChild(createEl('div', 'error', '请输入检索词'));
        const queryInput = document.getElementById('search-q');
        if (queryInput) queryInput.focus();
        return;
    }

    const params = new URLSearchParams({
        limit: SEARCH_PAGE_LIMIT.toString()
    });

    params.set('q', filters.q);
    if (searchState.cursor) params.set('cursor', searchState.cursor);
    if (searchState.lookbackDays) params.set('lookback_days', String(searchState.lookbackDays));

    try {
        const res = await searchDrawerFetch(`/api/articles/search?${params.toString()}`);
        if (!res.ok) throw new Error('检索失败');
        const data = await res.json();
        renderDrawerSearchResults(data);
    } catch (e) {
        container.innerHTML = `<div class="error">检索失败：${e.message}</div>`;
    }
}

// 空态「扩大时间范围重搜」：窗口翻倍（不超过后端上限），重搜后窗口信息随响应更新。
function expandSearchWindow() {
    const current = searchState.lookbackDays || SEARCH_DEFAULT_LOOKBACK_DAYS;
    const next = Math.min(current * 2, SEARCH_MAX_LOOKBACK_DAYS);
    if (next <= current) return;
    searchState.lookbackDays = next;
    searchState.cursor = null;
    searchState.nextCursor = null;
    performDrawerSearch();
}

function buildSearchResultItem(item, summaryToggles) {
    const itemEl = createEl('div', 'search-item', '', {
        dataset: { articleId: item.article_id || '' }
    });
    const attribution = item.attribution || null;

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

    // 命中的是被合并的重复报道时标明是哪一篇，避免使用者以为搜错了。
    if (attribution && attribution.matched_article_title) {
        itemEl.appendChild(createEl(
            'div',
            'search-matched-note',
            `命中稿：《${attribution.matched_article_title}》（与本篇为同一事件的合并报道）`,
            { dataset: { matchedArticleTitle: attribution.matched_article_title } }
        ));
    }

    if (typeof renderAttributionChain === 'function') {
        itemEl.appendChild(renderAttributionChain(attribution));
    }

    const meta = createEl('div', 'search-meta');
    meta.appendChild(createEl('span', '', item.source || '-'));
    // 时间口径统一为「收录时间」，不显示发布时间；
    // 与内容抽屉一样走 formatLocalDateTime（UTC → 本地时区）。
    const ingestedSource = attribution ? attribution.ingested_at_source : '';
    const ingestedSpan = createEl('span', 'search-ingested-time', '', {
        dataset: { ingestedSource: ingestedSource || '' }
    });
    ingestedSpan.appendChild(document.createTextNode(
        `收录时间：${formatLocalDateTime(attribution && attribution.ingested_at)}`
    ));
    if (ingestedSource === 'raw_articles.fetched_at') {
        ingestedSpan.appendChild(createEl('span', 'ingested-source-badge', '仅抓取未入库', {
            title: '该时间为抓取时间（raw_articles.fetched_at），文章尚未进入摘要环节'
        }));
    }
    meta.appendChild(ingestedSpan);
    meta.appendChild(createEl('span', `badge ${getSentimentClass(item.sentiment_label)}`, item.sentiment_label || '-'));
    itemEl.appendChild(meta);

    const actions = createEl('div', 'search-item-actions');
    let attributionDetails = null;
    if (typeof renderAttributionDetails === 'function') {
        attributionDetails = renderAttributionDetails(attribution);
        const detailToggle = createEl('button', 'search-attribution-toggle', '归因详情 ▾', {
            type: 'button',
            'aria-expanded': 'false'
        });
        detailToggle.addEventListener('click', () => {
            const expanded = attributionDetails.hidden;
            attributionDetails.hidden = !expanded;
            detailToggle.textContent = expanded ? '归因详情 ▴' : '归因详情 ▾';
            detailToggle.setAttribute('aria-expanded', String(expanded));
        });
        actions.appendChild(detailToggle);
    }
    const archiveBtn = createEl('button', 'search-archive-jump', '查报送存档', {
        type: 'button',
        title: '以本篇标题检索报送存档，检索词可再修改'
    });
    archiveBtn.addEventListener('click', () => {
        if (typeof searchArchiveByTitle === 'function') {
            searchArchiveByTitle(item.title || '');
        }
    });
    actions.appendChild(archiveBtn);
    itemEl.appendChild(actions);
    if (attributionDetails) itemEl.appendChild(attributionDetails);

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

    return itemEl;
}

function renderDrawerSearchResults(data) {
    const container = document.getElementById('search-results-list');
    const statsInfo = document.getElementById('search-results-stats');
    const pagination = document.getElementById('search-pagination');

    if (!container || !statsInfo || !pagination) return;

    const items = data.items || [];
    const hasMore = Boolean(data.has_more);
    searchState.nextCursor = data.next_cursor || null;
    searchState.lookbackDays = data.lookback_days || searchState.lookbackDays;

    clearEl(statsInfo);
    clearEl(container);

    // 空结果就是结论：写明检索窗口，并给出扩大范围重搜的入口。
    if (!items.length) {
        clearEl(pagination);
        if (typeof renderSearchEmptyState === 'function') {
            container.appendChild(renderSearchEmptyState(data));
        } else {
            container.appendChild(createEl('div', 'empty', '未找到结果'));
        }
        return;
    }

    statsInfo.appendChild(createEl(
        'span',
        'search-total',
        hasMore ? `本次显示 ${items.length} 条，仍有更多结果` : `本次显示 ${items.length} 条`
    ));
    if (typeof renderSearchWindowInfo === 'function') {
        statsInfo.appendChild(renderSearchWindowInfo(data));
    }

    const fragment = document.createDocumentFragment();
    const summaryToggles = [];

    items.forEach(item => {
        fragment.appendChild(buildSearchResultItem(item, summaryToggles));
    });

    container.appendChild(fragment);
    requestAnimationFrame(() => {
        summaryToggles.forEach(({ summary, toggle }) => {
            const truncated = summary.scrollHeight > summary.clientHeight + 1;
            toggle.hidden = !truncated;
        });
    });
    clearEl(pagination);

    const nextBtn = createEl('button', 'btn btn-secondary btn-sm', '下一页');
    if (hasMore && searchState.nextCursor) {
        nextBtn.addEventListener('click', loadNextSearchPage);
    } else {
        nextBtn.disabled = true;
    }

    pagination.appendChild(nextBtn);
}

function loadNextSearchPage() {
    if (!searchState.nextCursor) return;
    searchState.cursor = searchState.nextCursor;
    performDrawerSearch();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupSearchDrawer, { once: true });
} else {
    setupSearchDrawer();
}
