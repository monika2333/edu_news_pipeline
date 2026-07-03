// Manual Filter JS - Duplicate Review State

let duplicateReviewDisplayedScope = null;
let duplicateReviewRequestSequence = 0;
const duplicateReviewJobs = new Map();

function getDuplicateReviewScope(reportType = state.reviewReportType, decision = state.reviewView) {
    return {
        reportType: reportType === 'wanbao' ? 'wanbao' : 'zongbao',
        decision: decision === 'backup' ? 'backup' : 'selected'
    };
}

function getDuplicateReviewScopeKey(scope) {
    return `${scope.reportType}:${scope.decision}`;
}

function getDuplicateReviewColumnLabel(scope = getDuplicateReviewScope()) {
    const reportLabel = scope.reportType === 'wanbao' ? '晚报' : '综报';
    const decisionLabel = scope.decision === 'backup' ? '备选' : '采纳';
    return `${reportLabel}${decisionLabel}`;
}

function isDuplicateReviewScopeActive(scope) {
    return state.currentTab === 'review'
        && state.reviewReportType === scope.reportType
        && state.reviewView === scope.decision;
}

function updateDuplicateReviewJobUI() {
    const scope = getDuplicateReviewScope();
    const job = duplicateReviewJobs.get(getDuplicateReviewScopeKey(scope));
    const checkButton = document.getElementById('btn-check-duplicates');
    if (checkButton) {
        checkButton.disabled = job?.status === 'running';
        checkButton.classList.toggle('has-result', job?.status === 'ready');
        if (job?.status === 'running') checkButton.textContent = '正在检查…';
        else if (job?.status === 'ready') {
            checkButton.textContent = `查看查重结果（${(job.result?.groups || []).length}组）`;
        } else if (job?.status === 'error') checkButton.textContent = '重新检查';
        else checkButton.textContent = '检查重复';
    }
    elements.reviewRailButtons.forEach(button => {
        const buttonScope = getDuplicateReviewScope(button.dataset.reportType, button.dataset.view);
        const buttonJob = duplicateReviewJobs.get(getDuplicateReviewScopeKey(buttonScope));
        button.classList.toggle('duplicate-check-running', buttonJob?.status === 'running');
        button.classList.toggle('duplicate-check-ready', buttonJob?.status === 'ready');
    });
    const recheckButton = document.getElementById('btn-recheck-duplicates');
    if (recheckButton && duplicateReviewDisplayedScope) {
        const displayedJob = duplicateReviewJobs.get(
            getDuplicateReviewScopeKey(duplicateReviewDisplayedScope)
        );
        recheckButton.disabled = displayedJob?.status === 'running';
        recheckButton.textContent = displayedJob?.status === 'running' ? '正在检查…' : '重新检查';
    }
}

function setDuplicateReviewModalBusy(isBusy) {
    const modal = document.getElementById('duplicate-review-modal');
    if (!modal || !modal.classList.contains('active')) return;
    const content = modal.querySelector('.duplicate-review-modal-content');
    const results = document.getElementById('duplicate-review-results');
    const toolbar = document.getElementById('duplicate-review-toolbar');
    if (content) content.classList.toggle('is-checking', isBusy);
    if (results) results.inert = isBusy;
    if (toolbar) toolbar.inert = isBusy;
    modal.setAttribute('aria-busy', isBusy ? 'true' : 'false');
    ['btn-close-duplicate-review', 'btn-finish-duplicate-review'].forEach(buttonId => {
        const button = document.getElementById(buttonId);
        if (button) button.disabled = isBusy;
    });
}

function notifyDuplicateReviewComplete(scope, result) {
    const groupCount = (result.groups || []).length;
    showToast(`${getDuplicateReviewColumnLabel(scope)}查重完成，发现 ${groupCount} 组重复`);
}
