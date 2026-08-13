// Submission Archive JS - Article Content Drawer (原文抽屉)
//
// 复用 manual_filter 的抽屉 markup（_content_drawer.html）与 content_drawer.css。
// 正文通过 /api/articles/content 按需单篇获取，页面会话内内存缓存。
// 与 manual_filter/content_drawer.js 相互独立：存档页没有侧栏折叠与列表锚定逻辑。

const archiveDrawerState = {
    open: false,
    articleId: null,
    currentData: null,
    searchMatches: [],
    searchIndex: -1
};

// article_id -> 接口响应数据；仅内存缓存，不写 localStorage
const archiveDrawerCache = new Map();
let archiveDrawerRequestSeq = 0;

function getArchiveDrawerEls() {
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

function drawerCreateEl(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null) el.textContent = text;
    return el;
}

function formatDrawerDateTime(iso) {
    if (!iso) return '-';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return String(iso);
    const pad = value => String(value).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
        + `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function setArchiveDrawerOpen(open) {
    const { drawer } = getArchiveDrawerEls();
    if (!drawer || archiveDrawerState.open === open) return;
    archiveDrawerState.open = open;
    drawer.classList.toggle('active', open);
    drawer.setAttribute('aria-hidden', open ? 'false' : 'true');
    document.body.classList.toggle('content-drawer-open', open);
}

function closeArchiveDrawer() {
    setArchiveDrawerOpen(false);
}

function appendArchiveDrawerText(parent, text, term) {
    let rest = String(text);
    const needle = String(term || '').toLowerCase();
    while (rest) {
        const idx = needle ? rest.toLowerCase().indexOf(needle) : -1;
        if (idx === -1) {
            parent.appendChild(document.createTextNode(rest));
            return;
        }
        if (idx > 0) {
            parent.appendChild(document.createTextNode(rest.slice(0, idx)));
        }
        const mark = document.createElement('mark');
        mark.className = 'content-hl-search';
        mark.textContent = rest.slice(idx, idx + needle.length);
        parent.appendChild(mark);
        rest = rest.slice(idx + needle.length);
    }
}

// 正文一次性完整渲染进 DOM；抽屉顶栏的检索框可对当前正文做高亮定位
function renderArchiveDrawerBody(content, searchTerm) {
    const { body } = getArchiveDrawerEls();
    body.textContent = '';
    const paragraphs = String(content)
        .replace(/\r\n/g, '\n')
        .split(/\n\s*\n/)
        .map(paragraph => paragraph.trim())
        .filter(Boolean);
    paragraphs.forEach(paragraphText => {
        const p = document.createElement('p');
        appendArchiveDrawerText(p, paragraphText, searchTerm);
        body.appendChild(p);
    });
    body.scrollTop = 0;
}

function renderArchiveDrawerHeader(data, charCount) {
    const { title, meta, sourceLink } = getArchiveDrawerEls();
    title.textContent = data.title || '(无标题)';
    meta.textContent = '';
    const items = [
        `来源: ${data.source || '-'}`,
        `收录时间: ${formatDrawerDateTime(data.created_at)}`
    ];
    if (charCount !== null) items.push(`正文 ${charCount.toLocaleString('zh-CN')} 字`);
    items.forEach(text => meta.appendChild(drawerCreateEl('span', '', text)));
    if (data.url) {
        sourceLink.href = data.url;
        sourceLink.hidden = false;
    } else {
        sourceLink.hidden = true;
    }
}

function renderArchiveDrawerFallback(data) {
    const { body } = getArchiveDrawerEls();
    body.textContent = '';
    const fallback = drawerCreateEl('div', 'content-drawer-fallback');
    fallback.appendChild(drawerCreateEl(
        'p',
        '',
        '暂未抓取到该新闻的正文内容，无法在此展示。可前往源站查看原文。'
    ));
    if (data.url) {
        const link = drawerCreateEl('a', 'content-drawer-fallback-link', '前往源站查看原文');
        link.href = data.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        fallback.appendChild(link);
    }
    body.appendChild(fallback);
    body.scrollTop = 0;
}

function renderArchiveDrawerError(articleId) {
    const { body } = getArchiveDrawerEls();
    body.textContent = '';
    const wrap = drawerCreateEl('div', 'content-drawer-fallback');
    wrap.appendChild(drawerCreateEl('p', '', '正文加载失败，请检查网络后重试。'));
    const retry = drawerCreateEl('button', 'btn btn-secondary', '重试');
    retry.type = 'button';
    retry.addEventListener('click', () => loadArchiveDrawerArticle(articleId));
    wrap.appendChild(retry);
    body.appendChild(wrap);
}

function renderArchiveDrawerArticle(data) {
    const content = String(data.content_markdown || '').trim();
    const charCount = content ? Array.from(content.replace(/\s+/g, '')).length : null;
    archiveDrawerState.currentData = data;
    renderArchiveDrawerHeader(data, charCount);
    if (!content) {
        renderArchiveDrawerFallback(data);
        return;
    }
    const { search } = getArchiveDrawerEls();
    renderArchiveDrawerBody(content, search ? search.value : '');
}

async function loadArchiveDrawerArticle(articleId) {
    const { body, title, meta, sourceLink, search, searchCount } = getArchiveDrawerEls();
    const seq = ++archiveDrawerRequestSeq;
    // 清空上一条的头部信息与检索状态，避免加载期间展示串台内容
    title.textContent = '';
    meta.textContent = '';
    sourceLink.hidden = true;
    if (search) search.value = '';
    if (searchCount) searchCount.textContent = '';
    archiveDrawerState.currentData = null;
    archiveDrawerState.searchMatches = [];
    archiveDrawerState.searchIndex = -1;
    body.textContent = '';
    body.appendChild(drawerCreateEl('div', 'content-drawer-placeholder', '正在加载…'));
    body.scrollTop = 0;

    if (archiveDrawerCache.has(articleId)) {
        renderArchiveDrawerArticle(archiveDrawerCache.get(articleId));
        return;
    }

    try {
        const res = await window.fetch(
            `/api/articles/content?article_id=${encodeURIComponent(articleId)}`
        );
        if (!res.ok) throw new Error(`content fetch failed: ${res.status}`);
        const data = await res.json();
        archiveDrawerCache.set(articleId, data);
        if (seq !== archiveDrawerRequestSeq) return;
        renderArchiveDrawerArticle(data);
    } catch (error) {
        if (seq !== archiveDrawerRequestSeq) return;
        renderArchiveDrawerError(articleId);
    }
}

function updateArchiveDrawerSearchCount(term) {
    const { searchCount } = getArchiveDrawerEls();
    if (!searchCount) return;
    if (!term) {
        searchCount.textContent = '';
        return;
    }
    const total = archiveDrawerState.searchMatches.length;
    searchCount.textContent = total
        ? `${archiveDrawerState.searchIndex + 1}/${total}`
        : '0/0';
}

function focusArchiveDrawerSearchMatch(index) {
    const matches = archiveDrawerState.searchMatches;
    if (!matches.length) return;
    const next = ((index % matches.length) + matches.length) % matches.length;
    matches.forEach(mark => mark.classList.remove('content-hl-search-active'));
    const mark = matches[next];
    mark.classList.add('content-hl-search-active');
    mark.scrollIntoView({ block: 'center' });
    archiveDrawerState.searchIndex = next;
    const { search } = getArchiveDrawerEls();
    updateArchiveDrawerSearchCount(search ? search.value.trim() : '');
}

// 按检索词重绘当前正文并高亮命中，自动定位到第一个命中处
function applyArchiveDrawerSearch() {
    const { body, search } = getArchiveDrawerEls();
    const term = search ? search.value.trim() : '';
    archiveDrawerState.searchMatches = [];
    archiveDrawerState.searchIndex = -1;
    const data = archiveDrawerState.currentData;
    if (data) {
        const content = String(data.content_markdown || '').trim();
        if (content) {
            renderArchiveDrawerBody(content, term);
            if (term) {
                archiveDrawerState.searchMatches = Array.from(
                    body.querySelectorAll('mark.content-hl-search')
                );
            }
        }
    }
    if (archiveDrawerState.searchMatches.length) {
        focusArchiveDrawerSearchMatch(0);
    } else {
        updateArchiveDrawerSearchCount(term);
    }
}

function stepArchiveDrawerSearch(direction) {
    if (!archiveDrawerState.searchMatches.length) return;
    focusArchiveDrawerSearchMatch(archiveDrawerState.searchIndex + direction);
}

function handleArchiveDrawerTrigger(triggerBtn) {
    const articleId = triggerBtn.dataset.articleId;
    if (!articleId) return;
    // 再次点击同一条的 pill -> 关闭
    if (archiveDrawerState.open && archiveDrawerState.articleId === articleId) {
        closeArchiveDrawer();
        return;
    }
    archiveDrawerState.articleId = articleId;
    setArchiveDrawerOpen(true);
    loadArchiveDrawerArticle(articleId);
}

function setupArchiveContentDrawer() {
    const { drawer, closeBtn, search, searchPrev, searchNext } = getArchiveDrawerEls();
    if (!drawer) return;
    if (drawer.dataset.archiveDrawerReady === 'true') return;
    drawer.dataset.archiveDrawerReady = 'true';

    if (closeBtn) closeBtn.addEventListener('click', closeArchiveDrawer);
    if (searchPrev) searchPrev.addEventListener('click', () => stepArchiveDrawerSearch(-1));
    if (searchNext) searchNext.addEventListener('click', () => stepArchiveDrawerSearch(1));
    if (search) {
        let searchDebounce = null;
        search.addEventListener('input', () => {
            window.clearTimeout(searchDebounce);
            searchDebounce = window.setTimeout(applyArchiveDrawerSearch, 150);
        });
        search.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                stepArchiveDrawerSearch(event.shiftKey ? -1 : 1);
            }
        });
    }
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || !archiveDrawerState.open) return;
        // 焦点在检索框且已有输入时，Escape 先清空检索而不是关闭抽屉
        if (search && event.target === search && search.value) {
            search.value = '';
            applyArchiveDrawerSearch();
            return;
        }
        closeArchiveDrawer();
    });
    document.addEventListener('click', (event) => {
        const triggerBtn = event.target.closest('.archive-link-pill-btn');
        if (triggerBtn) handleArchiveDrawerTrigger(triggerBtn);
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupArchiveContentDrawer, { once: true });
} else {
    setupArchiveContentDrawer();
}
