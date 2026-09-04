// Submission Archive JS - Prior Matches Modal (反馈条目已报送命中明细)
//
// 反馈条目卡片上的「已报送 / 疑似已报送 / 未报送(dismissed)」标签点开本弹窗，展示
// 该条目命中的全部更早综报/晚报条目（GET /items/{id}/prior-matches）。「未报送」
// 有两种：dismissed（人工判定不是同一条）可点击、有明细可看；无命中的是纯展示
// span、不可点击（委托只匹配 button），也没有明细可看。标签只在反馈报告上渲染，
// 其他报别不会触发本弹窗。弹窗底部是人工判定入口：decidable 的条目未判时给
// 「不是同一条 / 确认已报送」，已判时给说明文字 +「撤销判断」
// （POST /items/{id}/prior-match-decision）；成功后不关闭弹窗，就地重渲染底部，
// 并经 browser.js 的 applyPriorMatchDecisionResult 局部更新背后的卡片。
// 本脚本必须先于 content_drawer.js 加载：原文抽屉盖在弹窗上时 Escape 先关抽屉，
// 依赖这里的 keydown 处理器先注册并主动跳过。

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
        results: document.getElementById('archive-prior-matches-results'),
        footer: document.getElementById('archive-prior-matches-footer')
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

// 弹窗底部按 prior_match 渲染判定入口：decidable === false（确定性命中）整个隐藏，
// 后端也会拒绝（422）；未判给「不是同一条 / 确认已报送」（沿用回链队列的叫法），
// 已判给说明文字 +「撤销判断」
function renderPriorMatchesFooter(item) {
    const { footer } = getPriorMatchesEls();
    if (!footer) return;
    const priorMatch = item ? item.prior_match : null;
    if (!priorMatch || priorMatch.decidable === false) {
        footer.hidden = true;
        footer.innerHTML = '';
        return;
    }
    footer.hidden = false;
    if (priorMatch.decision === 'submitted') {
        footer.innerHTML = '<span class="archive-prior-matches-decision-note">已人工确认为已报送</span>'
            + '<button class="btn btn-secondary" id="archive-prior-matches-undo" type="button">撤销判断</button>';
    } else if (priorMatch.decision === 'not_submitted') {
        footer.innerHTML = '<span class="archive-prior-matches-decision-note">已人工判定为未报送</span>'
            + '<button class="btn btn-secondary" id="archive-prior-matches-undo" type="button">撤销判断</button>';
    } else {
        footer.innerHTML = '<button class="btn btn-secondary" id="archive-prior-matches-reject" type="button">不是同一条</button>'
            + '<button class="btn btn-primary" id="archive-prior-matches-confirm" type="button">确认已报送</button>';
    }
}

// 提交人工判定：提交期间禁用按钮；成功后不关闭弹窗，就地重渲染底部并同步背后卡片；
// 失败重渲染底部恢复按钮并报错，卡片不变
async function submitPriorMatchDecision(decision) {
    const { footer } = getPriorMatchesEls();
    const itemId = priorMatchesState.itemId;
    const item = activeReportItems.find(entry => String(entry.id) === String(itemId));
    if (!footer || !item) return;
    footer.querySelectorAll('button').forEach(button => { button.disabled = true; });
    try {
        const data = await api(`/items/${encodeURIComponent(itemId)}/prior-match-decision`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ decision })
        });
        if (priorMatchesState.itemId !== itemId) return;
        applyPriorMatchDecisionResult(itemId, data.prior_match);
        const updated = activeReportItems.find(entry => String(entry.id) === String(itemId));
        renderPriorMatchesFooter(updated);
        toast(decision === 'submitted' ? '已确认为已报送'
            : decision === 'not_submitted' ? '已判定为未报送' : '已撤销判断');
    } catch (error) {
        if (priorMatchesState.itemId !== itemId) return;
        renderPriorMatchesFooter(item);
        toast(error.message, 'error');
    }
}

async function openPriorMatchesModal(itemId) {
    const els = getPriorMatchesEls();
    const item = activeReportItems.find(entry => String(entry.id) === String(itemId));
    // 兜底：标签只出现在反馈报告上（已报送/疑似/dismissed 三种可点击），其他报别不渲染也就点不到
    if (!els.modal || !item) return;
    priorMatchesState.open = true;
    priorMatchesState.itemId = item.id;
    els.refTitle.textContent = item.title || '(无标题)';
    els.refBody.textContent = item.body || '';
    els.results.innerHTML = '<div class="archive-empty">正在加载…</div>';
    renderPriorMatchesFooter(item);
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
    if (els.footer) {
        els.footer.hidden = true;
        els.footer.innerHTML = '';
    }
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
    // 底部按钮随判定结果重渲染（innerHTML 替换），必须事件委托
    els.footer?.addEventListener('click', event => {
        if (event.target.closest('#archive-prior-matches-confirm')) {
            submitPriorMatchDecision('submitted');
        } else if (event.target.closest('#archive-prior-matches-reject')) {
            submitPriorMatchDecision('not_submitted');
        } else if (event.target.closest('#archive-prior-matches-undo')) {
            submitPriorMatchDecision(null);
        }
    });
    // 标签会被轮询的局部更新替换，必须用事件委托；无命中的「未报送」是 span，天然点不进这里
    document.getElementById('archive-detail')?.addEventListener('click', event => {
        const pill = event.target.closest('button.archive-prior-match-pill');
        if (pill) openPriorMatchesModal(pill.dataset.itemId);
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupPriorMatchesModal, { once: true });
} else {
    setupPriorMatchesModal();
}
