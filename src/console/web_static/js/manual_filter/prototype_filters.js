// Prototype-only module：人工筛选页「细化筛选」折叠工具栏原型（定稿方向）。
// 本文件、prototype.css、模板中的 proto-* 挂载点在定稿后转为正式实现并清理。
//
// 交互：检索行内的「筛选 ▾」按钮展开/收起工具栏第二行（Google「工具」式）。
// 折叠时筛选仍然生效，靠按钮徽标（激活维数）与 meta 行摘要传达；
// 载入时若有激活筛选则自动展开。
// 后端参数：hour_from / hour_to（收录时间小时，0-23，跨零点回绕）、
//          duplicate_state=untagged（隐藏已报送开关）、min_score / max_score。

const PROTO_FILTER_STORAGE_KEY = 'proto_filter_state_v1';

const protoFilterState = {
    hourFrom: '',
    hourTo: '',
    hideTagged: '',
    minScore: '',
    maxScore: ''
};

const PROTO_FILTER_FIELDS = ['hourFrom', 'hourTo', 'hideTagged', 'minScore', 'maxScore'];

function protoFilterReadStorage() {
    try {
        const rawState = localStorage.getItem(PROTO_FILTER_STORAGE_KEY);
        if (!rawState) return;
        const saved = JSON.parse(rawState);
        PROTO_FILTER_FIELDS.forEach(key => {
            if (saved && typeof saved[key] === 'string') protoFilterState[key] = saved[key];
        });
    } catch (error) {
        // 存储不可用时按默认值处理
    }
}

function protoFilterWriteStorage() {
    try {
        const payload = {};
        PROTO_FILTER_FIELDS.forEach(key => { payload[key] = protoFilterState[key]; });
        localStorage.setItem(PROTO_FILTER_STORAGE_KEY, JSON.stringify(payload));
    } catch (error) {
        // 写入失败仅影响持久化
    }
}

function protoFilterNormalizeHour(value) {
    if (value === '' || value === null || value === undefined) return null;
    const hour = Number(value);
    return Number.isInteger(hour) && hour >= 0 && hour <= 23 ? hour : null;
}

function protoFilterNormalizeScore(value) {
    if (value === '' || value === null || value === undefined) return null;
    const score = Number(value);
    return Number.isFinite(score) ? score : null;
}

// 「筛选中」的维度数（按钮徽标、重置按钮显隐共用）
function protoFilterActiveCount() {
    let count = 0;
    if (protoFilterNormalizeHour(protoFilterState.hourFrom) !== null) count += 1;
    if (protoFilterNormalizeHour(protoFilterState.hourTo) !== null) count += 1;
    if (protoFilterState.hideTagged === '1') count += 1;
    if (protoFilterNormalizeScore(protoFilterState.minScore) !== null) count += 1;
    if (protoFilterNormalizeScore(protoFilterState.maxScore) !== null) count += 1;
    return count;
}

function protoFilterActive() {
    return protoFilterActiveCount() > 0;
}

// 供 loadFilterData / loadFilterCounts 拼查询串；未启用时返回空串对，不产生任何参数。
function protoFilterQueryParams() {
    const pairs = [];
    const hourFrom = protoFilterNormalizeHour(protoFilterState.hourFrom);
    const hourTo = protoFilterNormalizeHour(protoFilterState.hourTo);
    if (hourFrom !== null) pairs.push(['hour_from', String(hourFrom)]);
    if (hourTo !== null) pairs.push(['hour_to', String(hourTo)]);
    if (protoFilterState.hideTagged === '1') {
        pairs.push(['duplicate_state', 'untagged']);
    }
    const minScore = protoFilterNormalizeScore(protoFilterState.minScore);
    const maxScore = protoFilterNormalizeScore(protoFilterState.maxScore);
    if (minScore !== null) pairs.push(['min_score', String(minScore)]);
    if (maxScore !== null) pairs.push(['max_score', String(maxScore)]);
    return pairs;
}

// 供批量放弃等 JSON body 复用；未启用时返回空对象。
function protoFilterRequestBody() {
    const body = {};
    protoFilterQueryParams().forEach(([key, value]) => {
        body[key] = key === 'duplicate_state' ? value : Number(value);
    });
    return body;
}

