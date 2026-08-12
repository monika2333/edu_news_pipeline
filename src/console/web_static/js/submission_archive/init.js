// Submission Archive JS - Init
/* ---------- 初始化 ---------- */

document.addEventListener('DOMContentLoaded', () => {
    loadNavPending();
    if (view === 'list' || view === 'detail') {
        initBrowserView();
    } else if (view === 'new') {
        document.getElementById('archive-parse').addEventListener('click', parsePastedReport);
        document.getElementById('archive-report-type').addEventListener('change', () => {
            renderWarnings(parsedState?.warnings || []);
        });
        document.getElementById('archive-add-item').addEventListener('click', () => {
            previewItems.push(blankPreviewItem());
            renderPreviewItems();
            const cards = document.querySelectorAll('#archive-preview-items [data-index]');
            cards[cards.length - 1]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
        document.getElementById('archive-save').addEventListener('click', () => saveReport(false));
        bindPreviewItems();
    } else if (view === 'link-queue') {
        loadLinkQueue();
    }
});
