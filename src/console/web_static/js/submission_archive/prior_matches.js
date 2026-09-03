// Submission Archive JS - Prior Matches Modal (反馈条目已报送命中明细)
//
// 反馈条目卡片上的「已报送 / 疑似已报送」标签点开本弹窗，展示该条目命中的
// 全部更早综报/晚报条目（GET /items/{id}/prior-matches）。标签只在反馈报告且
// 命中时渲染，其他报别不会触发本弹窗。本脚本必须先于 content_drawer.js 加载：
// 原文抽屉盖在弹窗上时 Escape 先关抽屉，依赖这里的 keydown 处理器先注册并主动跳过。

// 判定依据的中文说明，与后端 match_method 取值一一对应
const priorMatchMethodLabels = {
    article: '同一篇原文',
    title_hash: '标题一致',
    vector: '语义相似'
};

const priorMatchesState = { open: false, itemId: '' };

function getPriorMatchesEls() {
    return {
        modal: document.getElementById('archive-prior-matches-modal'),
        closeBtn: document.getElementById('archive-prior-matches-modal-close'),
        refTitle: document.getElementById('archive-prior-matches-ref-title'),
        refBody: document.getElementById('archive-prior-matches-ref-body'),
        results: document.getElementById('archive-prior-matches-results')
    };
}

function priorMatchEntryHtml(entry) {
    const method = priorMatchMethodLabels[entry.match_method]
        || entry.match_method || '未知';
    return `
        <section class="archive-prior-match-entry">
            <div class="archive-prior-match-entry-head">
                ${typePill(entry.report_type)}
                <span>${escapeHtml(dateValue(entry.report_date))}</span>
                ${entry.issue_no ? `<span>${escapeHtml(entry.issue_no)}</span>` : ''}
                <span class="archive-prior-match-entry-similarity">相似度 ${escapeHtml(scoreValue(entry.similarity))}</span>
                <span class="archive-prior-match-entry-method">${escapeHtml(method)}</span>
            </div>
            <h4 class="archive-prior-match-entry-title">${escapeHtml(entry.title || '(无标题)')}</h4>
            ${entry.body ? `<p class="archive-prior-match-entry-body">${escapeHtml(entry.body)}</p>` : ''}
            ${entry.source ? `<div class="archive-prior-match-entry-source">来源：${escapeHtml(entry.source)}</div>` : ''}
        </section>
    `;
}

async function openPriorMatchesModal(itemId) {
    const els = getPriorMatchesEls();
    const item = activeReportItems.find(entry => String(entry.id) === String(itemId));
    // 兜底：只有 item.prior_match 非空（反馈报告且命中）的条目才允许打开
    if (!els.modal || !item || !item.prior_match) return;
    priorMatchesState.open = true;
    priorMatchesState.itemId = item.id;
    els.refTitle.textContent = item.title || '(无标题)';
    els.refBody.textContent = item.body || '';
    els.results.innerHTML = '<div class="archive-empty">正在加载…</div>';
    els.modal.classList.add('active');
    els.modal.setAttribute('aria-hidden', 'false');
    try {
        const data = await api(`/items/${encodeURIComponent(itemId)}/prior-matches`);
        if (priorMatchesState.itemId !== itemId) return;
        const matches = data.matches || [];
        els.results.innerHTML = matches.length
            ? matches.map(priorMatchEntryHtml).join('')
            : '<div class="archive-empty">没有命中明细。</div>';
    } catch (error) {
        if (priorMatchesState.itemId !== itemId) return;
        els.results.innerHTML = `<div class="archive-empty">${escapeHtml(error.message)}</div>`;
    }
}

function closePriorMatchesModal() {
    const els = getPriorMatchesEls();
    if (!els.modal) return;
    priorMatchesState.open = false;
    priorMatchesState.itemId = '';
    els.modal.classList.remove('active');
    els.modal.setAttribute('aria-hidden', 'true');
}

function setupPriorMatchesModal() {
    const els = getPriorMatchesEls();
    if (!els.modal) return;
    els.closeBtn.addEventListener('click', closePriorMatchesModal);
    els.modal.addEventListener('click', event => {
        if (event.target === els.modal) closePriorMatchesModal();
    });
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || !priorMatchesState.open) return;
        // 原文抽屉盖在弹窗上时 Escape 先关抽屉：本脚本先于 content_drawer.js 注册，
        // 这里直接跳过，抽屉的处理器随后执行并关闭抽屉
        if (archiveDrawerState.open) return;
        closePriorMatchesModal();
    });
    // 标签会被轮询的局部更新替换，必须用事件委托
    document.getElementById('archive-detail')?.addEventListener('click', event => {
        const pill = event.target.closest('.archive-prior-match-pill');
        if (pill) openPriorMatchesModal(pill.dataset.itemId);
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupPriorMatchesModal, { once: true });
} else {
    setupPriorMatchesModal();
}
