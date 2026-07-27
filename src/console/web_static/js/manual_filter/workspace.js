// Reuse the administrator workspace UI while translating duty-editor requests
// to the existing shift-scoped API.

let dutyWorkspaceItems = new Map();
let dutyWorkspaceClusters = null;

function clearDutyWorkspaceCache() {
    dutyWorkspaceItems = new Map();
    dutyWorkspaceClusters = null;
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
    state.filterPublishedBefore = '';
    state.filterViewMode = 'browse';
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
    await Promise.all([loadStats(), loadFilterCounts()]);
    reloadCurrentTab({ forceClusterRefresh: true });
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

    const initial = shifts[0];
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

function publishedDateInShanghai(item) {
    const value = item.publish_time_iso || (
        item.publish_time ? Number(item.publish_time) * 1000 : null
    );
    if (!value) return '';
    const publishedAt = new Date(value);
    if (Number.isNaN(publishedAt.getTime())) return '';
    const parts = new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit'
    }).formatToParts(publishedAt);
    const fields = Object.fromEntries(parts.map(part => [part.type, part.value]));
    return `${fields.year}-${fields.month}-${fields.day}`;
}

function filterDutyCandidates(items, params) {
    const region = params.get('region');
    const sentiment = params.get('sentiment');
    const query = (params.get('q') || '').trim().toLocaleLowerCase('zh-CN');
    const publishedBefore = params.get('published_before') || '';
    return items.filter(item => {
        if (itemReportType(item) !== 'zongbao') return false;
        if (region && Boolean(item.is_beijing_related) !== (region === 'internal')) return false;
        if (sentiment && item.sentiment_label !== sentiment) return false;
        if (publishedBefore && publishedDateInShanghai(item) >= publishedBefore) return false;
        if (!query) return true;
        const text = [
            item.title,
            item.summary,
            item.llm_summary,
            item.content_markdown
        ].filter(Boolean).join(' ').toLocaleLowerCase('zh-CN');
        return text.includes(query);
    });
}

async function loadDutyClusters() {
    if (dutyWorkspaceClusters) return dutyWorkspaceClusters;
    dutyWorkspaceClusters = window.fetch(`${API_BASE}/clusters?report_type=zongbao`)
        .then(response => {
            if (!response.ok) throw new Error('聚类加载失败');
            return response.json();
        })
        .then(payload => payload.clusters || [])
        .catch(error => {
            dutyWorkspaceClusters = null;
            throw error;
        });
    return dutyWorkspaceClusters;
}

async function dutyCandidatesResponse(params) {
    const filtered = filterDutyCandidates(
        await loadAllDutyItems('pending'),
        params
    );
    const limit = Math.max(1, Math.min(Number(params.get('limit')) || 10, 200));
    const offset = Math.max(0, Number(params.get('offset')) || 0);
    const searchMode = params.get('view_mode') === 'search'
        || Boolean(params.get('q') || params.get('published_before'));
    if (searchMode || params.get('cluster') !== 'true') {
        return workspaceJsonResponse({
            items: filtered.slice(offset, offset + limit),
            total: filtered.length,
            limit,
            offset,
            view_mode: searchMode ? 'search' : 'browse'
        });
    }

    const itemById = new Map(filtered.map(item => [String(item.article_id), item]));
    const used = new Set();
    const clusters = (await loadDutyClusters()).flatMap(cluster => {
        const items = (cluster.item_ids || [])
            .map(articleId => itemById.get(String(articleId)))
            .filter(Boolean);
        if (!items.length) return [];
        items.forEach(item => used.add(String(item.article_id)));
        return [{ ...cluster, items, size: items.length }];
    });
    filtered.forEach(item => {
        if (used.has(String(item.article_id))) return;
        clusters.push({
            cluster_id: `single-${item.article_id}`,
            items: [item],
            size: 1
        });
    });
    return workspaceJsonResponse({
        clusters: clusters.slice(offset, offset + limit),
        total: clusters.length,
        item_total: filtered.length,
        view_mode: 'browse'
    });
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

async function dutyStatsResponse(params) {
    const reportType = params.get('report_type');
    const decisions = ['pending', 'selected', 'backup', 'discarded'];
    const lists = await Promise.all(decisions.map(loadAllDutyItems));
    const counts = Object.fromEntries(decisions.map((decision, index) => [
        decision,
        lists[index].filter(item => !reportType || itemReportType(item) === reportType).length
    ]));
    return workspaceJsonResponse({
        ...counts,
        exported: counts.discarded
    });
}

async function dutyEditResponse(options) {
    clearDutyWorkspaceCache();
    return window.fetch(`${API_BASE}/edit`, options);
}

async function dutyDecideResponse(options) {
    clearDutyWorkspaceCache();
    return window.fetch(`${API_BASE}/decide`, options);
}

async function dutyDiscardBeforeDateResponse(options) {
    const payload = JSON.parse(options.body || '{}');
    const params = new URLSearchParams();
    ['region', 'sentiment', 'q', 'published_before'].forEach(key => {
        if (payload[key]) params.set(key, payload[key]);
    });
    const items = filterDutyCandidates(
        await loadAllDutyItems('pending'),
        params
    );
    if (payload.dry_run) {
        return workspaceJsonResponse({ matched: items.length, updated: 0 });
    }

    const versions = Object.fromEntries(
        items
            .filter(item => Number(item.version) > 0)
            .map(item => [item.article_id, Number(item.version)])
    );
    const response = await window.fetch(`${API_BASE}/decide`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            selected_ids: [],
            backup_ids: [],
            discarded_ids: items.map(item => item.article_id),
            pending_ids: [],
            versions,
            report_type: 'zongbao'
        })
    });
    const result = await response.json().catch(() => ({}));
    clearDutyWorkspaceCache();
    if (!response.ok) {
        return workspaceJsonResponse(result, response.status);
    }
    return workspaceJsonResponse({
        ...result,
        matched: items.length,
        updated: result.discarded || 0
    });
}

async function dutyOrderResponse(options) {
    const payload = JSON.parse(options.body || '{}');
    const response = await window.fetch(`${API_BASE}/order`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            selected_order: payload.selected_order || [],
            backup_order: payload.backup_order || []
        })
    });
    if (response.ok) clearDutyWorkspaceCache();
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
    if (action === '/discarded') return dutyListResponse('discarded', url.searchParams);
    if (action === '/stats') return dutyStatsResponse(url.searchParams);
    if (action === '/edit') return dutyEditResponse(options);
    if (action === '/decide') return dutyDecideResponse(options);
    if (action === '/duplicate-check') return window.fetch(`${API_BASE}/duplicate-check`, options);
    if (action === '/discard_before_date') return dutyDiscardBeforeDateResponse(options);
    if (action === '/order') return dutyOrderResponse(options);
    return workspaceJsonResponse({ detail: '值班账号不能执行此操作' }, 403);
}
