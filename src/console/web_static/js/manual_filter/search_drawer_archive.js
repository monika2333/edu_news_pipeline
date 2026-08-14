// Manual Filter JS - Search Drawer · 报送存档命中（B 块）
// 事件级检索：调用已有的 /api/submission-archive/search，只呈现候选条目，
// 是否同一件事由使用者判断，这里不给「已报送」的硬结论。
// 由 _search_drawer.html 引入，运行期依赖 utils.js 与 search_drawer.js 的
// searchDrawerFetch。

const ARCHIVE_REPORT_TYPE_LABELS = {
    zongbao: '综报',
    wanbao: '晚报',
    feedback: '反馈'
};

function setupArchiveSearch() {
    const section = document.getElementById('archive-search-section');
    if (!section) return;
    if (section.dataset.archiveSearchReady === 'true') return;
    section.dataset.archiveSearchReady = 'true';

    const searchBtn = document.getElementById('btn-archive-search');
    const clearBtn = document.getElementById('btn-archive-clear');
    const queryInput = document.getElementById('archive-search-q');
    if (searchBtn) {
        searchBtn.addEventListener('click', () => performArchiveSearch());
    }
    if (clearBtn) {
        clearBtn.addEventListener('click', () => clearArchiveSearch());
    }
    if (queryInput) {
        queryInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') performArchiveSearch();
        });
    }
}

// 清空存档检索：检索词与候选结果一并清掉。
function clearArchiveSearch() {
    const queryInput = document.getElementById('archive-search-q');
    const results = document.getElementById('archive-search-results');
    if (queryInput) queryInput.value = '';
    if (results) clearEl(results);
}

// 关键词高亮：行为与报送存档库全库搜索的 highlight 一致（大小写不敏感的 <mark>），
// 但用 DOM 节点拼装，保持 createEl 路径的转义安全。
// 注意：content_drawer.js 有一个同名不同签名的 appendHighlightedText（terms 数组），
// 这里必须保持不同名，否则后加载的一方会覆盖另一方。
function appendArchiveHighlight(container, text, query) {
    const value = String(text || '');
    const needle = (query || '').trim().toLowerCase();
    if (!needle) {
        container.appendChild(document.createTextNode(value));
        return;
    }
    const haystack = value.toLowerCase();
    let index = 0;
    let found = haystack.indexOf(needle);
    while (found !== -1) {
        if (found > index) {
            container.appendChild(document.createTextNode(value.slice(index, found)));
        }
        container.appendChild(createEl('mark', '', value.slice(found, found + needle.length)));
        index = found + needle.length;
        found = haystack.indexOf(needle, index);
    }
    container.appendChild(document.createTextNode(value.slice(index)));
}

async function performArchiveSearch() {
    const queryInput = document.getElementById('archive-search-q');
    const results = document.getElementById('archive-search-results');
    if (!queryInput || !results) return;

    const query = queryInput.value.trim();
    if (!query) {
        clearEl(results);
        results.appendChild(createEl('div', 'archive-search-hint', '请输入检索词。'));
        return;
    }

    results.innerHTML = renderSkeleton(2);
    try {
        const params = new URLSearchParams({ q: query, limit: '20' });
        const res = await searchDrawerFetch(`/api/submission-archive/search?${params.toString()}`);
        if (!res.ok) throw new Error('存档检索失败');
        const data = await res.json();
        renderArchiveResults(data, query);
    } catch (e) {
        results.innerHTML = `<div class="error">存档检索失败：${e.message}</div>`;
    }
}

function renderArchiveResults(data, query) {
    const results = document.getElementById('archive-search-results');
    if (!results) return;
    clearEl(results);

    const items = data.items || [];
    if (!items.length) {
        results.appendChild(createEl(
            'div',
            'empty',
            '当前检索词未命中任何存档条目，可调整检索词后重试。',
            { dataset: { archiveEmpty: 'true' } }
        ));
        return;
    }

    results.appendChild(createEl(
        'div',
        'archive-search-stats',
        `${items.length} 条存档候选`,
        { dataset: { archiveCount: String(items.length) } }
    ));

    const fragment = document.createDocumentFragment();
    items.forEach(item => {
        const reportType = item.report_type || '';
        const itemEl = createEl('div', 'archive-item', '', {
            dataset: {
                archiveItem: item.id != null ? String(item.id) : '',
                reportType
            }
        });

        const head = createEl('div', 'archive-item-head');
        head.appendChild(createEl(
            'span',
            'archive-item-badge',
            ARCHIVE_REPORT_TYPE_LABELS[reportType] || reportType || '未知',
            { dataset: { reportType } }
        ));
        head.appendChild(createEl('span', 'archive-item-date', formatSearchDate(item.report_date) || '-'));
        const titleSpan = createEl('span', 'archive-item-title');
        appendArchiveHighlight(titleSpan, item.title || '（无标题）', query);
        head.appendChild(titleSpan);
        itemEl.appendChild(head);

        // 存档正文是最终报送版，长度可控，直接显示全文，不做截断。
        const bodyText = String(item.body || '').trim();
        if (bodyText) {
            const bodyEl = createEl('div', 'archive-item-body');
            appendArchiveHighlight(bodyEl, bodyText, query);
            // 来源拼在正文结尾，如「（北京日报）」。
            const sourceText = String(item.source || '').trim();
            if (sourceText) {
                bodyEl.appendChild(createEl('span', 'archive-item-source', `（${sourceText}）`));
            }
            itemEl.appendChild(bodyEl);
        }
        fragment.appendChild(itemEl);
    });
    results.appendChild(fragment);
}