function protoFilterDescribe() {
    const parts = [];
    const hourFrom = protoFilterNormalizeHour(protoFilterState.hourFrom);
    const hourTo = protoFilterNormalizeHour(protoFilterState.hourTo);
    if (hourFrom !== null || hourTo !== null) {
        if (hourFrom !== null && hourTo !== null) {
            parts.push(`时段 ${hourFrom}–${hourTo} 时`);
        } else if (hourFrom !== null) {
            parts.push(`${hourFrom} 时起`);
        } else {
            parts.push(`${hourTo} 时前`);
        }
    }
    if (protoFilterState.hideTagged === '1') parts.push('隐藏已报送');
    const minScore = protoFilterNormalizeScore(protoFilterState.minScore);
    const maxScore = protoFilterNormalizeScore(protoFilterState.maxScore);
    if (minScore !== null && maxScore !== null) parts.push(`分数 ${minScore}–${maxScore}`);
    else if (minScore !== null) parts.push(`分数 ≥ ${minScore}`);
    else if (maxScore !== null) parts.push(`分数 ≤ ${maxScore}`);
    return parts.join(' · ');
}

// meta 行后缀：唯一的「清空筛选」入口（筛选行内不重复放置）。基础文案由
// syncFilterToolbarState 以文本形式写入，这里返回的 HTML 只包含本模块生成的
// 固定文案与一个按钮，不含用户数据。
function protoFilterMetaSuffixHtml() {
    if (!protoFilterActive()) return '';
    const summary = protoFilterDescribe() || '筛选中';
    return `<span class="proto-filter-meta-suffix">筛选中：${summary}`
        + ` <button type="button" class="proto-filter-reset-link">清空筛选</button></span>`;
}

function protoFilterReset() {
    PROTO_FILTER_FIELDS.forEach(key => { protoFilterState[key] = ''; });
    protoFilterWriteStorage();
    protoFilterSyncInputs();
    protoFilterAfterChange();
}

// 筛选变化后的统一出口：回第 1 页并重拉列表与侧栏计数（与「值班未处理」开关行为一致）
function protoFilterAfterChange() {
    protoFilterUpdateBadge();
    if (typeof state === 'undefined' || !state) return;
    state.filterPage = 1;
    if (typeof loadFilterData === 'function') loadFilterData();
    if (typeof loadFilterCounts === 'function') loadFilterCounts();
}

function protoFilterSyncInputs() {
    const panel = document.getElementById('proto-filter-panel');
    if (!panel) return;
    panel.querySelectorAll('select[data-proto-field]').forEach(select => {
        select.value = protoFilterState[select.dataset.protoField] || '';
    });
    panel.querySelectorAll('input[type="number"][data-proto-field]').forEach(input => {
        input.value = protoFilterState[input.dataset.protoField] || '';
    });
    panel.querySelectorAll('input[type="checkbox"][data-proto-field]').forEach(checkbox => {
        checkbox.checked = protoFilterState[checkbox.dataset.protoField] === '1';
    });
    const wrapNote = panel.querySelector('.proto-filter-wrap-note');
    if (wrapNote) {
        const from = protoFilterNormalizeHour(protoFilterState.hourFrom);
        const to = protoFilterNormalizeHour(protoFilterState.hourTo);
        wrapNote.hidden = !(from !== null && to !== null && from > to);
    }
}

function protoFilterUpdateBadge() {
    const badge = document.getElementById('proto-filter-count-badge');
    if (!badge) return;
    const count = protoFilterActiveCount();
    badge.textContent = count ? String(count) : '';
    badge.hidden = !count;
    const toggle = document.getElementById('proto-filter-toggle');
    if (toggle) toggle.classList.toggle('has-active', count > 0);
}

// 展开/收起工具栏第二行。收起后筛选仍然生效，靠按钮徽标与 meta 行摘要传达。
function protoFilterToggleRow(forceOpen) {
    const shouldOpen = typeof forceOpen === 'boolean'
        ? forceOpen
        : !document.body.classList.contains('proto-filter-row-open');
    document.body.classList.toggle('proto-filter-row-open', shouldOpen);
    const toggle = document.getElementById('proto-filter-toggle');
    if (toggle) {
        toggle.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
        toggle.classList.toggle('is-open', shouldOpen);
    }
}

