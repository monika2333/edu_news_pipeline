// Manual Filter JS - Review Tab

// --- Review Tab Logic ---

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
    } catch (e) {
        elements.reviewList.innerHTML = '<div class="error">加载审阅数据失败</div>';
    }
}

function filterReviewItems(term) {
    if (!elements.reviewList) return;
    const cards = elements.reviewList.querySelectorAll('.article-card');
    cards.forEach(card => {
        if (!term) {
            card.style.display = '';
            return;
        }
        const titleEl = card.querySelector('.article-title');
        const summaryEl = card.querySelector('.summary-box');
        const title = titleEl ? titleEl.textContent.toLowerCase() : '';
        const summary = summaryEl ? summaryEl.value.toLowerCase() : '';

        if (title.includes(term) || summary.includes(term)) {
            card.style.display = '';
        } else {
            card.style.display = 'none';
        }
    });
}

function renderReviewView() {
    const currentView = state.reviewView === 'backup' ? 'backup' : 'selected';
    const items = state.reviewData[currentView] || [];
    const content = isSortMode ? renderSortableReviewItems(items) : renderGroupedReviewItems(items);

    elements.reviewList.innerHTML = `
        <div class="review-grid single-view" data-view="${currentView}">
            <div class="review-items" id="review-items">
                ${content}
            </div>
        </div>
    `;
    applyReviewViewMode();
    bindReviewSelectionControls();
    applySortModeState();
}

function applySortModeState() {
    const grid = document.querySelector('.review-grid');
    const toggleBtn = elements.sortToggleBtn;
    if (grid) {
        grid.classList.toggle('compact-mode', isSortMode);
        grid.classList.toggle('sort-mode', isSortMode);
    }
    if (toggleBtn) {
        toggleBtn.classList.toggle('active', isSortMode);
        toggleBtn.innerHTML = `<span class="icon">⇅</span> ${isSortMode ? '退出排序' : '排序模式'}`;
    }
}

function toggleSortMode() {
    isSortMode = !isSortMode;
    renderReviewView();
}

function resolveGroupKey(item) {
    if (item.group_key) return item.group_key;
    const region = item.is_beijing_related ? 'internal' : 'external';
    const sentiment = (item.sentiment_label || '').toLowerCase() === 'negative' ? 'negative' : 'positive';
    return `${region}_${sentiment}`;
}

function renderGroupedReviewItems(items) {
    if (!items || !items.length) {
        return '<div class="empty">当前列表为空</div>';
    }
    const buckets = {};
    items.forEach(item => {
        const key = resolveGroupKey(item);
        if (!buckets[key]) buckets[key] = [];
        buckets[key].push(item);
    });

    const renderCards = (list) => list.map(renderReviewCard).join('');
    let html = '';
    GROUP_ORDER.forEach(group => {
        const groupItems = buckets[group.key] || [];
        if (!groupItems.length) {
            return;
        }
        if (state.showGroups) {
            html += `
                <div class="review-group" data-group="${group.key}">
                    <div class="review-group-header">${group.label} (${groupItems.length})</div>
                    <div class="review-group-body">
                        ${renderCards(groupItems)}
                    </div>
                </div>
            `;
        } else {
            html += renderCards(groupItems);
        }
    });
    return html;
}

function getGroupLabel(key) {
    const found = GROUP_ORDER.find(g => g.key === key);
    return found ? found.label : (key || '未分组');
}

function renderSortableReviewItems(items) {
    if (!items || !items.length) {
        return '<div class="empty">当前列表为空</div>';
    }
    const buckets = {};
    items.forEach(item => {
        const key = resolveGroupKey(item);
        if (!buckets[key]) buckets[key] = [];
        buckets[key].push(item);
    });

    let html = '';
    GROUP_ORDER.forEach(group => {
        const groupItems = buckets[group.key] || [];
        if (!groupItems.length) return;
        html += `
            <div class="review-group sort-group" data-group="${group.key}">
                <div class="review-group-header">${group.label}(${groupItems.length})</div>
                <div class="review-group-body sort-group-body">
                    ${groupItems.map(item => {
            const currentStatus = item.manual_status || item.status || state.reviewView || 'selected';
            const title = item.title || '(No Title)';
            const link = item.url ? `<a href="${item.url}" target="_blank" rel="noopener noreferrer">${title}</a>` : title;
            return `
                            <div class="article-card sort-card" data-id="${item.article_id || ''}" data-status="${currentStatus}">
                                <div class="card-header sort-header">
                                    <span class="drag-handle" title="拖动排序">&#8942;</span>
                                    <h4 class="article-title">${link}</h4>
                                </div>
                            </div>
                        `;
        }).join('')}
                </div>
            </div>
        `;
    });
    return html || '<div class="empty">当前列表为空</div>';
}

