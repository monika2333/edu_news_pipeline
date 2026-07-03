// Manual Filter JS - Duplicate Review Modal

let duplicateReviewActiveGroupIndex = 0;

function escapeDuplicateHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
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
    const summary = escapeDuplicateHtml(item.summary || '');
    const summaryCount = formatReviewSummaryCount(countReviewSummaryChars(item.summary));
    const score = formatScore(item.score);
    const bonusText = (item.bonus_keywords || []).join(', ');
    const safeUrl = safeDuplicateUrl(item.url);
    const link = safeUrl
        ? `<a href="${escapeDuplicateHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">🔗</a>`
        : '';
    return `
        <article class="article-card duplicate-review-item" data-id="${escapeDuplicateHtml(item.article_id)}"
            data-status="${escapeDuplicateHtml(item.status)}"
            data-report-type="${escapeDuplicateHtml(item.report_type)}">
            <div class="duplicate-review-item-header">
                <label class="review-select-wrap" title="选择">
                    <input type="checkbox" class="duplicate-review-select" aria-label="选择《${title}》">
                </label>
                <h5>${title} ${link}</h5>
                <div class="review-card-actions">
                    <button type="button" class="review-discard-btn duplicate-review-discard"
                        title="放弃新闻" aria-label="放弃《${title}》">🗑️</button>
                    <select class="status-select duplicate-review-status" aria-label="修改《${title}》的栏目">
                        ${duplicateStatusOptions(item)}
                    </select>
                </div>
            </div>
            <div class="meta-row duplicate-review-item-meta">
                <div class="meta-item">来源：${source}</div>
                <div class="meta-item">分数：${escapeDuplicateHtml(score)}</div>
                ${bonusText ? `<div class="meta-item">Bonus：${escapeDuplicateHtml(bonusText)}</div>` : ''}
            </div>
            <div class="review-summary-wrap">
                <textarea class="summary-box duplicate-review-summary-box"
                    data-id="${escapeDuplicateHtml(item.article_id)}">${summary}</textarea>
                <span class="review-summary-count" title="摘要非空白字符数">${summaryCount}字</span>
            </div>
            <input class="source-box duplicate-review-source" data-id="${escapeDuplicateHtml(item.article_id)}"
                value="${source}" placeholder="新闻来源">
            <div class="duplicate-review-processed" hidden>已处理</div>
        </article>
    `;
}

function reconcileDuplicateReviewResult(result, scope) {
    if (!isDuplicateReviewScopeActive(scope)) return result;
    const currentItems = state.reviewData[scope.decision] || [];
    const itemLookup = new Map(
        currentItems.filter(item => item.article_id).map(item => [item.article_id, item])
    );
    const checkedIds = new Set(result.checked_article_ids || []);
    const groups = (result.groups || []).map(group => ({
        ...group,
        items: (group.items || []).filter(item => itemLookup.has(item.article_id)).map(item => {
            const current = itemLookup.get(item.article_id);
            return {
                ...item,
                title: current.title || item.title,
                summary: current.summary || '',
                source: current.llm_source_display || current.source || '',
                url: current.url || item.url,
                status: current.manual_status || current.status || scope.decision,
                report_type: current.report_type || scope.reportType,
                score: current.external_importance_score ?? current.score ?? item.score,
                bonus_keywords: current.bonus_keywords || item.bonus_keywords || []
            };
        })
    })).filter(group => group.items.length >= 2);
    const currentIds = new Set(itemLookup.keys());
    return {
        ...result,
        current_count: currentItems.length,
        added_count: Array.from(currentIds).filter(articleId => !checkedIds.has(articleId)).length,
        removed_count: Array.from(checkedIds).filter(articleId => !currentIds.has(articleId)).length,
        groups
    };
}

function renderDuplicateReviewResult(rawResult, scope = getDuplicateReviewScope()) {
    const modal = document.getElementById('duplicate-review-modal');
    const meta = document.getElementById('duplicate-review-meta');
    const results = document.getElementById('duplicate-review-results');
    const toolbar = document.getElementById('duplicate-review-toolbar');
    if (!modal || !meta || !results || !toolbar) return;

    const result = reconcileDuplicateReviewResult(rawResult, scope);
    const groups = result.groups || [];
    duplicateReviewDisplayedScope = scope;
    toolbar.hidden = !groups.length;
    const addedText = result.added_count
        ? ` · ${result.added_count} 条新增新闻未参与本次检查`
        : '';
    meta.textContent = `${getDuplicateReviewColumnLabel(scope)} · 已检查 ${result.checked_count || 0} 条 · 发现 ${groups.length} 组重复${addedText}`;
    if (!groups.length) {
        results.innerHTML = `
            <div class="duplicate-review-empty">
                <strong>未发现重复新闻</strong>
                <span>当前栏目中的新闻未被识别为同一事件报道。</span>
            </div>
        `;
    } else {
        results.innerHTML = groups.map((group, index) => `
            <section class="duplicate-review-group" data-group-id="${escapeDuplicateHtml(group.group_id)}"
                data-group-index="${index}" ${index === 0 ? '' : 'hidden'}>
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
    duplicateReviewActiveGroupIndex = 0;
    bindDuplicateReviewStatusControls();
    showDuplicateReviewGroup(0);
    modal.classList.add('active');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('duplicate-review-open');
    const closeButton = document.getElementById('btn-close-duplicate-review');
    if (closeButton) closeButton.focus();
}

function getDuplicateReviewGroups() {
    return Array.from(document.querySelectorAll('.duplicate-review-group'));
}

function getActiveDuplicateReviewGroup() {
    const groups = getDuplicateReviewGroups();
    return groups[duplicateReviewActiveGroupIndex] || null;
}

function showDuplicateReviewGroup(index) {
    const groups = getDuplicateReviewGroups();
    const pager = document.getElementById('duplicate-review-pager');
    const indicator = document.getElementById('duplicate-review-page-indicator');
    const previousButton = document.getElementById('btn-duplicate-prev-group');
    const nextButton = document.getElementById('btn-duplicate-next-group');
    if (!groups.length) {
        if (pager) pager.hidden = true;
        return;
    }

    duplicateReviewActiveGroupIndex = Math.max(0, Math.min(index, groups.length - 1));
    groups.forEach((group, groupIndex) => {
        group.hidden = groupIndex !== duplicateReviewActiveGroupIndex;
    });
    if (pager) pager.hidden = groups.length <= 1;
    if (indicator) indicator.textContent = `第 ${duplicateReviewActiveGroupIndex + 1} / ${groups.length} 组`;
    if (previousButton) previousButton.disabled = duplicateReviewActiveGroupIndex === 0;
    if (nextButton) nextButton.disabled = duplicateReviewActiveGroupIndex === groups.length - 1;

    const activeGroup = getActiveDuplicateReviewGroup();
    if (activeGroup) {
        activeGroup.querySelectorAll('.duplicate-review-summary-box').forEach(box => {
            refreshReviewSummaryBox(box);
        });
    }
    const results = document.getElementById('duplicate-review-results');
    if (results) results.scrollTop = 0;
    updateDuplicateReviewSelectionState();
}

function moveDuplicateReviewGroup(offset) {
    showDuplicateReviewGroup(duplicateReviewActiveGroupIndex + offset);
}

function closeDuplicateReviewModal() {
    const modal = document.getElementById('duplicate-review-modal');
    if (!modal) return;
    modal.classList.remove('active');
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('duplicate-review-open');
    if (duplicateReviewTrigger) duplicateReviewTrigger.focus();
}
