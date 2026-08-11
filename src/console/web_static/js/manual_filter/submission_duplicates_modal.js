// Manual Filter JS - Submission Duplicates Modal

// 页面会话内缓存：article_id -> matches 数组，与 content_drawer 的正文缓存思路一致。
const submissionDuplicatesCache = new Map();
let submissionDuplicatesActiveArticleId = '';
let submissionDuplicatesTrigger = null;

const SUBMISSION_DUPLICATE_REPORT_LABELS = {
    zongbao: '综报',
    wanbao: '晚报',
    feedback: '反馈'
};

function getSubmissionDuplicatesModal() {
    return document.getElementById('submission-duplicates-modal');
}

function renderSubmissionDuplicateMatch(match) {
    const reportLabel = SUBMISSION_DUPLICATE_REPORT_LABELS[match.report_type]
        || match.report_type
        || '存档';
    const score = Number(match.similarity);
    const scoreText = Number.isFinite(score) ? ` · 相似度 ${score.toFixed(2)}` : '';
    const stateText = match.state === 'confirmed' ? '已确认' : '疑似';
    const body = String(match.body || '').trim();
    return `
        <section class="submission-duplicate-item">
            <div class="submission-duplicate-item-meta">${safeHtml(match.report_date || '')} ${safeHtml(reportLabel)}${scoreText} · ${stateText}</div>
            <h4 class="submission-duplicate-item-title">${safeHtml(match.title || '(无标题)')}</h4>
            <div class="submission-duplicate-item-body">${body ? safeHtml(body) : '（该条目没有正文）'}</div>
        </section>
    `;
}

async function loadSubmissionDuplicateDetails(articleId) {
    if (submissionDuplicatesCache.has(articleId)) {
        return submissionDuplicatesCache.get(articleId);
    }
    const response = await window.fetch(
        `/api/submission-archive/duplicates/${encodeURIComponent(articleId)}`
    );
    if (!response.ok) throw new Error('fetch duplicate details failed');
    const payload = await response.json();
    const matches = Array.isArray(payload.matches) ? payload.matches : [];
    submissionDuplicatesCache.set(articleId, matches);
    return matches;
}

function openSubmissionDuplicatesModal(articleId, duplicateState, trigger) {
    const modal = getSubmissionDuplicatesModal();
    const body = document.getElementById('submission-duplicates-body');
    const dismissButton = document.getElementById('btn-submission-duplicates-dismiss');
    const footer = document.getElementById('submission-duplicates-footer');
    if (!modal || !body) return;
    submissionDuplicatesActiveArticleId = articleId;
    submissionDuplicatesTrigger = trigger || null;
    if (dismissButton) dismissButton.disabled = false;
    if (footer) {
        // dismiss 只作用于 suspected 记录；confirmed 标签只读展示，整个页脚隐藏。
        footer.hidden = duplicateState !== 'suspected';
    }
    body.innerHTML = '<div class="empty-state">加载中…</div>';
    modal.classList.add('active');
    modal.setAttribute('aria-hidden', 'false');
    loadSubmissionDuplicateDetails(articleId).then(matches => {
        if (submissionDuplicatesActiveArticleId !== articleId) return;
        body.innerHTML = matches.length
            ? matches.map(renderSubmissionDuplicateMatch).join('')
            : '<div class="empty-state">没有找到重复记录。</div>';
    }).catch(() => {
        if (submissionDuplicatesActiveArticleId !== articleId) return;
        body.innerHTML = '<div class="error">加载重复记录失败，请稍后重试。</div>';
    });
}

function closeSubmissionDuplicatesModal() {
    const modal = getSubmissionDuplicatesModal();
    if (!modal) return;
    modal.classList.remove('active');
    modal.setAttribute('aria-hidden', 'true');
    submissionDuplicatesActiveArticleId = '';
    if (submissionDuplicatesTrigger) submissionDuplicatesTrigger.focus();
}

async function dismissSubmissionDuplicatesFromModal() {
    const articleId = submissionDuplicatesActiveArticleId;
    if (!articleId) return;
    const dismissButton = document.getElementById('btn-submission-duplicates-dismiss');
    if (dismissButton) dismissButton.disabled = true;
    try {
        const response = await window.fetch(
            `/api/submission-archive/duplicates/${encodeURIComponent(articleId)}/dismiss`,
            { method: 'POST' }
        );
        if (!response.ok) throw new Error('dismiss failed');
        submissionDuplicatesCache.delete(articleId);
        document.querySelectorAll('.article-card[data-id]').forEach(card => {
            if (card.dataset.id !== articleId) return;
            card.querySelectorAll('.submission-duplicate-wrap').forEach(wrap => wrap.remove());
        });
        closeSubmissionDuplicatesModal();
        showToast('已移除重复标记');
    } catch (error) {
        if (dismissButton) dismissButton.disabled = false;
        showToast('移除重复标记失败', 'error');
    }
}

function setupSubmissionDuplicatesModal() {
    const modal = getSubmissionDuplicatesModal();
    if (!modal) return;
    document.getElementById('btn-submission-duplicates-close')
        ?.addEventListener('click', closeSubmissionDuplicatesModal);
    document.getElementById('btn-submission-duplicates-dismiss')
        ?.addEventListener('click', dismissSubmissionDuplicatesFromModal);
    modal.addEventListener('click', event => {
        if (event.target === modal) closeSubmissionDuplicatesModal();
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && modal.classList.contains('active')) {
            closeSubmissionDuplicatesModal();
        }
    });
    if (elements.filterList) {
        elements.filterList.addEventListener('click', event => {
            const badge = event.target.closest('.submission-duplicate-badge');
            if (!badge) return;
            const articleId = badge.dataset.articleId;
            if (!articleId) return;
            openSubmissionDuplicatesModal(
                articleId,
                badge.dataset.duplicateState || 'suspected',
                badge
            );
        });
    }
}
