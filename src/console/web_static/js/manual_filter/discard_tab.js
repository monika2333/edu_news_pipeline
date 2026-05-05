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
        <div class="article-card">
            <div class="card-header">
                <h4 class="article-title">${item.title}</h4>
                <button class="btn btn-secondary btn-sm" onclick="restoreToBackup('${item.article_id}')">恢复至备选</button>
            </div>
            <div class="meta-row">
                <div class="meta-item">来源: ${item.source}</div>
                <div class="meta-item">分数: ${item.score}</div>
            </div>
        </div>
    `).join('');
}

window.restoreToBackup = async function (id) {
    try {
        await fetch(`${API_BASE}/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                selected_ids: [],
                backup_ids: [id],
                discarded_ids: [],
                actor: state.actor,
                report_type: state.reviewReportType
            })
        });
        showToast('已恢复到备选');
        loadStats();
        loadDiscardData();
    } catch (e) {
        showToast('恢复失败', 'error');
    }
};
