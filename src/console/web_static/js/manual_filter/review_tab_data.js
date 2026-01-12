// Manual Filter JS - Review Tab

// --- Review Tab Data ---

async function loadReviewData() {
    const listEmpty = !elements.reviewList.querySelector('.article-card');
    const hasData = state.reviewData && (state.reviewData.selected.length || state.reviewData.backup.length);
    if (!hasData || listEmpty) {
        elements.reviewList.innerHTML = renderSkeleton(5);
    }
    try {
        const now = Date.now();
        const paramsSelected = new URLSearchParams({
            decision: 'selected',
            limit: '200',
            report_type: state.reviewReportType,
            _t: now
        });
        const paramsBackup = new URLSearchParams({
            decision: 'backup',
            limit: '200',
            report_type: state.reviewReportType,
            _t: now
        });

        const [selRes, bakRes] = await Promise.all([
            fetch(`${API_BASE}/review?${paramsSelected.toString()}`),
            fetch(`${API_BASE}/review?${paramsBackup.toString()}`)
        ]);

        const selData = await selRes.json();
        const bakData = await bakRes.json();

        state.reviewData = {
            selected: selData.items || [],
            backup: bakData.items || []
        };
        updateReviewRailCounts();
        renderReviewView();
    } catch (e) {
        elements.reviewList.innerHTML = '<div class="error">加载审阅数据失败</div>';
    }
}

async function applyReviewBulkStatus() {
    if (!elements.reviewBulkStatus) return;
    const value = elements.reviewBulkStatus.value;
    if (!value) return;
    const scope = getActiveReviewContainer();
    const targets = scope.querySelectorAll('.review-select:checked');
    if (!targets.length) {
        elements.reviewBulkStatus.value = '';
        showToast('请先选择要移动的条目', 'error');
        return;
    }

    const selected_ids = [];
    const backup_ids = [];
    const discarded_ids = [];
    const pending_ids = [];

    let targetReportType = state.reviewReportType;
    if (value.includes(':')) {
        const [rt, st] = value.split(':');
        targetReportType = rt === 'wanbao' ? 'wanbao' : 'zongbao';
        targets.forEach(cb => {
            const card = cb.closest('.article-card');
            if (!card) return;
            const id = card.dataset.id;
            if (!id) return;
            if (st === 'selected') selected_ids.push(id);
            else if (st === 'backup') backup_ids.push(id);
        });
    } else if (value === 'discarded' || value === 'pending') {
        targets.forEach(cb => {
            const card = cb.closest('.article-card');
            if (!card) return;
            const id = card.dataset.id;
            if (!id) return;
            if (value === 'discarded') discarded_ids.push(id);
            else pending_ids.push(id);
        });
    }

    elements.reviewBulkStatus.value = '';
    if (!selected_ids.length && !backup_ids.length && !discarded_ids.length && !pending_ids.length) {
        return;
    }

    const movedIds = [...selected_ids, ...backup_ids, ...discarded_ids, ...pending_ids];
    const previousView = state.reviewView; // 'selected' or 'backup'
    const previousReportType = state.reviewReportType; // 'zongbao' or 'wanbao'

    try {
        isBulkUpdatingReview = true;
        const scrollY = window.scrollY;
        await fetch(`${API_BASE}/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                selected_ids,
                backup_ids,
                discarded_ids,
                pending_ids,
                actor: state.actor,
                report_type: targetReportType
            })
        });
        await loadReviewData();
        window.scrollTo({ top: scrollY, behavior: 'auto' });
        loadStats();
        const totalMoved = movedIds.length;
        let targetLabel = '';
        if (value === 'zongbao:selected') targetLabel = '综报采纳';
        else if (value === 'zongbao:backup') targetLabel = '综报备选';
        else if (value === 'wanbao:selected') targetLabel = '晚报采纳';
        else if (value === 'wanbao:backup') targetLabel = '晚报备选';
        else if (value === 'discarded') targetLabel = '放弃';
        else if (value === 'pending') targetLabel = '待处理';

        // Undo Action
        const undoAction = {
            icon: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
  <path d="M9 10h7a4 4 0 0 1 0 8h-1" />
  <path d="M12 7l-3 3 3 3" />
</svg>`,
            title: '撤销操作',
            callback: async () => {
                try {
                    // Determine which list to put them back into based on previousView
                    const undoPayload = {
                        selected_ids: [],
                        backup_ids: [],
                        discarded_ids: [],
                        pending_ids: [],
                        actor: state.actor,
                        report_type: previousReportType
                    };

                    if (previousView === 'selected') undoPayload.selected_ids = movedIds;
                    else if (previousView === 'backup') undoPayload.backup_ids = movedIds;
                    else undoPayload.pending_ids = movedIds; // Fallback

                    await fetch(`${API_BASE}/decide`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(undoPayload)
                    });

                    showToast('已撤销操作');
                    await loadReviewData(); // Reload to show items back
                    loadStats();
                } catch (e) {
                    showToast('撤销失败', 'error');
                }
            }
        };

        showToast(`已批量移动 ${totalMoved} 条文章到 ${targetLabel}`, 'success', undoAction);
    } catch (e) {
        showToast('批量移动失败', 'error');
    } finally {
        isBulkUpdatingReview = false;
        updateReviewSelectAllState();
    }
}

