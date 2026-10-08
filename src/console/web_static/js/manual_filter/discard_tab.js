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

// 「分类」「放弃批次」下拉的元素引用与绑定收敛在本文件（本轮不改动 core.js/init.js）；
// 「放弃时间」下拉的 change 绑定仍在 init.js，调 handleDiscardSinceChange。
const discardBucketSelect = document.getElementById('discard-bucket-select');
const discardBatchSelect = document.getElementById('discard-batch-select');

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

// 恢复 body：与列表查询同一份条件状态（pairs + 关键词），dry_run 之外逐字同源
function discardFilterRequestBody(dryRun) {
    const body = { dry_run: dryRun };
    if (state.discardQuery) body.q = state.discardQuery;
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

// loadDiscardData 请求序号：只有最新一次请求允许渲染列表，被取代的请求
// 无论成败都静默丢弃（与筛选页 loadFilterData 的做法一致）
let discardLoadSeq = 0;
function isLatestDiscardLoad(seq) {
    return seq === discardLoadSeq;
}

async function loadDiscardData() {
    const seq = ++discardLoadSeq;
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
        if (!isLatestDiscardLoad(seq)) return false;
        discardLastTotal = data.total || 0;
        renderDiscardList(data.items);
        if (!(data.items || []).length && state.discardPage > 1) {
            // 当前页已越界（并发恢复后总数变少），退回上一页重试
            state.discardPage -= 1;
            return loadDiscardData();
        }
        updatePagination('discard', data.total || 0, state.discardPage, data.limit);
        updateDiscardSearchMeta(data.total || 0);
        return true;
    } catch (e) {
        if (!isLatestDiscardLoad(seq)) return false;
        elements.discardList.innerHTML = '<div class="error">加载数据失败</div>';
        discardLastTotal = 0;
        updateDiscardSearchMeta(null);
        return false;
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

// 用最近批次重建「放弃批次」下拉：平铺批次项，不用 optgroup；没有任何
// 批次时只剩「放弃批次：全部」一项，保持可见不隐藏。当前生效的批次不在
// 最新列表里时，插入对应选项，保证控件如实显示当前条件，绝不显示成「全部」。
function rebuildDiscardBatchOptions() {
    if (!discardBatchSelect) return;
    const current = discardFilterState.batchDecidedAt;
    const known = discardBatches.some(batch => String(batch.decided_at) === current);
    discardBatchSelect.innerHTML = `<option value="">放弃批次：全部</option>`
        + discardBatches.map(batch => {
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
    syncDiscardFilterValues();
}

// 各下拉的当前值都由状态驱动：批次与时间预设互斥，不会同时设置
function syncDiscardFilterValues() {
    if (elements.discardSinceSelect) {
        elements.discardSinceSelect.value = discardFilterState.since || '';
    }
    if (discardBatchSelect) {
        discardBatchSelect.value = discardFilterState.batchDecidedAt || '';
    }
    if (discardBucketSelect) {
        discardBucketSelect.value = discardFilterState.bucket || 'all';
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
    syncDiscardFilterValues();
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

// 「放弃时间」下拉只含时间预设：清除批次条件，分类/分数/关键词保持不变
async function handleDiscardSinceChange(value) {
    if (DISCARD_SINCE_PRESETS.includes(value)) {
        discardFilterState.since = value;
        discardFilterState.batchDecidedAt = '';
    }
    state.discardPage = 1;
    await loadDiscardData();
}

// 「放弃批次」下拉：选中某一批时清掉其他全部条件（含时间预设）；
// 选回「全部」只清除批次。批次生效时改分类/分数/关键词允许叠加。
async function handleDiscardBatchChange(value) {
    if (value) {
        state.discardQuery = '';
        discardFilterState.bucket = 'all';
        discardFilterState.since = '';
        discardFilterState.minScore = '';
        discardFilterState.maxScore = '';
        discardFilterState.batchDecidedAt = value;
    } else {
        discardFilterState.batchDecidedAt = '';
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

// 统一的放弃时间显示：与当前日期（Asia/Shanghai）同年显示 MM-DD HH:mm，
// 跨年显示 YYYY-MM-DD HH:mm。下拉选项、行内时间、meta「这一批」三处共用。
function formatDiscardTime(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    const parts = new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23'
    }).formatToParts(date);
    const get = (type) => (parts.find(part => part.type === type) || {}).value || '';
    const currentYear = new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric'
    }).format(new Date());
    const year = get('year');
    const month = get('month');
    const day = get('day');
    const hour = get('hour');
    const minute = get('minute');
    return year === currentYear
        ? `${month}-${day} ${hour}:${minute}`
        : `${year}-${month}-${day} ${hour}:${minute}`;
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
            // 「全部 M 条」加粗强调当前作用范围
            elements.discardSelectAllLabel.innerHTML =
                `已选<strong>全部 ${discardLastTotal} 条</strong>（按当前筛选）`;
        } else if (discardSelection.size > 0) {
            elements.discardSelectAllLabel.textContent = allPageChecked
                ? `已选本页 ${discardSelection.size} 条`
                : `已选 ${discardSelection.size} 条`;
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
    // 范围切换是按钮（次要按钮样式），不是下划线链接；与「恢复到」下拉同高
    if (elements.discardSelectAllMatchedBtn) {
        elements.discardSelectAllMatchedBtn.className = 'btn btn-secondary';
        elements.discardSelectAllMatchedBtn.textContent = `选择全部 ${discardLastTotal} 条`;
        elements.discardSelectAllMatchedBtn.hidden = !canExtend;
    }
    if (elements.discardExitAllBtn) {
        elements.discardExitAllBtn.className = 'btn btn-secondary';
        elements.discardExitAllBtn.hidden = discardSelectionMode !== 'all';
    }
    // 全部匹配模式给批量栏整体加强调状态，退出即移除
    elements.discardBulkBar?.classList.toggle('is-all-mode', discardSelectionMode === 'all');
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

// 「分类」「放弃批次」下拉的 change 绑定（本文件自初始化，init.js 本轮不动；
// 分段按钮的 init.js 绑定随按钮删除自然空转）
function discardFilterWireControls() {
    if (discardBucketSelect) {
        discardBucketSelect.addEventListener('change', () => {
            discardFilterState.bucket = discardBucketSelect.value || 'all';
            applyDiscardFilterChange();
        });
    }
    if (discardBatchSelect) {
        discardBatchSelect.addEventListener('change', () => {
            handleDiscardBatchChange(discardBatchSelect.value || '');
        });
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', discardFilterWireControls);
} else {
    discardFilterWireControls();
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