function protoFilterBuildHourSelect(field, label) {
    let options = `<option value="">不限</option>`;
    for (let hour = 0; hour <= 23; hour += 1) {
        options += `<option value="${hour}">${hour} 时</option>`;
    }
    return `<label class="proto-filter-control-label" data-proto-label="${label}">`
        + `<span>${label}</span>`
        + `<select data-proto-field="${field}" aria-label="${label}">${options}</select>`
        + `</label>`;
}

function protoFilterBuildScoreInput(field, label) {
    return `<label class="proto-filter-control-label" data-proto-label="${label}">`
        + `<span>${label}</span>`
        + `<input type="number" data-proto-field="${field}" aria-label="${label}"`
        + ` placeholder="不限" min="0" max="100" step="1" inputmode="numeric">`
        + `</label>`;
}

// 三个条件各自成卡片：顶部小标签 + 控件，界限清晰、高度一致
function protoFilterBuildPanel() {
    const hourHint = '按收录时间的小时筛选（上海时区）。从 &gt; 到 视为跨零点区间，如 22 时–6 时。';
    const panel = document.createElement('div');
    panel.id = 'proto-filter-panel';
    panel.innerHTML = `
        <div class="proto-filter-group" title="开启后隐藏带「已报送/疑似已报送」徽章的条目">
            <span class="proto-filter-group-label">报送标签</span>
            <label class="proto-switch-control">
                <span class="proto-switch">
                    <input type="checkbox" data-proto-field="hideTagged" aria-label="隐藏已报送">
                    <span class="proto-switch-slider" aria-hidden="true"></span>
                </span>
                <span class="proto-switch-text">隐藏已报送</span>
            </label>
        </div>
        <div class="proto-filter-group" title="${hourHint}">
            <span class="proto-filter-group-label">收录时段</span>
            <div class="proto-filter-pair">
                ${protoFilterBuildHourSelect('hourFrom', '从')}
                <span class="proto-filter-range-sep">–</span>
                ${protoFilterBuildHourSelect('hourTo', '到')}
            </div>
            <span class="proto-filter-wrap-note" hidden>跨零点区间：从晚上段连到次日凌晨段</span>
        </div>
        <div class="proto-filter-group">
            <span class="proto-filter-group-label">分数</span>
            <div class="proto-filter-pair">
                ${protoFilterBuildScoreInput('minScore', '最低')}
                <span class="proto-filter-range-sep">–</span>
                ${protoFilterBuildScoreInput('maxScore', '最高')}
            </div>
        </div>
    `;
    return panel;
}

function protoFilterWireEvents(panel) {
    panel.addEventListener('change', event => {
        const target = event.target.closest('[data-proto-field]');
        if (!target) return;
        protoFilterState[target.dataset.protoField] = target.type === 'checkbox'
            ? (target.checked ? '1' : '')
            : target.value.trim();
        protoFilterWriteStorage();
        protoFilterSyncInputs();
        protoFilterAfterChange();
    });
}

function protoFilterWireGlobalEvents() {
    const toggle = document.getElementById('proto-filter-toggle');
    if (toggle) {
        toggle.addEventListener('click', () => protoFilterToggleRow());
    }
    // meta 行的「清空筛选」链接（由 innerHTML 重渲染，用委托）
    document.addEventListener('click', event => {
        if (event.target.closest('.proto-filter-reset-link')) {
            event.preventDefault();
            protoFilterReset();
        }
    });
}

function protoFilterInit() {
    protoFilterReadStorage();
    const panel = protoFilterBuildPanel();
    protoFilterWireEvents(panel);
    protoFilterWireGlobalEvents();
    const mount = document.getElementById('proto-filter-mount');
    if (mount) mount.appendChild(panel);
    protoFilterSyncInputs();
    protoFilterUpdateBadge();
    if (protoFilterActive()) protoFilterToggleRow(true);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', protoFilterInit);
} else {
    protoFilterInit();
}
