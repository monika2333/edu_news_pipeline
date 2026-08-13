// Manual Filter JS - Report Type Tab
// 右侧报别切换标签 + 弹出面板。报别状态本身由 setReviewReportType（utils.js）统一管理，
// 本模块只负责面板的开合；admin review 模式不渲染该标签，所有入口都要容忍元素缺失。

function openReportTypePopover() {
    if (!elements.reportTypePopover || !elements.reportTypeTab) return;
    elements.reportTypePopover.classList.add('active');
    elements.reportTypeTab.setAttribute('aria-expanded', 'true');
}

function closeReportTypePopover() {
    if (!elements.reportTypePopover || !elements.reportTypeTab) return;
    elements.reportTypePopover.classList.remove('active');
    elements.reportTypeTab.setAttribute('aria-expanded', 'false');
}

function setupReportTypeTab() {
    if (!elements.reportTypeTab || !elements.reportTypePopover) return;

    elements.reportTypeTab.addEventListener('click', () => {
        if (elements.reportTypePopover.classList.contains('active')) {
            closeReportTypePopover();
        } else {
            openReportTypePopover();
        }
    });

    // 选中报别后收起面板；切换逻辑本身走 init.js 里 .report-type-btn 的既有绑定
    elements.reportTypePopover.addEventListener('click', event => {
        if (event.target.closest('.report-type-btn')) closeReportTypePopover();
    });

    document.addEventListener('click', event => {
        if (!elements.reportTypePopover.classList.contains('active')) return;
        if (event.target.closest('#report-type-tab') || event.target.closest('#report-type-popover')) return;
        closeReportTypePopover();
    });

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') closeReportTypePopover();
    });
}