function renderReviewCard(item) {
    const currentStatus = item.manual_status || item.status || state.reviewView || 'selected';
    const currentReportType = item.report_type || state.reviewReportType || 'zongbao';
    const placeholder = item.llm_source_raw ? `(LLM: ${item.llm_source_raw})` : '留空则回退抓取来源';
    const sourceText = item.source || item.llm_source_display || '-';
    const scoreVal = item.external_importance_score ?? item.score ?? '-';
    const bonusText = (item.bonus_keywords && item.bonus_keywords.length) ? item.bonus_keywords.join(', ') : '';
    const bonusClass = bonusText ? ' has-bonus' : '';
    const selectValue =
        currentStatus === 'selected' || currentStatus === 'backup'
            ? `${currentReportType}:${currentStatus}`
            : currentStatus;
    return `
        <div class="article-card${bonusClass}" data-id="${item.article_id || ''}" data-status="${currentStatus}" data-report-type="${currentReportType}">
            <div class="card-header">
                <label class="review-select-wrap" title="选择">
                    <input type="checkbox" class="review-select">
                </label>
                <span class="drag-handle" title="拖动排序">&#8942;</span>
                <h4 class="article-title">
                    ${item.title || '(No Title)'}
                    ${item.url ? `<a href="${item.url}" target="_blank" rel="noopener noreferrer">🔗</a>` : ''}
                </h4>
                <select class="status-select" data-id="${item.article_id || ''}">
                    <option value="zongbao:selected" ${selectValue === 'zongbao:selected' ? 'selected' : ''}>综报采纳</option>
                    <option value="zongbao:backup" ${selectValue === 'zongbao:backup' ? 'selected' : ''}>综报备选</option>
                    <option value="wanbao:selected" ${selectValue === 'wanbao:selected' ? 'selected' : ''}>晚报采纳</option>
                    <option value="wanbao:backup" ${selectValue === 'wanbao:backup' ? 'selected' : ''}>晚报备选</option>
                    <option value="discarded" ${selectValue === 'discarded' ? 'selected' : ''}>放弃</option>
                    <option value="pending" ${selectValue === 'pending' ? 'selected' : ''}>待处理</option>
                </select>
            </div>
            <div class="meta-row">
                <div class="meta-item">来源: ${sourceText}</div>
                <div class="meta-item">分数: ${scoreVal === '-' ? '-' : scoreVal}</div>
                ${bonusText ? `<div class="meta-item">Bonus: ${bonusText}</div>` : ''}
            </div>
            <textarea class="summary-box" data-id="${item.article_id || ''}">${item.summary || ''}</textarea>
            <input class="source-box" data-id="${item.article_id || ''}" value="${item.llm_source_display || ''}" placeholder="${placeholder}">
        </div>
    `;
}

function initReviewSortable() {
    if (reviewSortableInstances && reviewSortableInstances.length) {
        reviewSortableInstances.forEach(inst => inst && inst.destroy());
        reviewSortableInstances = [];
    }
    if (!isSortMode || typeof Sortable === 'undefined') return;
    const lists = document.querySelectorAll('.sort-group-body');
    if (!lists || !lists.length) return;
    const isMobileSort = window.innerWidth <= MOBILE_REVIEW_BREAKPOINT;
    lists.forEach(list => {
        const inst = new Sortable(list, {
            animation: 150,
            handle: isMobileSort ? undefined : '.drag-handle',
            ghostClass: 'review-ghost',
            forceFallback: true,
            fallbackOnBody: true,
            draggable: '.article-card',
            group: { name: 'review-groups', pull: false, put: false },
            onEnd: persistReviewOrder,
        });
        reviewSortableInstances.push(inst);
    });
}

function bindReviewSelectionControls() {
    const checkboxes = elements.reviewList.querySelectorAll('.review-select');
    checkboxes.forEach(cb => {
        cb.addEventListener('change', updateReviewSelectAllState);
    });

    const statusSelects = elements.reviewList.querySelectorAll('.status-select');
    statusSelects.forEach(sel => {
        sel.addEventListener('change', handleReviewStatusChange);
    });

    const summaries = elements.reviewList.querySelectorAll('.summary-box');
    summaries.forEach(box => {
        box.addEventListener('change', handleSummaryUpdate);
    });
    const sources = elements.reviewList.querySelectorAll('.source-box');
    sources.forEach(input => {
        input.addEventListener('change', handleSourceUpdate);
    });
    updateReviewSelectAllState();
}

function updateReviewSelectAllState() {
    const selectAll = elements.reviewSelectAll;
    if (!selectAll) return;
    const scope = getActiveReviewContainer();
    const checkboxes = scope.querySelectorAll('.review-select');
    const total = checkboxes.length;
    const checkedCount = Array.from(checkboxes).filter(cb => cb.checked).length;
    selectAll.indeterminate = checkedCount > 0 && checkedCount < total;
    selectAll.checked = total > 0 && checkedCount === total;
}

