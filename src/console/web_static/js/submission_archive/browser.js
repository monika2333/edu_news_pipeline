// Submission Archive JS - Browser
/* ---------- 存档库（列表 + 详情双栏） ---------- */

const listState = { offset: 0, total: 0, loading: false, type: '' };
let activeReportId = initialReportId;
let reportRefreshTimer = null;
let activeReportStatusSignature = '';
// 详情区按回链状态筛选：'' 表示不过滤，其余取值 linked/pending/uncovered；
// pending 的筛选 chip 已删除（数量改由标题栏批量按钮体现），取值机制与分组保留
let detailStatusFilter = '';
// 详情区按已报送判定筛选（与回链状态筛选独立、可叠加）：'' / matched / unmatched
let detailPriorFilter = '';
// 处于编辑态的条目 id 集合：点卡片上的铅笔图标按条展开/收起
const detailEditingItemIds = new Set();
// 当前详情报告的条目缓存与整理日期：人工回链弹窗的参照区与局部更新都从这里取数
let activeReportItems = [];
let activeReportCompiledDate = '';
// 当前详情报告的报别：已报送命中 chip 只在反馈报告上渲染（detailStats 的两条调用路径都读它）
let activeReportType = '';
// 当前详情报告的已报送判定是否进行中：进行中条目 prior_match 全为 null，
// 此时不得给未命中条目贴「未报送」（detailPriorMatchPill 读它）
let activeReportPriorMatchPending = false;
// 已报送判定在回链完成后才执行（含加载嵌入模型，十几秒），轮询必须等它结束；
// 后台进程崩溃会让 prior_match_pending 永远为真，因此仅剩这一原因时给轮询设兜底上限
// （60 次 × 1.5s ≈ 90 秒），达到上限后静默停止
const PRIOR_MATCH_POLL_LIMIT = 60;
let priorMatchPollCount = 0;

function linkStatusGroup(status) {
    if (status === 'matched') return 'linked';
    if (status === 'pending') return 'pending';
    if (status === 'unmatched' || status === 'rejected') return 'uncovered';
    return 'processing';
}

function reportCardStatsHtml(report) {
    const linked = Number(report.matched_count || 0);
    const processing = Number(report.processing_count || 0);
    const pending = Number(report.pending_count || 0);
    const unmatched = Number(report.unmatched_count || 0) + Number(report.rejected_count || 0);
    const total = linked + processing + pending + unmatched;
    const pct = value => total > 0 ? (value / total) * 100 : 0;
    const bar = total > 0 ? `
        <div class="archive-linkbar" aria-hidden="true">
            ${linked ? `<span class="seg-linked" style="width:${pct(linked)}%"></span>` : ''}
            ${processing ? `<span class="seg-processing" style="width:${pct(processing)}%"></span>` : ''}
            ${pending ? `<span class="seg-pending" style="width:${pct(pending)}%"></span>` : ''}
            ${unmatched ? `<span class="seg-unmatched" style="width:${pct(unmatched)}%"></span>` : ''}
        </div>` : '';
    return `
        ${bar}
        <div class="archive-report-card-counts">
            <span><strong>${report.item_count || 0}</strong> 条</span>
            ${processing ? `<span>正在判断 ${processing}</span>` : ''}
            ${pending ? `<span class="has-pending">待确认 ${pending}</span>` : ''}
        </div>
    `;
}

function reportCard(report) {
    return `
        <button class="archive-report-card${report.id === activeReportId ? ' is-active' : ''}"
            data-report-id="${report.id}" type="button">
            <div class="archive-report-card-top">
                ${typePill(report.report_type)}
                <span class="archive-report-card-date">${dateValue(report.report_date)}</span>
            </div>
            <div class="archive-report-card-issue">${escapeHtml(report.issue_no || '无期号')}</div>
            <div class="archive-report-card-stats">
                ${reportCardStatsHtml(report)}
            </div>
        </button>
    `;
}

function markActiveReport(id) {
    document.querySelectorAll('.archive-report-card').forEach(card => {
        card.classList.toggle('is-active', card.dataset.reportId === id);
    });
}

