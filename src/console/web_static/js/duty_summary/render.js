// Duty Summary JS - Render
function getVisibleItems() {
    const query = state.searchQuery.trim().toLocaleLowerCase('zh-CN');
    if (!query) return state.items;
    return state.items.filter(item => [
        item.title,
        item.edited_summary,
        item.summary,
        item.llm_summary,
        item.source,
        item.llm_source,
        item.decision,
        item.admin_discarded_by_display_name
    ].some(value => String(value ?? '').toLocaleLowerCase('zh-CN').includes(query)));
}

async function request(path, options) {
    const response = await fetch(path, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(formatApiError(payload, '请求失败'));
    return payload;
}

function renderShifts() {
    if (!state.shifts.length) {
        elements.shiftList.innerHTML = '<div class="summary-empty empty-state">暂无当前或历史班次。</div>';
        renderColumnCounts();
        return;
    }
    elements.shiftList.innerHTML = state.shifts.map(shift => `
        <button class="filter-tab-btn summary-shift-card ${state.shiftId === shift.shift_id ? 'active' : ''}" data-shift-id="${escapeHtml(shift.shift_id)}">
            <span class="summary-shift-date">${escapeHtml(window.formatDutyShiftDate(shift.ends_at))}</span>
        </button>
    `).join('');
    elements.shiftList.querySelectorAll('[data-shift-id]').forEach(button => {
        button.addEventListener('click', () => {
            state.shiftId = button.dataset.shiftId;
            state.selected.clear();
            renderShifts();
            setShiftPanelOpen(false);
            loadResults();
        });
    });
    renderColumnCounts();
}

function renderColumnCounts() {
    const shift = state.shifts.find(item => item.shift_id === state.shiftId);
    elements.columnTabs.forEach(tab => {
        const reportType = tab.dataset.reportType;
        const status = tab.dataset.targetStatus;
        const baseField = `${reportType}_${status}`;
        const countField = state.adminProcessScope === 'all'
            ? `${baseField}_all`
            : baseField;
        const count = Number(shift?.[countField]) || 0;
        tab.textContent = `${reviewColumnLabel(reportType, status)}（${count}）`;
    });
}

function updateSelection(visibleItems = getVisibleItems()) {
    const canImport = !state.adminDiscarded && Boolean(state.shiftId);
    elements.selectAll.closest('.summary-select-all').hidden = !canImport;
    elements.discardButton.hidden = !canImport;
    elements.importTarget.disabled = !canImport;
    elements.discardButton.disabled = !canImport || !state.selected.size;
    elements.importBar.hidden = !state.shiftId;
    const visibleIds = visibleItems.map(item => item.article_id);
    const selectedVisibleCount = visibleIds.filter(id => state.selected.has(id)).length;
    elements.selectAll.disabled = !visibleIds.length || !canImport;
    elements.selectAll.checked = Boolean(visibleIds.length)
        && selectedVisibleCount === visibleIds.length;
    elements.selectAll.indeterminate = selectedVisibleCount > 0
        && selectedVisibleCount < visibleIds.length;
}

function renderItems() {
    const visibleItems = getVisibleItems();
    updateSelection(visibleItems);
    if (!state.items.length) {
        const emptyText = state.adminDiscarded || state.adminProcessScope === 'all'
            ? '当前没有新闻'
            : '当前没有待处理新闻';
        elements.items.innerHTML = `<div class="summary-empty empty-state">${emptyText}</div>`;
        return;
    }
    if (!visibleItems.length) {
        elements.items.innerHTML = '<div class="summary-empty empty-state">没有找到匹配的新闻。</div>';
        return;
    }
    elements.items.innerHTML = visibleItems.map(item => {
        const recoveredManualDiscard = isRecoveredManualDiscard(item);
        const discardedActive = !recoveredManualDiscard && (
            Boolean(item.admin_discarded_at) || item.admin_status === 'discarded'
        );
        const selectedActive = !discardedActive && item.admin_status === 'selected';
        const showUndoAction = canUndoAdminProcess(item);
        const adminProcessTag = `<span class="summary-admin-process-tag${
            isAdminProcessed(item) ? ' is-processed' : ' is-pending'
        }">${escapeHtml(adminProcessLabel(item))}</span>`;
        const categoryLabel = articleCategoryLabel(item);
        const categoryTag = categoryLabel
            ? `<span class="badge summary-article-category ${getSentimentClass(item.sentiment_label)}">${categoryLabel}</span>`
            : '';
        const finalizationTag = item.decision === 'selected'
            ? `<span class="summary-finalization-tag${item.finalized_at ? '' : ' is-pending'}">${
                item.finalized_at
                    ? '已定稿'
                    : '未定稿'
            }</span>`
            : '';
        return `
        <article class="article-card summary-item">
            <div class="card-header">
                ${state.adminDiscarded ? '' : `<input type="checkbox" data-article-id="${escapeHtml(item.article_id)}" ${state.selected.has(item.article_id) ? 'checked' : ''}>`}
                <div class="summary-title-line">
                    <h3 class="article-title">${escapeHtml(item.title || '无标题')}</h3>
                    <div class="summary-title-tags">
                        ${adminProcessTag}
                        ${finalizationTag}
                    </div>
                </div>
                ${state.adminDiscarded ? `
                    <div class="review-card-actions">
                        <button class="btn btn-secondary summary-restore-action" type="button"
                            data-admin-discard-action="restore"
                            data-article-id="${escapeHtml(item.article_id)}">
                            恢复
                        </button>
                    </div>
                ` : `
                    <div class="review-card-actions" role="group" aria-label="单条新闻操作">
                        <button class="summary-quick-action summary-quick-accept${selectedActive ? ' is-active' : ''}"
                            type="button" data-quick-status="selected"
                            data-article-id="${escapeHtml(item.article_id)}"
                            data-cancel-selected="${String(selectedActive)}"
                            aria-label="${selectedActive ? '取消采纳这条新闻' : '采纳这条新闻'}"
                            title="${selectedActive ? '取消采纳' : '采纳'}"
                            aria-pressed="${String(selectedActive)}">
                            ✅
                        </button>
                        <button class="summary-quick-action summary-quick-discard${discardedActive ? ' is-active' : ''}"
                            type="button" data-quick-status="discarded"
                            data-article-id="${escapeHtml(item.article_id)}"
                            data-cancel-discarded="${String(discardedActive)}"
                            aria-label="${discardedActive ? '取消放弃这条新闻' : '放弃这条新闻'}"
                            title="${discardedActive ? '取消放弃' : '放弃'}"
                            aria-pressed="${String(discardedActive)}">
                            ❌
                        </button>
                        ${showUndoAction ? `
                            <button class="summary-quick-action summary-quick-undo"
                                type="button" data-admin-undo-action
                                data-article-id="${escapeHtml(item.article_id)}"
                                aria-label="撤回管理员处理" title="撤回处理">
                                ↩
                            </button>
                        ` : ''}
                    </div>
                `}
            </div>
            <div class="meta-row">
                    ${state.adminDiscarded ? `<span>放弃人：${escapeHtml(item.admin_discarded_by_display_name || '管理员')}</span>` : ''}
                    <span>${escapeHtml(item.source || item.llm_source || '未知来源')}</span>
                    <span>${escapeHtml(formatDateTime(item.publish_time_iso || item.created_at))}</span>
                    ${categoryTag}
            </div>
            <p class="summary-box">${escapeHtml(item.edited_summary || item.summary || item.llm_summary || '')}</p>
        </article>
    `;
    }).join('');
    elements.items.querySelectorAll('input[type="checkbox"][data-article-id]').forEach(checkbox => {
        checkbox.addEventListener('change', () => {
            if (checkbox.checked) state.selected.add(checkbox.dataset.articleId);
            else state.selected.delete(checkbox.dataset.articleId);
            updateSelection(visibleItems);
        });
    });
    elements.items.querySelectorAll('[data-quick-status]').forEach(button => {
        button.addEventListener('click', () => {
            quickDecideItem(button);
        });
    });
    elements.items.querySelectorAll('[data-admin-discard-action]').forEach(button => {
        button.addEventListener('click', () => {
            setAdminDiscarded(button, false);
        });
    });
    elements.items.querySelectorAll('[data-admin-undo-action]').forEach(button => {
        button.addEventListener('click', () => {
            undoAdminProcessing(button);
        });
    });
}

function syncSearchClearButton() {
    elements.searchClear.hidden = !elements.searchInput.value;
}

function syncAdminProcessTabs() {
    elements.processFilter.hidden = state.adminDiscarded;
    elements.processTabs.forEach(tab => {
        const active = tab.dataset.adminProcessScope === state.adminProcessScope;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-pressed', String(active));
    });
}

