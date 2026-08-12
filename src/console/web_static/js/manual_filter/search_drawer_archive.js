// Manual Filter JS - Search Drawer · 报送存档命中（B 块）
// 事件级检索：调用已有的 /api/submission-archive/search，只呈现候选条目，
// 是否同一件事由使用者判断，这里不给「已报送」的硬结论。
// 由 _search_drawer.html 引入，运行期依赖 utils.js 与 search_drawer.js 的 searchDrawerFetch。

const ARCHIVE_REPORT_TYPE_LABELS = {
    zongbao: '综报',
    wanbao: '晚报',
    feedback: '反馈'
};

const ARCHIVE_BODY_SNIPPET_LENGTH = 200;

function setupArchiveSearch() {
    const section = document.getElementById('archive-search-section');
    if (!section) return;
    if (section.dataset.archiveSearchReady === 'true') return;
    section.dataset.archiveSearchReady = 'true';

    const searchBtn = document.getElementById('btn-archive-search');
    const queryInput = document.getElementById('archive-search-q');
    if (searchBtn) {
        searchBtn.addEventListener('click', () => performArchiveSearch());
    }
    if (queryInput) {
        queryInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') performArchiveSearch();
        });
    }
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
        renderArchiveResults(data);
    } catch (e) {
        results.innerHTML = `<div class="error">存档检索失败：${e.message}</div>`;
    }
}

function renderArchiveResults(data) {
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
        `${items.length} 条存档候选（是否同一件事请自行判断）`,
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
        head.appendChild(createEl('span', 'archive-item-title', item.title || '（无标题）'));
        itemEl.appendChild(head);

        const bodyText = String(item.body || '').trim();
        if (bodyText) {
            const snippet = bodyText.length > ARCHIVE_BODY_SNIPPET_LENGTH
                ? `${bodyText.slice(0, ARCHIVE_BODY_SNIPPET_LENGTH)}…`
                : bodyText;
            itemEl.appendChild(createEl('div', 'archive-item-body', snippet));
        }
        fragment.appendChild(itemEl);
    });
    results.appendChild(fragment);
}

// A 块每条结果的「查报送存档」入口：带入该结果标题并立即检索。
// 检索词始终可编辑——匹配是事件级的，逐字搜标题很容易搜空。
function searchArchiveByTitle(title) {
    const queryInput = document.getElementById('archive-search-q');
    const section = document.getElementById('archive-search-section');
    if (!queryInput) return;
    queryInput.value = title || '';
    performArchiveSearch();
    if (section && typeof section.scrollIntoView === 'function') {
        section.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
}
