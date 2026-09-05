// Submission Archive JS - Core
const body = document.body;
const view = body.dataset.archiveView;
const initialReportId = body.dataset.reportId || '';
const isAdminUser = body.dataset.userRole === 'admin';
const typeLabels = { zongbao: '综报', wanbao: '晚报', feedback: '反馈' };
// 回链标签只贴给「给不出操作入口」的状态：processing/pending 没有任何操作入口，
// 必须靠标签说明；matched 的状态由标题后的「原文」标签和 meta 的「解绑」表达，
// unmatched/rejected 由 meta 的「手动匹配」表达，再贴文字标签是重复信息。
// 因此这张表只收会渲染的状态，不在表内的一律不渲染（见 linkPill）
const linkStatusMeta = {
    processing: { label: '正在判断中', className: 'is-processing' },
    pending: { label: '待确认', className: 'is-pending' }
};
let parsedState = null;
let previewItems = [];

const escapeHtml = value => String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

const dateValue = value => String(value || '').slice(0, 10);
const shortDate = value => {
    const text = dateValue(value);
    if (!text) return '';
    const [, month, day] = text.split('-');
    return `${Number(month)}月${Number(day)}日`;
};
const scoreValue = value => {
    const score = Number(value);
    return Number.isFinite(score) ? score.toFixed(2) : '-';
};
const typePill = type => `<span class="archive-type-pill is-${escapeHtml(type)}">${typeLabels[type] || escapeHtml(type)}</span>`;
// pill 只表示回链状态，不再可点击；查看原文走标题后的「原文」标签（detailOriginalTriggerHtml）。
// 状态不在 linkStatusMeta 内时返回空串，不做兜底渲染：回链状态在库里是封闭集合，
// 表外取值时静默不渲染比渲染一个没有样式的标签更安全
const linkPill = (status) => {
    const meta = linkStatusMeta[status];
    if (!meta) return '';
    return `<span class="archive-link-pill ${meta.className}">${meta.label}</span>`;
};

// 已报送标签（反馈条目）：与回链 pill 完全独立，不共用 .archive-link-pill 的 is-* 修饰；
// 点击打开命中明细弹窗（prior_matches.js）。「未报送」是否展示由调用方按报告级状态
// 决定（showUnmatched）：已报送判定进行中时条目 prior_match 全为 null，不能误贴
const priorMatchStatusMeta = {
    submitted: { label: '已报送', className: 'is-submitted' },
    suspected: { label: '疑似已报送', className: 'is-suspected' },
    dismissed: { label: '未报送', className: 'is-dismissed' }
};
// 「已报送」分组的共用判定：dismissed（人工判为不是同一条）有命中明细但归入未报送；
// detailStats 计数与 detailPriorGroup 分组必须共用这一处，不能各写一遍布尔表达式
const isPriorSubmitted = item => Boolean(item.prior_match) && item.prior_match.status !== 'dismissed';
const priorMatchPill = (item, { showUnmatched = false } = {}) => {
    const priorMatch = item.prior_match;
    if (!priorMatch) {
        if (!showUnmatched) return '';
        // 无命中的「未报送」是纯展示标签，不可点击（没有命中明细可看）；
        // dismissed 的「未报送」走下方 button 分支，两者不要合并
        return '<span class="archive-prior-match-pill is-unmatched"'
            + ' title="此前未通过综报/晚报报送">未报送</span>';
    }
    const meta = priorMatchStatusMeta[priorMatch.status]
        || { label: priorMatch.status || '未知', className: 'is-suspected' };
    const count = Number(priorMatch.count) || 0;
    const similarity = scoreValue(priorMatch.top_similarity);
    // 人工判定来源只放 title：人工确认的已报送不加角标，dismissed 标明可查看明细
    let title;
    if (priorMatch.decision === 'submitted') {
        title = '人工确认已报送，点击查看明细';
    } else if (priorMatch.decision === 'not_submitted') {
        title = '人工判定为未报送，点击查看明细';
    } else {
        title = count > 1
            ? `命中 ${count} 条更早报送，最高相似度 ${similarity}，点击查看明细`
            : `最高相似度 ${similarity}，点击查看明细`;
    }
    return `<button type="button" class="archive-prior-match-pill ${meta.className}"`
        + ` data-item-id="${escapeHtml(item.id)}" title="${escapeHtml(title)}">${meta.label}</button>`;
};

let archiveToastTimer = null;
function toast(message, type = 'success') {
    const target = document.getElementById('archive-toast');
    if (!target) return;
    if (archiveToastTimer) {
        window.clearTimeout(archiveToastTimer);
        archiveToastTimer = null;
    }
    target.textContent = '';
    const span = document.createElement('span');
    span.textContent = message;
    target.appendChild(span);
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'toast-close';
    closeBtn.title = '关闭提示';
    closeBtn.setAttribute('aria-label', '关闭提示');
    closeBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18" /><path d="m6 6 12 12" /></svg>';
    closeBtn.onclick = () => {
        if (archiveToastTimer) {
            window.clearTimeout(archiveToastTimer);
            archiveToastTimer = null;
        }
        target.classList.remove('show');
    };
    target.appendChild(closeBtn);
    target.className = `toast show ${type}`;
    archiveToastTimer = window.setTimeout(() => {
        target.classList.remove('show');
        target.textContent = '';
        archiveToastTimer = null;
    }, 3500);
}

async function api(path, options = {}) {
    const response = await window.fetch(`/api/submission-archive${path}`, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        const error = new Error(formatApiError(payload, '操作失败'));
        error.status = response.status;
        error.payload = payload;
        throw error;
    }
    return payload;
}

// 全局待确认提示（模板 .archive-nav-pending）：替代已下线的「回链确认」tab 角标。
// 总数为 0 时整体隐藏；大于 0 时渲染指向目标报告详情页的链接
function setNavPending(total, reportId) {
    const hint = document.getElementById('archive-nav-pending');
    if (!hint) return;
    const count = Number(total) || 0;
    hint.hidden = count <= 0;
    if (count > 0) {
        hint.textContent = `待确认回链 ${count}`;
        hint.href = `/submission-archive/${encodeURIComponent(reportId || '')}`;
    }
}

async function loadNavPending() {
    try {
        // limit=1 只为拿总数与第一份待处理报告的 id，不需要完整列表
        const data = await api('/link-queue?limit=1');
        const first = (data.items || [])[0];
        setNavPending(data.total, first ? first.report_id : '');
    } catch (error) {
        // 提示失败不影响主流程
    }
}

