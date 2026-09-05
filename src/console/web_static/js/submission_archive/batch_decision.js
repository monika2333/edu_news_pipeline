// Submission Archive JS - Batch Decision Modals（详情页当期批量处理）
//
// 详情标题栏的两个批量入口（browser.js 的 detailHeadActionsHtml 渲染到
// #archive-detail-actions，按钮带 data-batch-action）：
// - 当期回链确认：拉取 GET /link-queue?report_id=<当前报告>，对照卡由本文件的
//   linkCard 渲染（原 link_queue.js，随回链确认队列页下线并入）；事件处理在
//   本弹窗容器上自行委托。提交仍走 POST /items/{id}/link-decision，成功后经
//   browser.js 的 applyManualLinkResult 局部同步背后的详情卡片，不重拉报告；
//   关闭时 loadNavPending() 刷新顶部的全局待确认提示。
// - 当期疑似已报送判定（仅反馈报告且判定结束后入口才出现）：条目取自
//   activeReportItems（prior_match.status === 'suspected'），命中明细逐条并发
//   拉取 GET /items/{id}/prior-matches，单条失败只在该卡片内提示；命中明细复用
//   prior_matches.js 的 priorMatchEntryHtml 渲染（判定依据文案的三种取值由它覆盖）。
//   提交走 POST /items/{id}/prior-match-decision，成功后经
//   applyPriorMatchDecisionResult 局部同步。批量入口只处理未判定条目，不提供撤销——
//   撤销仍走条目标签点开的单条明细弹窗（prior_matches.js）。
// 两个弹窗全部处理完后显示空态文案，不自动关闭。
// Escape 约定与现有弹窗一致：原文抽屉开着时先关抽屉。本脚本按模板顺序在
// content_drawer.js 之后加载，keydown 必须用捕获阶段注册，才能抢在抽屉的
// 冒泡处理器之前读到「抽屉仍开着」的状态并跳过。

const batchLinkState = { open: false, remaining: 0 };
const batchPriorState = { open: false, remaining: 0 };

function getBatchLinkEls() {
    return {
        modal: document.getElementById('archive-batch-link-modal'),
        closeBtn: document.getElementById('archive-batch-link-modal-close'),
        count: document.getElementById('archive-batch-link-count'),
        list: document.getElementById('archive-batch-link-list')
    };
}

function getBatchPriorEls() {
    return {
        modal: document.getElementById('archive-batch-prior-modal'),
        closeBtn: document.getElementById('archive-batch-prior-modal-close'),
        count: document.getElementById('archive-batch-prior-count'),
        list: document.getElementById('archive-batch-prior-list')
    };
}

function renderBatchLinkCount() {
    const { count } = getBatchLinkEls();
    if (count) {
        count.textContent = batchLinkState.remaining > 0 ? `剩余 ${batchLinkState.remaining} 条` : '';
    }
}

function renderBatchPriorCount() {
    const { count } = getBatchPriorEls();
    if (count) {
        count.textContent = batchPriorState.remaining > 0 ? `剩余 ${batchPriorState.remaining} 条` : '';
    }
}

/* ===== 当期回链确认 ===== */

// 对照卡：左栏存档条目，右栏系统最佳候选，底部相似度条与操作按钮
// （自 link_queue.js 并入，样式在 batch_decision.css 的 archive-link-* 段）
function linkCard(item) {
    const combined = Number(item.link_combined_score);
    const percent = Number.isFinite(combined) ? Math.round(combined * 100) : 0;
    return `
        <article class="archive-link-card" data-item-id="${item.id}">
            <div class="archive-link-grid">
                <section class="archive-link-col">
                    <p class="archive-link-col-label">
                        存档条目 ${typePill(item.report_type)} <span>${shortDate(item.report_date)}</span>
                    </p>
                    <h3>${escapeHtml(item.title)}</h3>
                    <p class="archive-link-body">${escapeHtml(item.body || '')}</p>
                    <footer><span>来源：${escapeHtml(item.source || '-')}</span></footer>
                </section>
                <section class="archive-link-col is-candidate">
                    <p class="archive-link-col-label">系统最佳候选</p>
                    <h3>${escapeHtml(item.candidate_title || '候选已不存在')}</h3>
                    <p class="archive-link-body">${escapeHtml(item.candidate_body || '')}</p>
                    <footer>
                        <span>来源：${escapeHtml(item.candidate_source || '-')}</span>
                        ${item.candidate_url ? `<a href="${escapeHtml(item.candidate_url)}" target="_blank" rel="noopener noreferrer">打开原文</a>` : ''}
                    </footer>
                </section>
            </div>
            <div class="archive-link-score">
                <div class="archive-score-bar" role="img" aria-label="综合相似度 ${scoreValue(item.link_combined_score)}">
                    <span style="width: ${percent}%"></span>
                </div>
                <div class="archive-score-nums">
                    <span>综合 <strong>${scoreValue(item.link_combined_score)}</strong></span>
                    <span>标题 <strong>${scoreValue(item.link_title_score)}</strong></span>
                    <span>正文 <strong>${scoreValue(item.link_body_score)}</strong></span>
                </div>
            </div>
            <div class="archive-link-actions">
                <button class="btn btn-secondary archive-link-reject" type="button">不是同一条</button>
                <button class="btn btn-primary archive-link-accept" type="button">确认绑定</button>
            </div>
        </article>
    `;
}

