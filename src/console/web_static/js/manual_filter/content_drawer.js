// Manual Filter JS - Article Content Drawer (原文抽屉)
//
// 右侧抽屉展示单篇正文全文，无遮罩，打开时挤压列表宽度。
// 正文通过 /api/articles/content 按需单篇获取，页面会话内内存缓存。

const contentDrawerState = {
    open: false,
    articleId: null,
    anchorCard: null
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
        body: document.getElementById('content-drawer-body')
    };
}

function formatContentDrawerTime(iso) {
    if (!iso) return '-';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return String(iso);
    const pad = value => String(value).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
        + `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function resolveContentDrawerBonusKeywords(triggerBtn) {
    const bonusRaw = triggerBtn.dataset.bonusKeywords || '';
    return bonusRaw.split('\n').map(kw => kw.trim()).filter(Boolean);
}

function setContentDrawerOpen(open) {
    const { drawer } = getContentDrawerEls();
    if (!drawer || contentDrawerState.open === open) return;
    const anchorCard = contentDrawerState.anchorCard;
    const previousTop = anchorCard && anchorCard.isConnected
        ? anchorCard.getBoundingClientRect().top
        : null;
    contentDrawerState.open = open;
    drawer.classList.toggle('active', open);
    drawer.setAttribute('aria-hidden', open ? 'false' : 'true');
    document.body.classList.toggle('content-drawer-open', open);
    relayoutListsAfterWidthChange(anchorCard, previousTop);
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

function buildHighlightTerms(bonusKeywords) {
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
    return terms;
}

// 正文一次性完整渲染进 DOM，用户可依赖浏览器页面查找（Ctrl+F）定位任意词
function renderContentDrawerBody(content, bonusKeywords) {
    const { body } = getContentDrawerEls();
    clearEl(body);
    const terms = buildHighlightTerms(bonusKeywords);
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
        `发布时间: ${formatContentDrawerTime(data.publish_time_iso)}`,
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
    renderContentDrawerHeader(data, charCount);
    if (!content) {
        renderContentDrawerFallback(data);
        return;
    }
    renderContentDrawerBody(content, bonusKeywords);
}

async function loadContentDrawerArticle(articleId, bonusKeywords) {
    const { body, title, meta, sourceLink } = getContentDrawerEls();
    const seq = ++contentDrawerRequestSeq;
    // 清空上一条的头部信息，避免加载期间展示串台内容
    title.textContent = '';
    clearEl(meta);
    sourceLink.hidden = true;
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
    setContentDrawerOpen(true);
    loadContentDrawerArticle(articleId, bonusKeywords);
}

function setupContentDrawer() {
    const { drawer, closeBtn } = getContentDrawerEls();
    if (!drawer) return;
    if (drawer.dataset.contentDrawerReady === 'true') return;
    drawer.dataset.contentDrawerReady = 'true';

    if (closeBtn) closeBtn.addEventListener('click', closeContentDrawer);
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && contentDrawerState.open) {
            closeContentDrawer();
        }
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
