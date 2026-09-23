// Manual Filter JS - Refine Filters（细化筛选折叠行）
//
// 「筛选」按钮展开/收起工具条第二行（处理状态[管理员] → 报送标签 → 收录时段 → 分数）。
// 折叠时筛选仍然生效，靠按钮徽标（激活维数）与 meta 行「筛选中：…」摘要传达；
// 载入时有激活筛选则自动展开。折叠行 markup 静态在 manual_filter.html，本模块只管
// 状态、交互与参数拼装；样式在 filter.css 的「细化筛选」段。
//
// 不变量：列表、侧栏计数、meta 行计数与批量放弃必须同口径，
// 参数只能经 refineFilterQueryParams / refineFilterRequestBody 取自本模块状态。
//
// 后端参数：hour_from / hour_to（收录时间小时，0-23，上海时区，跨零点回绕）、
//          duplicate_state=untagged（隐藏带已报送/疑似已报送徽章的条目）、
//          min_score / max_score（外部重要性分数闭区间）。

const REFINE_FILTER_STORAGE_KEY = 'manual_filter_refine_filters';

const refineFilterState = {
    hourFrom: '',
    hourTo: '',
    hideTagged: '',
    minScore: '',
    maxScore: ''
};

const REFINE_FILTER_FIELDS = ['hourFrom', 'hourTo', 'hideTagged', 'minScore', 'maxScore'];

function refineFilterReadStorage() {
    try {
        const rawState = localStorage.getItem(REFINE_FILTER_STORAGE_KEY);
        if (!rawState) return;
        const saved = JSON.parse(rawState);
        REFINE_FILTER_FIELDS.forEach(key => {
            if (saved && typeof saved[key] === 'string') refineFilterState[key] = saved[key];
        });
    } catch (error) {
        // 存储不可用时按默认值处理
    }
}

function refineFilterWriteStorage() {
    try {
        const payload = {};
        REFINE_FILTER_FIELDS.forEach(key => { payload[key] = refineFilterState[key]; });
        localStorage.setItem(REFINE_FILTER_STORAGE_KEY, JSON.stringify(payload));
    } catch (error) {
        // 写入失败仅影响持久化
    }
}

function refineFilterNormalizeHour(value) {
    if (value === '' || value === null || value === undefined) return null;
    const hour = Number(value);
    return Number.isInteger(hour) && hour >= 0 && hour <= 23 ? hour : null;
}

function refineFilterNormalizeScore(value) {
    if (value === '' || value === null || value === undefined) return null;
    const score = Number(value);
    return Number.isFinite(score) ? score : null;
}

// 激活的细化条件维数（按钮徽标、清空按钮显隐共用；不含管理员「值班范围」）
function refineFilterActiveCount() {
    let count = 0;
    if (refineFilterNormalizeHour(refineFilterState.hourFrom) !== null) count += 1;
    if (refineFilterNormalizeHour(refineFilterState.hourTo) !== null) count += 1;
    if (refineFilterState.hideTagged === '1') count += 1;
    if (refineFilterNormalizeScore(refineFilterState.minScore) !== null) count += 1;
    if (refineFilterNormalizeScore(refineFilterState.maxScore) !== null) count += 1;
    return count;
}

function refineFilterActive() {
    return refineFilterActiveCount() > 0;
}

// 供 loadFilterData / loadFilterCounts 拼查询串；未启用时返回空数组，不产生任何参数。
function refineFilterQueryParams() {
    const pairs = [];
    const hourFrom = refineFilterNormalizeHour(refineFilterState.hourFrom);
    const hourTo = refineFilterNormalizeHour(refineFilterState.hourTo);
    if (hourFrom !== null) pairs.push(['hour_from', String(hourFrom)]);
    if (hourTo !== null) pairs.push(['hour_to', String(hourTo)]);
    if (refineFilterState.hideTagged === '1') {
        pairs.push(['duplicate_state', 'untagged']);
    }
    const minScore = refineFilterNormalizeScore(refineFilterState.minScore);
    const maxScore = refineFilterNormalizeScore(refineFilterState.maxScore);
    if (minScore !== null) pairs.push(['min_score', String(minScore)]);
    if (maxScore !== null) pairs.push(['max_score', String(maxScore)]);
    return pairs;
}

// 供批量放弃等 JSON body 复用；未启用时返回空对象。
function refineFilterRequestBody() {
    const body = {};
    refineFilterQueryParams().forEach(([key, value]) => {
        body[key] = key === 'duplicate_state' ? value : Number(value);
    });
    return body;
}

