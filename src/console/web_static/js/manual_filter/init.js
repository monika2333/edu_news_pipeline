// Manual Filter JS - Init

// Init
document.addEventListener('DOMContentLoaded', () => {
    setupTabs();
    setupActor();
    loadStats();
    loadFilterData();
    loadFilterCounts();
    setupFilterRealtimeDecisionHandlers();
    setupSearchDrawer();

    // Global event listeners
    document.getElementById('btn-refresh').addEventListener('click', () => {
        loadStats();
        shouldForceClusterRefresh = true;
        reloadCurrentTab({ forceClusterRefresh: true });
    });

    document.getElementById('btn-submit-filter').addEventListener('click', discardRemainingItems);

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
        elements.reviewSearchInput.addEventListener('input', (e) => {
            const term = e.target.value.trim().toLowerCase();
            filterReviewItems(term);
        });
    }

    // Pagination listeners (delegated or specific)
    setupPagination();
    window.addEventListener('resize', applyReviewViewMode);
});