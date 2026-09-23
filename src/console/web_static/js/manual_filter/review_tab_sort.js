// Manual Filter JS - Review Tab

// --- Review Tab Sorting ---
// 分类词表来自服务端嵌入的 #review-sort-rules（设置页「高级 · 自动排序关键词」
// 维护），本文件不再内置词表；嵌入缺失或损坏时所有条目落入「其他」桶，
// 自动排序退化为保持原顺序。

const CATEGORY_ORDER = ['市教委', '中小学', '高校', '其他'];

const REVIEW_SORT_RULES = (() => {
    const node = document.getElementById('review-sort-rules');
    if (!node) return {};
    try {
        const parsed = JSON.parse(node.textContent);
        return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (error) {
        return {};
    }
})();

const CATEGORY_RULES_LOWER = Object.fromEntries(
    CATEGORY_ORDER
        .filter((category) => category !== '其他')
        .map((category) => {
            const keywords = REVIEW_SORT_RULES[category];
            const list = Array.isArray(keywords) ? keywords : [];
            return [category, list.map((keyword) => String(keyword).toLowerCase())];
        })
);

function applySortModeState() {
    const grid = document.querySelector('.review-grid');
    const reviewTab = document.getElementById('review-tab');
    const toggleBtn = elements.sortToggleBtn;
    if (grid) {
        grid.classList.toggle('compact-mode', isSortMode);
        grid.classList.toggle('sort-mode', isSortMode);
    }
    if (reviewTab) {
        reviewTab.classList.toggle('review-sort-mode', isSortMode);
    }
    if (toggleBtn) {
        toggleBtn.classList.toggle('active', isSortMode);
        toggleBtn.innerHTML = `<span class="icon">⇅</span> ${isSortMode ? '退出排序' : '排序模式'}`;
    }
}

function toggleSortMode() {
    isSortMode = !isSortMode;
    renderReviewView();
}

function resolveGroupKey(item) {
    if (item.group_key) return item.group_key;
    const region = item.is_beijing_related ? 'internal' : 'external';
    const sentiment = (item.sentiment_label || '').toLowerCase() === 'negative' ? 'negative' : 'positive';
    return `${region}_${sentiment}`;
}

function buildReviewCategoryText(item) {
    const title = item.title || '';
    const summary = item.summary || '';
    const source = item.llm_source_display || '';
    return `${title} ${summary} ${source}`.trim();
}

function classifyCategory(text) {
    const normalized = (text || '').toLowerCase();
    for (const category of CATEGORY_ORDER) {
        if (category === '其他') continue;
        const keywords = CATEGORY_RULES_LOWER[category] || [];
        for (const keyword of keywords) {
            if (keyword && normalized.includes(keyword)) {
                return category;
            }
        }
    }
    return '其他';
}

function reorderReviewItemsByCategory(items, categoryCounts) {
    const categoryBuckets = {
        '市教委': [],
        '中小学': [],
        '高校': [],
        '其他': []
    };
    items.forEach(item => {
        const text = buildReviewCategoryText(item);
        const category = classifyCategory(text);
        categoryBuckets[category].push(item);
        categoryCounts[category] += 1;
    });
    const ordered = [];
    CATEGORY_ORDER.forEach(category => {
        ordered.push(...categoryBuckets[category]);
    });
    return ordered;
}

async function autoReorderReviewItems() {
    if (state.currentTab !== 'review') {
        showToast('请先进入审阅页');
        return;
    }
    if (!isSortMode) {
        showToast('请先打开排序模式');
        return;
    }
    const view = state.reviewView === 'backup' ? 'backup' : 'selected';
    const items = state.reviewData[view] || [];
    if (!items.length) {
        showToast('当前列表为空');
        return;
    }

    const groupBuckets = {};
    items.forEach(item => {
        const key = resolveGroupKey(item);
        if (!groupBuckets[key]) groupBuckets[key] = [];
        groupBuckets[key].push(item);
    });

    const categoryCounts = {
        '市教委': 0,
        '中小学': 0,
        '高校': 0,
        '其他': 0
    };
    const ordered = [];
    const seenKeys = new Set();

    GROUP_ORDER.forEach(group => {
        const groupItems = groupBuckets[group.key] || [];
        if (!groupItems.length) return;
        seenKeys.add(group.key);
        ordered.push(...reorderReviewItemsByCategory(groupItems, categoryCounts));
    });

    Object.keys(groupBuckets).forEach(key => {
        if (seenKeys.has(key)) return;
        const groupItems = groupBuckets[key] || [];
        if (!groupItems.length) return;
        ordered.push(...reorderReviewItemsByCategory(groupItems, categoryCounts));
    });

    state.reviewData[view] = ordered;
    renderReviewView();
    const saved = await persistReviewOrder();
    if (saved) {
        showToast(
            `自动排序完成：市教委 ${categoryCounts['市教委']}，中小学 ${categoryCounts['中小学']}，高校 ${categoryCounts['高校']}，其他 ${categoryCounts['其他']}`
        );
    }
}

function updateReviewSortGroupCounts() {
    document.querySelectorAll('.sort-group').forEach(group => {
        const key = group.dataset.group || '';
        const header = group.querySelector('.review-group-header');
        const body = group.querySelector('.sort-group-body');
        const count = body ? body.querySelectorAll('.article-card').length : 0;
        if (header) header.innerHTML = `<span class="toggle-icon">▼</span> ${getGroupLabel(key)} (${count})`;
        if (body) body.classList.toggle('is-empty', count === 0);
    });
}

async function handleReviewSortEnd() {
    updateReviewSortGroupCounts();
    await persistReviewOrder();
}

function initReviewSortable() {
    if (reviewSortableInstances && reviewSortableInstances.length) {
        reviewSortableInstances.forEach(inst => inst && inst.destroy());
        reviewSortableInstances = [];
    }
    if (!isSortMode || typeof Sortable === 'undefined') return;
    const lists = document.querySelectorAll('.sort-group-body');
    if (!lists || !lists.length) return;
    const isMobileSort = window.innerWidth <= MOBILE_REVIEW_BREAKPOINT;
    lists.forEach(list => {
        const inst = new Sortable(list, {
            animation: 150,
            handle: isMobileSort ? undefined : '.drag-handle',
            ghostClass: 'review-ghost',
            forceFallback: true,
            fallbackOnBody: true,
            draggable: '.article-card',
            group: { name: 'review-groups', pull: true, put: true },
            onEnd: handleReviewSortEnd,
        });
        reviewSortableInstances.push(inst);
    });
}
