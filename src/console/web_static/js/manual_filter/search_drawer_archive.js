// Manual Filter JS - Search Drawer · 报送存档命中（B 块）
// 事件级检索：调用已有的 /api/submission-archive/search，只呈现候选条目，
// 是否同一件事由使用者判断，这里不给「已报送」的硬结论。
// 由 _search_drawer.html 引入，运行期依赖 utils.js 与 search_drawer.js 的
// searchDrawerFetch / switchSearchTab。

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
        head.appendChild(createEl('span', 'archive-item-title', item.title || '（无标题）'));
        itemEl.appendChild(head);

        // 存档正文是最终报送版，长度可控，直接显示全文，不做截断。
        const bodyText = String(item.body || '').trim();
        if (bodyText) {
            itemEl.appendChild(createEl('div', 'archive-item-body', bodyText));
        }
        fragment.appendChild(itemEl);
    });
    results.appendChild(fragment);
}

// A 块每条结果的「查报送存档」入口：切到存档 tab，带入该结果标题并立即检索。
// 检索词始终可编辑——匹配是事件级的，逐字搜标题很容易搜空。
function searchArchiveByTitle(title) {
    const queryInput = document.getElementById('archive-search-q');
    if (!queryInput) return;
    if (typeof switchSearchTab === 'function') {
        switchSearchTab('archive');
    }
    queryInput.value = title || '';
    performArchiveSearch();
}