async function openBatchLinkModal() {
    const els = getBatchLinkEls();
    if (!els.modal || !activeReportId) return;
    batchLinkState.open = true;
    batchLinkState.remaining = 0;
    els.count.textContent = '';
    els.list.innerHTML = '<div class="archive-empty">正在加载…</div>';
    els.modal.classList.add('active');
    els.modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('archive-batch-modal-open');
    try {
        const data = await api(
            `/link-queue?report_id=${encodeURIComponent(activeReportId)}&limit=100`
        );
        if (!batchLinkState.open) return;
        batchLinkState.remaining = Number(data.total) || 0;
        renderBatchLinkCount();
        const items = data.items || [];
        els.list.innerHTML = items.length
            ? items.map(linkCard).join('')
            : '<div class="archive-empty">当期报告没有待确认的回链条目。</div>';
    } catch (error) {
        if (!batchLinkState.open) return;
        els.list.innerHTML = `<div class="archive-empty">${escapeHtml(error.message)}</div>`;
    }
}

function closeBatchLinkModal() {
    const els = getBatchLinkEls();
    if (!els.modal) return;
    batchLinkState.open = false;
    els.modal.classList.remove('active');
    els.modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('archive-batch-modal-open');
    // 弹窗里的确认会改变全局待确认数量，关闭时刷新顶部待确认提示
    loadNavPending();
}

async function submitBatchLinkDecision(button) {
    const card = button.closest('.archive-link-card');
    if (!card) return;
    const accepted = button.classList.contains('archive-link-accept');
    // 提交期间禁用该卡片的两个按钮，防止重复提交
    card.querySelectorAll('.archive-link-accept, .archive-link-reject')
        .forEach(btn => { btn.disabled = true; });
    try {
        const updated = await api(`/items/${encodeURIComponent(card.dataset.itemId)}/link-decision`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ accepted })
        });
        // 接口返回的字段与手动回链一致，复用同一条局部更新路径同步背后的详情卡片
        applyManualLinkResult(updated);
        card.remove();
        batchLinkState.remaining = Math.max(batchLinkState.remaining - 1, 0);
        renderBatchLinkCount();
        const { list } = getBatchLinkEls();
        if (list && !list.querySelector('.archive-link-card')) {
            list.innerHTML = '<div class="archive-empty">当期报告的待确认回链已全部处理完。</div>';
        }
        toast(accepted ? '已确认绑定' : '已标记为不是同一条');
    } catch (error) {
        card.querySelectorAll('.archive-link-accept, .archive-link-reject')
            .forEach(btn => { btn.disabled = false; });
        toast(error.message, 'error');
    }
}

/* ===== 当期疑似已报送判定 ===== */

// 对照卡与回链确认卡保持同一视觉语言（batch_decision.css 的 archive-link-card /
// archive-link-grid / archive-link-col）：左栏当前反馈条目，右栏命中的更早报送
function batchPriorCardHtml(item) {
    return `
        <article class="archive-link-card archive-batch-prior-card" data-item-id="${escapeHtml(item.id)}">
            <div class="archive-link-grid">
                <section class="archive-link-col">
                    <p class="archive-link-col-label">当前反馈条目</p>
                    <h3>${escapeHtml(item.title)}</h3>
                    <p class="archive-link-body">${escapeHtml(item.body || '')}</p>
                    <footer><span>来源：${escapeHtml(item.source || '-')}</span></footer>
                </section>
                <section class="archive-link-col is-candidate">
                    <p class="archive-link-col-label">命中的更早报送</p>
                    <div class="archive-batch-prior-matches">
                        <div class="archive-empty">正在加载…</div>
                    </div>
                </section>
            </div>
            <div class="archive-link-actions">
                <button class="btn btn-secondary archive-batch-prior-reject" type="button">不是同一条</button>
                <button class="btn btn-primary archive-batch-prior-confirm" type="button">确认已报送</button>
            </div>
        </article>
    `;
}

// 单条命中明细独立拉取：失败只在该卡片内显示错误，不影响其他卡片
async function loadBatchPriorMatches(itemId) {
    const { list } = getBatchPriorEls();
    const card = list?.querySelector(
        `.archive-batch-prior-card[data-item-id="${CSS.escape(String(itemId))}"]`
    );
    const target = card?.querySelector('.archive-batch-prior-matches');
    if (!target) return;
    try {
        const data = await api(`/items/${encodeURIComponent(itemId)}/prior-matches`);
        const matches = data.matches || [];
        target.innerHTML = matches.length
            ? matches.map(priorMatchEntryHtml).join('')
            : '<div class="archive-empty">没有命中明细。</div>';
    } catch (error) {
        target.innerHTML = `<div class="archive-empty">命中明细加载失败：${escapeHtml(error.message)}</div>`;
    }
}

