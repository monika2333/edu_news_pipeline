// Reuse the administrator workspace UI while translating duty-editor requests
// to the existing shift-scoped API.

let dutyWorkspaceItems = new Map();

function invalidateDutyListCache() {
    dutyWorkspaceItems = new Map();
}

function clearDutyWorkspaceCache() {
    invalidateDutyListCache();
}

function workspaceShiftStatusLabel(status) {
    return {
        active: '当前班次',
        upcoming: '未来班次',
        ended: '历史班次',
        cancelled: '已取消'
    }[status] || status || '';
}

function compareWorkspaceShifts(left, right) {
    const priority = { active: 0, upcoming: 1, ended: 2 };
    const priorityDifference = (priority[left.status] ?? 3) - (priority[right.status] ?? 3);
    if (priorityDifference) return priorityDifference;
    const leftTime = new Date(left.starts_at).getTime();
    const rightTime = new Date(right.starts_at).getTime();
    return left.status === 'ended' ? rightTime - leftTime : leftTime - rightTime;
}

function chooseInitialWorkspaceShift(shifts) {
    return shifts.find(shift => shift.status === 'active')
        || shifts.find(shift => shift.status === 'ended')
        || shifts.find(shift => shift.status === 'upcoming')
        || shifts[0]
        || null;
}

function escapeWorkspaceHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function setDutyWorkspaceShift(shift) {
    const coverage = document.getElementById('workspace-shift-coverage');
    clearDutyWorkspaceCache();
    if (!shift) {
        API_BASE = '';
        if (coverage) coverage.textContent = '暂无可用班次，请联系管理员检查排班。';
        return;
    }
    API_BASE = `/api/duty/shifts/${encodeURIComponent(shift.id)}`;
    if (coverage) {
        coverage.textContent = `${workspaceShiftStatusLabel(shift.status)} · ${window.formatDutyShiftDate(shift.ends_at)}`;
    }
}

function resetWorkspaceViewState() {
    state.filterPage = 1;
    state.reviewPage = 1;
    state.discardPage = 1;
    state.filterQuery = '';
    state.discardQuery = '';
    state.filterViewMode = 'browse';
    state.latestIngestedAt = null;
    state.reviewData = { selected: [], backup: [] };
    state.filterCounts = {
        internal_positive: 0,
        internal_negative: 0,
        external_positive: 0,
        external_negative: 0
    };
}

async function reloadDutyWorkspace() {
    resetWorkspaceViewState();
    syncFilterToolbarState();
    await Promise.all([loadStats(), loadFilterCounts()]);
    reloadCurrentTab();
}

async function prepareManualFilterWorkspace() {
    if (!IS_DUTY_WORKSPACE) return true;
    const select = document.getElementById('workspace-shift-select');
    const response = await window.fetch('/api/duty/shifts');
    if (!response.ok) throw new Error('班次加载失败');

    const payload = await response.json();
    const shifts = (payload.items || [])
        .filter(shift => shift.status !== 'cancelled')
        .sort(compareWorkspaceShifts);
    if (!shifts.length) {
        if (select) {
            select.innerHTML = '<option>暂无可用班次</option>';
            select.disabled = true;
        }
        setDutyWorkspaceShift(null);
        return false;
    }

    const initial = chooseInitialWorkspaceShift(shifts);
    if (select) {
        select.innerHTML = shifts.map(shift => {
            const label = `${window.formatDutyShiftDate(shift.ends_at)} · ${workspaceShiftStatusLabel(shift.status)}`;
            return `<option value="${escapeWorkspaceHtml(shift.id)}">${escapeWorkspaceHtml(label)}</option>`;
        }).join('');
        select.value = initial.id;
        select.addEventListener('change', async () => {
            setDutyWorkspaceShift(shifts.find(shift => shift.id === select.value));
            await reloadDutyWorkspace();
        });
    }
    setDutyWorkspaceShift(initial);
    return true;
}

function workspaceJsonResponse(payload, status = 200) {
    return new Response(JSON.stringify(payload), {
        status,
        headers: { 'Content-Type': 'application/json' }
    });
}

async function loadAllDutyItems(decision) {
    if (dutyWorkspaceItems.has(decision)) return dutyWorkspaceItems.get(decision);
    const request = (async () => {
        const items = [];
        let offset = 0;
        let total = 0;
        do {
            const resource = decision === 'pending' ? 'candidates' : 'reviews';
            const params = new URLSearchParams({ limit: '200', offset: String(offset) });
            if (resource === 'reviews') params.set('decision', decision);
            const response = await window.fetch(`${API_BASE}/${resource}?${params.toString()}`);
            if (!response.ok) throw new Error('值班数据加载失败');
            const page = await response.json();
            items.push(...(page.items || []));
            total = Number(page.total) || 0;
            offset = items.length;
        } while (offset < total);
        return items;
    })();
    dutyWorkspaceItems.set(decision, request);
    try {
        return await request;
    } catch (error) {
        dutyWorkspaceItems.delete(decision);
        throw error;
    }
}

