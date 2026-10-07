// Manual Filter JS - Discard Tab

// --- Discard Tab Logic ---

const DISCARD_PAGE_SIZE = 30;

// 放弃页条件状态：与筛选页 filter_refine.js 完全独立，不写 localStorage。
// bucket 是「分类」分段按钮（region + sentiment 的组合键）；
// batchDecidedAt（同一批）与 since（时间预设）同属「放弃时间」下拉，天然互斥。
const discardFilterState = {
    bucket: 'all',
    since: '',
    minScore: '',
    maxScore: '',
    batchDecidedAt: ''
};

// 选择状态有两种模式：'page'（勾选本页若干行）与 'all'（扩展到全部匹配）。
// 用显式字段区分，不靠 DOM 推断；翻页 / 改条件 / 列表重新加载都会退出全部模式。
let discardSelectionMode = 'page';
const discardSelection = new Set();
// 最近一次列表响应的 total，用于「选择全部 M 条」与恢复后判断当前页是否越界
let discardLastTotal = 0;
// 最近批次下拉数据：[{ decided_at, count }]，由 /discarded-batches 提供
let discardBatches = [];

const DISCARD_BUCKET_KEYS = {
    all: { region: '', sentiment: '' },
    internal_positive: { region: 'internal', sentiment: 'positive' },
    internal_negative: { region: 'internal', sentiment: 'negative' },
    external_positive: { region: 'external', sentiment: 'positive' },
    external_negative: { region: 'external', sentiment: 'negative' }
};

const DISCARD_SINCE_DAYS = { today: 0, '3d': 2, '7d': 6 };
const DISCARD_SINCE_PRESETS = ['', 'today', '3d', '7d'];

// 用 Intl 按 Asia/Shanghai 计算自然日，不依赖浏览器本地时区
function discardShanghaiToday() {
    const formatter = new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit'
    });
    return formatter.format(new Date());
}

function discardSinceToDate(value) {
    if (!DISCARD_SINCE_DAYS.hasOwnProperty(value)) return '';
    const back = DISCARD_SINCE_DAYS[value];
    const today = discardShanghaiToday();
    const base = new Date(`${today}T00:00:00Z`);
    base.setUTCDate(base.getUTCDate() - back);
    return base.toISOString().slice(0, 10);
}

// 唯一的条件参数来源：列表查询串与恢复 body 都从这里生成（同口径不变量）。
// 未启用的条件不产生条目；min/max 分数转成 Number。
function discardFilterPairs() {
    const pairs = [];
    const bucket = DISCARD_BUCKET_KEYS[discardFilterState.bucket];
    if (bucket) {
        if (bucket.region) pairs.push(['region', bucket.region]);
        if (bucket.sentiment) pairs.push(['sentiment', bucket.sentiment]);
    }
    const since = discardSinceToDate(discardFilterState.since);
    if (since) pairs.push(['decided_since', since]);
    const minScore = Number(discardFilterState.minScore);
    if (discardFilterState.minScore !== '' && Number.isFinite(minScore)) {
        pairs.push(['min_score', String(minScore)]);
    }
    const maxScore = Number(discardFilterState.maxScore);
    if (discardFilterState.maxScore !== '' && Number.isFinite(maxScore)) {
        pairs.push(['max_score', String(maxScore)]);
    }
    if (discardFilterState.batchDecidedAt) {
        pairs.push(['batch_decided_at', discardFilterState.batchDecidedAt]);
    }
    return pairs;
}

function discardFilterActive() {
    return Boolean(state.discardQuery) || discardFilterPairs().length > 0;
}

// 恢复 body：与列表查询同一份 pairs（同口径约束）
function discardFilterRequestBody(dryRun) {
    const body = { dry_run: dryRun };
    discardFilterPairs().forEach(([key, value]) => {
        body[key] = key === 'min_score' || key === 'max_score'
            ? Number(value)
            : value;
    });
    return body;
}

