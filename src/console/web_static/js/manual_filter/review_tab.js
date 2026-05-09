// Manual Filter JS - Review Tab

// --- Review Tab View ---

function buildReviewSearchText(item) {
    return [
        item.title,
        item.summary,
        item.llm_summary,
        item.llm_source_display,
        item.llm_source_raw,
        item.source
    ]
        .map(value => String(value || '').trim())
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
}

function escapeReviewAttr(value) {
    return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
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
        const searchText = [
            card.dataset.searchText || '',
            title,
            summary
        ].join(' ').toLowerCase();

        if (searchText.includes(term)) {
            card.style.display = '';
        } else {
            card.style.display = 'none';
        }
    });
    updateReviewSelectAllState();
}

function applyReviewSearchFilter() {
    if (!elements.reviewSearchInput) return;
    const term = elements.reviewSearchInput.value.trim().toLowerCase();
    filterReviewItems(term);
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
    applyReviewSearchFilter();
    bindReviewGroupToggles();
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
                    <div class="review-group-header" title="点击展开/收起">
                        <span class="toggle-icon">▼</span>
                         ${group.label} (${groupItems.length})
                    </div>
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

function bindReviewGroupToggles() {
    if (!elements.reviewList) return;
    const headers = elements.reviewList.querySelectorAll('.review-group-header');
    headers.forEach(header => {
        header.addEventListener('click', () => {
            const group = header.closest('.review-group');
            if (group) {
                group.classList.toggle('collapsed');
                updateReviewSelectAllState();
            }
        });
    });
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
            const searchText = escapeReviewAttr(buildReviewSearchText(item));
            return `
                            <div class="article-card sort-card" data-id="${item.article_id || ''}" data-status="${currentStatus}" data-search-text="${searchText}">
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
    const searchText = escapeReviewAttr(buildReviewSearchText(item));
    const selectValue =
        currentStatus === 'selected' || currentStatus === 'backup'
            ? `${currentReportType}:${currentStatus}`
            : currentStatus;
    return `
        <div class="article-card${bonusClass}" data-id="${item.article_id || ''}" data-status="${currentStatus}" data-report-type="${currentReportType}" data-search-text="${searchText}">
            <div class="card-header">
                <label class="review-select-wrap" title="选择">
                    <input type="checkbox" class="review-select">
                </label>
                <span class="drag-handle" title="拖动排序">&#8942;</span>
                <h4 class="article-title">
                    ${item.title || '(No Title)'}
                    ${item.url ? `<a href="${item.url}" target="_blank" rel="noopener noreferrer">🔗</a>` : ''}
                </h4>
                <div class="review-card-actions">
                    <button type="button" class="review-discard-btn" data-id="${item.article_id || ''}" title="放弃新闻" aria-label="放弃新闻">🗑️</button>
                    <select class="status-select" data-id="${item.article_id || ''}">
                        <option value="zongbao:selected" ${selectValue === 'zongbao:selected' ? 'selected' : ''}>综报采纳</option>
                        <option value="zongbao:backup" ${selectValue === 'zongbao:backup' ? 'selected' : ''}>综报备选</option>
                        <option value="wanbao:selected" ${selectValue === 'wanbao:selected' ? 'selected' : ''}>晚报采纳</option>
                        <option value="wanbao:backup" ${selectValue === 'wanbao:backup' ? 'selected' : ''}>晚报备选</option>
                        <option value="discarded" ${selectValue === 'discarded' ? 'selected' : ''}>放弃</option>
                        <option value="pending" ${selectValue === 'pending' ? 'selected' : ''}>待处理</option>
                    </select>
                </div>
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

function bindReviewSelectionControls() {
    const checkboxes = elements.reviewList.querySelectorAll('.review-select');
    checkboxes.forEach(cb => {
        cb.addEventListener('change', updateReviewSelectAllState);
    });

    const statusSelects = elements.reviewList.querySelectorAll('.status-select');
    statusSelects.forEach(sel => {
        sel.addEventListener('change', handleReviewStatusChange);
    });

    const discardButtons = elements.reviewList.querySelectorAll('.review-discard-btn');
    discardButtons.forEach(btn => {
        btn.addEventListener('click', handleReviewDiscardClick);
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
    const checkboxes = getVisibleReviewCheckboxes();
    const total = checkboxes.length;
    const checkedCount = Array.from(checkboxes).filter(cb => cb.checked).length;
    selectAll.indeterminate = checkedCount > 0 && checkedCount < total;
    selectAll.checked = total > 0 && checkedCount === total;
}

function toggleReviewSelectAll(checked) {
    const checkboxes = getVisibleReviewCheckboxes();
    checkboxes.forEach(cb => {
        cb.checked = checked;
    });
    updateReviewSelectAllState();
}

function getVisibleReviewCheckboxes() {
    const scope = getActiveReviewContainer();
    const checkboxes = scope.querySelectorAll('.review-select');
    return Array.from(checkboxes).filter(cb => {
        const card = cb.closest('.article-card');
        if (!card) return false;
        return card.getClientRects().length > 0;
    });
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
    const archiveBtn = document.getElementById('btn-archive');
    if (archiveBtn) {
        archiveBtn.style.display = state.reviewView === 'backup' ? 'none' : '';
    }
    updateReviewRailCounts();
    initReviewSortable();
}

function getActiveReviewContainer() {
    const container = document.getElementById('review-items');
    return container || elements.reviewList;
}
