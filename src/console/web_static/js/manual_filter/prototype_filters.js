// Prototype-only module：人工筛选页「细化筛选」三方案对比原型。
// 全部内容（本文件、prototype.css、模板中的 proto-* 挂载点与切换器）在方案定稿后整体移除，
// 届时把胜出方案的挂载点改为正式 markup、本模块逻辑并入 manual_filter 现有模块即可。
//
// 三方案共用同一套控件（#proto-filter-panel），按 variant 移动到不同挂载点：
//   a = 工具栏第二行（常驻）  b = 「筛选」按钮 + 弹出面板  c = 左侧栏分组
// 后端参数：hour_from / hour_to（收录时间小时，0-23，跨零点回绕）、
//          duplicate_state（all / untagged / tagged）、min_score / max_score。

const PROTO_FILTER_STORAGE_KEY = 'proto_filter_state_v1';
const PROTO_VARIANT_STORAGE_KEY = 'proto_filter_variant';
const PROTO_FILTER_VARIANTS = ['a', 'b', 'c'];

const protoFilterState = {
    variant: 'a',
    hourFrom: '',
    hourTo: '',
    duplicateState: 'all',
    minScore: '',
    maxScore: ''
};

function protoFilterReadStorage() {
    try {
        const rawState = localStorage.getItem(PROTO_FILTER_STORAGE_KEY);
        if (rawState) {
            const saved = JSON.parse(rawState);
            ['hourFrom', 'hourTo', 'duplicateState', 'minScore', 'maxScore'].forEach(key => {
                if (saved && typeof saved[key] === 'string') protoFilterState[key] = saved[key];
            });
        }
        const savedVariant = localStorage.getItem(PROTO_VARIANT_STORAGE_KEY);
        if (PROTO_FILTER_VARIANTS.includes(savedVariant)) protoFilterState.variant = savedVariant;
    } catch (error) {
        // 存储不可用时按默认值处理
    }
}

