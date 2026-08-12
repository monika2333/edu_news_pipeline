// Manual Filter JS - Article Content Drawer (原文抽屉)
//
// 右侧抽屉展示单篇正文全文，无遮罩，打开时挤压列表宽度。
// 正文通过 /api/articles/content 按需单篇获取，页面会话内内存缓存。

const contentDrawerState = {
    open: false,
    articleId: null,
    anchorCard: null,
    // 当前已渲染的文章数据与加分词，供抽屉内检索时重绘正文
    currentData: null,
    currentBonusKeywords: [],
    searchMatches: [],
    searchIndex: -1
};

// article_id -> 接口响应数据；仅内存缓存，不写 localStorage
const articleContentCache = new Map();
let contentDrawerRequestSeq = 0;

function getContentDrawerEls() {
    return {
        drawer: document.getElementById('content-drawer'),
        closeBtn: document.getElementById('content-drawer-close'),
        title: document.getElementById('content-drawer-title'),
        meta: document.getElementById('content-drawer-meta'),
        sourceLink: document.getElementById('content-drawer-source-link'),
        body: document.getElementById('content-drawer-body'),
        search: document.getElementById('content-drawer-search'),
        searchCount: document.getElementById('content-drawer-search-count'),
        searchPrev: document.getElementById('content-drawer-search-prev'),
        searchNext: document.getElementById('content-drawer-search-next')
    };
}

// formatContentDrawerTime 已移至 utils.js（检索抽屉共用），此处不再重复定义。

function resolveContentDrawerBonusKeywords(triggerBtn) {
    const bonusRaw = triggerBtn.dataset.bonusKeywords || '';
    return bonusRaw.split('\n').map(kw => kw.trim()).filter(Boolean);
}

function setContentDrawerOpen(open, { anchor = true } = {}) {
    const { drawer } = getContentDrawerEls();
    if (!drawer || contentDrawerState.open === open) return;
    const anchorCard = contentDrawerState.anchorCard;
    const previousTop = anchor && anchorCard && anchorCard.isConnected
        ? anchorCard.getBoundingClientRect().top
        : null;
    contentDrawerState.open = open;
    drawer.classList.toggle('active', open);
    drawer.setAttribute('aria-hidden', open ? 'false' : 'true');
    document.body.classList.toggle('content-drawer-open', open);
    // anchor:false 时由调用方在完成所有布局变化后统一补偿，避免连续宽度变化重复锚定
    if (anchor) relayoutListsAfterWidthChange(anchorCard, previousTop);
}

function closeContentDrawer() {
    setContentDrawerOpen(false);
}

function appendHighlightedText(parent, text, terms) {
    let rest = String(text);
    while (rest) {
        let earliestIndex = -1;
        let matchedTerm = null;
        terms.forEach(item => {
            const idx = rest.toLowerCase().indexOf(item.term.toLowerCase());
            if (idx !== -1 && (earliestIndex === -1 || idx < earliestIndex)) {
                earliestIndex = idx;
                matchedTerm = item;
            }
        });
        if (!matchedTerm) {
            parent.appendChild(document.createTextNode(rest));
            return;
        }
        if (earliestIndex > 0) {
            parent.appendChild(document.createTextNode(rest.slice(0, earliestIndex)));
        }
        const mark = document.createElement('mark');
        mark.className = matchedTerm.className;
        mark.textContent = rest.slice(earliestIndex, earliestIndex + matchedTerm.term.length);
        parent.appendChild(mark);
        rest = rest.slice(earliestIndex + matchedTerm.term.length);
    }
}

function buildHighlightTerms(bonusKeywords, searchTerm) {
    const terms = [];
    const seen = new Set();
    (bonusKeywords || []).forEach(rawTerm => {
        const term = String(rawTerm || '').trim();
        if (!term) return;
        const key = term.toLowerCase();
        if (seen.has(key)) return;
        seen.add(key);
        terms.push({ term, className: 'content-hl-bonus' });
    });
    const keyword = String(searchTerm || '').trim();
    if (keyword && !seen.has(keyword.toLowerCase())) {
        terms.push({ term: keyword, className: 'content-hl-search' });
    }
    return terms;
}

