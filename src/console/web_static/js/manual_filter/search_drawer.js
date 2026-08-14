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
        // body 类用于把原文抽屉抬到检索抽屉之上（见 content_drawer.css）
        document.body.classList.toggle('search-drawer-open', show);
        if (toggleBtn) {
            toggleBtn.style.display = show ? 'none' : 'flex';
        }
        localStorage.setItem('search_drawer_open', show);
    }

    if (toggleBtn) toggleBtn.addEventListener('click', () => toggleDrawer(true));
    if (closeBtn) closeBtn.addEventListener('click', () => toggleDrawer(false));
    if (overlay) {
        overlay.addEventListener('click', () => {
            // 原文抽屉盖在检索抽屉上时，点到的是遮罩但意图是关原文，不动检索抽屉
            if (document.body.classList.contains('content-drawer-open')) return;
            toggleDrawer(false);
        });
    }
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape' || !drawer.classList.contains('active')) return;
        // 原文抽屉还开着时 Escape 先关原文（由内容抽屉脚本处理），检索抽屉保持
        if (document.body.classList.contains('content-drawer-open')) return;
        // 评分反馈弹层开着时 Escape 先关弹层（由 score_feedback.js 处理）
        if (typeof activeScoreFeedbackControl !== 'undefined' && activeScoreFeedbackControl) return;
        toggleDrawer(false);
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

// 全库检索卡片的分类徽章：京内/京外 × 正面/负面。
// 与 duty_summary/utils.js 的 articleCategoryLabel 同一口径；
// 地域（geo-classify）或情感（summarize）任一判定未就绪时不归类，返回空串。
function searchCategoryLabel(item) {
    if (item.is_beijing_related === null || item.is_beijing_related === undefined) return '';
    if (!item.sentiment_label) return '';
    const region = item.is_beijing_related ? '京内' : '京外';
    const sentiment = String(item.sentiment_label).toLowerCase() === 'negative' ? '负面' : '正面';
    return `${region}${sentiment}`;
}

// 回链方式的中文文案；「有存档」口径在后端 SQL（仅 exact/fuzzy/manual），前端不重算。
const ARCHIVE_LINK_STATUS_LABELS = {
    exact: '精确回链',
    fuzzy: '模糊回链',
    manual: '人工回链'
};

// 「有存档」展开区：列出每个已确认回链的存档条目（报别/日期/回链方式/标题/正文）。
function buildArchiveLinksDetails(links) {
    const box = createEl('div', 'search-archive-details', '', { hidden: true });
    const typeLabels = typeof ARCHIVE_REPORT_TYPE_LABELS === 'object' && ARCHIVE_REPORT_TYPE_LABELS
        ? ARCHIVE_REPORT_TYPE_LABELS
        : {};
    links.forEach(link => {
        const entry = createEl('div', 'search-archive-entry');
        const head = createEl('div', 'archive-item-head');
        const reportType = link.report_type || '';
        head.appendChild(createEl(
            'span',
            'archive-item-badge',
            typeLabels[reportType] || reportType || '未知',
            { dataset: { reportType } }
        ));
        head.appendChild(createEl('span', 'archive-item-date', formatSearchDate(link.report_date) || '-'));
        head.appendChild(createEl(
            'span',
            'search-archive-link-status',
            ARCHIVE_LINK_STATUS_LABELS[link.link_status] || link.link_status || '-'
        ));
        entry.appendChild(head);
        entry.appendChild(createEl('div', 'search-archive-entry-title', link.title || '（无标题）'));
        // 存档正文是最终报送版，长度可控，直接显示全文，不做截断。
        const bodyText = String(link.body || '').trim();
        if (bodyText) {
            const bodyEl = createEl('div', 'archive-item-body', bodyText);
            const sourceText = String(link.source || '').trim();
            if (sourceText) {
                bodyEl.appendChild(createEl('span', 'archive-item-source', `（${sourceText}）`));
            }
            entry.appendChild(bodyEl);
        }
        box.appendChild(entry);
    });
    return box;
}

function buildSearchResultItem(item, summaryToggles) {
    const itemEl = createEl('div', 'search-item', '', {
        dataset: { articleId: item.article_id || '' }
    });
    const attribution = item.attribution || null;

    const header = createEl('h4');
    const title = item.title || '未命名标题';
    header.appendChild(document.createTextNode(title));
    // 「原文」按钮打开内容抽屉（content-drawer），复用筛选页逻辑：
    // 触发走各页面内容抽屉脚本的 .content-drawer-trigger 委托，
    // 外部原始链接在抽屉顶栏的「原文链接」里保留。
    // 内容抽屉直接盖在检索抽屉之上，关掉后检索结果还在。
    const contentBtn = createEl('button', 'content-drawer-trigger', '原文', {
        type: 'button',
        title: '查看原文',
        dataset: { articleId: item.article_id || '', bonusKeywords: '' }
    });
    header.appendChild(contentBtn);
    // 「有存档」徽章跟在「原文」后面：回链在存档流程中已确定，
    // 只有已确认回链（exact/fuzzy/manual）的文章才有，点开在卡片内看存档详情。
    const archiveLinks = Array.isArray(item.archive_links) ? item.archive_links : [];
    let archiveDetails = null;
    if (archiveLinks.length) {
        const archiveBadge = createEl(
            'button',
            'search-archive-badge',
            archiveLinks.length > 1 ? `有存档 ×${archiveLinks.length}` : '有存档',
            { type: 'button', 'aria-expanded': 'false', title: '查看报送存档详情' }
        );
        archiveDetails = buildArchiveLinksDetails(archiveLinks);
        archiveBadge.addEventListener('click', () => {
            const expanded = archiveDetails.hidden;
            archiveDetails.hidden = !expanded;
            archiveBadge.setAttribute('aria-expanded', String(expanded));
        });
        header.appendChild(archiveBadge);
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
    // 分类徽章取代原 sentiment 徽章：京内/京外 × 正面/负面。
    // 两个判定（地域、情感）都就绪才显示，没走到分类环节的文章不显示。
    const categoryText = searchCategoryLabel(item);
    if (categoryText) {
        meta.appendChild(createEl(
            'span',
            `badge search-category-badge ${getSentimentClass(item.sentiment_label)}`,
            categoryText
        ));
    }
    // 评分反馈入口：复用筛选页的 ⓘ 控件（score_feedback.js 的委托处理交互），
    // 控件带 data-score-feedback-scope="article" 走通用 /api/articles 接口。
    // 只有有重要性评分的文章才显示——反馈绑定当前评分上下文，
    // 没走到评分的文章后端也会拒绝（ScoreFeedbackContextMissingError）。
    if (
        item.external_importance_score !== null
        && item.external_importance_score !== undefined
        && typeof renderScoreFeedbackControl === 'function'
    ) {
        const feedbackWrap = document.createElement('span');
        feedbackWrap.innerHTML = renderScoreFeedbackControl(item);
        const feedbackControl = feedbackWrap.firstElementChild;
        if (feedbackControl) {
            feedbackControl.dataset.scoreFeedbackScope = 'article';
            meta.appendChild(feedbackControl);
        }
    }
    itemEl.appendChild(meta);
    if (archiveDetails) itemEl.appendChild(archiveDetails);

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