async function loadReportList(append = false) {
    const params = new URLSearchParams();
    const dateFrom = document.getElementById('archive-date-from').value;
    const dateTo = document.getElementById('archive-date-to').value;
    if (listState.type) params.set('report_type', listState.type);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    params.set('offset', String(append ? listState.offset : 0));
    params.set('limit', '30');
    const target = document.getElementById('archive-report-list');
    if (!append) {
        target.innerHTML = '<div class="archive-empty">正在加载…</div>';
        listState.offset = 0;
    }
    listState.loading = true;
    try {
        const data = await api(`/reports?${params.toString()}`);
        listState.total = data.total || 0;
        listState.offset = (append ? listState.offset : 0) + data.items.length;
        if (!append) {
            target.innerHTML = data.items.length
                ? data.items.map(reportCard).join('')
                : '<div class="archive-empty">还没有存档报告，点右上角「新增存档」录入第一份。</div>';
        } else {
            target.insertAdjacentHTML('beforeend', data.items.map(reportCard).join(''));
        }
        const footer = document.getElementById('archive-list-footer');
        const more = document.getElementById('archive-list-more');
        footer.hidden = listState.total === 0;
        document.getElementById('archive-list-total').textContent =
            `共 ${listState.total} 份 · 已显示 ${listState.offset} 份`;
        more.hidden = listState.offset >= listState.total;
        return data.items;
    } catch (error) {
        if (!append) {
            target.innerHTML = `<div class="archive-empty">${escapeHtml(error.message)}</div>`;
        } else {
            toast(error.message, 'error');
        }
        return [];
    } finally {
        listState.loading = false;
    }
}

function detailStats(items) {
    const stats = {
        processing: 0,
        matched: 0,
        pending: 0,
        unmatched: 0,
        rejected: 0
    };
    items.forEach(item => {
        if (item.link_status in stats) stats[item.link_status] += 1;
    });
    const matched = stats.matched;
    const uncovered = stats.unmatched + stats.rejected;
    // 已报送/未报送 chip：仅反馈报告且判定结束后渲染（进行中 prior_match 全为 null，
    // 计数没有含义），可点击筛选，与 link_status 筛选相互独立；
    // dismissed（人工判为未报送）计入未报送，与 detailPriorGroup 共用 isPriorSubmitted
    const showPriorChips = activeReportType === 'feedback' && !activeReportPriorMatchPending;
    const priorMatched = showPriorChips ? items.filter(isPriorSubmitted).length : 0;
    const priorChip = (filter, label, count, extraClass = '') => {
        const active = detailPriorFilter === filter;
        return `<button class="archive-stat-chip${extraClass}${active ? ' is-active' : ''}" type="button"`
            + ` data-prior-filter="${filter}" aria-pressed="${active}">${label} <strong>${count}</strong></button>`;
    };
    // 已匹配/未覆盖渲染为可点击按钮，点击后仅展示该分类条目，再次点击取消筛选；
    // 待确认（pending）不提供筛选 chip——其数量由标题栏的「待确认回链」批量按钮
    // （detailHeadActionsHtml）体现，否则「已匹配+未覆盖」与「共 N 条」的差值无处解释
    const filterChip = (filter, label, count, extraClass = '') => {
        const active = detailStatusFilter === filter;
        return `<button class="archive-stat-chip${extraClass}${active ? ' is-active' : ''}" type="button"`
            + ` data-status-filter="${filter}" aria-pressed="${active}">${label} <strong>${count}</strong></button>`;
    };
    return `
        <div class="archive-stat-chips" id="archive-detail-stats">
            <span class="archive-stat-chip">共 <strong>${items.length}</strong> 条</span>
            ${stats.processing ? `<span class="archive-stat-chip is-processing">正在判断 <strong>${stats.processing}</strong></span>` : ''}
            ${showPriorChips ? priorChip('matched', '已报送', priorMatched, ' is-prior-matched')
                + priorChip('unmatched', '未报送', items.length - priorMatched, ' is-prior-unmatched') : ''}
            ${filterChip('linked', '已匹配', matched, ' is-linked')}
            ${filterChip('uncovered', '未覆盖', uncovered)}
        </div>
    `;
}