// 正文一次性完整渲染进 DOM；抽屉顶栏的检索框可对当前正文做高亮定位
function renderContentDrawerBody(content, bonusKeywords, searchTerm) {
    const { body } = getContentDrawerEls();
    clearEl(body);
    const terms = buildHighlightTerms(bonusKeywords, searchTerm);
    const paragraphs = String(content)
        .replace(/\r\n/g, '\n')
        .split(/\n\s*\n/)
        .map(paragraph => paragraph.trim())
        .filter(Boolean);
    paragraphs.forEach(paragraphText => {
        const p = document.createElement('p');
        appendHighlightedText(p, paragraphText, terms);
        body.appendChild(p);
    });
    body.scrollTop = 0;
}

function renderContentDrawerHeader(data, charCount) {
    const { title, meta, sourceLink } = getContentDrawerEls();
    title.textContent = data.title || '(无标题)';
    clearEl(meta);
    const items = [
        `来源: ${data.source || '-'}`,
        `收录时间: ${formatContentDrawerTime(data.created_at)}`
    ];
    if (charCount !== null) items.push(`正文 ${charCount.toLocaleString('zh-CN')} 字`);
    items.forEach(text => meta.appendChild(createEl('span', '', text)));
    if (data.url) {
        sourceLink.href = data.url;
        sourceLink.hidden = false;
    } else {
        sourceLink.hidden = true;
    }
}

function renderContentDrawerFallback(data) {
    const { body } = getContentDrawerEls();
    clearEl(body);
    const fallback = createEl('div', 'content-drawer-fallback');
    fallback.appendChild(createEl(
        'p',
        '',
        '暂未抓取到该新闻的正文内容，无法在此展示。可前往源站查看原文。'
    ));
    if (data.url) {
        fallback.appendChild(createEl('a', 'content-drawer-fallback-link', '前往源站查看原文', {
            href: data.url,
            target: '_blank',
            rel: 'noopener noreferrer'
        }));
    }
    body.appendChild(fallback);
    body.scrollTop = 0;
}

function renderContentDrawerError(articleId, bonusKeywords) {
    const { body } = getContentDrawerEls();
    clearEl(body);
    const wrap = createEl('div', 'content-drawer-fallback');
    wrap.appendChild(createEl('p', '', '正文加载失败，请检查网络后重试。'));
    wrap.appendChild(createEl('button', 'btn btn-secondary', '重试', {
        type: 'button',
        onclick: () => loadContentDrawerArticle(articleId, bonusKeywords)
    }));
    body.appendChild(wrap);
}

function renderContentDrawerArticle(data, bonusKeywords) {
    const content = String(data.content_markdown || '').trim();
    const charCount = content ? Array.from(content.replace(/\s+/g, '')).length : null;
    contentDrawerState.currentData = data;
    contentDrawerState.currentBonusKeywords = bonusKeywords || [];
    renderContentDrawerHeader(data, charCount);
    if (!content) {
        renderContentDrawerFallback(data);
        return;
    }
    const { search } = getContentDrawerEls();
    renderContentDrawerBody(content, bonusKeywords, search ? search.value : '');
}

async function loadContentDrawerArticle(articleId, bonusKeywords) {
    const { body, title, meta, sourceLink, search, searchCount } = getContentDrawerEls();
    const seq = ++contentDrawerRequestSeq;
    // 清空上一条的头部信息与检索状态，避免加载期间展示串台内容
    title.textContent = '';
    clearEl(meta);
    sourceLink.hidden = true;
    if (search) search.value = '';
    if (searchCount) searchCount.textContent = '';
    contentDrawerState.currentData = null;
    contentDrawerState.currentBonusKeywords = [];
    contentDrawerState.searchMatches = [];
    contentDrawerState.searchIndex = -1;
    body.innerHTML = renderSkeleton(2);
    body.scrollTop = 0;

    if (articleContentCache.has(articleId)) {
        renderContentDrawerArticle(articleContentCache.get(articleId), bonusKeywords);
        return;
    }

    try {
        const res = await window.fetch(
            `/api/articles/content?article_id=${encodeURIComponent(articleId)}`
        );
        if (!res.ok) throw new Error(`content fetch failed: ${res.status}`);
        const data = await res.json();
        articleContentCache.set(articleId, data);
        if (seq !== contentDrawerRequestSeq) return;
        renderContentDrawerArticle(data, bonusKeywords);
    } catch (error) {
        if (seq !== contentDrawerRequestSeq) return;
        renderContentDrawerError(articleId, bonusKeywords);
    }
}

function updateContentDrawerSearchCount(term) {
    const { searchCount } = getContentDrawerEls();
    if (!searchCount) return;
    if (!term) {
        searchCount.textContent = '';
        return;
    }
    const total = contentDrawerState.searchMatches.length;
    searchCount.textContent = total
        ? `${contentDrawerState.searchIndex + 1}/${total}`
        : '0/0';
}

