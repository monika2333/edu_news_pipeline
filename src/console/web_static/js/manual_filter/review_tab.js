// Manual Filter JS - Review Tab
// Depends on: api.js, ui_templates.js, export_utils.js, core.js, utils.js

// --- Review Tab Logic ---

async function loadReviewData() {
    elements.reviewList.innerHTML = renderSkeleton(3);
    try {
        // Fetch all reviewed items (server returns { selected: [...], backup: [...] })
        const data = await manualFilterApi.fetchReviewItems(state.reviewReportType);

        state.reviewData = data;
        state.reviewCounts.zongbao = {
            selected: data.selected.length,
            backup: data.backup.length
        };

        // Use local calculation for counts to be safe
        updateReviewRailCounts();
        renderReviewView();
    } catch (e) {
        console.error(e);
        elements.reviewList.innerHTML = '<div class="error">加载数据失败</div>';
    }
}

// Filters memory data based on search term & current view (zongbao/wanbao)
function filterReviewItems(term) {
    const view = state.reviewView; // 'selected' or 'backup'
    const reportType = state.reviewReportType; // 'zongbao' or 'wanbao'

    // 1. Pick the list based on view
    let list = state.reviewData[view] || [];

    // 2. Filter by search term
    if (term) {
        const lower = term.toLowerCase();
        list = list.filter(item =>
            (item.title || '').toLowerCase().includes(lower) ||
            (item.summary || '').toLowerCase().includes(lower) ||
            (item.source || '').toLowerCase().includes(lower)
        );
    }

    return list;
}

function renderReviewView() {
    const term = elements.reviewSearchInput.value.trim();
    const items = filterReviewItems(term);

    elements.reviewList.innerHTML = '';

    if (!items.length) {
        elements.reviewList.innerHTML = '<div class="empty">无相关内容</div>';
        return;
    }

    if (isSortMode) {
        renderSortableReviewItems(items);
    } else {
        renderGroupedReviewItems(items);
    }

    updateReviewSelectAllState();
}

function applySortModeState() {
    // Re-render
    renderReviewView();
    // Toggle button style
    const btn = elements.sortToggleBtn;
    if (isSortMode) {
        btn.classList.add('active');
        btn.innerHTML = '<span class="icon">📝</span> 详细模式'; // Switch back text
    } else {
        btn.classList.remove('active');
        btn.innerHTML = '<span class="icon">🔃</span> 排序模式';
    }
}

function toggleSortMode() {
    isSortMode = !isSortMode;
    applySortModeState();
}

function resolveGroupKey(item) {
    const isInternal = !!item.is_beijing_related;
    const sentiment = (item.sentiment_label || '').toLowerCase() === 'negative' ? 'negative' : 'positive';

    if (isInternal && sentiment === 'positive') return 'internal_positive';
    if (isInternal && sentiment === 'negative') return 'internal_negative';
    if (!isInternal && sentiment === 'positive') return 'external_positive';
    return 'external_negative';
}

function renderGroupedReviewItems(items) {
    // Group items
    const groups = {
        internal_positive: [],
        internal_negative: [],
        external_positive: [],
        external_negative: []
    };

    items.forEach(item => {
        const key = resolveGroupKey(item);
        if (groups[key]) groups[key].push(item);
    });

    const order = GROUP_ORDER; // Defined in core.js

    let html = '';
    order.forEach(g => {
        const list = groups[g.key];
        if (list && list.length) {
            html += `<div class="review-group-section">
                ${manualFilterUi.renderReviewGroupHeader(g.key, g.label, list.length)}
                <div class="review-group-list">
                    ${list.map(item => manualFilterUi.renderReviewCard(item)).join('')}
                </div>
            </div>`;
        }
    });

    elements.reviewList.innerHTML = html;
    bindReviewSelectionControls();
}

function renderSortableReviewItems(items) {
    // Flat list for sorting
    const html = `<div id="sortable-list" class="review-sortable-list">
        ${items.map(item => manualFilterUi.renderReviewCard(item)).join('')}
    </div>`;

    elements.reviewList.innerHTML = html;

    initReviewSortable();
    bindReviewSelectionControls();
}


function initReviewSortable() {
    const el = document.getElementById('sortable-list');
    if (!el) return;

    // Initialize new sortable instance
    new Sortable(el, {
        animation: 150,
        handle: '.drag-handle',
        onEnd: async (evt) => {
            // Reorder happened
            // Update state order
            // Get all IDs in new order
            const orderedIds = Array.from(el.querySelectorAll('.review-card')).map(card => card.dataset.id);

            // Sync to state
            syncReviewStateOrder(state.reviewView, orderedIds);

            // Persist
            await persistReviewOrder();
        }
    });
}

function bindReviewSelectionControls() {
    // Status Selects
    elements.reviewList.querySelectorAll('.status-select').forEach(sel => {
        sel.addEventListener('change', (e) => handleReviewStatusChange(e));
    });

    // Edits (Summary/Source)
    elements.reviewList.querySelectorAll('.review-summary-edit, .review-source-edit').forEach(input => {
        input.addEventListener('change', (e) => handleSummaryUpdate(e));
    });
}