// 详情标题栏右侧的当期批量处理入口（#archive-detail-actions 的内容）。
// 两条渲染路径都要走这个函数：selectReport 首次渲染详情、
// updateReportStatusComponents 随统计 chips 一起刷新——漏掉任何一条，
// 轮询回来或批量弹窗处理掉几条之后，按钮计数会停在旧值、该消失的按钮不消失
function detailHeadActionsHtml(items) {
    const buttons = [];
    // 待确认回链：统计区没有 pending 筛选 chip，待确认数量只在这个按钮上体现
    const pending = items.filter(item => item.link_status === 'pending').length;
    if (pending > 0) {
        buttons.push(
            '<button class="btn btn-secondary archive-detail-action-btn is-pending" type="button"'
                + ` data-batch-action="link">待确认回链 <strong>${pending}</strong></button>`
        );
    }
    // 疑似已报送：仅反馈报告；判定进行中时所有条目 prior_match 为 null，计数没有意义，
    // 必须等判定结束（与 detailPriorMatchPill 的门控一致）。status === 'suspected'
    // 即「可人工判定且尚未判定」，后端 SQL 已保证，前端不再叠加 decidable/decision 判断
    const suspected = items.filter(
        item => item.prior_match && item.prior_match.status === 'suspected'
    ).length;
    if (activeReportType === 'feedback' && !activeReportPriorMatchPending && suspected > 0) {
        buttons.push(
            '<button class="btn btn-secondary archive-detail-action-btn is-suspected" type="button"'
                + ` data-batch-action="prior">疑似已报送 <strong>${suspected}</strong></button>`
        );
    }
    return buttons.join('');
}

function detailItemMetaHtml(item) {
    const meta = [];
    meta.push(`来源：${escapeHtml(item.source || '-')}`);
    (item.urls || []).forEach(url => {
        meta.push(`<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>`);
    });
    const score = Number(item.link_combined_score);
    if (Number.isFinite(score) && item.link_status !== 'matched') {
        meta.push(`综合分 ${scoreValue(score)}`);
    }
    if (item.link_status === 'pending') {
        meta.push('<a href="/submission-archive/link-queue">去确认</a>');
    }
    // 人工回链入口：未覆盖给「手动匹配」，已匹配给「解绑」；processing/pending 不给入口
    if (item.link_status === 'unmatched' || item.link_status === 'rejected') {
        meta.push(`<button class="archive-manual-link-btn" type="button" data-item-id="${escapeHtml(item.id)}">手动匹配</button>`);
    } else if (item.link_status === 'matched') {
        meta.push(`<button class="archive-manual-unlink-btn" type="button" data-item-id="${escapeHtml(item.id)}">解绑</button>`);
    }
    return meta.map(part => `<span>${part}</span>`).join('');
}

// 已匹配条目在标题后渲染「原文」标签，点击打开内容抽屉（.content-drawer-trigger 委托）
const detailOriginalTriggerHtml = item => (
    item.link_status === 'matched' && item.article_id
        ? `<button type="button" class="content-drawer-trigger" data-article-id="${escapeHtml(item.article_id)}"`
            + ` data-bonus-keywords="" title="查看原文">原文</button>`
        : ''
);

function detailItemEditFormHtml(item) {
    const urls = (item.urls || []).join('\n');
    return `
        <div class="archive-item-edit">
            <div class="archive-item-editor-grid">
                <label class="archive-field is-wide">
                    <span>标题</span>
                    <input data-edit-field="title" value="${escapeHtml(item.title)}">
                </label>
                <label class="archive-field is-wide">
                    <span>正文</span>
                    <textarea data-edit-field="body" rows="4">${escapeHtml(item.body || '')}</textarea>
                </label>
                <label class="archive-field is-wide">
                    <span>来源</span>
                    <input data-edit-field="source" value="${escapeHtml(item.source || '')}">
                </label>
                <label class="archive-field is-wide">
                    <span>URL（每行一个）</span>
                    <textarea data-edit-field="urls" rows="1">${escapeHtml(urls)}</textarea>
                </label>
            </div>
            <div class="archive-item-editor-actions">
                <button class="btn btn-secondary archive-item-cancel-btn" type="button"
                    data-item-id="${escapeHtml(item.id)}">退出修改</button>
                <button class="btn btn-primary archive-item-save-btn" type="button"
                    data-item-id="${escapeHtml(item.id)}">保存本条</button>
            </div>
        </div>
    `;
}