function syncReviewStateOrder(status, orderedIds) {
    const lookup = {};
    [...(state.reviewData.selected || []), ...(state.reviewData.backup || [])].forEach(item => {
        if (item && item.article_id) lookup[item.article_id] = item;
    });
    const orderedItems = orderedIds.map(id => lookup[id]).filter(Boolean);
    state.reviewData[status] = orderedItems;
}

async function persistReviewOrder() {
    const list = document.querySelector('#review-items');
    if (!list) return;

    const orderedIds = Array.from(list.querySelectorAll('.article-card')).map(card => card.dataset.id);
    syncReviewStateOrder(state.reviewView, orderedIds);

    const payload = {
        selected_order: (state.reviewData.selected || []).map(item => item.article_id),
        backup_order: (state.reviewData.backup || []).map(item => item.article_id),
        actor: state.actor,
        report_type: state.reviewReportType
    };

    try {
        await fetch(`${API_BASE}/order`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        showToast('排序已保存');
    } catch (e) {
        showToast('保存排序失败', 'error');
    }
}

async function handleReviewStatusChange(e) {
    const select = e.target;
    const card = select.closest('.article-card');
    if (!card) return;
    const id = card.dataset.id;
    const rawValue = select.value;
    let status = rawValue;
    let targetReportType = card.dataset.reportType || state.reviewReportType;
    if (rawValue.includes(':')) {
        const [rt, st] = rawValue.split(':');
        targetReportType = rt === 'wanbao' ? 'wanbao' : 'zongbao';
        status = st;
    }
    const summaryBox = card.querySelector('.summary-box');
    const summary = summaryBox ? summaryBox.value : '';
    const sourceBox = card.querySelector('.source-box');
    const llm_source = sourceBox ? sourceBox.value : '';

    select.disabled = true;
    try {
        const scrollY = window.scrollY;
        await fetch(`${API_BASE}/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ edits: { [id]: { summary, llm_source } }, actor: state.actor, report_type: state.reviewReportType })
        });

        await fetch(`${API_BASE}/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                selected_ids: status === 'selected' ? [id] : [],
                backup_ids: status === 'backup' ? [id] : [],
                discarded_ids: status === 'discarded' ? [id] : [],
                pending_ids: status === 'pending' ? [id] : [],
                actor: state.actor,
                report_type: targetReportType
            })
        });

        await loadReviewData();
        window.scrollTo({ top: scrollY, behavior: 'auto' });
        loadStats();

        // Undo Logic
        const prevStatus = card.dataset.status;
        const prevReportType = card.dataset.reportType || state.reviewReportType;

        const undoAction = {
            icon: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
  <path d="M9 10h7a4 4 0 0 1 0 8h-1" />
  <path d="M12 7l-3 3 3 3" />
</svg>`,
            title: '撤销操作',
            callback: async () => {
                try {
                    await fetch(`${API_BASE}/decide`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            selected_ids: prevStatus === 'selected' ? [id] : [],
                            backup_ids: prevStatus === 'backup' ? [id] : [],
                            discarded_ids: prevStatus === 'discarded' ? [id] : [],
                            pending_ids: prevStatus === 'pending' ? [id] : [],
                            actor: state.actor,
                            report_type: prevReportType
                        })
                    });
                    showToast('已撤销操作');
                    await loadReviewData();
                    loadStats();
                } catch (e) {
                    showToast('撤销失败', 'error');
                }
            }
        };

        showToast('已更新状态', 'success', undoAction);
    } catch (err) {
        showToast('更新失败，请重试', 'error');
    } finally {
        select.disabled = false;
    }
}

async function handleSummaryUpdate(e) {
    const box = e.target;
    const card = box.closest('.article-card');
    if (!card) return;
    const id = card.dataset.id;
    const summary = box.value;
    const sourceBox = card.querySelector('.source-box');
    const llm_source = sourceBox ? sourceBox.value : '';
    try {
        await fetch(`${API_BASE}/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ edits: { [id]: { summary, llm_source } }, actor: state.actor, report_type: state.reviewReportType })
        });
        applyReviewEditsToState(id, summary, llm_source);
        showToast('摘要已保存');
    } catch (err) {
        showToast('摘要保存失败', 'error');
    }
}

