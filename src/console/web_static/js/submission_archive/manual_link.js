// Submission Archive JS - Manual Link Modal (存档条目人工回链)
//
// 「未覆盖」条目的兜底路径：编辑在弹窗内检索候选新闻并手动绑定回链；绑错可解绑。
// 绑定/解绑成功后走 applyManualLinkResult 局部更新条目卡片，不重拉整份报告。
// 本脚本必须先于 content_drawer.js 加载：原文抽屉盖在弹窗上时 Escape 先关抽屉，
// 依赖这里的 keydown 处理器先注册并主动跳过。

const manualLinkState = {
    open: false,
    itemId: '',
    query: '',
    windowDays: 15,
    offset: 0,
    hasMore: false,
    loading: false
};

// 与模板中 #archive-link-window 的选项一一对应；「不限」是后端 window_days 上限
const MANUAL_LINK_WINDOW_OPTIONS = [
    { value: 15, label: '±15 天' },
    { value: 30, label: '±30 天' },
    { value: 90, label: '±90 天' },
    { value: 3650, label: '不限' }
];

function getManualLinkEls() {
    return {
        modal: document.getElementById('archive-link-modal'),
        closeBtn: document.getElementById('archive-link-modal-close'),
        refTitle: document.getElementById('archive-link-ref-title'),
        refBody: document.getElementById('archive-link-ref-body'),
        query: document.getElementById('archive-link-q'),
        windowSelect: document.getElementById('archive-link-window'),
        searchBtn: document.getElementById('archive-link-search'),
        windowHint: document.getElementById('archive-link-window-hint'),
        results: document.getElementById('archive-link-results'),
        moreBtn: document.getElementById('archive-link-more')
    };
}

function nextWindowOption(current) {
    const index = MANUAL_LINK_WINDOW_OPTIONS.findIndex(option => option.value === current);
    return index >= 0 && index < MANUAL_LINK_WINDOW_OPTIONS.length - 1
        ? MANUAL_LINK_WINDOW_OPTIONS[index + 1]
        : null;
}

// 空态不能说「库里没有」：候选池只是 news_summaries（相关性评分达标、已生成摘要），
// 评分不达标的文章在库里但检索不到；同时给出扩大时间窗的入口
function manualLinkEmptyHtml() {
    const next = nextWindowOption(manualLinkState.windowDays);
    const widen = next
        ? `<button class="archive-btn-text archive-link-widen-btn" type="button"`
            + ` data-window-days="${next.value}">扩大到${escapeHtml(next.label)}重新检索</button>`
        : '';
    return '<div class="archive-empty">'
        + '<p>这个时间窗内没有匹配的已入库新闻。</p>'
        + '<p>候选池只包含相关性评分达标、已生成摘要的文章；评分不达标的文章不在检索范围内。</p>'
        + widen
        + '</div>';
}

function manualLinkLinkedBadgeHtml(candidate) {
    // 后端的 linked_items 按 article_id 聚合，不排除正在操作的条目；改绑时会把自己
    // 也列出来（「已绑定到（本条）」），渲染前过滤掉，过滤完为空则不显示标记
    const linked = (candidate.linked_items || []).filter(
        entry => String(entry.item_id) !== String(manualLinkState.itemId)
    );
    if (!linked.length) return '';
    const text = linked.map(entry => {
        const label = typeLabels[entry.report_type] || entry.report_type || '存档';
        return `${label} ${dateValue(entry.report_date)}《${entry.title || '无标题'}》`;
    }).join('；');
    return `<div class="archive-link-candidate-linked">已绑定到：${escapeHtml(text)}（仅提示，不阻止绑定）</div>`;
}