// 修改入口：未覆盖/已匹配条目的状态标签左边渲染铅笔图标（仅管理员）；
// processing/pending 条目不给入口（与后端拒绝 processing 的约定一致）
const detailEditTriggerHtml = item => {
    if (!isAdminUser) return '';
    if (!['unmatched', 'rejected', 'matched'].includes(item.link_status)) return '';
    const active = detailEditingItemIds.has(String(item.id));
    return `<button type="button" class="archive-item-edit-btn${active ? ' is-active' : ''}"
        data-item-id="${escapeHtml(item.id)}" title="修改条目" aria-label="修改条目"
        aria-pressed="${active}"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"
        stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg></button>`;
};

// 已报送筛选分组：matched（已报送，含人工确认）/ unmatched（未报送，含 dismissed
// 人工判定与无命中两种）/ ''（非反馈报告或判定进行中——此时 prior_match 全为 null，
// 分组没有意义，也不能误贴「未报送」）。dismissed 的归属与 detailStats 计数一致，
// 都走 isPriorSubmitted；卡片标签与 data-prior-group、统计 chip 共用这一门控
const detailPriorGroup = item => (
    activeReportType === 'feedback' && !activeReportPriorMatchPending
        ? (isPriorSubmitted(item) ? 'matched' : 'unmatched')
        : ''
);

const detailPriorMatchPill = item => priorMatchPill(item, {
    showUnmatched: detailPriorGroup(item) === 'unmatched'
});

// 回链标签只贴给「给不出操作入口」的状态：processing/pending 没有任何操作入口，
// 必须靠标签说明；matched 的状态已由标题后的「原文」标签和 meta 的「解绑」表达，
// unmatched/rejected 已由 meta 的「手动匹配」表达，再贴文字标签是重复信息
const detailLinkPill = item => (
    item.link_status === 'processing' || item.link_status === 'pending'
        ? linkPill(item.link_status)
        : ''
);

// archive-item-flags 是「铅笔按钮 + 回链标签 + 已报送标签」的恒定容器：
// 无论条目什么状态都渲染（内容可为空），updateReportStatusComponents
// 靠它做整块 innerHTML 替换，不依赖某个标签元素是否存在
function detailItemCard(item) {
    const editing = detailEditingItemIds.has(String(item.id));
    return `
        <article class="archive-item${item.link_status === 'pending' ? ' is-pending' : ''}${item.link_status === 'processing' ? ' is-processing' : ''}${editing ? ' is-editing' : ''}"
            data-item-id="${escapeHtml(item.id)}" data-link-group="${linkStatusGroup(item.link_status)}"
            data-prior-group="${detailPriorGroup(item)}">
            <div class="archive-item-head">
                <span class="archive-item-order">${item.order_index + 1}</span>
                <h4 class="archive-item-title">${escapeHtml(item.title)}${detailOriginalTriggerHtml(item)}</h4>
                <span class="archive-item-flags">${detailEditTriggerHtml(item)}${detailLinkPill(item)}${detailPriorMatchPill(item)}</span>
            </div>
            ${item.body ? `<p class="archive-item-body">${escapeHtml(item.body)}</p>` : ''}
            <div class="archive-item-meta">${detailItemMetaHtml(item)}</div>
            ${editing ? detailItemEditFormHtml(item) : ''}
        </article>
    `;
}

function detailItemsHtml(items) {
    const parts = [];
    let lastSection = null;
    items.forEach(item => {
        const section = item.section || '未分章节';
        if (section !== lastSection) {
            parts.push(`<h3 class="archive-section-heading">【${escapeHtml(section)}】</h3>`);
            lastSection = section;
        }
        parts.push(detailItemCard(item));
    });
    return parts.join('');
}