function openBatchPriorModal() {
    const els = getBatchPriorEls();
    if (!els.modal) return;
    // 条目清单直接取自当前详情缓存，不额外请求；suspected 即「可判定且尚未判定」
    const items = activeReportItems.filter(
        item => item.prior_match && item.prior_match.status === 'suspected'
    );
    if (!items.length) return;
    batchPriorState.open = true;
    batchPriorState.remaining = items.length;
    renderBatchPriorCount();
    els.list.innerHTML = items.map(batchPriorCardHtml).join('');
    els.modal.classList.add('active');
    els.modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('archive-batch-modal-open');
    // 并发拉取全部条目的命中明细，逐条填充
    items.forEach(item => { loadBatchPriorMatches(item.id); });
}

function closeBatchPriorModal() {
    const els = getBatchPriorEls();
    if (!els.modal) return;
    batchPriorState.open = false;
    els.modal.classList.remove('active');
    els.modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('archive-batch-modal-open');
}

// 与 prior_matches.js 的 submitPriorMatchDecision 相互独立：那个函数与单条弹窗的
// 状态（priorMatchesState、footer 重渲染）耦合，批量弹窗另写一份，不改单条弹窗
async function submitBatchPriorDecision(button, decision) {
    const card = button.closest('.archive-batch-prior-card');
    if (!card) return;
    const itemId = card.dataset.itemId;
    card.querySelectorAll('.archive-batch-prior-confirm, .archive-batch-prior-reject')
        .forEach(btn => { btn.disabled = true; });
    try {
        const data = await api(`/items/${encodeURIComponent(itemId)}/prior-match-decision`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ decision })
        });
        applyPriorMatchDecisionResult(itemId, data.prior_match);
        card.remove();
        batchPriorState.remaining = Math.max(batchPriorState.remaining - 1, 0);
        renderBatchPriorCount();
        const { list } = getBatchPriorEls();
        if (list && !list.querySelector('.archive-batch-prior-card')) {
            list.innerHTML = '<div class="archive-empty">当期报告的疑似已报送条目已全部判定完。</div>';
        }
        toast(decision === 'submitted' ? '已确认为已报送' : '已判定为未报送');
    } catch (error) {
        card.querySelectorAll('.archive-batch-prior-confirm, .archive-batch-prior-reject')
            .forEach(btn => { btn.disabled = false; });
        toast(error.message, 'error');
    }
}

function setupBatchDecisionModals() {
    const linkEls = getBatchLinkEls();
    const priorEls = getBatchPriorEls();
    if (!linkEls.modal || !priorEls.modal) return;
    // 标题栏按钮随轮询/局部更新重渲染（#archive-detail-actions 的 innerHTML），必须事件委托
    document.getElementById('archive-detail')?.addEventListener('click', event => {
        const action = event.target.closest('[data-batch-action]');
        if (!action) return;
        if (action.dataset.batchAction === 'link') {
            openBatchLinkModal();
        } else if (action.dataset.batchAction === 'prior') {
            openBatchPriorModal();
        }
    });
    linkEls.closeBtn.addEventListener('click', closeBatchLinkModal);
    linkEls.modal.addEventListener('click', event => {
        if (event.target === linkEls.modal) closeBatchLinkModal();
    });
    // 卡片会被逐张移除，按钮事件在列表容器上委托
    linkEls.list.addEventListener('click', event => {
        const button = event.target.closest('.archive-link-accept, .archive-link-reject');
        if (button) submitBatchLinkDecision(button);
    });
    priorEls.closeBtn.addEventListener('click', closeBatchPriorModal);
    priorEls.modal.addEventListener('click', event => {
        if (event.target === priorEls.modal) closeBatchPriorModal();
    });
    priorEls.list.addEventListener('click', event => {
        const confirmBtn = event.target.closest('.archive-batch-prior-confirm');
        if (confirmBtn) {
            submitBatchPriorDecision(confirmBtn, 'submitted');
            return;
        }
        const rejectBtn = event.target.closest('.archive-batch-prior-reject');
        if (rejectBtn) submitBatchPriorDecision(rejectBtn, 'not_submitted');
    });
    // 捕获阶段注册：本脚本晚于 content_drawer.js 加载，捕获阶段才能抢在抽屉的
    // 冒泡处理器之前读到抽屉仍开着的状态——抽屉开着时跳过，Escape 先关抽屉
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        if (!batchLinkState.open && !batchPriorState.open) return;
        if (archiveDrawerState.open) return;
        if (batchLinkState.open) closeBatchLinkModal();
        if (batchPriorState.open) closeBatchPriorModal();
    }, true);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupBatchDecisionModals, { once: true });
} else {
    setupBatchDecisionModals();
}
