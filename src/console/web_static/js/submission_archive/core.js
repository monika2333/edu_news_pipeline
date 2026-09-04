// Submission Archive JS - Core
const body = document.body;
const view = body.dataset.archiveView;
const initialReportId = body.dataset.reportId || '';
const isAdminUser = body.dataset.userRole === 'admin';
const typeLabels = { zongbao: '综报', wanbao: '晚报', feedback: '反馈' };
const linkStatusMeta = {
    processing: { label: '正在判断中', className: 'is-processing' },
    matched: { label: '已匹配', className: 'is-linked' },
    pending: { label: '待确认', className: 'is-pending' },
    unmatched: { label: '未覆盖', className: 'is-unmatched' },
    rejected: { label: '已否决', className: 'is-rejected' }
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
// pill 只表示回链状态，不再可点击；查看原文走标题后的「原文」标签（detailOriginalTriggerHtml）
const linkPill = (status) => {
    const meta = linkStatusMeta[status] || { label: status || '未知', className: 'is-unmatched' };
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

function setNavPending(total) {
    const badge = document.getElementById('archive-nav-pending');
    if (!badge) return;
    const count = Number(total) || 0;
    badge.hidden = count <= 0;
    badge.textContent = count > 99 ? '99+' : String(count);
}

async function loadNavPending() {
    try {
        const data = await api('/link-queue?limit=1');
        setNavPending(data.total);
    } catch (error) {
        // 角标失败不影响主流程
    }
}