// 按 detailStatusFilter / detailPriorFilter 隐藏不匹配的条目卡片（两个维度叠加）；
// 所在章节全部不可见时一并隐藏章节标题
function applyDetailFilter() {
    const container = document.querySelector('#archive-detail .archive-detail-items');
    if (!container) return;
    const filtering = Boolean(detailStatusFilter) || Boolean(detailPriorFilter);
    container.querySelectorAll('.archive-item[data-item-id]').forEach(card => {
        const statusHidden = Boolean(detailStatusFilter)
            && card.dataset.linkGroup !== detailStatusFilter;
        const priorHidden = Boolean(detailPriorFilter)
            && card.dataset.priorGroup !== detailPriorFilter;
        card.hidden = statusHidden || priorHidden;
    });
    let heading = null;
    let anyVisible = false;
    const flushHeading = () => {
        if (heading) heading.hidden = filtering && !anyVisible;
    };
    container.childNodes.forEach(node => {
        if (!(node instanceof HTMLElement)) return;
        if (node.classList.contains('archive-section-heading')) {
            flushHeading();
            heading = node;
            anyVisible = false;
        } else if (node.classList.contains('archive-item') && !node.hidden) {
            anyVisible = true;
        }
    });
    flushHeading();
}

function reportStatusSignature(items, priorMatchPending = false) {
    // prior_match 与判定进度都必须纳入指纹：prior_match 在回链完成后的某一轮轮询里才出现；
    // 判定结束（pending 由真变假）时，即使全部未命中也要给条目贴上「未报送」，
    // 指纹不含进度的话这一刻页面不会重绘；decision 也要入指纹——撤销与再判定的组合下
    // status 可能相同而弹窗底部入口状态不同，指纹必须能区分
    return (priorMatchPending ? 'pending|' : 'done|') + items.map(item => [
        item.id,
        item.link_status,
        item.link_combined_score ?? '',
        item.article_id ?? '',
        item.best_candidate_article_id ?? '',
        item.prior_match ? `${item.prior_match.status}~${item.prior_match.count}~${item.prior_match.decision ?? ''}` : ''
    ].join(':')).join('|');
}

function reportCountsFromItems(items) {
    const counts = {
        item_count: items.length,
        processing_count: 0,
        matched_count: 0,
        pending_count: 0,
        unmatched_count: 0
    };
    items.forEach(item => {
        const key = `${item.link_status}_count`;
        if (key in counts) {
            counts[key] += 1;
        } else if (item.link_status === 'rejected') {
            counts.unmatched_count += 1;
        }
    });
    return counts;
}

function updateReportStatusComponents(id, items) {
    activeReportItems = items;
    const statsTarget = document.getElementById('archive-detail-stats');
    if (statsTarget) {
        statsTarget.outerHTML = detailStats(items);
    }
    // 标题栏批量按钮随统计 chips 一起刷新（detailHeadActionsHtml 的另一条渲染路径）：
    // 漏掉的话，轮询回来或批量弹窗处理掉几条之后按钮计数会停在旧值
    const actionsTarget = document.getElementById('archive-detail-actions');
    if (actionsTarget) {
        actionsTarget.innerHTML = detailHeadActionsHtml(items);
    }
    const itemsById = new Map(items.map(item => [String(item.id), item]));
    document.querySelectorAll('#archive-detail .archive-item[data-item-id]').forEach(card => {
        const item = itemsById.get(card.dataset.itemId);
        if (!item) return;
        card.classList.toggle('is-processing', item.link_status === 'processing');
        card.classList.toggle('is-pending', item.link_status === 'pending');
        card.dataset.linkGroup = linkStatusGroup(item.link_status);
        card.dataset.priorGroup = detailPriorGroup(item);
        // flags 容器恒定渲染（见 detailItemCard），局部更新直接整块替换 innerHTML：
        // 铅笔入口、回链标签、已报送标签的三种迁移（无→有、内容变化、有→无）都被覆盖，
        // 不再借某个标签元素做重插锚点——matched/unmatched/rejected 本来就不渲染
        // 回链标签，锚点在这些状态上不存在
        const flags = card.querySelector('.archive-item-flags');
        if (flags) {
            flags.innerHTML = `${detailEditTriggerHtml(item)}${detailLinkPill(item)}${detailPriorMatchPill(item)}`;
        }
        // 状态变化后同步标题后的「原文」标签：仅 matched 且有 article_id 时存在
        const titleEl = card.querySelector('.archive-item-title');
        const trigger = titleEl?.querySelector('.content-drawer-trigger');
        if (titleEl) {
            if (item.link_status === 'matched' && item.article_id) {
                if (trigger) {
                    trigger.dataset.articleId = item.article_id;
                } else {
                    titleEl.insertAdjacentHTML('beforeend', detailOriginalTriggerHtml(item));
                }
            } else if (trigger) {
                trigger.remove();
            }
        }
        const meta = card.querySelector('.archive-item-meta');
        if (meta) meta.innerHTML = detailItemMetaHtml(item);
    });
    applyDetailFilter();
    const activeCard = Array.from(
        document.querySelectorAll('.archive-report-card')
    ).find(card => card.dataset.reportId === id);
    const cardStats = activeCard?.querySelector('.archive-report-card-stats');
    if (cardStats) {
        cardStats.innerHTML = reportCardStatsHtml(
            reportCountsFromItems(items)
        );
    }
}

