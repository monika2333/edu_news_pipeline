// Manual Filter JS - Duplicate Review

let duplicateReviewTrigger = null;

function escapeDuplicateHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function getDuplicateReviewColumnLabel() {
    const reportLabel = state.reviewReportType === 'wanbao' ? '晚报' : '综报';
    const decisionLabel = state.reviewView === 'backup' ? '备选' : '采纳';
    return `${reportLabel}${decisionLabel}`;
}

function safeDuplicateUrl(value) {
    const url = String(value || '').trim();
    return /^https?:\/\//i.test(url) ? url : '';
}

function duplicateStatusOptions(item) {
    const currentValue = `${item.report_type || state.reviewReportType}:${item.status || state.reviewView}`;
    const options = [
        ['zongbao:selected', '综报采纳'],
        ['zongbao:backup', '综报备选'],
        ['wanbao:selected', '晚报采纳'],
        ['wanbao:backup', '晚报备选'],
        ['discarded', '放弃'],
        ['pending', '待处理']
    ];
    return options.map(([value, label]) => (
        `<option value="${value}" ${value === currentValue ? 'selected' : ''}>${label}</option>`
    )).join('');
}

function renderDuplicateReviewItem(item) {
    const title = escapeDuplicateHtml(item.title || '(无标题)');
    const source = escapeDuplicateHtml(item.source || '-');
    const summary = escapeDuplicateHtml(item.summary || '（无摘要）');
    const safeUrl = safeDuplicateUrl(item.url);
    const link = safeUrl
        ? `<a href="${escapeDuplicateHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">查看原文</a>`
        : '';
    return `
        <article class="duplicate-review-item" data-id="${escapeDuplicateHtml(item.article_id)}"
            data-status="${escapeDuplicateHtml(item.status)}"
            data-report-type="${escapeDuplicateHtml(item.report_type)}">
            <div class="duplicate-review-item-header">
                <div>
                    <h5>${title}</h5>
                    <div class="duplicate-review-item-meta">来源：${source}${link ? ` · ${link}` : ''}</div>
                </div>
                <select class="status-select duplicate-review-status" aria-label="修改《${title}》的栏目">
                    ${duplicateStatusOptions(item)}
                </select>
            </div>
            <p class="duplicate-review-summary">${summary}</p>
            <div class="duplicate-review-processed" hidden>已处理</div>
        </article>
    `;
}

function renderDuplicateReviewResult(result) {
    const modal = document.getElementById('duplicate-review-modal');
    const meta = document.getElementById('duplicate-review-meta');
    const results = document.getElementById('duplicate-review-results');
    if (!modal || !meta || !results) return;

    const groups = result.groups || [];
    meta.textContent = `${getDuplicateReviewColumnLabel()} · 已检查 ${result.checked_count || 0} 条 · 发现 ${groups.length} 组重复`;
    if (!groups.length) {
        results.innerHTML = `
            <div class="duplicate-review-empty">
                <strong>未发现重复新闻</strong>
                <span>当前栏目中的新闻未被识别为同一事件报道。</span>
            </div>
        `;
    } else {
        results.innerHTML = groups.map((group, index) => `
            <section class="duplicate-review-group" data-group-id="${escapeDuplicateHtml(group.group_id)}">
                <div class="duplicate-review-group-heading">
                    <h4>重复组 ${index + 1}</h4>
                    <span>${group.items.length} 条新闻</span>
                </div>
                <div class="duplicate-review-group-items">
                    ${group.items.map(renderDuplicateReviewItem).join('')}
                </div>
            </section>
        `).join('');
    }
    bindDuplicateReviewStatusControls();
    modal.classList.add('active');
    modal.setAttribute('aria-hidden', 'false');
    const closeButton = document.getElementById('btn-close-duplicate-review');
    if (closeButton) closeButton.focus();
}