function discardFilterDescribe() {
    const parts = [];
    if (state.discardQuery) parts.push(`关键词「${state.discardQuery}」`);
    const bucket = DISCARD_BUCKET_KEYS[discardFilterState.bucket];
    const bucketLabels = {
        internal_positive: '京内正面',
        internal_negative: '京内负面',
        external_positive: '京外正面',
        external_negative: '京外负面'
    };
    if (bucket && (bucket.region || bucket.sentiment)) {
        parts.push(bucketLabels[discardFilterState.bucket]);
    }
    const sinceLabels = { today: '今天', '3d': '近 3 天', '7d': '近 7 天' };
    if (discardFilterState.since) parts.push(sinceLabels[discardFilterState.since]);
    const hasMin = discardFilterState.minScore !== '';
    const hasMax = discardFilterState.maxScore !== '';
    if (hasMin && hasMax) parts.push(`分数 ${discardFilterState.minScore}–${discardFilterState.maxScore}`);
    else if (hasMin) parts.push(`分数 ≥ ${discardFilterState.minScore}`);
    else if (hasMax) parts.push(`分数 ≤ ${discardFilterState.maxScore}`);
    if (discardFilterState.batchDecidedAt) {
        parts.push(`${formatDiscardTime(discardFilterState.batchDecidedAt)} 这一批`);
    }
    return parts.join(' · ');
}

async function loadDiscardData() {
    syncDiscardToolbar();
    clearDiscardSelection();
    setDiscardControlsDisabled(false);
    elements.discardList.innerHTML = '<div class="loading">加载中...</div>';
    // 批次下拉与列表并行刷新（进页、恢复成功、切班次都会走到这里）
    refreshDiscardBatches();
    try {
        const params = new URLSearchParams({
            limit: String(DISCARD_PAGE_SIZE),
            offset: `${(state.discardPage - 1) * DISCARD_PAGE_SIZE}`
        });
        if (state.discardQuery) params.set('q', state.discardQuery);
        discardFilterPairs().forEach(([key, value]) => params.set(key, value));
        const res = await workspaceFetch(`${API_BASE}/discarded?${params.toString()}`);
        const data = await res.json();
        discardLastTotal = data.total || 0;
        renderDiscardList(data.items);
        if (!(data.items || []).length && state.discardPage > 1) {
            // 当前页已越界（并发恢复后总数变少），退回上一页重试
            state.discardPage -= 1;
            return loadDiscardData();
        }
        updatePagination('discard', data.total || 0, state.discardPage, data.limit);
        updateDiscardSearchMeta(data.total || 0);
    } catch (e) {
        elements.discardList.innerHTML = '<div class="error">加载数据失败</div>';
        discardLastTotal = 0;
        updateDiscardSearchMeta(null);
    }
}

async function refreshDiscardBatches() {
    try {
        const res = await workspaceFetch(`${API_BASE}/discarded-batches`);
        if (res.ok) {
            const data = await res.json();
            discardBatches = (data.items || []).filter(batch => batch && batch.decided_at);
        }
    } catch (e) {
        return; // 批次下拉刷新失败不影响列表
    }
    rebuildDiscardBatchOptions();
}

// 用最近批次重建下拉的 optgroup；当前生效的批次不在最新列表里时，
// 在组首插入对应选项，保证控件如实显示当前条件，绝不显示成「全部」。
function rebuildDiscardBatchOptions() {
    const group = document.getElementById('discard-batch-group');
    if (!group) return;
    const current = discardFilterState.batchDecidedAt;
    const known = discardBatches.some(batch => String(batch.decided_at) === current);
    group.innerHTML = discardBatches.map(batch => {
        const raw = String(batch.decided_at);
        return `<option value="${escapeDiscardAttr(raw)}">${escapeDiscardHtml(
            `${formatDiscardTime(raw)} · ${batch.count} 条`
        )}</option>`;
    }).join('')
        + (current && !known
            ? `<option value="${escapeDiscardAttr(current)}">${escapeDiscardHtml(
                `${formatDiscardTime(current)} 这一批`
            )}</option>`
            : '');
    group.hidden = !group.children.length;
    syncDiscardSinceValue();
}

// 下拉当前值由状态驱动：批次优先（互斥状态下两者不会同时设置）
function syncDiscardSinceValue() {
    if (!elements.discardSinceSelect) return;
    elements.discardSinceSelect.value =
        discardFilterState.batchDecidedAt || discardFilterState.since || '';
}

function syncDiscardToolbar() {
    if (elements.discardSearchInput) {
        elements.discardSearchInput.value = state.discardQuery || '';
    }
    syncDiscardSearchClearButton();
    syncDiscardFilterControls();
}

function syncDiscardSearchClearButton() {
    if (!elements.discardSearchClear) return;
    const hasCondition = Boolean(
        elements.discardSearchInput?.value.trim()
        || state.discardQuery
    );
    elements.discardSearchClear.hidden = !hasCondition;
}