function manualLinkCandidateHtml(candidate) {
    const summary = String(candidate.llm_summary || '').trim();
    const snippet = summary.length > 120 ? `${summary.slice(0, 120)}…` : summary;
    const articleId = escapeHtml(candidate.article_id || '');
    // ingested_at / publish_time_iso 是带 Z 的 UTC 时间，必须走 formatLocalDateTime
    // 按本地时区取值；直接截字符串会把 UTC 当本地时间，凌晨入库的记录日期差一天
    return `
        <section class="archive-link-candidate">
            <div class="archive-link-candidate-title">${escapeHtml(candidate.title || '(无标题)')}</div>
            <div class="archive-link-candidate-meta">
                <span>来源：${escapeHtml(candidate.source || '-')}</span>
                <span>入库：${escapeHtml(formatLocalDateTime(candidate.ingested_at))}</span>
                ${candidate.publish_time_iso ? `<span>发布：${escapeHtml(formatLocalDateTime(candidate.publish_time_iso))}</span>` : ''}
            </div>
            ${manualLinkLinkedBadgeHtml(candidate)}
            ${snippet ? `<p class="archive-link-candidate-summary">${escapeHtml(snippet)}</p>` : ''}
            <div class="archive-link-candidate-actions">
                <button class="archive-btn-text content-drawer-trigger" type="button"
                    data-article-id="${articleId}">原文</button>
                <button class="btn btn-primary archive-link-bind-btn" type="button"
                    data-article-id="${articleId}">绑定</button>
            </div>
        </section>
    `;
}

function openManualLinkModal(itemId) {
    const els = getManualLinkEls();
    const item = activeReportItems.find(entry => String(entry.id) === String(itemId));
    if (!els.modal || !item) return;
    manualLinkState.open = true;
    manualLinkState.itemId = item.id;
    manualLinkState.query = '';
    manualLinkState.windowDays = 15;
    manualLinkState.offset = 0;
    manualLinkState.hasMore = false;
    manualLinkState.loading = false;
    els.refTitle.textContent = item.title || '(无标题)';
    els.refBody.textContent = item.body || '';
    els.query.value = '';
    els.windowSelect.value = '15';
    // 检索窗口以报告整理日期为中心向前后展开，不是「最近 N 天」，文案必须讲清楚，
    // 否则编辑会以为搜不到就是库里没有
    const compiled = dateValue(activeReportCompiledDate);
    els.windowHint.textContent = compiled
        ? `检索窗口以报告整理日期 ${compiled} 为中心，向前向后各取所选天数。`
        : '检索窗口以报告整理日期为中心，向前向后各取所选天数。';
    els.results.innerHTML = '<div class="archive-empty">输入关键词开始检索。</div>';
    els.moreBtn.hidden = true;
    els.modal.classList.add('active');
    els.modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('archive-link-modal-open');
    els.query.focus();
}

function closeManualLinkModal() {
    const els = getManualLinkEls();
    if (!els.modal) return;
    manualLinkState.open = false;
    manualLinkState.itemId = '';
    els.modal.classList.remove('active');
    els.modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('archive-link-modal-open');
}

async function searchManualLinkCandidates(append = false) {
    const els = getManualLinkEls();
    const query = els.query.value.trim();
    if (!query) {
        els.results.innerHTML = '<div class="archive-empty">请输入检索关键词。</div>';
        els.moreBtn.hidden = true;
        return;
    }
    if (manualLinkState.loading) return;
    manualLinkState.loading = true;
    manualLinkState.query = query;
    manualLinkState.windowDays = Number(els.windowSelect.value) || 15;
    const itemId = manualLinkState.itemId;
    const offset = append ? manualLinkState.offset : 0;
    if (!append) {
        els.results.innerHTML = '<div class="archive-empty">正在检索…</div>';
        els.moreBtn.hidden = true;
    }
    const params = new URLSearchParams({
        q: query,
        window_days: String(manualLinkState.windowDays),
        limit: '20',
        offset: String(offset)
    });
    try {
        const data = await api(
            `/items/${encodeURIComponent(itemId)}/link-candidates?${params.toString()}`
        );
        if (manualLinkState.itemId !== itemId) return;
        const items = data.items || [];
        manualLinkState.offset = offset + items.length;
        manualLinkState.hasMore = Boolean(data.has_more);
        // window_start/window_end 是不带时区的 date，按字符串截取，不走 formatLocalDateTime
        els.windowHint.textContent = '检索窗口以报告整理日期为中心：'
            + `当前 ${dateValue(data.window_start)} 至 ${dateValue(data.window_end)}。`;
        const html = items.map(manualLinkCandidateHtml).join('');
        if (append) {
            els.results.insertAdjacentHTML('beforeend', html);
        } else {
            els.results.innerHTML = items.length ? html : manualLinkEmptyHtml();
        }
        els.moreBtn.hidden = !manualLinkState.hasMore;
    } catch (error) {
        if (manualLinkState.itemId !== itemId) return;
        if (append) {
            toast(error.message, 'error');
        } else {
            els.results.innerHTML = `<div class="archive-empty">${escapeHtml(error.message)}</div>`;
        }
    } finally {
        if (manualLinkState.itemId === itemId) {
            manualLinkState.loading = false;
        }
    }
}

