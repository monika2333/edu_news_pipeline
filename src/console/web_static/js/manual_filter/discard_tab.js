// Manual Filter JS - Discard Tab

// --- Discard Tab Logic ---

const DISCARD_PAGE_SIZE = 30;

async function loadDiscardData() {
    syncDiscardToolbar();
    elements.discardList.innerHTML = '<div class="loading">加载中...</div>';
    try {
        const params = new URLSearchParams({
            limit: String(DISCARD_PAGE_SIZE),
            offset: `${(state.discardPage - 1) * DISCARD_PAGE_SIZE}`
        });
        if (state.discardQuery) params.set('q', state.discardQuery);
        const res = await workspaceFetch(`${API_BASE}/discarded?${params.toString()}`);
        const data = await res.json();
        renderDiscardList(data.items);
        updatePagination('discard', data.total || 0, state.discardPage, data.limit);
        updateDiscardSearchMeta(data.total || 0);
    } catch (e) {
        elements.discardList.innerHTML = '<div class="error">加载数据失败</div>';
        updateDiscardSearchMeta(null);
    }
}

function syncDiscardToolbar() {
    if (elements.discardSearchInput) {
        elements.discardSearchInput.value = state.discardQuery || '';
    }
    syncDiscardSearchClearButton();
}

function syncDiscardSearchClearButton() {
    if (!elements.discardSearchClear) return;
    const hasCondition = Boolean(
        elements.discardSearchInput?.value.trim()
        || state.discardQuery
    );
    elements.discardSearchClear.hidden = !hasCondition;
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

function updateDiscardSearchMeta(total) {
    if (!elements.discardSearchMeta) return;
    if (total === null || total === undefined) {
        elements.discardSearchMeta.textContent = '';
    } else if (state.discardQuery) {
        elements.discardSearchMeta.textContent = `关键词「${state.discardQuery}」命中 ${total} 条`;
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
        if (state.discardQuery) {
            elements.discardList.innerHTML = `
                <div class="empty discard-empty">
                    <span>没有找到与「${escapeDiscardHtml(state.discardQuery)}」匹配的已放弃新闻</span>
                    <button type="button" class="btn btn-secondary discard-empty-clear"
                        onclick="clearDiscardSearch()">清除检索</button>
                </div>
            `;
        } else {
            elements.discardList.innerHTML = '<div class="empty">当前没有已放弃新闻</div>';
        }
        return;
    }

    elements.discardList.innerHTML = items.map(item => {
        const title = item.title || '(No Title)';
        return `
        <div class="article-card discard-item" data-id="${escapeDiscardAttr(item.article_id || '')}" data-version="${Number(item.version) || 0}">
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
        reportType: state.reviewReportType
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
        loadDiscardData();
    } catch (e) {
        showToast(e.message || '恢复失败', 'error');
        select.value = '';
        select.disabled = false;
    }
}