function syncDiscardFilterControls() {
    document.querySelectorAll('[data-discard-bucket]').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.discardBucket === discardFilterState.bucket);
    });
    syncDiscardSinceValue();
    if (elements.discardMinScore) {
        elements.discardMinScore.value = discardFilterState.minScore;
    }
    if (elements.discardMaxScore) {
        elements.discardMaxScore.value = discardFilterState.maxScore;
    }
    discardRefineUpdateBadge();
}

// --- 「筛选」折叠开关（交互与筛选页细化筛选一致） ---

// 开关徽标只统计条件区里的维数（分类 / 放弃时间 / 分数）；
// 关键词有自己的清除按钮，批次在 meta 摘要里，都不计入
function discardRefineActiveCount() {
    let count = 0;
    if (discardFilterState.bucket !== 'all') count += 1;
    if (discardFilterState.since || discardFilterState.batchDecidedAt) count += 1;
    if (discardFilterState.minScore !== '') count += 1;
    if (discardFilterState.maxScore !== '') count += 1;
    return count;
}

function discardRefineUpdateBadge() {
    if (!elements.discardRefineBadge) return;
    const count = discardRefineActiveCount();
    elements.discardRefineBadge.textContent = count ? String(count) : '';
    elements.discardRefineBadge.hidden = !count;
    elements.discardRefineToggle?.classList.toggle('has-active', count > 0);
}

// 展开/收起条件区。收起后筛选仍然生效。
function discardRefineToggleRow(forceOpen) {
    const shouldOpen = typeof forceOpen === 'boolean'
        ? forceOpen
        : !document.body.classList.contains('discard-refine-row-open');
    document.body.classList.toggle('discard-refine-row-open', shouldOpen);
    elements.discardRefineToggle?.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
    elements.discardRefineToggle?.classList.toggle('is-open', shouldOpen);
}

function discardRefineInit() {
    // 载入时条件区里有激活条件（如刷新前收着筛选）则自动展开
    if (discardRefineActiveCount() > 0) discardRefineToggleRow(true);
}

async function applyDiscardSearch() {
    state.discardQuery = elements.discardSearchInput
        ? elements.discardSearchInput.value.trim()
        : '';
    state.discardPage = 1;
    await loadDiscardData();
}

async function clearDiscardSearch() {
    state.discardQuery = '';
    state.discardPage = 1;
    await loadDiscardData();
}

// 「清空筛选」：清掉包括关键词、批次在内的全部条件
async function clearDiscardFilters() {
    state.discardQuery = '';
    discardFilterState.bucket = 'all';
    discardFilterState.since = '';
    discardFilterState.minScore = '';
    discardFilterState.maxScore = '';
    discardFilterState.batchDecidedAt = '';
    state.discardPage = 1;
    await loadDiscardData();
}

// 「放弃时间」下拉变化：预设与批次互斥；选中批次时清掉其他全部条件（含关键词）
async function handleDiscardSinceChange(value) {
    if (DISCARD_SINCE_PRESETS.includes(value)) {
        discardFilterState.since = value;
        discardFilterState.batchDecidedAt = '';
    } else if (value) {
        state.discardQuery = '';
        discardFilterState.bucket = 'all';
        discardFilterState.since = '';
        discardFilterState.minScore = '';
        discardFilterState.maxScore = '';
        discardFilterState.batchDecidedAt = value;
    }
    state.discardPage = 1;
    await loadDiscardData();
}

async function applyDiscardFilterChange() {
    state.discardPage = 1;
    await loadDiscardData();
}

function updateDiscardSearchMeta(total) {
    if (!elements.discardSearchMeta) return;
    if (total === null || total === undefined) {
        elements.discardSearchMeta.textContent = '';
    } else if (discardFilterActive()) {
        elements.discardSearchMeta.innerHTML =
            `${escapeDiscardHtml(`筛选中：${discardFilterDescribe()} · 命中 ${total} 条`)}`
            + ` <button type="button" class="discard-filter-clear-link">清空筛选</button>`;
    } else {
        elements.discardSearchMeta.textContent = `共 ${total} 条已放弃新闻`;
    }
}