function refineFilterDescribe() {
    const parts = [];
    const hourFrom = refineFilterNormalizeHour(refineFilterState.hourFrom);
    const hourTo = refineFilterNormalizeHour(refineFilterState.hourTo);
    if (hourFrom !== null || hourTo !== null) {
        if (hourFrom !== null && hourTo !== null) {
            parts.push(`时段 ${hourFrom}–${hourTo} 时`);
        } else if (hourFrom !== null) {
            parts.push(`${hourFrom} 时起`);
        } else {
            parts.push(`${hourTo} 时前`);
        }
    }
    if (refineFilterState.hideTagged === '1') parts.push('隐藏已报送');
    const minScore = refineFilterNormalizeScore(refineFilterState.minScore);
    const maxScore = refineFilterNormalizeScore(refineFilterState.maxScore);
    if (minScore !== null && maxScore !== null) parts.push(`分数 ${minScore}–${maxScore}`);
    else if (minScore !== null) parts.push(`分数 ≥ ${minScore}`);
    else if (maxScore !== null) parts.push(`分数 ≤ ${maxScore}`);
    return parts.join(' · ');
}

// meta 行后缀：唯一的「清空筛选」入口（折叠行内不重复放置）。基础文案由
// syncFilterToolbarState 以文本形式写入，这里返回的 HTML 只包含本模块生成的
// 固定文案与一个按钮，不含用户数据。
function refineFilterMetaSuffixHtml() {
    if (!refineFilterActive()) return '';
    const summary = refineFilterDescribe() || '筛选中';
    return `<span class="filter-refine-meta-suffix">筛选中：${summary}`
        + ` <button type="button" class="filter-refine-clear-link">清空筛选</button></span>`;
}

function refineFilterClear() {
    REFINE_FILTER_FIELDS.forEach(key => { refineFilterState[key] = ''; });
    refineFilterWriteStorage();
    refineFilterSyncInputs();
    refineFilterAfterChange();
}

// 筛选变化后的统一出口：回第 1 页并重拉列表与侧栏计数（与「值班未处理」开关行为一致）
function refineFilterAfterChange() {
    refineFilterUpdateBadge();
    if (typeof state === 'undefined' || !state) return;
    state.filterPage = 1;
    if (typeof loadFilterData === 'function') loadFilterData();
    if (typeof loadFilterCounts === 'function') loadFilterCounts();
}

function refineFilterSyncInputs() {
    const row = document.querySelector('.filter-refine-row');
    if (!row) return;
    row.querySelectorAll('select[data-refine-field]').forEach(select => {
        select.value = refineFilterState[select.dataset.refineField] || '';
    });
    row.querySelectorAll('input[type="number"][data-refine-field]').forEach(input => {
        input.value = refineFilterState[input.dataset.refineField] || '';
    });
    row.querySelectorAll('input[type="checkbox"][data-refine-field]').forEach(checkbox => {
        checkbox.checked = refineFilterState[checkbox.dataset.refineField] === '1';
    });
    const wrapNote = row.querySelector('.filter-refine-wrap-note');
    if (wrapNote) {
        const from = refineFilterNormalizeHour(refineFilterState.hourFrom);
        const to = refineFilterNormalizeHour(refineFilterState.hourTo);
        wrapNote.hidden = !(from !== null && to !== null && from > to);
    }
}

function refineFilterUpdateBadge() {
    const badge = document.getElementById('filter-refine-count-badge');
    if (!badge) return;
    const count = refineFilterActiveCount();
    badge.textContent = count ? String(count) : '';
    badge.hidden = !count;
    const toggle = document.getElementById('filter-refine-toggle');
    if (toggle) toggle.classList.toggle('has-active', count > 0);
}

// 展开/收起折叠行。收起后筛选仍然生效。
function refineFilterToggleRow(forceOpen) {
    const shouldOpen = typeof forceOpen === 'boolean'
        ? forceOpen
        : !document.body.classList.contains('filter-refine-row-open');
    document.body.classList.toggle('filter-refine-row-open', shouldOpen);
    const toggle = document.getElementById('filter-refine-toggle');
    if (toggle) {
        toggle.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
        toggle.classList.toggle('is-open', shouldOpen);
    }
}

function refineFilterWireEvents() {
    const toggle = document.getElementById('filter-refine-toggle');
    if (toggle) {
        toggle.addEventListener('click', () => refineFilterToggleRow());
    }
    const row = document.querySelector('.filter-refine-row');
    if (row) {
        row.addEventListener('change', event => {
            const target = event.target.closest('[data-refine-field]');
            if (!target) return;
            refineFilterState[target.dataset.refineField] = target.type === 'checkbox'
                ? (target.checked ? '1' : '')
                : target.value.trim();
            refineFilterWriteStorage();
            refineFilterSyncInputs();
            refineFilterAfterChange();
        });
    }
    // meta 行的「清空筛选」按钮（由 innerHTML 重渲染，用委托）
    document.addEventListener('click', event => {
        if (event.target.closest('.filter-refine-clear-link')) {
            event.preventDefault();
            refineFilterClear();
        }
    });
}

function refineFilterInit() {
    refineFilterReadStorage();
    refineFilterWireEvents();
    refineFilterSyncInputs();
    refineFilterUpdateBadge();
    if (refineFilterActive()) refineFilterToggleRow(true);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', refineFilterInit);
} else {
    refineFilterInit();
}
