// Manual Filter JS - Discard Tab

// --- Discard Tab Logic ---

const DISCARD_PAGE_SIZE = 30;

// 放弃页条件状态：与筛选页 filter_refine.js 完全独立，不写 localStorage。
// bucket 是「分类」分段按钮（region + sentiment 的组合键）。
const discardFilterState = {
    bucket: 'all',
    since: '',
    minScore: '',
    maxScore: '',
    batchDecidedAt: ''
};

// 本页勾选的条目 id（只针对当前页，翻页/改条件/重新加载即清空）
const discardSelection = new Set();
// 最近一次列表响应的 total，用于恢复后判断当前页是否越界
let discardLastTotal = 0;

const DISCARD_BUCKET_KEYS = {
    all: { region: '', sentiment: '' },
    internal_positive: { region: 'internal', sentiment: 'positive' },
    internal_negative: { region: 'internal', sentiment: 'negative' },
    external_positive: { region: 'external', sentiment: 'positive' },
    external_negative: { region: 'external', sentiment: 'negative' }
};

const DISCARD_SINCE_DAYS = { today: 0, '3d': 2, '7d': 6 };

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

// 恢复 body：与列表查询同一份 pairs（J4/M8 的同口径约束）
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
    if (elements.discardSinceSelect) {
        elements.discardSinceSelect.value = discardFilterState.since;
    }
    if (elements.discardMinScore) {
        elements.discardMinScore.value = discardFilterState.minScore;
    }
    if (elements.discardMaxScore) {
        elements.discardMaxScore.value = discardFilterState.maxScore;
    }
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

// 「只看这一批」：清掉其他所有条件，只保留批次（叠加旧条件会让「这一批 N 条」失真）
async function filterDiscardBatch(rawDecidedAt) {
    state.discardQuery = '';
    discardFilterState.bucket = 'all';
    discardFilterState.since = '';
    discardFilterState.minScore = '';
    discardFilterState.maxScore = '';
    discardFilterState.batchDecidedAt = rawDecidedAt;
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
        // 批次条件是可单独移除的 chip；其余条件由「清空筛选」统一清掉
        const batchChip = discardFilterState.batchDecidedAt
            ? ` <span class="discard-batch-chip">${escapeDiscardHtml(
                `${formatDiscardTime(discardFilterState.batchDecidedAt)} 这一批`
            )}<button type="button" class="discard-batch-clear"
                aria-label="取消批次条件" title="取消批次条件">✕</button></span>`
            : '';
        elements.discardSearchMeta.innerHTML =
            `${escapeDiscardHtml(`筛选中：${discardFilterDescribe()} · 命中 ${total} 条`)}`
            + ` <button type="button" class="discard-filter-clear-link">清空筛选</button>`
            + batchChip;
    } else {
        elements.discardSearchMeta.textContent = `共 ${total} 条已放弃新闻`;
    }
    syncDiscardBulkRestoreButton(total);
}