function toggleReviewSelectAll(checked) {
    const scope = getActiveReviewContainer();
    const checkboxes = scope.querySelectorAll('.review-select');
    checkboxes.forEach(cb => {
        cb.checked = checked;
    });
    updateReviewSelectAllState();
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

function applyReviewViewMode() {
    if (elements.reviewRailButtons && elements.reviewRailButtons.length) {
        elements.reviewRailButtons.forEach(btn => {
            btn.classList.toggle(
                'active',
                btn.dataset.reportType === state.reviewReportType && btn.dataset.view === state.reviewView
            );
        });
    }
    updateReviewRailCounts();
    initReviewSortable();
}

function getActiveReviewContainer() {
    const container = document.getElementById('review-items');
    return container || elements.reviewList;
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

function toChineseNum(num) {
    const chineseNums = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九'];
    if (num < 10) return chineseNums[num];
    if (num < 20) return '十' + (num % 10 !== 0 ? chineseNums[num % 10] : '');
    if (num < 100) {
        const ten = Math.floor(num / 10);
        const unit = num % 10;
        return chineseNums[ten] + '十' + (unit !== 0 ? chineseNums[unit] : '');
    }
    return num.toString();
}

function generatePreviewText() {
    const reportType = state.reviewReportType;
    const isWanbao = reportType === 'wanbao';
    const view = state.reviewView || 'selected';
    const items = state.reviewData[view] || [];

    if (!items.length) return '';

    // Grouping
    const groups = {
        'internal_negative': [],
        'internal_positive': [],
        'external_negative': [],
        'external_positive': []
    };

    items.forEach(item => {
        const key = resolveGroupKey(item);
        if (groups[key]) groups[key].push(item);
        else {
            if (!groups['other']) groups['other'] = [];
            groups['other'].push(item);
        }
    });

    let content = '';

    const sections = [];
    if (isWanbao) {
        // Wanbao: Internal (Pos+Neg) -> 【舆情速览】, External (Pos+Neg) -> 【舆情参考】
        sections.push({
            label: '【舆情速览】',
            items: [...groups['internal_positive'], ...groups['internal_negative']],
            numbered: true
        });
        sections.push({
            label: '【舆情参考】',
            items: [...groups['external_positive'], ...groups['external_negative']],
            numbered: true
        });
    } else {
        // Zongbao
        // 1. Internal Negative -> 【重点关注舆情】
        sections.push({
            label: '【重点关注舆情】',
            items: groups['internal_negative'],
            marker: '★'
        });
        // 2. Internal Positive + External Positive -> 【新闻信息纵览】
        const mergedPositive = [...groups['internal_positive'], ...groups['external_positive']];
        sections.push({
            label: '【新闻信息纵览】',
            items: mergedPositive,
            marker: '■'
        });
        // 3. External Negative -> 【国内教育热点】
        sections.push({
            label: '【国内教育热点】',
            items: groups['external_negative'],
            marker: '▲'
        });
    }

    sections.forEach(section => {
        const sectionItems = section.items || [];
        if (!sectionItems.length) return;

        content += `${section.label}\n`;
        sectionItems.forEach((item, index) => {
            const title = (item.title || '').trim();
            // Use manual_summary if available, else llm_summary, else summary
            // Assuming order: manual > llm > raw. The backend usually normalizes this into 'summary' but we check properties.
            const summary = (item.manual_summary || item.summary || '').trim();
            const source = (item.llm_source_display || item.source || '').trim();

            let prefix = '';
            if (section.marker) {
                prefix = `${section.marker}`;
            } else if (section.numbered) {
                prefix = `${toChineseNum(index + 1)}、`;
            }

            content += `${prefix}${title}\n`;
            if (summary) content += `${summary}`;
            if (source) content += `（${source}）`;
            content += '\n\n'; // Empty line between items for readability? User example shows distinct blocks.
        });
        content += '\n'; // Separator between sections
    });

    return content.trim(); // Clean up trailing newlines
}

async function handlePreviewCopy() {
    try {
        const text = generatePreviewText();

        if (!text) {
            showToast('当前列表为空，无内容可生成', 'error');
            return;
        }

        const modal = document.getElementById('preview-modal');
        const textarea = document.getElementById('preview-text');
        if (modal && textarea) {
            textarea.value = text;
            modal.classList.add('active');
        } else {
            // Fallback if modal not present
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(text);
            }
            showToast('已复制到剪贴板(弹窗未找到)');
        }
    } catch (e) {
        console.error(e);
        showToast('预览生成失败', 'error');
    }
}

async function handleArchive() {
    if (!confirm('确定要归档当前列表吗？归档后文章将标记为已导出并从列表中移除。')) {
        return;
    }

    const reportType = state.reviewReportType;
    const template = reportType === 'wanbao' ? 'wanbao' : 'zongbao';
    const payload = {
        report_tag: new Date().toISOString().split('T')[0],
        template: template,
        dry_run: false,
        mark_exported: true,
        report_type: reportType
    };

    try {
        const res = await fetch(`${API_BASE}/export`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await res.json();

        const count = result.count || 0;
        showToast(`归档成功，已标记 ${count} 条文章`);
        await loadReviewData();
        loadStats();
    } catch (e) {
        showToast('归档失败', 'error');
    }
}
