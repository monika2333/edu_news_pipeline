// Manual Filter JS - Init

// Init
document.addEventListener('DOMContentLoaded', async () => {
    try {
        const workspaceReady = await prepareManualFilterWorkspace();
        if (!workspaceReady) {
            if (elements.filterList) {
                elements.filterList.innerHTML = '<div class="empty empty-state">暂无可用班次，请联系管理员安排班次。</div>';
            }
            return;
        }
    } catch (error) {
        const errorTarget = elements.filterList || elements.reviewList || elements.discardList;
        if (errorTarget) {
            errorTarget.innerHTML = '<div class="error">工作区加载失败，请刷新后重试。</div>';
        }
        return;
    }

    setupTabs();
    loadStats();
    if (state.currentTab === 'review') {
        loadReviewData();
    } else {
        loadFilterData();
        loadFilterCounts();
    }
    setupFilterRealtimeDecisionHandlers();
    if (elements.reviewList) {
        setupDuplicateReview();
    }
    setupScoreFeedback();

    // Global event listeners
    const btnRefresh = document.getElementById('btn-refresh');
    if (btnRefresh) {
        btnRefresh.addEventListener('click', () => {
            loadStats();
            const forceClusterRefresh = state.currentTab === 'filter';
            shouldForceClusterRefresh = forceClusterRefresh;
            reloadCurrentTab({ forceClusterRefresh });
        });
    }

    const btnSubmitFilter = document.getElementById('btn-submit-filter');
    if (btnSubmitFilter) {
        btnSubmitFilter.addEventListener('click', discardRemainingItems);
    }
    const btnFilterSearch = document.getElementById('btn-filter-search');
    if (btnFilterSearch) {
        btnFilterSearch.addEventListener('click', applyFilterSearch);
    }
    if (elements.filterSearchClear) {
        elements.filterSearchClear.addEventListener('click', async () => {
            await clearFilterSearch();
            elements.filterSearchInput?.focus();
        });
    }
    const btnFilterDiscardBeforeDate = document.getElementById('btn-filter-discard-before-date');
    if (btnFilterDiscardBeforeDate) {
        btnFilterDiscardBeforeDate.addEventListener('click', discardBeforeDate);
    }
    if (elements.filterSearchInput) {
        elements.filterSearchInput.addEventListener('input', syncFilterSearchClearButton);
        elements.filterSearchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') applyFilterSearch();
        });
    }
    if (elements.filterDateBefore) {
        elements.filterDateBefore.addEventListener('change', () => {
            state.filterPublishedBefore = elements.filterDateBefore.value || '';
            syncFilterToolbarState();
        });
    }

    // New Export/Archive Handlers
    const btnPreview = document.getElementById('btn-preview-copy');
    if (btnPreview) {
        btnPreview.addEventListener('click', handlePreviewCopy);
    }
    const btnArchive = document.getElementById('btn-archive');
    if (btnArchive) {
        btnArchive.addEventListener('click', handleArchive);
    }

    // Preview Modal Handlers
    const btnClosePreview = document.getElementById('btn-close-preview');
    if (btnClosePreview) {
        btnClosePreview.addEventListener('click', () => {
            const modal = document.getElementById('preview-modal');
            if (modal) modal.classList.remove('active');
        });
    }
    const btnCopyPreview = document.getElementById('btn-copy-preview');
    if (btnCopyPreview) {
        btnCopyPreview.addEventListener('click', async () => {
            const textarea = document.getElementById('preview-text');
            if (!textarea) return;
            const text = textarea.value;
            if (!text) return;
            try {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    await navigator.clipboard.writeText(text);
                } else {
                    textarea.select();
                    document.execCommand('copy');
                }
                showToast('已复制到剪贴板');
            } catch (err) {
                showToast('复制失败', 'error');
            }
        });
    }

    if (elements.sortToggleBtn) {
        elements.sortToggleBtn.addEventListener('click', toggleSortMode);
    }
    const btnAutoReorder = document.getElementById('btn-auto-reorder');
    if (btnAutoReorder) {
        btnAutoReorder.addEventListener('click', autoReorderReviewItems);
    }
    if (elements.reviewSelectAll) {
        elements.reviewSelectAll.addEventListener('change', (e) => {
            toggleReviewSelectAll(Boolean(e.target.checked));
        });
    }
    if (elements.reviewBulkStatus) {
        elements.reviewBulkStatus.addEventListener('change', applyReviewBulkStatus);
    }
    if (elements.reviewRailButtons && elements.reviewRailButtons.length) {
        elements.reviewRailButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                const targetType = btn.dataset.reportType || 'zongbao';
                const targetView = btn.dataset.view || 'selected';
                setReviewReportType(targetType);
                setReviewView(targetView);
            });
        });
    }
    if (elements.filterTabButtons && elements.filterTabButtons.length) {
        elements.filterTabButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                elements.filterTabButtons.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                state.filterCategory = btn.dataset.category || 'all';
                state.filterPage = 1;
                syncFilterToolbarState();
                loadFilterData();
            });
        });
        updateFilterCountsUI();
    }
    // Removed old export modal listeners
    if (elements.reportTypeButtons && elements.reportTypeButtons.length) {
        elements.reportTypeButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                const val = btn.dataset.type || 'zongbao';
                setReviewReportType(val);
            });
        });
        elements.reportTypeButtons.forEach(btn => {
            btn.classList.toggle('active', btn.dataset.type === state.reviewReportType);
        });
    }
    if (elements.reviewSearchInput) {
        elements.reviewSearchInput.addEventListener('input', applyReviewSearchFilter);
        syncReviewSearchClearButton();
    }
    if (elements.reviewSearchClear && elements.reviewSearchInput) {
        elements.reviewSearchClear.addEventListener('click', () => {
            elements.reviewSearchInput.value = '';
            applyReviewSearchFilter();
            elements.reviewSearchInput.focus();
        });
    }

    // Pagination listeners (delegated or specific)
    setupPagination();
    let reviewResizeTimer = null;
    window.addEventListener('resize', () => {
        applyReviewViewMode();
        window.clearTimeout(reviewResizeTimer);
        reviewResizeTimer = window.setTimeout(resizeReviewSummaryBoxes, 120);
    });
});