function syncDiscardBulkRestoreButton(total) {
    if (!elements.discardBulkRestoreBtn) return;
    const active = discardFilterActive();
    elements.discardBulkRestoreBtn.hidden = !active;
    elements.discardBulkRestoreBtn.disabled = !active || !(Number(total) > 0);
    elements.discardBulkRestoreBtn.title = active
        ? ''
        : '请先设置筛选条件（关键词 / 分类 / 时间 / 分数 / 批次）';
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
        const rawDecidedAt = item.decided_at ? String(item.decided_at) : '';
        const checked = discardSelection.has(item.article_id) ? ' checked' : '';
        const timeControl = rawDecidedAt
            ? `<button type="button" class="discard-item-time discard-batch-btn" title="只看这一批"
                data-decided-at="${escapeDiscardAttr(rawDecidedAt)}">${escapeDiscardHtml(formatDiscardTime(rawDecidedAt))}</button>`
            : `<span class="discard-item-time">${escapeDiscardHtml(formatDiscardTime(item.decided_at))}</span>`;
        return `
        <div class="article-card discard-item" data-id="${escapeDiscardAttr(item.article_id || '')}" data-version="${Number(item.version) || 0}">
            <input type="checkbox" class="discard-row-check" data-id="${escapeDiscardAttr(item.article_id || '')}"
                aria-label="选择本条"${checked}>
            <h4 class="article-title discard-item-title" title="${escapeDiscardAttr(title)}">${escapeDiscardHtml(title)}</h4>
            <div class="discard-item-meta">
                <span class="discard-item-source">来源: ${escapeDiscardHtml(item.source || '-')}</span>
                ${timeControl}
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

// --- 本页勾选恢复 ---

function clearDiscardSelection() {
    discardSelection.clear();
    syncDiscardBulkBar();
}

function selectedDiscardIds() {
    return [...discardSelection];
}

function syncDiscardBulkBar() {
    if (!elements.discardBulkBar) return;
    const count = discardSelection.size;
    elements.discardBulkBar.hidden = count === 0;
    if (elements.discardSelectedCount) {
        elements.discardSelectedCount.textContent = count ? `已选 ${count} 条` : '';
    }
    if (elements.discardSelectAll) {
        const checkboxes = [...elements.discardList.querySelectorAll('.discard-row-check')];
        const checkedCount = checkboxes.filter(box => box.checked).length;
        elements.discardSelectAll.checked = checkboxes.length > 0 && checkedCount === checkboxes.length;
        // 部分选中时显示为 indeterminate
        elements.discardSelectAll.indeterminate = checkedCount > 0 && checkedCount < checkboxes.length;
    }
}

function handleDiscardRowCheckChange(event) {
    const box = event.target;
    if (!box.classList.contains('discard-row-check')) return;
    if (box.checked) discardSelection.add(box.dataset.id);
    else discardSelection.delete(box.dataset.id);
    syncDiscardBulkBar();
}

function handleDiscardSelectAllChange(event) {
    const checked = event.target.checked;
    elements.discardList.querySelectorAll('.discard-row-check').forEach(box => {
        box.checked = checked;
        if (checked) discardSelection.add(box.dataset.id);
        else discardSelection.delete(box.dataset.id);
    });
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
    if (!rawValue || !discardSelection.size) return;

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

        showToast(`已恢复 ${ids.length} 条到${getDiscardRestoreLabel(rawValue)}`);
        discardSelection.clear();
        loadStats();
        clampDiscardPageAfterRestore(ids.length);
        loadDiscardData();
    } catch (e) {
        showToast(e.message || '批量恢复失败', 'error');
        discardSelection.clear();
        loadDiscardData();
    }
}

// --- 按条件全部恢复 ---

async function handleDiscardBulkRestore() {
    if (!discardFilterActive()) {
        showToast('请先设置筛选条件', 'error');
        return;
    }
    try {
        const preview = await workspaceFetch(`${API_BASE}/bulk-restore`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(discardFilterRequestBody(true))
        }).then(response => requireManualMutationSuccess(response, 'failed to preview bulk restore'));
        if (!(preview.matched > 0)) {
            showToast('没有符合条件的已放弃新闻');
            return;
        }
        const summary = discardFilterState.batchDecidedAt
            ? `${formatDiscardTime(discardFilterState.batchDecidedAt)} 这一批`
            : discardFilterDescribe();
        const confirmed = window.confirm(
            `确定把「${summary}」的 ${preview.matched} 条已放弃新闻恢复到待处理吗？`
        );
        if (!confirmed) return;
        const result = await workspaceFetch(`${API_BASE}/bulk-restore`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(discardFilterRequestBody(false))
        }).then(response => requireManualMutationSuccess(response, 'failed to bulk restore'));
        showToast(`已恢复 ${result.updated} 条到待处理`);
        state.discardPage = 1;
        loadStats();
        loadDiscardData();
    } catch (e) {
        showToast(e.message || '批量恢复失败', 'error');
    }
}
