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
    document.getElementById('btn-export').addEventListener('click', openExportModal);
    document.getElementById('btn-close-modal').addEventListener('click', closeModal);
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
    if (elements.exportPreviewBtn) {
        elements.exportPreviewBtn.addEventListener('click', refreshPreviewAndCopy);
    }
    if (elements.exportConfirmBtn) {
        elements.exportConfirmBtn.addEventListener('click', confirmExportAndCopy);
    }
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