function itemReportType(item) {
    return item.report_type === 'wanbao' ? 'wanbao' : 'zongbao';
}

function dutyCandidateBackendParams(params, limit, offset) {
    const backendParams = new URLSearchParams({
        limit: String(limit),
        offset: String(offset),
        report_type: 'zongbao'
    });
    ['region', 'sentiment', 'q', 'created_before'].forEach(key => {
        const value = params.get(key);
        if (value) backendParams.set(key, value);
    });
    return backendParams;
}

async function dutyCandidatesResponse(params) {
    const limit = Math.max(1, Math.min(Number(params.get('limit')) || 10, 200));
    const offset = Math.max(0, Number(params.get('offset')) || 0);
    const searchMode = params.get('view_mode') === 'search'
        || Boolean(params.get('q'));
    if (searchMode || params.get('cluster') !== 'true') {
        const backendParams = dutyCandidateBackendParams(params, limit, offset);
        return window.fetch(`${API_BASE}/candidates?${backendParams.toString()}`);
    }

    const clusterParams = new URLSearchParams({
        report_type: 'zongbao',
        limit: String(limit),
        offset: String(offset),
        include_items: 'true'
    });
    ['region', 'sentiment', 'force_refresh'].forEach(key => {
        const value = params.get(key);
        if (value) clusterParams.set(key, value);
    });
    return window.fetch(`${API_BASE}/clusters?${clusterParams.toString()}`);
}

async function dutyListResponse(decision, params) {
    const reportType = params.get('report_type');
    const items = (await loadAllDutyItems(decision))
        .filter(item => !reportType || itemReportType(item) === reportType);
    const limit = Math.max(1, Math.min(Number(params.get('limit')) || 30, 200));
    const offset = Math.max(0, Number(params.get('offset')) || 0);
    return workspaceJsonResponse({
        items: items.slice(offset, offset + limit),
        total: items.length,
        limit,
        offset
    });
}

async function dutyEditResponse(options) {
    const response = await window.fetch(`${API_BASE}/edit`, options);
    if (response.ok) invalidateDutyListCache();
    return response;
}

async function dutyDecideResponse(options) {
    const response = await window.fetch(`${API_BASE}/decide`, options);
    if (response.ok) invalidateDutyListCache();
    return response;
}

async function dutyOrderResponse(options) {
    const payload = JSON.parse(options.body || '{}');
    const response = await window.fetch(`${API_BASE}/order`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            selected_order: payload.selected_order || [],
            backup_order: payload.backup_order || [],
            group_orders: payload.group_orders || {}
        })
    });
    if (response.ok) invalidateDutyListCache();
    return response;
}

async function workspaceFetch(input, options = {}) {
    if (!IS_DUTY_WORKSPACE) return window.fetch(input, options);
    const url = new URL(String(input), window.location.href);
    if (!API_BASE || !url.pathname.startsWith(`${API_BASE}/`)) {
        return window.fetch(input, options);
    }

    const action = url.pathname.slice(API_BASE.length);
    if (action === '/candidates') return dutyCandidatesResponse(url.searchParams);
    if (action === '/review') {
        return dutyListResponse(url.searchParams.get('decision') || 'selected', url.searchParams);
    }
    if (action === '/discarded') {
        const query = (url.searchParams.get('q') || '').trim();
        if (query) {
            const limit = Math.max(1, Math.min(Number(url.searchParams.get('limit')) || 30, 200));
            const offset = Math.max(0, Number(url.searchParams.get('offset')) || 0);
            const backendParams = new URLSearchParams({
                decision: 'discarded',
                limit: String(limit),
                offset: String(offset),
                q: query
            });
            return window.fetch(`${API_BASE}/reviews?${backendParams.toString()}`);
        }
        return dutyListResponse('discarded', url.searchParams);
    }
    if (action === '/stats') return window.fetch(`${API_BASE}/stats${url.search}`, options);
    if (action === '/score-feedback' || action === '/score-feedback/clear') {
        return window.fetch(`${API_BASE}${action}`, options);
    }
    if (action === '/edit') return dutyEditResponse(options);
    if (action === '/decide') return dutyDecideResponse(options);
    if (action === '/duplicate-check') return window.fetch(`${API_BASE}/duplicate-check`, options);
    if (action === '/bulk-discard') return window.fetch(`${API_BASE}/bulk-discard`, options);
    if (action === '/finalizations' || action.startsWith('/finalizations/')) {
        return window.fetch(`${API_BASE}${action}${url.search}`, options);
    }
    if (action === '/order') return dutyOrderResponse(options);
    return workspaceJsonResponse({ detail: '值班账号不能执行此操作' }, 403);
}
