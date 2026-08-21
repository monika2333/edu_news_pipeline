// Duty Summary JS - Utils
function escapeHtml(value) {
    const node = document.createElement('div');
    node.textContent = String(value ?? '');
    return node.innerHTML;
}

function formatDateTime(value) {
    if (!value) return '—';
    return new Intl.DateTimeFormat('zh-CN', {
        timeZone: businessTimeZone,
        month: 'numeric',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
    }).format(new Date(value));
}

function businessDateKey(value) {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    const parts = new Intl.DateTimeFormat('en', {
        timeZone: businessTimeZone,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit'
    }).formatToParts(date);
    const fields = Object.fromEntries(parts.map(part => [part.type, part.value]));
    return `${fields.year}-${fields.month}-${fields.day}`;
}

function normalizeShifts(items) {
    const now = Date.now();
    return (items || [])
        .filter(shift => new Date(shift.starts_at).getTime() <= now)
        .sort((left, right) => (
            new Date(right.ends_at).getTime() - new Date(left.ends_at).getTime()
        ));
}

function chooseInitialShift() {
    const todayKey = businessDateKey(new Date());
    return state.shifts.find(shift => (
        businessDateKey(shift.ends_at) === todayKey
    )) || state.shifts[0] || null;
}

function setShiftPanelOpen(open) {
    state.shiftsOpen = Boolean(open);
    elements.shiftsPanel.hidden = !state.shiftsOpen;
    elements.layout.classList.toggle('is-shifts-collapsed', !state.shiftsOpen);
    elements.shiftsToggle.setAttribute('aria-expanded', String(state.shiftsOpen));
    elements.shiftsToggle.textContent = state.shiftsOpen
        ? '收起历史班次'
        : '查看历史班次';
}

function showToast(message, type = 'success', action = null) {
    showToastAt(elements.toast, message, type, action);
}

function reviewColumnLabel(reportType, status) {
    const reportLabel = reportType === 'wanbao' ? '晚报' : '综报';
    const statusLabels = {
        selected: '采纳',
        backup: '备选',
        pending: '待处理',
        discarded: '放弃',
        exported: '已归档'
    };
    const statusLabel = statusLabels[status] || '待处理';
    return `${reportLabel}${statusLabel}`;
}

function selectedImportTarget() {
    const [reportType, targetStatus] = elements.importTarget.value.split(':');
    return { reportType, targetStatus };
}

function isAdminProcessed(item) {
    if (isRecoveredManualDiscard(item)) return false;
    return Boolean(item.admin_discarded_at)
        || ['selected', 'backup', 'discarded', 'exported'].includes(item?.admin_status);
}

function isRecoveredManualDiscard(item) {
    return !item?.admin_discarded_at && item?.admin_status === 'discarded';
}

function adminProcessLabel(item) {
    if (isRecoveredManualDiscard(item)) return '未处理';
    if (item.admin_discarded_at || item.admin_status === 'discarded') return '已放弃';
    if (item.admin_status === 'selected') return '已采纳';
    if (item.admin_status === 'backup') return '已备选';
    if (item.admin_status === 'exported') return '已归档';
    return '未处理';
}

function articleCategoryLabel(item) {
    const region = item.is_beijing_related ? '京内' : '京外';
    const sentiment = String(item.sentiment_label || '').toLowerCase() === 'negative'
        ? '负面'
        : '正面';
    return `${region}${sentiment}`;
}

function findAdminItem(articleId) {
    return state.items.find(item => item.article_id === articleId) || null;
}

function canUndoAdminProcess(item) {
    if (isRecoveredManualDiscard(item)) return false;
    if (item?.admin_status === 'exported') return false;
    return Boolean(item.admin_discarded_at)
        || ['selected', 'backup', 'discarded'].includes(item?.admin_status);
}