function formatDiscardTime(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    const pad = n => String(n).padStart(2, '0');
    return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function renderDiscardList(items) {
    if (!items.length) {
        if (discardFilterActive()) {
            elements.discardList.innerHTML = `
                <div class="empty discard-empty">
                    <span>没有符合条件的已放弃新闻</span>
                    <button type="button" class="btn btn-secondary discard-empty-clear"
                        onclick="clearDiscardFilters()">清空筛选</button>
                </div>
            `;
        } else {
            elements.discardList.innerHTML = '<div class="empty">当前没有已放弃新闻</div>';
        }
        return;
    }

    elements.discardList.innerHTML = items.map(item => {
        const title = item.title || '(No Title)';
        const checked = discardSelectionMode === 'all' || discardSelection.has(item.article_id)
            ? ' checked'
            : '';
        return `
        <div class="article-card discard-item" data-id="${escapeDiscardAttr(item.article_id || '')}" data-version="${Number(item.version) || 0}">
            <input type="checkbox" class="discard-row-check" data-id="${escapeDiscardAttr(item.article_id || '')}"
                aria-label="选择本条"${checked}>
            <h4 class="article-title discard-item-title" title="${escapeDiscardAttr(title)}">${escapeDiscardHtml(title)}</h4>
            <div class="discard-item-meta">
                <span class="discard-item-source">来源: ${escapeDiscardHtml(item.source || '-')}</span>
                <span class="discard-item-time">${escapeDiscardHtml(formatDiscardTime(item.decided_at))}</span>
                ${renderScoreFeedbackControl(item)}
            </div>
            <div class="discard-card-actions">
                <select class="status-select discard-restore-select" data-id="${escapeDiscardAttr(item.article_id || '')}" aria-label="恢复位置">
                    <option value="">恢复到</option>
                    <option value="zongbao:selected">综报采纳</option>
                    <option value="zongbao:backup">综报备选</option>
                    <option value="wanbao:selected">晚报采纳</option>
                    <option value="wanbao:backup">晚报备选</option>
                    <option value="pending">待处理</option>
                </select>
            </div>
        </div>
    `;
    }).join('');

    bindDiscardRestoreControls();
    syncDiscardBulkBar();
}

function escapeDiscardHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function escapeDiscardAttr(value) {
    return escapeDiscardHtml(value);
}

function bindDiscardRestoreControls() {
    const selects = elements.discardList.querySelectorAll('.discard-restore-select');
    selects.forEach(select => {
        select.addEventListener('change', handleDiscardRestoreChange);
    });
}

function parseDiscardRestoreTarget(rawValue) {
    if (rawValue.includes(':')) {
        const [rt, status] = rawValue.split(':');
        return {
            status,
            reportType: rt === 'wanbao' ? 'wanbao' : 'zongbao'
        };
    }

    return {
        status: rawValue,
        reportType: state.filterAssignReportType
    };
}

function buildDiscardRestorePayload(id, status, reportType) {
    return {
        selected_ids: status === 'selected' ? [id] : [],
        backup_ids: status === 'backup' ? [id] : [],
        discarded_ids: [],
        pending_ids: status === 'pending' ? [id] : [],
        versions: collectManualReviewVersions([id]),
        report_type: reportType
    };
}

// 从放弃列表 DOM 收集版本号（放弃页条目不在 state.reviewItems 里）
function collectDiscardRowVersions(ids) {
    const versions = {};
    const idSet = new Set(ids);
    elements.discardList.querySelectorAll('.article-card[data-id]').forEach(card => {
        if (!idSet.has(card.dataset.id)) return;
        const version = Number(card.dataset.version);
        if (Number.isInteger(version) && version > 0) versions[card.dataset.id] = version;
    });
    return versions;
}

function getDiscardRestoreLabel(rawValue) {
    const labels = {
        'zongbao:selected': '综报采纳',
        'zongbao:backup': '综报备选',
        'wanbao:selected': '晚报采纳',
        'wanbao:backup': '晚报备选',
        pending: '待处理'
    };
    return labels[rawValue] || '目标位置';
}

// 恢复成功后按「恢复前的总数 - 本次恢复条数」估算新总数；若当前页越界则
// 退到最后一页（至少第 1 页），不停在空页上。列表加载另有兜底守卫。
function clampDiscardPageAfterRestore(restoredCount = 1) {
    const remaining = Math.max(0, discardLastTotal - restoredCount);
    const totalPages = Math.max(1, Math.ceil(remaining / DISCARD_PAGE_SIZE));
    if (state.discardPage > totalPages) state.discardPage = totalPages;
}

async function handleDiscardRestoreChange(event) {
    const select = event.target;
    const rawValue = select.value;
    if (!rawValue) return;

    const id = select.dataset.id;
    if (!id) {
        select.value = '';
        return;
    }

    const { status, reportType } = parseDiscardRestoreTarget(rawValue);
    select.disabled = true;
    try {
        const res = await workspaceFetch(`${API_BASE}/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(buildDiscardRestorePayload(id, status, reportType))
        });
        await requireManualMutationSuccess(res, 'failed to restore discarded item');

        showToast(`已恢复到${getDiscardRestoreLabel(rawValue)}`);
        loadStats();
        clampDiscardPageAfterRestore(1);
        loadDiscardData();
    } catch (e) {
        showToast(e.message || '恢复失败', 'error');
        select.value = '';
        select.disabled = false;
    }
}

// --- 批量选择与恢复（范围由选择决定：本页勾选 → 可扩展到全部匹配） ---

function clearDiscardSelection() {
    discardSelectionMode = 'page';
    discardSelection.clear();
    syncDiscardBulkBar();
}

// 退出全部匹配模式：回到本页选择，清空勾选与行内勾选状态
function exitDiscardAllMode() {
    discardSelectionMode = 'page';
    discardSelection.clear();
    elements.discardList.querySelectorAll('.discard-row-check').forEach(box => {
        box.checked = false;
    });
}

function selectedDiscardIds() {
    return [...discardSelection];
}

function syncDiscardBulkBar() {
    if (!elements.discardBulkBar) return;
    const checkboxes = [...elements.discardList.querySelectorAll('.discard-row-check')];
    const rows = checkboxes.length;
    const checkedCount = checkboxes.filter(box => box.checked).length;
    const allPageChecked = rows > 0 && checkedCount === rows;
    const hasSelection = discardSelectionMode === 'all' || discardSelection.size > 0;

    if (elements.discardSelectAll) {
        elements.discardSelectAll.checked = discardSelectionMode === 'all' || allPageChecked;
        elements.discardSelectAll.indeterminate = discardSelectionMode === 'page'
            && checkedCount > 0
            && !allPageChecked;
        elements.discardSelectAll.disabled = rows === 0;
    }
    if (elements.discardSelectAllLabel) {
        if (discardSelectionMode === 'all') {
            elements.discardSelectAllLabel.textContent =
                `已选全部 ${discardLastTotal} 条（按当前筛选）`;
        } else if (discardSelection.size > 0) {
            elements.discardSelectAllLabel.textContent = `已选 ${discardSelection.size} 条`;
        } else {
            elements.discardSelectAllLabel.textContent = '全选本页';
        }
    }
    // 「选择全部 M 条」的扩展条件：本页全选 + 总数超过一页 + 至少一个筛选条件。
    // 无条件时不出现——这是「按条件恢复至少需要一个条件」护栏的表现方式。
    const canExtend = discardSelectionMode === 'page'
        && allPageChecked
        && discardLastTotal > rows
        && discardFilterActive();
    if (elements.discardSelectAllMatchedBtn) {
        elements.discardSelectAllMatchedBtn.textContent = `选择全部 ${discardLastTotal} 条`;
        elements.discardSelectAllMatchedBtn.hidden = !canExtend;
    }
    if (elements.discardExitAllBtn) {
        elements.discardExitAllBtn.hidden = discardSelectionMode !== 'all';
    }
    const targets = elements.discardBulkTarget
        ? elements.discardBulkTarget.querySelectorAll('option[data-bulk-target]')
        : [];
    targets.forEach(option => {
        if (discardSelectionMode === 'all') {
            option.disabled = option.value !== 'pending';
            option.title = option.value !== 'pending' ? '跨页恢复只能到待处理' : '';
        } else {
            option.disabled = !hasSelection;
            option.title = '';
        }
    });
    if (elements.discardBulkTarget) {
        elements.discardBulkTarget.disabled = !hasSelection;
    }
}

function handleDiscardRowCheckChange(event) {
    const box = event.target;
    if (!box.classList.contains('discard-row-check')) return;
    if (discardSelectionMode === 'all' && !box.checked) {
        // 全部模式下取消任意一行：退回本页选择模式（该行不勾，其余行保持勾选）
        discardSelectionMode = 'page';
        discardSelection.clear();
        elements.discardList.querySelectorAll('.discard-row-check').forEach(other => {
            if (other === box) return;
            other.checked = true;
            discardSelection.add(other.dataset.id);
        });
    } else if (box.checked) {
        discardSelection.add(box.dataset.id);
    } else {
        discardSelection.delete(box.dataset.id);
    }
    syncDiscardBulkBar();
}

function handleDiscardSelectAllChange(event) {
    const checked = event.target.checked;
    if (discardSelectionMode === 'all' && !checked) {
        exitDiscardAllMode();
        syncDiscardBulkBar();
        return;
    }
    elements.discardList.querySelectorAll('.discard-row-check').forEach(box => {
        box.checked = checked;
        if (checked) discardSelection.add(box.dataset.id);
        else discardSelection.delete(box.dataset.id);
    });
    syncDiscardBulkBar();
}

function handleDiscardSelectAllMatchedClick() {
    discardSelectionMode = 'all';
    syncDiscardBulkBar();
}

function handleDiscardExitAllClick() {
    exitDiscardAllMode();
    syncDiscardBulkBar();
}

function setDiscardControlsDisabled(disabled) {
    if (elements.discardBulkBar) {
        elements.discardBulkBar.querySelectorAll('input, select, button').forEach(node => {
            node.disabled = disabled;
        });
    }
    elements.discardList.querySelectorAll('.discard-row-check, .discard-restore-select')
        .forEach(node => {
            node.disabled = disabled;
        });
}

async function handleDiscardBulkTargetChange(event) {
    const select = event.target;
    const rawValue = select.value;
    if (!rawValue) return;
    select.value = '';
    if (discardSelectionMode === 'all') {
        await restoreAllMatching(rawValue);
        return;
    }
    if (!discardSelection.size) return;

    const ids = selectedDiscardIds();
    const { status, reportType } = parseDiscardRestoreTarget(rawValue);
    const payload = {
        selected_ids: status === 'selected' ? ids : [],
        backup_ids: status === 'backup' ? ids : [],
        discarded_ids: [],
        pending_ids: status === 'pending' ? ids : [],
        versions: collectDiscardRowVersions(ids),
        report_type: reportType
    };
    setDiscardControlsDisabled(true);
    try {
        const res = await workspaceFetch(`${API_BASE}/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        await requireManualMutationSuccess(res, 'failed to restore discarded items');

        // 先清空选择再提示，保证提示出现时批量栏状态已同步
        discardSelection.clear();
        showToast(`已恢复 ${ids.length} 条到${getDiscardRestoreLabel(rawValue)}`);
        loadStats();
        clampDiscardPageAfterRestore(ids.length);
        loadDiscardData();
    } catch (e) {
        showToast(e.message || '批量恢复失败', 'error');
        discardSelection.clear();
        loadDiscardData();
    }
}

// 全部匹配模式：只恢复到待处理，先 dry_run 预览再 confirm 执行
async function restoreAllMatching(rawValue) {
    const { status } = parseDiscardRestoreTarget(rawValue);
    if (status !== 'pending') {
        showToast('跨页恢复只能到待处理', 'error');
        return;
    }
    setDiscardControlsDisabled(true);
    try {
        const preview = await workspaceFetch(`${API_BASE}/bulk-restore`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(discardFilterRequestBody(true))
        }).then(response => requireManualMutationSuccess(response, 'failed to preview bulk restore'));
        if (!(preview.matched > 0)) {
            showToast('没有符合条件的已放弃新闻');
            setDiscardControlsDisabled(false);
            return;
        }
        const summary = discardFilterDescribe() || '当前筛选';
        const confirmed = window.confirm(
            `确定把「${summary}」的 ${preview.matched} 条已放弃新闻恢复到待处理吗？`
        );
        if (!confirmed) {
            setDiscardControlsDisabled(false);
            return;
        }
        const result = await workspaceFetch(`${API_BASE}/bulk-restore`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(discardFilterRequestBody(false))
        }).then(response => requireManualMutationSuccess(response, 'failed to bulk restore'));
        showToast(`已恢复 ${result.updated} 条到待处理`);
        // 恢复成功后退出全部模式，回第 1 页重新加载
        exitDiscardAllMode();
        state.discardPage = 1;
        loadStats();
        loadDiscardData();
    } catch (e) {
        showToast(e.message || '批量恢复失败', 'error');
        exitDiscardAllMode();
        loadDiscardData();
    }
}