function focusContentDrawerSearchMatch(index) {
    const matches = contentDrawerState.searchMatches;
    if (!matches.length) return;
    const next = ((index % matches.length) + matches.length) % matches.length;
    matches.forEach(mark => mark.classList.remove('content-hl-search-active'));
    const mark = matches[next];
    mark.classList.add('content-hl-search-active');
    mark.scrollIntoView({ block: 'center' });
    contentDrawerState.searchIndex = next;
    const { search } = getContentDrawerEls();
    updateContentDrawerSearchCount(search ? search.value.trim() : '');
}

// 按检索词重绘当前正文并高亮命中，自动定位到第一个命中处
function applyContentDrawerSearch() {
    const { body, search } = getContentDrawerEls();
    const term = search ? search.value.trim() : '';
    contentDrawerState.searchMatches = [];
    contentDrawerState.searchIndex = -1;
    const data = contentDrawerState.currentData;
    if (data) {
        const content = String(data.content_markdown || '').trim();
        if (content) {
            renderContentDrawerBody(content, contentDrawerState.currentBonusKeywords, term);
            if (term) {
                contentDrawerState.searchMatches = Array.from(
                    body.querySelectorAll('mark.content-hl-search')
                );
            }
        }
    }
    if (contentDrawerState.searchMatches.length) {
        focusContentDrawerSearchMatch(0);
    } else {
        updateContentDrawerSearchCount(term);
    }
}

function stepContentDrawerSearch(direction) {
    if (!contentDrawerState.searchMatches.length) return;
    focusContentDrawerSearchMatch(contentDrawerState.searchIndex + direction);
}

function handleContentDrawerTrigger(triggerBtn) {
    const articleId = triggerBtn.dataset.articleId;
    if (!articleId) return;
    // 再次点击同一条的触发按钮 -> 关闭
    if (contentDrawerState.open && contentDrawerState.articleId === articleId) {
        closeContentDrawer();
        return;
    }
    const card = triggerBtn.closest('.article-card');
    contentDrawerState.anchorCard = card || null;
    contentDrawerState.articleId = articleId;
    const bonusKeywords = resolveContentDrawerBonusKeywords(triggerBtn);

    // 打开抽屉时自动折叠侧栏（不写 localStorage，仅本次浏览生效）。
    // 折叠与抽屉挤压是两次连续宽度变化：基准位置取两者都未发生之前，
    // 布局全部切换完后统一做一次锚定补偿，避免列表跳动。
    const willCollapseSidebar = !isSidebarCollapsed();
    const willOpenDrawer = !contentDrawerState.open;
    if (willCollapseSidebar || willOpenDrawer) {
        const previousTop = card && card.isConnected
            ? card.getBoundingClientRect().top
            : null;
        if (willCollapseSidebar) {
            setSidebarCollapsed(true, { persist: false, anchor: false });
        }
        setContentDrawerOpen(true, { anchor: false });
        relayoutListsAfterWidthChange(card, previousTop);
    }
    loadContentDrawerArticle(articleId, bonusKeywords);
}

function setupContentDrawer() {
    const { drawer, closeBtn, search, searchPrev, searchNext } = getContentDrawerEls();
    if (!drawer) return;
    if (drawer.dataset.contentDrawerReady === 'true') return;
    drawer.dataset.contentDrawerReady = 'true';

    if (closeBtn) closeBtn.addEventListener('click', closeContentDrawer);
    if (searchPrev) searchPrev.addEventListener('click', () => stepContentDrawerSearch(-1));
    if (searchNext) searchNext.addEventListener('click', () => stepContentDrawerSearch(1));
    if (search) {
        let searchDebounce = null;
        search.addEventListener('input', () => {
            window.clearTimeout(searchDebounce);
            searchDebounce = window.setTimeout(applyContentDrawerSearch, 150);
        });
        search.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                stepContentDrawerSearch(event.shiftKey ? -1 : 1);
            }
        });
    }
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || !contentDrawerState.open) return;
        // 焦点在检索框且已有输入时，Escape 先清空检索而不是关闭抽屉
        if (search && event.target === search && search.value) {
            search.value = '';
            applyContentDrawerSearch();
            return;
        }
        closeContentDrawer();
    });
    document.addEventListener('click', (event) => {
        const triggerBtn = event.target.closest('.content-drawer-trigger');
        if (triggerBtn) handleContentDrawerTrigger(triggerBtn);
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupContentDrawer, { once: true });
} else {
    setupContentDrawer();
}