// 人工绑定/解绑成功后局部更新这一条卡片：复用轮询的组件更新（pill、meta、统计 chips、
// 左侧进度条、筛选显隐），并同步状态指纹，避免轮询误判变化触发一次多余的整体重绘。
function applyManualLinkResult(updatedItem) {
    const index = activeReportItems.findIndex(
        item => String(item.id) === String(updatedItem.id)
    );
    if (index !== -1) {
        // 接口只返回回链字段，合并进缓存条目以保留 title/body/source 等展示字段
        activeReportItems[index] = { ...activeReportItems[index], ...updatedItem };
    }
    updateReportStatusComponents(activeReportId, activeReportItems);
    activeReportStatusSignature = reportStatusSignature(
        activeReportItems, activeReportPriorMatchPending
    );
}

// 已报送人工判定（确认已报送 / 不是同一条 / 撤销）成功后的局部更新：把返回的
// prior_match 合并进缓存条目，复用轮询的组件更新并同步状态指纹，不重拉报告；
// 与 applyManualLinkResult 同一套路（prior_matches.js 调用）
function applyPriorMatchDecisionResult(itemId, priorMatch) {
    const index = activeReportItems.findIndex(
        item => String(item.id) === String(itemId)
    );
    if (index === -1) return;
    activeReportItems[index] = { ...activeReportItems[index], prior_match: priorMatch };
    updateReportStatusComponents(activeReportId, activeReportItems);
    activeReportStatusSignature = reportStatusSignature(
        activeReportItems, activeReportPriorMatchPending
    );
}

// 单条卡片整体重绘（编辑态展开/收起、保存成功后回到展示态），随后恢复筛选显隐
function rerenderDetailItemCard(item) {
    const card = document.querySelector(
        `#archive-detail .archive-item[data-item-id="${CSS.escape(String(item.id))}"]`
    );
    if (card) card.outerHTML = detailItemCard(item);
    applyDetailFilter();
}

// 条目字段保存成功后合并缓存并收起该条编辑框，整体重绘卡片；
// 回链字段不变，状态指纹不受影响
function applyItemEditResult(updatedItem) {
    const index = activeReportItems.findIndex(
        item => String(item.id) === String(updatedItem.id)
    );
    if (index === -1) return;
    activeReportItems[index] = { ...activeReportItems[index], ...updatedItem };
    detailEditingItemIds.delete(String(updatedItem.id));
    rerenderDetailItemCard(activeReportItems[index]);
}

