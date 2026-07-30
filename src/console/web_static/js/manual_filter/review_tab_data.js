// Manual Filter JS - Review Tab

// --- Review Tab Data ---

async function loadReviewData() {
    if (!elements.reviewList) return;
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
            workspaceFetch(`${API_BASE}/review?${paramsSelected.toString()}`),
            workspaceFetch(`${API_BASE}/review?${paramsBackup.toString()}`)
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

function getReviewDecisionLabel(rawValue) {
    if (rawValue === 'zongbao:selected') return '综报采纳';
    if (rawValue === 'zongbao:backup') return '综报备选';
    if (rawValue === 'wanbao:selected') return '晚报采纳';
    if (rawValue === 'wanbao:backup') return '晚报备选';
    if (rawValue === 'discarded') return '放弃';
    if (rawValue === 'pending') return '待处理';
    return '目标栏目';
}

function buildReviewMoveMessage(count, rawValue) {
    if (rawValue === 'discarded') {
        return `已放弃 ${count} 条新闻`;
    }
    return `已将 ${count} 条新闻移动到${getReviewDecisionLabel(rawValue)}`;
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
        const response = await workspaceFetch(`${API_BASE}/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                selected_ids,
                backup_ids,
                discarded_ids,
                pending_ids,
                versions: collectManualReviewVersions(movedIds),
                report_type: targetReportType
            })
        });
        const mutation = await requireManualMutationSuccess(response, '批量移动失败');
        await loadReviewData();
        window.scrollTo({ top: scrollY, behavior: 'auto' });
        loadStats();
        const totalMoved = movedIds.length;

        // Undo Action
        const undoAction = buildUndoToastAction(
            async () => {
                try {
                    // Determine which list to put them back into based on previousView
                    const undoPayload = {
                        selected_ids: [],
                        backup_ids: [],
                        discarded_ids: [],
                        pending_ids: [],
                        versions: mutation.versions || {},
                        report_type: previousReportType
                    };

                    if (previousView === 'selected') undoPayload.selected_ids = movedIds;
                    else if (previousView === 'backup') undoPayload.backup_ids = movedIds;
                    else undoPayload.pending_ids = movedIds; // Fallback

                    const undoResponse = await workspaceFetch(`${API_BASE}/decide`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(undoPayload)
                    });
                    await requireManualMutationSuccess(undoResponse, '撤销失败');

                    showToast('已撤销操作');
                    await loadReviewData(); // Reload to show items back
                    loadStats();
                } catch (e) {
                    showToast(e.message || '撤销失败', 'error');
                }
            }
        );

        showToast(buildReviewMoveMessage(totalMoved, value), 'success', undoAction);
    } catch (e) {
        showToast(e.message || '批量移动失败', 'error');
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

function collectReviewGroupOrders(list) {
    const groupOrders = {};
    list.querySelectorAll('.sort-group').forEach(group => {
        const key = group.dataset.group;
        if (!key) return;
        groupOrders[key] = Array.from(group.querySelectorAll('.article-card'))
            .map(card => card.dataset.id)
            .filter(Boolean);
    });
    return groupOrders;
}

function syncReviewStateGroups(groupOrders) {
    const lookup = {};
    [...(state.reviewData.selected || []), ...(state.reviewData.backup || [])].forEach(item => {
        if (item && item.article_id) lookup[item.article_id] = item;
    });
    Object.entries(groupOrders).forEach(([groupKey, articleIds]) => {
        const [region, sentiment] = groupKey.split('_');
        articleIds.forEach(articleId => {
            const item = lookup[articleId];
            if (!item) return;
            item.group_key = groupKey;
            item.region = region;
            item.sentiment_key = sentiment;
            item.is_beijing_related = region === 'internal';
            item.sentiment_label = sentiment;
        });
    });
}

async function persistReviewOrder() {
    const list = document.querySelector('#review-items');
    if (!list) return false;

    const orderedIds = Array.from(list.querySelectorAll('.article-card'))
        .map(card => card.dataset.id)
        .filter(Boolean);
    const groupOrders = collectReviewGroupOrders(list);
    syncReviewStateGroups(groupOrders);
    syncReviewStateOrder(state.reviewView, orderedIds);

    const payload = {
        selected_order: (state.reviewData.selected || []).map(item => item.article_id),
        backup_order: (state.reviewData.backup || []).map(item => item.article_id),
        group_orders: groupOrders,
        report_type: state.reviewReportType
    };

    try {
        const response = await workspaceFetch(`${API_BASE}/order`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!response.ok) throw new Error(`Order update failed: ${response.status}`);
        showToast('排序已保存');
        return true;
    } catch (e) {
        showToast('保存排序失败', 'error');
        await loadReviewData();
        return false;
    }
}

function parseReviewDecision(rawValue, card) {
    let status = rawValue;
    let targetReportType = card.dataset.reportType || state.reviewReportType;
    if (rawValue.includes(':')) {
        const [rt, st] = rawValue.split(':');
        targetReportType = rt === 'wanbao' ? 'wanbao' : 'zongbao';
        status = st;
    }

    return { status, targetReportType };
}

function buildReviewUndoAction(id, prevStatus, prevReportType, versions) {
    return buildUndoToastAction(
        async () => {
            try {
                const response = await workspaceFetch(`${API_BASE}/decide`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        selected_ids: prevStatus === 'selected' ? [id] : [],
                        backup_ids: prevStatus === 'backup' ? [id] : [],
                        discarded_ids: prevStatus === 'discarded' ? [id] : [],
                        pending_ids: prevStatus === 'pending' ? [id] : [],
                        versions,
                        report_type: prevReportType
                    })
                });
                await requireManualMutationSuccess(response, '撤销失败');
                showToast('已撤销操作');
                await loadReviewData();
                loadStats();
            } catch (e) {
                showToast(e.message || '撤销失败', 'error');
            }
        }
    );
}

async function applyReviewCardDecision(card, rawValue, successMessage = '已更新状态') {
    const id = card.dataset.id;
    if (!id) return;

    const { status, targetReportType } = parseReviewDecision(rawValue, card);
    const summaryBox = card.querySelector('.summary-box');
    const summary = summaryBox ? summaryBox.value : '';
    const sourceBox = card.querySelector('.source-box');
    const llm_source = sourceBox ? sourceBox.value : '';
    const controls = card.querySelectorAll('.status-select, .review-discard-btn');
    const prevStatus = card.dataset.status || state.reviewView || 'selected';
    const prevReportType = card.dataset.reportType || state.reviewReportType;

    controls.forEach(control => {
        control.disabled = true;
    });
    try {
        const scrollY = window.scrollY;
        const editResponse = await workspaceFetch(`${API_BASE}/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                edits: { [id]: { summary, llm_source } },
                versions: collectManualReviewVersions([id])
            })
        });
        const editMutation = await requireManualMutationSuccess(editResponse, '保存编辑失败');

        const decisionResponse = await workspaceFetch(`${API_BASE}/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                selected_ids: status === 'selected' ? [id] : [],
                backup_ids: status === 'backup' ? [id] : [],
                discarded_ids: status === 'discarded' ? [id] : [],
                pending_ids: status === 'pending' ? [id] : [],
                versions: editMutation.versions || collectManualReviewVersions([id]),
                report_type: targetReportType
            })
        });
        const decisionMutation = await requireManualMutationSuccess(
            decisionResponse,
            '更新状态失败'
        );

        await loadReviewData();
        window.scrollTo({ top: scrollY, behavior: 'auto' });
        loadStats();

        showToast(
            successMessage,
            'success',
            buildReviewUndoAction(
                id,
                prevStatus,
                prevReportType,
                decisionMutation.versions || {}
            )
        );
    } catch (err) {
        showToast(err.message || '更新失败，请重试', 'error');
    } finally {
        controls.forEach(control => {
            control.disabled = false;
        });
    }
}

async function handleReviewStatusChange(e) {
    const select = e.target;
    const card = select.closest('.article-card');
    if (!card) return;
    await applyReviewCardDecision(card, select.value, buildReviewMoveMessage(1, select.value));
}

async function handleReviewDiscardClick(e) {
    const button = e.currentTarget;
    const card = button.closest('.article-card');
    if (!card) return;
    await applyReviewCardDecision(card, 'discarded', buildReviewMoveMessage(1, 'discarded'));
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
        const response = await workspaceFetch(`${API_BASE}/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                edits: { [id]: { summary, llm_source } },
                versions: collectManualReviewVersions([id])
            })
        });
        await requireManualMutationSuccess(response, '摘要保存失败');
        applyReviewEditsToState(id, summary, llm_source);
        showToast('摘要已保存');
    } catch (err) {
        showToast(err.message || '摘要保存失败', 'error');
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
        const response = await workspaceFetch(`${API_BASE}/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                edits: { [id]: { summary, llm_source } },
                versions: collectManualReviewVersions([id])
            })
        });
        await requireManualMutationSuccess(response, '来源保存失败');
        applyReviewEditsToState(id, summary, llm_source);
        showToast('来源已保存');
    } catch (err) {
        showToast(err.message || '来源保存失败', 'error');
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
            target.manual_summary = summary;
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

    const view = state.reviewView === 'backup' ? 'backup' : 'selected';
    const items = state.reviewData[view] || [];
    const articleIds = items.map(item => item.article_id).filter(Boolean);
    if (!articleIds.length) {
        showToast('当前列表为空', 'error');
        return;
    }
    const payload = {
        article_ids: articleIds,
        versions: collectManualReviewVersions(articleIds)
    };

    try {
        const res = await workspaceFetch(`${API_BASE}/archive`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await requireManualMutationSuccess(res, '归档失败');

        const count = result.exported || 0;
        showToast(`归档成功，已标记 ${count} 条文章`);
        await loadReviewData();
        loadStats();
    } catch (e) {
        showToast(e.message || '归档失败', 'error');
    }
}