async function flushReviewEditsBeforeDuplicateCheck() {
    const view = state.reviewView === 'backup' ? 'backup' : 'selected';
    const items = state.reviewData[view] || [];
    const itemLookup = {};
    items.forEach(item => {
        if (item.article_id) itemLookup[item.article_id] = item;
    });
    const edits = {};
    const scope = getActiveReviewContainer();
    scope.querySelectorAll('.article-card').forEach(card => {
        const articleId = card.dataset.id;
        const item = itemLookup[articleId];
        if (!articleId || !item) return;
        const summaryBox = card.querySelector('.summary-box');
        const sourceBox = card.querySelector('.source-box');
        const summary = summaryBox ? summaryBox.value : (item.summary || '');
        const source = sourceBox ? sourceBox.value : (item.llm_source_display || '');
        const edit = {};
        if (summary !== (item.summary || '')) edit.summary = summary;
        if (source !== (item.llm_source_display || '')) edit.llm_source = source;
        if (Object.keys(edit).length) edits[articleId] = edit;
    });
    if (!Object.keys(edits).length) return;
    const response = await fetch(`${API_BASE}/edit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ edits, actor: state.actor, report_type: state.reviewReportType })
    });
    if (!response.ok) throw new Error('保存当前编辑失败');
    Object.entries(edits).forEach(([articleId, edit]) => {
        applyReviewEditsToState(articleId, edit.summary, edit.llm_source);
    });
}

async function readDuplicateError(response) {
    try {
        const payload = await response.json();
        return payload.detail || 'AI 查重失败，请稍后重试';
    } catch (error) {
        return 'AI 查重失败，请稍后重试';
    }
}

async function handleDuplicateCheck() {
    const button = document.getElementById('btn-check-duplicates');
    if (!button || button.disabled) return;
    duplicateReviewTrigger = button;
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = '正在检查…';
    try {
        await flushReviewEditsBeforeDuplicateCheck();
        const response = await fetch(`${API_BASE}/duplicate-check`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                report_type: state.reviewReportType,
                decision: state.reviewView === 'backup' ? 'backup' : 'selected'
            })
        });
        if (!response.ok) throw new Error(await readDuplicateError(response));
        renderDuplicateReviewResult(await response.json());
    } catch (error) {
        showToast(error.message || 'AI 查重失败，请稍后重试', 'error');
    } finally {
        button.disabled = false;
        button.textContent = originalText;
    }
}

function buildDuplicateDecisionPayload(value, articleId, reportType) {
    const payload = {
        selected_ids: [],
        backup_ids: [],
        discarded_ids: [],
        pending_ids: [],
        actor: state.actor,
        report_type: reportType
    };
    if (value.includes(':')) {
        const [targetReportType, targetStatus] = value.split(':');
        payload.report_type = targetReportType === 'wanbao' ? 'wanbao' : 'zongbao';
        if (targetStatus === 'selected') payload.selected_ids = [articleId];
        if (targetStatus === 'backup') payload.backup_ids = [articleId];
    } else if (value === 'discarded') {
        payload.discarded_ids = [articleId];
    } else if (value === 'pending') {
        payload.pending_ids = [articleId];
    }
    return payload;
}

async function postDuplicateDecision(payload) {
    const response = await fetch(`${API_BASE}/decide`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    if (!response.ok) throw new Error('状态更新失败');
}

async function handleDuplicateStatusChange(event) {
    const select = event.target;
    const item = select.closest('.duplicate-review-item');
    if (!item) return;
    const articleId = item.dataset.id;
    const previousStatus = item.dataset.status;
    const previousReportType = item.dataset.reportType || state.reviewReportType;
    const previousValue = `${previousReportType}:${previousStatus}`;
    if (select.value === previousValue) return;

    select.disabled = true;
    try {
        await postDuplicateDecision(buildDuplicateDecisionPayload(select.value, articleId, previousReportType));
        item.classList.add('is-processed');
        const processed = item.querySelector('.duplicate-review-processed');
        if (processed) processed.hidden = false;
        await loadReviewData();
        loadStats();
        const undoAction = buildUndoToastAction(async () => {
            try {
                await postDuplicateDecision(buildDuplicateDecisionPayload(previousValue, articleId, previousReportType));
                item.classList.remove('is-processed');
                if (processed) processed.hidden = true;
                select.value = previousValue;
                select.disabled = false;
                await loadReviewData();
                loadStats();
                showToast('已撤销操作');
            } catch (error) {
                showToast('撤销失败', 'error');
            }
        });
        showToast('状态已更新', 'success', undoAction);
    } catch (error) {
        select.value = previousValue;
        select.disabled = false;
        showToast(error.message || '状态更新失败', 'error');
    }
}

function bindDuplicateReviewStatusControls() {
    document.querySelectorAll('.duplicate-review-status').forEach(select => {
        select.addEventListener('change', handleDuplicateStatusChange);
    });
}

function closeDuplicateReviewModal() {
    const modal = document.getElementById('duplicate-review-modal');
    if (!modal) return;
    modal.classList.remove('active');
    modal.setAttribute('aria-hidden', 'true');
    if (duplicateReviewTrigger) duplicateReviewTrigger.focus();
}

async function finishDuplicateReview() {
    closeDuplicateReviewModal();
    await loadReviewData();
    loadStats();
}

function setupDuplicateReview() {
    const checkButton = document.getElementById('btn-check-duplicates');
    const closeButton = document.getElementById('btn-close-duplicate-review');
    const finishButton = document.getElementById('btn-finish-duplicate-review');
    const recheckButton = document.getElementById('btn-recheck-duplicates');
    if (checkButton) checkButton.addEventListener('click', handleDuplicateCheck);
    if (closeButton) closeButton.addEventListener('click', closeDuplicateReviewModal);
    if (finishButton) finishButton.addEventListener('click', finishDuplicateReview);
    if (recheckButton) recheckButton.addEventListener('click', handleDuplicateCheck);
    document.addEventListener('keydown', event => {
        const modal = document.getElementById('duplicate-review-modal');
        if (event.key === 'Escape' && modal && modal.classList.contains('active')) {
            closeDuplicateReviewModal();
        }
    });
}