async function handleSourceUpdate(e) {
    const input = e.target;
    const card = input.closest('.article-card');
    if (!card) return;
    const id = input.dataset.id;
    const llm_source = input.value;
    const summaryBox = card.querySelector('.summary-box');
    const summary = summaryBox ? summaryBox.value : '';
    try {
        await fetch(`${API_BASE}/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ edits: { [id]: { summary, llm_source } }, actor: state.actor, report_type: state.reviewReportType })
        });
        applyReviewEditsToState(id, summary, llm_source);
        showToast('来源已保存');
    } catch (err) {
        showToast('来源保存失败', 'error');
    }
}

function applyReviewEditsToState(articleId, summary, llm_source) {
    if (!articleId) return;
    const normalizedSource = (llm_source || '').trim();
    ['selected', 'backup'].forEach(status => {
        const items = state.reviewData[status] || [];
        const target = items.find(item => item && item.article_id === articleId);
        if (!target) return;
        if (summary !== undefined) {
            target.summary = summary;
        }
        if (llm_source !== undefined) {
            target.llm_source_manual = normalizedSource;
            const raw = (target.llm_source_raw || '').trim();
            const source = (target.source || '').trim();
            target.llm_source_display = normalizedSource || raw || source;
        }
    });
}

async function handleArchive() {
    if (!confirm('确定要归档当前列表吗？归档后文章将标记为已导出并从列表中移除。')) {
        return;
    }

    const reportType = state.reviewReportType;
    const view = state.reviewView === 'backup' ? 'backup' : 'selected';
    const items = state.reviewData[view] || [];
    const articleIds = items.map(item => item.article_id).filter(Boolean);
    if (!articleIds.length) {
        showToast('当前列表为空', 'error');
        return;
    }
    const payload = {
        article_ids: articleIds,
        actor: state.actor,
        report_type: reportType
    };

    try {
        const res = await fetch(`${API_BASE}/archive`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await res.json();

        const count = result.exported || 0;
        showToast(`归档成功，已标记 ${count} 条文章`);
        await loadReviewData();
        loadStats();
    } catch (e) {
        showToast('归档失败', 'error');
    }
}