async function saveItemEdit(button) {
    const card = button.closest('.archive-item[data-item-id]');
    if (!card) return;
    const itemId = card.dataset.itemId;
    const readField = field => {
        const input = card.querySelector(`[data-edit-field="${field}"]`);
        return input ? input.value : '';
    };
    const payload = {
        title: readField('title').trim(),
        body: readField('body'),
        source: readField('source'),
        urls: readField('urls')
            .split(/\r?\n/)
            .map(part => part.trim())
            .filter(Boolean)
    };
    if (!payload.title) {
        toast('标题不能为空', 'error');
        return;
    }
    button.disabled = true;
    try {
        const updated = await api(`/items/${encodeURIComponent(itemId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        applyItemEditResult(updated);
        toast('已保存');
    } catch (error) {
        toast(error.message, 'error');
    } finally {
        button.disabled = false;
    }
}

// 铅笔图标开关单条编辑框：展开/收起只重绘这一条卡片，不动其他条目
function toggleItemEdit(button) {
    const itemId = String(button.dataset.itemId || '');
    const item = activeReportItems.find(entry => String(entry.id) === itemId);
    if (!item) return;
    if (detailEditingItemIds.has(itemId)) {
        detailEditingItemIds.delete(itemId);
    } else {
        detailEditingItemIds.add(itemId);
    }
    rerenderDetailItemCard(item);
}

function scheduleReportStatusPoll(id, delay = 1500) {
    reportRefreshTimer = window.setTimeout(
        () => pollReportStatus(id),
        delay
    );
}

async function pollReportStatus(id) {
    reportRefreshTimer = null;
    if (activeReportId !== id) return;
    try {
        const report = await api(`/reports/${encodeURIComponent(id)}`);
        if (activeReportId !== id) return;
        const items = report.items || [];
        activeReportPriorMatchPending = Boolean(report.prior_match_pending);
        const signature = reportStatusSignature(items, activeReportPriorMatchPending);
        if (signature !== activeReportStatusSignature) {
            updateReportStatusComponents(id, items);
            activeReportStatusSignature = signature;
        }
        // 继续轮询的条件：有条目处于 processing，或报告的已报送判定未结束
        // （prior_match_pending）。判定在回链完成后才跑，只看 processing 会提前停轮
        if (items.some(item => item.link_status === 'processing')) {
            priorMatchPollCount = 0;
            scheduleReportStatusPoll(id);
        } else if (report.prior_match_pending && priorMatchPollCount < PRIOR_MATCH_POLL_LIMIT) {
            // 仅剩已报送判定一个原因时计数兜底：后台崩溃导致标志永远为真时静默停止
            priorMatchPollCount += 1;
            scheduleReportStatusPoll(id);
        } else {
            priorMatchPollCount = 0;
            loadNavPending();
        }
    } catch (error) {
        if (activeReportId === id) {
            scheduleReportStatusPoll(id, 3000);
        }
    }
}

async function selectReport(id, pushUrl = true) {
    if (!id) return;
    if (reportRefreshTimer) {
        window.clearTimeout(reportRefreshTimer);
        reportRefreshTimer = null;
    }
    activeReportId = id;
    activeReportStatusSignature = '';
    detailStatusFilter = '';
    detailPriorFilter = '';
    detailEditingItemIds.clear();
    markActiveReport(id);
    if (pushUrl) {
        window.history.replaceState(null, '', `/submission-archive/${encodeURIComponent(id)}`);
    }
    const target = document.getElementById('archive-detail');
    target.innerHTML = '<div class="archive-empty">正在加载…</div>';
    try {
        const report = await api(`/reports/${encodeURIComponent(id)}`);
        const items = report.items || [];
        activeReportItems = items;
        activeReportCompiledDate = report.compiled_date || '';
        activeReportType = report.report_type || '';
        activeReportPriorMatchPending = Boolean(report.prior_match_pending);
        priorMatchPollCount = 0;
        target.innerHTML = `
            <div class="archive-detail-head">
                <div class="archive-detail-head-main">
                    <div class="archive-detail-title-row">
                        ${typePill(report.report_type)}
                        <h2>${dateValue(report.report_date)}</h2>
                    </div>
                    <p class="archive-detail-meta">
                        ${escapeHtml(report.issue_no || '无期号')} · 实际整理 ${dateValue(report.compiled_date)} · 录于 ${dateValue(report.imported_at)}
                    </p>
                    ${detailStats(items)}
                </div>
                <div class="archive-detail-actions" id="archive-detail-actions">${detailHeadActionsHtml(items)}</div>
            </div>
            <div class="archive-detail-items">
                ${items.length ? detailItemsHtml(items) : '<div class="archive-empty">这份报告没有条目。</div>'}
            </div>
        `;
        activeReportStatusSignature = reportStatusSignature(items, activeReportPriorMatchPending);
        // 与 pollReportStatus 同一条件：processing 或已报送判定未结束都继续轮询
        if (items.some(item => item.link_status === 'processing')) {
            scheduleReportStatusPoll(id);
        } else if (report.prior_match_pending && priorMatchPollCount < PRIOR_MATCH_POLL_LIMIT) {
            priorMatchPollCount += 1;
            scheduleReportStatusPoll(id);
        }
    } catch (error) {
        target.innerHTML = `<div class="archive-empty">${escapeHtml(error.message)}</div>`;
    }
}

async function initBrowserView() {
    document.getElementById('archive-type-filter').addEventListener('click', event => {
        const option = event.target.closest('.archive-type-option');
        if (!option) return;
        document.querySelectorAll('.archive-type-option').forEach(node => {
            node.classList.toggle('active', node === option);
        });
        listState.type = option.dataset.type || '';
        loadReportList(false);
    });
    const onDateChange = () => loadReportList(false);
    document.getElementById('archive-date-from').addEventListener('change', onDateChange);
    document.getElementById('archive-date-to').addEventListener('change', onDateChange);
    document.getElementById('archive-list-more').addEventListener('click', () => {
        if (!listState.loading) loadReportList(true);
    });
    document.getElementById('archive-report-list').addEventListener('click', event => {
        const card = event.target.closest('.archive-report-card');
        if (card) selectReport(card.dataset.reportId);
    });
    // 统计 chip 点击筛选：再次点击同一 chip 取消筛选（事件委托，轮询重渲染后仍生效）
    document.getElementById('archive-detail').addEventListener('click', event => {
        const chip = event.target.closest('[data-status-filter]');
        if (!chip) return;
        const value = chip.dataset.statusFilter || '';
        detailStatusFilter = detailStatusFilter === value ? '' : value;
        document.querySelectorAll('#archive-detail [data-status-filter]').forEach(btn => {
            const active = btn.dataset.statusFilter === detailStatusFilter;
            btn.classList.toggle('is-active', active);
            btn.setAttribute('aria-pressed', String(active));
        });
        applyDetailFilter();
    });
    // 已报送/未报送 chip 点击筛选：与 link_status 筛选相互独立、可叠加
    document.getElementById('archive-detail').addEventListener('click', event => {
        const chip = event.target.closest('[data-prior-filter]');
        if (!chip) return;
        const value = chip.dataset.priorFilter || '';
        detailPriorFilter = detailPriorFilter === value ? '' : value;
        document.querySelectorAll('#archive-detail [data-prior-filter]').forEach(btn => {
            const active = btn.dataset.priorFilter === detailPriorFilter;
            btn.classList.toggle('is-active', active);
            btn.setAttribute('aria-pressed', String(active));
        });
        applyDetailFilter();
    });
    // 条目修改：铅笔图标展开/收起单条编辑框，「保存本条」提交（事件委托，重绘后仍生效）
    document.getElementById('archive-detail').addEventListener('click', event => {
        const editBtn = event.target.closest('.archive-item-edit-btn');
        if (editBtn) {
            toggleItemEdit(editBtn);
            return;
        }
        const cancelBtn = event.target.closest('.archive-item-cancel-btn');
        if (cancelBtn) {
            toggleItemEdit(cancelBtn);
            return;
        }
        const saveBtn = event.target.closest('.archive-item-save-btn');
        if (saveBtn) saveItemEdit(saveBtn);
    });
    const items = await loadReportList(false);
    if (initialReportId) {
        if (items.some(item => item.id === initialReportId)) {
            await selectReport(initialReportId, false);
        } else {
            // 详情页直达但报告不在当前筛选结果里：仍然加载详情
            await selectReport(initialReportId, false);
        }
    }
    const notice = sessionStorage.getItem('archiveNotice');
    if (notice) {
        sessionStorage.removeItem('archiveNotice');
        toast(notice);
    }
}

