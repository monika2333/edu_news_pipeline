// Duty-editor finalization batches. This is independent from administrator archive.

function finalizationReportLabel() {
    return state.reviewReportType === 'wanbao' ? '晚报' : '综报';
}

function formatFinalizationDateTime(value) {
    if (!value) return '未知时间';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '未知时间';
    return new Intl.DateTimeFormat('zh-CN', {
        timeZone: 'Asia/Shanghai',
        month: 'numeric',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
    }).format(date);
}

async function requestDutyFinalization(path, fallbackMessage, payload) {
    const options = payload === undefined ? undefined : {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    };
    const response = await workspaceFetch(`${API_BASE}${path}`, options);
    return requireManualMutationSuccess(response, fallbackMessage);
}

function setFinalizationHistoryOpen(open) {
    const modal = document.getElementById('finalization-history-modal');
    if (!modal) return;
    modal.classList.toggle('active', open);
    modal.setAttribute('aria-hidden', String(!open));
}

function finalizationHistoryItemTemplate(item) {
    const summary = item.edited_summary
        || item.summary
        || item.excerpt_text
        || item.llm_summary
        || '暂无摘要';
    return `
        <article class="finalization-history-item">
            <div>
                <h5>${escapeWorkspaceHtml(item.title || '无标题')}</h5>
                <p>${escapeWorkspaceHtml(summary)}</p>
            </div>
        </article>`;
}

function renderFinalizationHistory(batches) {
    const list = document.getElementById('finalization-history-list');
    if (!list) return;
    if (!batches.length) {
        list.innerHTML = '<div class="empty empty-state">当前报告还没有已定稿批次。</div>';
        return;
    }
    list.innerHTML = batches.map(batch => `
        <section class="finalization-history-batch">
            <header>
                <div>
                    <h4>${escapeWorkspaceHtml(formatFinalizationDateTime(batch.finalized_at))} 定稿</h4>
                    <p>
                        ${escapeWorkspaceHtml(batch.finalized_by_display_name || '值班编辑')}
                        · ${Number(batch.item_count) || 0} 条
                    </p>
                </div>
                <button class="btn btn-secondary" type="button"
                    data-finalization-restore-batch="${escapeWorkspaceHtml(batch.batch_id)}">
                    整批撤回
                </button>
            </header>
            <div class="finalization-history-items">
                ${(batch.items || []).map(item => (
                    finalizationHistoryItemTemplate(item)
                )).join('')}
            </div>
        </section>
    `).join('');
}

function handleFinalizationHistoryClick(event) {
    const button = event.target.closest('[data-finalization-restore-batch]');
    if (button) restoreDutyFinalization(button);
}

async function loadDutyFinalizationHistory() {
    const title = document.getElementById('finalization-history-title');
    const list = document.getElementById('finalization-history-list');
    if (title) title.textContent = `${finalizationReportLabel()}已定稿批次`;
    if (list) list.innerHTML = renderSkeleton(3);
    const params = new URLSearchParams({
        report_type: state.reviewReportType,
        _t: String(Date.now())
    });
    const payload = await requestDutyFinalization(
        `/finalizations?${params.toString()}`,
        '已定稿批次加载失败'
    );
    renderFinalizationHistory(payload.batches || []);
}

async function openDutyFinalizationHistory() {
    if (!IS_DUTY_WORKSPACE) return;
    setFinalizationHistoryOpen(true);
    try {
        await loadDutyFinalizationHistory();
    } catch (error) {
        setFinalizationHistoryOpen(false);
        showToast(error.message || '已定稿批次加载失败', 'error');
    }
}

async function finalizeCurrentDutyReview() {
    if (!IS_DUTY_WORKSPACE || state.reviewView !== 'selected') return;
    const items = state.reviewData.selected || [];
    if (!items.length) {
        showToast('当前采纳列表为空', 'error');
        return;
    }
    if (!window.confirm(
        `确定将当前 ${items.length} 条${finalizationReportLabel()}采纳新闻定稿并清空列表吗？`
    )) {
        return;
    }

    const button = document.getElementById('btn-finalize-review');
    if (button) button.disabled = true;
    try {
        const activeElement = document.activeElement;
        if (activeElement?.matches('.summary-box, .source-box')) {
            activeElement.blur();
        }
        await pendingReviewEditPromise;
        const orderSaved = await persistReviewOrder();
        if (!orderSaved) return;
        const result = await requestDutyFinalization(
            '/finalizations',
            '定稿失败',
            { report_type: state.reviewReportType }
        );
        clearDutyWorkspaceCache();
        await Promise.all([loadReviewData(), loadStats()]);
        showToast(`已定稿 ${result.item_count} 条新闻`);
    } catch (error) {
        showToast(error.message || '定稿失败', 'error');
    } finally {
        if (button) button.disabled = false;
    }
}

async function restoreDutyFinalization(button) {
    const batchId = button.dataset.finalizationRestoreBatch;
    if (!batchId || !window.confirm(
        '确定将这一整批新闻撤回当前采纳列表吗？'
    )) {
        return;
    }

    button.disabled = true;
    try {
        const result = await requestDutyFinalization(
            `/finalizations/${encodeURIComponent(batchId)}/restore`,
            '撤回失败',
            {}
        );
        clearDutyWorkspaceCache();
        await Promise.all([
            loadReviewData(),
            loadStats(),
            loadDutyFinalizationHistory()
        ]);
        showToast(`已撤回 ${result.restored} 条新闻`);
    } catch (error) {
        button.disabled = false;
        showToast(error.message || '撤回失败', 'error');
    }
}