async function bindManualLink(articleId, button) {
    const itemId = manualLinkState.itemId;
    if (!itemId || !articleId) return;
    if (button) button.disabled = true;
    try {
        const item = await api(`/items/${encodeURIComponent(itemId)}/manual-link`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ article_id: articleId })
        });
        closeManualLinkModal();
        applyManualLinkResult(item);
        toast('已绑定回链');
    } catch (error) {
        if (button) button.disabled = false;
        // 409 时 error.message 就是后端的「条目正在自动回链处理中，请稍后再试」，直接展示
        toast(error.message, 'error');
    }
}

async function unlinkManualLink(itemId) {
    if (!itemId) return;
    // 解绑是破坏性操作，先确认再执行
    if (!window.confirm('确定解除这条存档条目的回链绑定吗？')) return;
    try {
        const item = await api(`/items/${encodeURIComponent(itemId)}/manual-link`, {
            method: 'DELETE'
        });
        applyManualLinkResult(item);
        toast('已解除绑定');
    } catch (error) {
        toast(error.message, 'error');
    }
}

function setupManualLinkModal() {
    const els = getManualLinkEls();
    if (!els.modal) return;
    els.closeBtn.addEventListener('click', closeManualLinkModal);
    els.modal.addEventListener('click', event => {
        if (event.target === els.modal) closeManualLinkModal();
    });
    els.searchBtn.addEventListener('click', () => searchManualLinkCandidates(false));
    els.query.addEventListener('keydown', event => {
        if (event.key === 'Enter') {
            event.preventDefault();
            searchManualLinkCandidates(false);
        }
    });
    els.windowSelect.addEventListener('change', () => {
        if (els.query.value.trim()) searchManualLinkCandidates(false);
    });
    els.moreBtn.addEventListener('click', () => searchManualLinkCandidates(true));
    els.results.addEventListener('click', event => {
        const widenBtn = event.target.closest('.archive-link-widen-btn');
        if (widenBtn) {
            els.windowSelect.value = widenBtn.dataset.windowDays;
            searchManualLinkCandidates(false);
            return;
        }
        const bindBtn = event.target.closest('.archive-link-bind-btn');
        if (bindBtn) bindManualLink(bindBtn.dataset.articleId, bindBtn);
    });
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || !manualLinkState.open) return;
        // 原文抽屉盖在弹窗上时 Escape 先关抽屉：本脚本先于 content_drawer.js 注册，
        // 这里直接跳过，抽屉的处理器随后执行并关闭抽屉
        if (archiveDrawerState.open) return;
        closeManualLinkModal();
    });
    // 条目卡片上的「手动匹配」「解绑」入口；meta 行会被轮询重渲染，必须用事件委托
    document.getElementById('archive-detail')?.addEventListener('click', event => {
        const linkBtn = event.target.closest('.archive-manual-link-btn');
        if (linkBtn) {
            openManualLinkModal(linkBtn.dataset.itemId);
            return;
        }
        const unlinkBtn = event.target.closest('.archive-manual-unlink-btn');
        if (unlinkBtn) unlinkManualLink(unlinkBtn.dataset.itemId);
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupManualLinkModal, { once: true });
} else {
    setupManualLinkModal();
}
