// Manual Filter JS - Discard Tab

// --- Discard Tab Logic ---

async function loadDiscardData() {
    elements.discardList.innerHTML = '<div class="loading">加载中...</div>';
    try {
        const params = new URLSearchParams({
            limit: '30',
            offset: `${(state.discardPage - 1) * 30}`,
            report_type: state.reviewReportType
        });
        const res = await fetch(`${API_BASE}/discarded?${params.toString()}`);
        const data = await res.json();
        renderDiscardList(data.items);
        updatePagination('discard', data.total, state.discardPage);
    } catch (e) {
        elements.discardList.innerHTML = '<div class="error">加载数据失败</div>';
    }
}

function renderDiscardList(items) {
    if (!items.length) {
        elements.discardList.innerHTML = '<div class="empty">当前没有已放弃新闻</div>';
        return;
    }

    elements.discardList.innerHTML = items.map(item => `
        <div class="article-card" data-id="${escapeDiscardAttr(item.article_id || '')}">
            <div class="card-header">
                <h4 class="article-title">${escapeDiscardHtml(item.title || '(No Title)')}</h4>
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
            <div class="meta-row">
                <div class="meta-item">来源: ${escapeDiscardHtml(item.source || '-')}</div>
                <div class="meta-item">分数: ${escapeDiscardHtml(item.score ?? '-')}</div>
            </div>
        </div>
    `).join('');

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
        actor: state.actor,
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
        const res = await fetch(`${API_BASE}/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(buildDiscardRestorePayload(id, status, reportType))
        });
        if (!res.ok) throw new Error('failed to restore discarded item');

        showToast(`已恢复到${getDiscardRestoreLabel(rawValue)}`);
        loadStats();
        loadDiscardData();
    } catch (e) {
        showToast('恢复失败', 'error');
        select.value = '';
        select.disabled = false;
    }
}