function protoFilterWriteStorage() {
    try {
        localStorage.setItem(PROTO_FILTER_STORAGE_KEY, JSON.stringify({
            hourFrom: protoFilterState.hourFrom,
            hourTo: protoFilterState.hourTo,
            duplicateState: protoFilterState.duplicateState,
            minScore: protoFilterState.minScore,
            maxScore: protoFilterState.maxScore
        }));
        localStorage.setItem(PROTO_VARIANT_STORAGE_KEY, protoFilterState.variant);
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
    if (value === null || value === undefined || value === '') return null;
    const score = Number(value);
    return Number.isFinite(score) ? score : null;
}

// 「筛选中」的维度数（B 方案按钮徽标、重置按钮显隐共用）
function protoFilterActiveCount() {
    let count = 0;
    if (protoFilterNormalizeHour(protoFilterState.hourFrom) !== null) count += 1;
    if (protoFilterNormalizeHour(protoFilterState.hourTo) !== null) count += 1;
    if (protoFilterState.duplicateState !== 'all') count += 1;
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
    if (protoFilterState.duplicateState === 'untagged' || protoFilterState.duplicateState === 'tagged') {
        pairs.push(['duplicate_state', protoFilterState.duplicateState]);
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
    if (protoFilterState.duplicateState === 'untagged') parts.push('未报送');
    if (protoFilterState.duplicateState === 'tagged') parts.push('已报送');
    const minScore = protoFilterNormalizeScore(protoFilterState.minScore);
    const maxScore = protoFilterNormalizeScore(protoFilterState.maxScore);
    if (minScore !== null && maxScore !== null) parts.push(`分数 ${minScore}–${maxScore}`);
    else if (minScore !== null) parts.push(`分数 ≥ ${minScore}`);
    else if (maxScore !== null) parts.push(`分数 ≤ ${maxScore}`);
    return parts.join(' · ');
}

// meta 行后缀（含「重置」入口）。基础文案由 syncFilterToolbarState 以文本形式写入，
// 这里返回的 HTML 只包含本模块生成的固定文案与一个按钮，不含用户数据。
function protoFilterMetaSuffixHtml() {
    if (!protoFilterActive()) return '';
    const summary = protoFilterDescribe() || '筛选中';
    return `<span class="proto-filter-meta-suffix">筛选中：${summary}`
        + ` <button type="button" class="proto-filter-reset-link">重置筛选</button></span>`;
}

function protoFilterReset() {
    protoFilterState.hourFrom = '';
    protoFilterState.hourTo = '';
    protoFilterState.duplicateState = 'all';
    protoFilterState.minScore = '';
    protoFilterState.maxScore = '';
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
    panel.querySelectorAll('input[data-proto-field]').forEach(input => {
        input.value = protoFilterState[input.dataset.protoField] || '';
    });
    panel.querySelectorAll('[data-proto-duplicate]').forEach(btn => {
        const isActive = btn.dataset.protoDuplicate === protoFilterState.duplicateState;
        btn.classList.toggle('is-active', isActive);
        btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });
    const resetBtn = panel.querySelector('.proto-filter-reset');
    if (resetBtn) resetBtn.hidden = !protoFilterActive();
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

function protoFilterSetVariant(variant) {
    const normalized = PROTO_FILTER_VARIANTS.includes(variant) ? variant : 'a';
    protoFilterState.variant = normalized;
    protoFilterWriteStorage();
    document.body.dataset.protoVariant = normalized;
    protoFilterMountPanel();
    document.querySelectorAll('.proto-variant-switcher button').forEach(btn => {
        const isActive = btn.dataset.protoVariant === normalized;
        btn.classList.toggle('is-active', isActive);
        btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });
    if (normalized !== 'b') protoFilterClosePopover();
}

function protoFilterMountPanel() {
    const panel = document.getElementById('proto-filter-panel');
    if (!panel) return;
    const mount = document.getElementById(`proto-filter-mount-${protoFilterState.variant}`);
    if (mount && panel.parentElement !== mount) mount.appendChild(panel);
    protoFilterSyncInputs();
}

function protoFilterOpenPopover() {
    document.body.classList.add('proto-filter-popover-open');
    const toggle = document.getElementById('proto-filter-toggle');
    if (toggle) toggle.setAttribute('aria-expanded', 'true');
}

function protoFilterClosePopover() {
    document.body.classList.remove('proto-filter-popover-open');
    const toggle = document.getElementById('proto-filter-toggle');
    if (toggle) toggle.setAttribute('aria-expanded', 'false');
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

function protoFilterBuildPanel() {
    const hourHint = '按收录时间的小时筛选（上海时区）。从 &gt; 到 视为跨零点区间，如 22 时–6 时。';
    const panel = document.createElement('div');
    panel.id = 'proto-filter-panel';
    panel.innerHTML = `
        <div class="proto-filter-group" title="${hourHint}">
            <span class="proto-filter-group-label">收录时段</span>
            ${protoFilterBuildHourSelect('hourFrom', '从')}
            <span class="proto-filter-range-sep">–</span>
            ${protoFilterBuildHourSelect('hourTo', '到')}
        </div>
        <div class="proto-filter-group" title="按报送查重徽章筛选：「未报送」只看无标签条目，「已报送」只看带已报送/疑似已报送标签的条目">
            <span class="proto-filter-group-label">报送标签</span>
            <div class="proto-filter-segmented" role="group" aria-label="报送标签筛选">
                <button type="button" data-proto-duplicate="all" aria-pressed="true">全部</button>
                <button type="button" data-proto-duplicate="untagged" aria-pressed="false">未报送</button>
                <button type="button" data-proto-duplicate="tagged" aria-pressed="false">已报送</button>
            </div>
        </div>
        <div class="proto-filter-group">
            <span class="proto-filter-group-label">分数</span>
            ${protoFilterBuildScoreInput('minScore', '最低')}
            <span class="proto-filter-range-sep">–</span>
            ${protoFilterBuildScoreInput('maxScore', '最高')}
        </div>
        <span class="proto-filter-wrap-note" hidden>跨零点区间：从晚上段连到次日凌晨段</span>
        <button type="button" class="btn btn-secondary proto-filter-reset" hidden>重置筛选</button>
    `;
    return panel;
}

function protoFilterWireEvents(panel) {
    panel.addEventListener('change', event => {
        const target = event.target.closest('[data-proto-field]');
        if (!target) return;
        protoFilterState[target.dataset.protoField] = target.value.trim();
        protoFilterWriteStorage();
        protoFilterSyncInputs();
        protoFilterAfterChange();
    });
    panel.addEventListener('click', event => {
        const segmentBtn = event.target.closest('[data-proto-duplicate]');
        if (segmentBtn) {
            protoFilterState.duplicateState = segmentBtn.dataset.protoDuplicate;
            protoFilterWriteStorage();
            protoFilterSyncInputs();
            protoFilterAfterChange();
            return;
        }
        if (event.target.closest('.proto-filter-reset')) protoFilterReset();
    });
}

function protoFilterWireGlobalEvents() {
    // 方案切换器
    document.querySelectorAll('.proto-variant-switcher button').forEach(btn => {
        btn.addEventListener('click', () => protoFilterSetVariant(btn.dataset.protoVariant));
    });
    // B 方案：弹出面板开关
    const toggle = document.getElementById('proto-filter-toggle');
    if (toggle) {
        toggle.addEventListener('click', event => {
            event.stopPropagation();
            if (document.body.classList.contains('proto-filter-popover-open')) {
                protoFilterClosePopover();
            } else {
                protoFilterOpenPopover();
            }
        });
    }
    // meta 行的「重置筛选」链接（由 innerHTML 重渲染，用委托）
    document.addEventListener('click', event => {
        if (event.target.closest('.proto-filter-reset-link')) {
            event.preventDefault();
            protoFilterReset();
        }
        // B 方案点外部关闭：命中面板或开关内部时不处理
        if (document.body.classList.contains('proto-filter-popover-open')
            && !event.target.closest('#proto-filter-panel')
            && !event.target.closest('#proto-filter-toggle')) {
            protoFilterClosePopover();
        }
    });
    // Escape：让位给检索抽屉与原文抽屉，它们开着时不处理
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        if (!document.body.classList.contains('proto-filter-popover-open')) return;
        if (document.body.classList.contains('search-drawer-open')) return;
        if (document.body.classList.contains('content-drawer-open')) return;
        protoFilterClosePopover();
    });
}

function protoFilterInit() {
    protoFilterReadStorage();
    const panel = protoFilterBuildPanel();
    protoFilterWireEvents(panel);
    protoFilterWireGlobalEvents();
    const mount = document.getElementById(`proto-filter-mount-${protoFilterState.variant}`)
        || document.getElementById('proto-filter-mount-a');
    if (mount) mount.appendChild(panel);
    protoFilterSetVariant(protoFilterState.variant);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', protoFilterInit);
} else {
    protoFilterInit();
}