function updateReviewSelectAllState() {
    const cb = elements.reviewSelectAll;
    if (!cb) return;
    cb.checked = false;
    // Logic to check if all are selected? We need to track selection state.
}

function toggleReviewSelectAll(checked) {
    // Since cards don't have checkboxes in my current UI template, this is a placeholder.
    // Real impl requires checkboxes.
    // TODO: Add checkboxes to review cards.
    console.log('Toggle select all:', checked);
}

async function applyReviewBulkStatus() {
    const statusWithPrefix = elements.reviewBulkStatus.value; // e.g. "zongbao:selected"
    if (!statusWithPrefix) return;

    // Parse status
    let status = statusWithPrefix;
    if (status.includes(':')) status = status.split(':')[1];

    // Get selected IDs
    // TODO: Implement checkbox selection in UI cards
    const ids = [];

    if (!ids.length) {
        showToast('请先选择文章');
        return;
    }

    try {
        isBulkUpdatingReview = true;
        await manualFilterApi.postDecisions(ids, status, state.actor, state.reviewReportType);

        // Remove from current view
        ids.forEach(id => {
            const card = elements.reviewList.querySelector(`.review-card[data-id="${id}"]`);
            if (card) card.remove();
        });

        showToast('批量更新成功');
        loadReviewData(); // refresh
    } catch (e) {
        showToast('更新失败', 'error');
    } finally {
        isBulkUpdatingReview = false;
        elements.reviewBulkStatus.value = '';
    }
}


function syncReviewStateOrder(view, orderedIds) {
    const list = state.reviewData[view] || [];
    // Sort list array to match orderedIds
    const map = new Map(list.map(item => [item.article_id, item]));
    const newList = orderedIds.map(id => map.get(id)).filter(Boolean);
    // Append any missing items (if any)
    const currentIds = new Set(orderedIds);
    list.forEach(item => {
        if (!currentIds.has(item.article_id)) newList.push(item);
    });

    state.reviewData[view] = newList;
}

async function persistReviewOrder() {
    const view = state.reviewView;
    const ids = (state.reviewData[view] || []).map(i => i.article_id);
    // Which type? zongbao or wanbao?
    const type = state.reviewReportType;

    try {
        await manualFilterApi.postReviewOrder({
            type,
            status: view,
            order: ids
        });
    } catch (e) {
        console.error('Failed to save order', e);
    }
}

async function handleReviewStatusChange(e) {
    const select = e.target;
    const id = select.dataset.id;
    const newStatus = select.value;

    if (!id || !newStatus) return;

    try {
        await manualFilterApi.postDecisions([id], newStatus, state.actor, state.reviewReportType);

        // Remove card from UI
        const card = select.closest('.review-card');
        if (card) {
            card.style.opacity = '0.5';
            setTimeout(() => card.remove(), 300);
        }

        // Update counts (optimistic)

        showToast('已更新状态');
    } catch (err) {
        showToast('更新失败', 'error');
        // revert select?
    }
}

async function handleSummaryUpdate(e) {
    const input = e.target;
    const id = input.dataset.id;
    const val = input.value;
    const isSource = input.classList.contains('review-source-edit');

    const edits = {};
    if (isSource) {
        edits[id] = { llm_source: val };
    } else {
        edits[id] = { summary: val };
    }

    try {
        await manualFilterApi.postEdits(edits, state.actor, state.reviewReportType);
        // Note: Update state.reviewData in memory too so preview is correct
        updateLocalStateReviewData(id, isSource ? { llm_source: val } : { summary: val });
        showToast('已保存修改');
    } catch (err) {
        showToast('保存失败', 'error');
    }
}

function updateLocalStateReviewData(id, changes) {
    // Find item in current view
    const view = state.reviewView;
    const list = state.reviewData[view] || [];
    const item = list.find(i => i.article_id === id);
    if (item) {
        Object.assign(item, changes);
        // Also update llm_source_display if needed
        if (changes.llm_source) item.llm_source_display = changes.llm_source;
    }
}

// --- Preview & Export ---

function handlePreviewCopy() {
    const text = manualFilterExport.generatePreviewText(state);
    manualFilterExport.copyToClipboard(text).then(ok => {
        if (ok) showToast('已复制预览内容');
        else showToast('复制失败', 'error');
    });

    // Also show in modal for fallback
    const modal = document.getElementById('preview-modal');
    const textarea = document.getElementById('preview-text');
    if (modal && textarea) {
        textarea.value = text;
        modal.style.display = 'block';
    }
}

function handleArchive() {
    // Implementation depends on requirements, usually just confirming export?
    showToast('归档功能待实现');
}

// --- Event Bindings ---

document.addEventListener('DOMContentLoaded', () => {
    const btnPreview = document.getElementById('btn-preview-copy');
    if (btnPreview) btnPreview.addEventListener('click', handlePreviewCopy);

    const btnArchive = document.getElementById('btn-archive');
    if (btnArchive) btnArchive.addEventListener('click', handleArchive);

    const btnSort = document.getElementById('btn-toggle-sort');
    if (btnSort) btnSort.addEventListener('click', toggleSortMode);

    const btnRefresh = document.getElementById('btn-refresh');
    // ...
});
