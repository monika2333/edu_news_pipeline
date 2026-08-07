// Submission Archive JS - Core
const body = document.body;
const view = body.dataset.archiveView;
const initialReportId = body.dataset.reportId || '';
const isAdmin = body.dataset.userRole === 'admin';
const typeLabels = { zongbao: '综报', wanbao: '晚报', feedback: '反馈' };
const linkStatusMeta = {
    processing: { label: '正在判断中', className: 'is-processing' },
    exact: { label: '精确匹配', className: 'is-exact' },
    fuzzy: { label: '模糊匹配', className: 'is-fuzzy' },
    manual: { label: '人工确认', className: 'is-manual' },
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
const linkPill = status => {
    const meta = linkStatusMeta[status] || { label: status || '未知', className: 'is-unmatched' };
    return `<span class="archive-link-pill ${meta.className}">${meta.label}</span>`;
};
const highlight = (text, query) => {
    const escaped = escapeHtml(text);
    if (!query) return escaped;
    const needle = escapeHtml(query).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return escaped.replace(new RegExp(needle, 'gi'), match => `<mark>${match}</mark>`);
};

function toast(message, type = 'success') {
    const target = document.getElementById('archive-toast');
    if (!target) return;
    target.textContent = message;
    target.className = `toast show ${type}`;
    window.setTimeout(() => {
        target.classList.remove('show');
        target.textContent = '';
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

