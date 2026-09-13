// 系统设置页 - 来源账号管理：数据源页签行内展开区的账号列表（启停、删除、名称展示）、
// 单个新增与批量粘贴预览（收进「添加账号」<details>，默认折叠），以及「刷新账号名称」
// 的分批串行刷新。账号名称由后端解析，前端不提供名称输入；所有用户输入经 textContent 渲染。
'use strict';

// 后端 refresh-names 接口单批上限 20（Pydantic max_length），前端切片必须与之对齐
const REFRESH_NAMES_BATCH_SIZE = 20;

const BULK_STATUS_LABELS = {
    addable: '可新增',
    existing_duplicate: '已存在',
    batch_duplicate: '本批内重复',
    invalid: '无法解析',
};

function currentAccountItems() {
    return state.accounts[state.accountSource] || [];
}

function filteredAccountItems() {
    const query = state.accountFilter.trim().toLowerCase();
    if (!query) return currentAccountItems();
    return currentAccountItems().filter((item) => {
        const haystack = [item.display_name, item.normalized_identifier, item.original_input]
            .map((value) => String(value || '').toLowerCase());
        return haystack.some((value) => value.includes(query));
    });
}

function accountRowError(row, message) {
    const errorEl = row.querySelector('.account-row-error');
    errorEl.textContent = message || '';
}

async function toggleAccountEnabled(item, row, toggle) {
    const target = toggle.checked;
    toggle.disabled = true;
    try {
        const { response, payload } = await apiRequest(
            `/api/admin/crawl-accounts/${encodeURIComponent(item.id)}`,
            { method: 'PATCH', body: { enabled: target } },
        );
        if (!response.ok) {
            toggle.checked = !target;
            accountRowError(row, `状态切换失败：${formatApiError(payload, '请重试')}`);
            return;
        }
        item.enabled = !!payload.item.enabled;
        toggle.checked = item.enabled;
        accountRowError(row, '');
        state.accountCounts[item.source] = currentAccountItems()
            .filter((account) => account.enabled).length;
        refreshSourcesAccountBadges();
    } catch (error) {
        toggle.checked = !target;
        accountRowError(row, `状态切换失败：${error.message || '网络错误'}`);
    } finally {
        toggle.disabled = false;
    }
}

let pendingDeleteAccount = null;

function openDeleteAccountModal(item) {
    pendingDeleteAccount = item;
    elements.deleteName.textContent = item.display_name || item.normalized_identifier;
    elements.deleteModal.classList.add('active');
    elements.deleteModal.setAttribute('aria-hidden', 'false');
}

function closeDeleteAccountModal() {
    pendingDeleteAccount = null;
    elements.deleteModal.classList.remove('active');
    elements.deleteModal.setAttribute('aria-hidden', 'true');
}

function bindDeleteAccountModal() {
    elements.deleteCancel.addEventListener('click', closeDeleteAccountModal);
    elements.deleteConfirm.addEventListener('click', async () => {
        const item = pendingDeleteAccount;
        if (!item) return;
        elements.deleteConfirm.disabled = true;
        try {
            const { response, payload } = await apiRequest(
                `/api/admin/crawl-accounts/${encodeURIComponent(item.id)}`,
                { method: 'DELETE' },
            );
            if (!response.ok) {
                showSettingsToast(`删除失败：${formatApiError(payload, '请重试')}`, 'error');
                return;
            }
            closeDeleteAccountModal();
            showSettingsToast('账号已删除');
            await refreshAccountsAndList();
        } catch (error) {
            showSettingsToast(`删除失败：${error.message || '网络错误'}`, 'error');
        } finally {
            elements.deleteConfirm.disabled = false;
        }
    });
}

async function refreshAccountsAndList() {
    const source = state.accountSource;
    if (!source) {
        refreshSourcesAccountBadges();
        return;
    }
    await loadAccountsForSource(source, { force: true });
    // 请求返回时展开区可能已切换到其他来源，不要覆盖别人的列表
    if (state.accountSource !== source) return;
    renderAccountList();
    refreshSourcesAccountBadges();
}

// 名称刷新结果就地落到单行：同步 state 缓存，只重绘该行的名称单元格，
// 不整表重建——重建会打断进行中的启停操作，且会让失败分支的错误写进已销毁的节点。
function applyRefreshedAccount(source, result) {
    const accounts = state.accounts[source];
    if (!accounts) return;
    const item = accounts.find((account) => account.id === result.id);
    if (!item) return;
    if (result.account) {
        Object.assign(item, result.account);
    } else if (result.status === 'failed' && result.error) {
        // 解析失败时后端不返回账号行，失败原因由 error 字段带入失败态标记
        item.display_name = null;
        item.display_name_error = result.error;
    }
    if (state.accountSource !== source) return;
    const row = document.querySelector(`#accounts-body tr[data-account-id="${item.id}"]`);
    if (!row) return;
    renderAccountNameCell(row.querySelector('.account-name-cell'), item);
    const toggle = row.querySelector('.account-enabled-toggle');
    if (toggle) toggle.setAttribute('aria-label', accountToggleLabel(item));
}

// 「刷新账号名称」：全部（或指定）账号按每批最多 20 个切片串行请求，
// skipped（后端 30 秒预算耗尽）的 id 重新排队；某批完全没有推进时停止兜底，
// 否则 skipped 一直回队会成为死循环。切换来源后经 state.accountSource 守卫终止，
// 结果不会写进已不属于当前来源的 DOM。
async function refreshAccountNames(source, ids, button) {
    if (state.accountRefreshInflight) return;
    const queue = [...ids];
    const total = queue.length;
    if (!total) return;
    const statusEl = document.getElementById('accounts-refresh-status');
    const setStatus = (message) => {
        // 切换来源后展开区已销毁，状态节点可能不存在
        if (statusEl && statusEl.isConnected) statusEl.textContent = message;
    };
    state.accountRefreshInflight = true;
    const originalText = button.textContent;
    let processed = 0;
    let resolved = 0;
    let failed = 0;
    let stalled = false;
    button.disabled = true;
    const syncProgress = () => {
        button.textContent = `刷新中… ${processed}/${total}`;
    };
    setStatus('');
    syncProgress();
    try {
        while (queue.length) {
            if (state.accountSource !== source) return;
            const batch = queue.splice(0, REFRESH_NAMES_BATCH_SIZE);
            const { response, payload } = await apiRequest(
                '/api/admin/crawl-accounts/refresh-names',
                { method: 'POST', body: { account_ids: batch } },
            );
            if (state.accountSource !== source) return;
            if (!response.ok) {
                setStatus(`刷新失败：${formatApiError(payload, '请重试')}`);
                return;
            }
            let progressed = 0;
            (payload.items || []).forEach((result) => {
                if (result.status === 'skipped') {
                    queue.push(result.id);
                    return;
                }
                progressed += 1;
                processed += 1;
                if (result.status === 'resolved') resolved += 1;
                if (result.status === 'failed') failed += 1;
                applyRefreshedAccount(source, result);
            });
            syncProgress();
            if (progressed === 0) {
                stalled = true;
                break;
            }
        }
        if (stalled) {
            setStatus('部分账号未能刷新，请稍后重试');
        } else {
            showSettingsToast(`已更新 ${resolved} 个名称，${failed} 个获取失败`);
        }
    } catch (error) {
        if (state.accountSource === source) {
            setStatus(`刷新失败：${error.message || '网络错误'}`);
        }
    } finally {
        state.accountRefreshInflight = false;
        if (button.isConnected) {
            button.disabled = false;
            button.textContent = originalText;
        }
    }
}

// 名称单元格的三种状态：已解析显示名称；未解析显示截断标识（CSS 省略号，
// 完整值放 title）加弱化标记；解析失败时标记变为「名称获取失败」、原因放标记的 title。
// 名称文本一律经 textContent 写入，禁止 innerHTML。
function renderAccountNameCell(cell, item) {
    clearEl(cell);
    const link = createEl('a', 'account-name-link', '', {
        href: item.profile_url,
        target: '_blank',
        rel: 'noopener noreferrer',
    });
    if (item.display_name) {
        link.textContent = item.display_name;
        link.title = '打开主页';
    } else {
        link.classList.add('is-unresolved');
        link.textContent = item.normalized_identifier;
        link.title = item.normalized_identifier;
    }
    cell.appendChild(link);
    if (!item.display_name) {
        const hasError = !!item.display_name_error;
        const badge = createEl('span',
            `account-name-badge${hasError ? ' is-error' : ''}`,
            hasError ? '名称获取失败' : '名称待获取');
        if (hasError) badge.title = item.display_name_error;
        cell.appendChild(badge);
    }
    cell.appendChild(createEl('span', 'account-row-error'));
}

function accountToggleLabel(item) {
    return `启用 ${item.display_name || item.normalized_identifier}`;
}

function buildAccountRow(item) {
    const row = createEl('tr', '', '', { dataset: { accountId: item.id } });

    // 启用开关放每行最前：开关本身说明含义，不再单设带表头的「启用」列
    const enabledCell = createEl('td', 'account-toggle-cell');
    const toggle = createEl('input', 'settings-switch account-enabled-toggle', '', {
        type: 'checkbox',
        'aria-label': accountToggleLabel(item),
    });
    toggle.checked = !!item.enabled;
    toggle.addEventListener('change', () => toggleAccountEnabled(item, row, toggle));
    enabledCell.appendChild(toggle);
    row.appendChild(enabledCell);

    const nameCell = createEl('td', 'account-name-cell');
    renderAccountNameCell(nameCell, item);
    row.appendChild(nameCell);

    const actionCell = createEl('td');
    const deleteBtn = createEl('button', 'btn btn-secondary account-delete-btn', '删除', {
        type: 'button',
    });
    deleteBtn.addEventListener('click', () => openDeleteAccountModal(item));
    actionCell.appendChild(deleteBtn);
    row.appendChild(actionCell);
    return row;
}

function renderAccountList() {
    const tbody = document.getElementById('accounts-body');
    if (!tbody) return;
    clearEl(tbody);
    const items = filteredAccountItems();
    if (!items.length) {
        const empty = createEl('tr');
        empty.appendChild(createEl('td', 'settings-source-empty',
            state.accountFilter ? '没有匹配的账号。' : '该来源还没有抓取账号。', {
                colspan: '3',
            }));
        tbody.appendChild(empty);
        return;
    }
    items.forEach((item) => tbody.appendChild(buildAccountRow(item)));
}

function bulkAddableCount() {
    if (!state.bulk.items || state.bulk.stale) return 0;
    return state.bulk.items.filter((item) => item.status === 'addable').length;
}

function syncBulkConfirmButton() {
    const confirmBtn = document.getElementById('btn-account-bulk-confirm');
    if (!confirmBtn) return;
    const count = bulkAddableCount();
    confirmBtn.textContent = count > 0 ? `确认添加 ${count} 个` : '确认添加';
    confirmBtn.disabled = count === 0;
}

function renderBulkRows(items, { final = false } = {}) {
    const container = document.getElementById('account-bulk-results');
    const summary = document.getElementById('account-bulk-summary');
    if (!container || !summary) return;
    clearEl(container);
    clearEl(summary);
    if (!items) {
        syncBulkConfirmButton();
        return;
    }
    const counts = { addable: 0, existing_duplicate: 0, batch_duplicate: 0, invalid: 0 };
    items.forEach((item) => {
        counts[item.status] = (counts[item.status] || 0) + 1;
    });
    const parts = Object.entries(counts)
        .filter(([, count]) => count > 0)
        .map(([status, count]) => `${BULK_STATUS_LABELS[status]} ${count}`);
    summary.appendChild(createEl(
        'span',
        'account-bulk-counts',
        parts.length ? parts.join(' · ') : '没有可解析的行',
    ));
    if (state.bulk.stale && !final) {
        summary.appendChild(createEl(
            'span',
            'account-bulk-stale',
            '内容已修改，预览结果已作废，请重新预览。',
        ));
    }
    items.forEach((item) => {
        const label = final && item.status === 'addable' && item.account
            ? '已添加'
            : BULK_STATUS_LABELS[item.status] || item.status;
        const row = createEl('li', `account-bulk-row is-${item.status}`);
        row.appendChild(createEl('span', 'account-bulk-input',
            `第 ${item.line_number} 行：${item.input}`));
        const statusEl = createEl('span', 'account-bulk-status', label);
        if (item.error) statusEl.textContent = `${label}：${item.error}`;
        row.appendChild(statusEl);
        container.appendChild(row);
    });
    syncBulkConfirmButton();
}

function resetBulkState() {
    state.bulk = { text: '', items: null, stale: false };
}

async function runBulkPreview(textarea) {
    const text = textarea.value;
    const previewBtn = document.getElementById('btn-account-bulk-preview');
    previewBtn.disabled = true;
    try {
        const { response, payload } = await apiRequest('/api/admin/crawl-accounts/preview', {
            method: 'POST',
            body: { source: state.accountSource, text },
        });
        if (!response.ok) {
            showSettingsToast(`预览失败：${formatApiError(payload, '请重试')}`, 'error');
            return;
        }
        state.bulk = { text, items: payload.items || [], stale: false };
        renderBulkRows(state.bulk.items);
    } catch (error) {
        showSettingsToast(`预览失败：${error.message || '网络错误'}`, 'error');
    } finally {
        previewBtn.disabled = false;
    }
}

async function runBulkConfirm() {
    if (bulkAddableCount() === 0) return;
    const confirmBtn = document.getElementById('btn-account-bulk-confirm');
    confirmBtn.disabled = true;
    try {
        const { response, payload } = await apiRequest('/api/admin/crawl-accounts/bulk', {
            method: 'POST',
            body: { source: state.accountSource, text: state.bulk.text },
        });
        if (!response.ok) {
            showSettingsToast(`批量添加失败：${formatApiError(payload, '请重试')}`, 'error');
            syncBulkConfirmButton();
            return;
        }
        state.bulk.items = payload.items || [];
        // 已提交的结果仅供查看：确认按钮失效，继续编辑需重新预览
        state.bulk.stale = true;
        renderBulkRows(state.bulk.items, { final: true });
        showSettingsToast('批量添加完成，账号名称将自动获取');
        // 批量添加后端不解析名称，对本批新建账号自动触发一次名称刷新（仅新建 id）
        const newIds = state.bulk.items
            .filter((item) => item.status === 'addable' && item.account)
            .map((item) => item.account.id);
        const source = state.accountSource;
        await refreshAccountsAndList();
        if (newIds.length && state.accountSource === source) {
            const refreshBtn = document.getElementById('btn-account-refresh-names');
            if (refreshBtn) await refreshAccountNames(source, newIds, refreshBtn);
        }
    } catch (error) {
        showSettingsToast(`批量添加失败：${error.message || '网络错误'}`, 'error');
        syncBulkConfirmButton();
    }
}

// 渲染某个来源的账号管理展开区。账号列表在页面初始化时已全量缓存，
// 展开本身不发新请求；新增/删除/批量添加后由 refreshAccountsAndList 强制刷新。
function renderSourceAccounts(sourceKey, containerEl) {
    clearEl(containerEl);
    state.accountSource = sourceKey;

    const toolbar = createEl('div', 'accounts-toolbar');
    const filter = createEl('input', 'accounts-filter', '', {
        id: 'accounts-filter',
        type: 'search',
        placeholder: '按名称或链接筛选…',
        'aria-label': '筛选账号',
    });
    filter.value = state.accountFilter;
    filter.addEventListener('input', () => {
        state.accountFilter = filter.value;
        renderAccountList();
    });
    const refreshBtn = createEl('button', 'btn btn-secondary accounts-refresh-btn', '刷新账号名称', {
        id: 'btn-account-refresh-names',
        type: 'button',
    });
    refreshBtn.addEventListener('click', () => {
        const source = state.accountSource;
        if (!source) return;
        const ids = currentAccountItems().map((item) => item.id);
        refreshAccountNames(source, ids, refreshBtn);
    });
    toolbar.appendChild(filter);
    toolbar.appendChild(refreshBtn);
    toolbar.appendChild(createEl('span', 'accounts-refresh-status', '', {
        id: 'accounts-refresh-status',
    }));
    containerEl.appendChild(toolbar);

    const tableWrap = createEl('div', 'admin-table-wrap');
    const table = createEl('table', 'admin-table accounts-table');
    const thead = createEl('thead');
    const headRow = createEl('tr');
    // 首列表头留空：启用开关本身说明含义，不单独占一列表头文字
    ['', '名称', '操作'].forEach((text) => {
        headRow.appendChild(createEl('th', '', text));
    });
    thead.appendChild(headRow);
    table.appendChild(thead);
    table.appendChild(createEl('tbody', '', '', { id: 'accounts-body' }));
    tableWrap.appendChild(table);
    containerEl.appendChild(tableWrap);

    const addDetails = createEl('details', 'account-add-details');
    addDetails.appendChild(createEl('summary', 'account-add-summary', '添加账号'));

    const addBox = createEl('div', 'account-add-box');
    addBox.appendChild(createEl('h3', 'settings-group-heading', '新增账号'));
    const addRow = createEl('div', 'account-add-row');
    const addInput = createEl('input', 'account-add-input', '', {
        id: 'account-add-input',
        type: 'text',
        placeholder: '主页链接或 ID',
        'aria-label': '账号链接或 ID',
    });
    const addBtn = createEl('button', 'btn btn-primary', '添加', {
        id: 'btn-account-add',
        type: 'button',
    });
    addRow.appendChild(addInput);
    addRow.appendChild(addBtn);
    addBox.appendChild(addRow);
    addBox.appendChild(createEl('p', 'account-add-error', '', { id: 'account-add-error' }));
    addDetails.appendChild(addBox);

    addBtn.addEventListener('click', async () => {
        const errorEl = document.getElementById('account-add-error');
        errorEl.textContent = '';
        const text = addInput.value.trim();
        if (!text) {
            errorEl.textContent = '请输入主页链接或 ID。';
            return;
        }
        addBtn.disabled = true;
        try {
            // 名称由后端同步解析，响应里的账号行直接可用，不再额外刷新
            const { response, payload } = await apiRequest('/api/admin/crawl-accounts', {
                method: 'POST',
                body: { source: state.accountSource, text },
            });
            if (!response.ok) {
                errorEl.textContent = formatApiError(payload, '添加失败，请重试');
                return;
            }
            addInput.value = '';
            showSettingsToast('账号已添加');
            await refreshAccountsAndList();
        } catch (error) {
            errorEl.textContent = `添加失败：${error.message || '网络错误'}`;
        } finally {
            addBtn.disabled = false;
        }
    });

    const bulkBox = createEl('div', 'account-bulk-box');
    bulkBox.appendChild(createEl('h3', 'settings-group-heading', '批量粘贴'));
    bulkBox.appendChild(createEl('p', 'settings-bulk-note',
        '每行一个主页链接或 ID；账号名称添加后由系统自动获取。'));
    const textarea = createEl('textarea', 'account-bulk-text', '', {
        id: 'account-bulk-text',
        rows: '5',
        placeholder: '每行一个链接或 ID',
    });
    textarea.addEventListener('input', () => {
        if (!state.bulk.items) return;
        state.bulk.stale = true;
        renderBulkRows(state.bulk.items);
    });
    bulkBox.appendChild(textarea);
    const bulkActions = createEl('div', 'account-bulk-actions');
    const previewBtn = createEl('button', 'btn btn-secondary', '预览', {
        id: 'btn-account-bulk-preview',
        type: 'button',
    });
    const confirmBtn = createEl('button', 'btn btn-primary', '确认添加', {
        id: 'btn-account-bulk-confirm',
        type: 'button',
    });
    confirmBtn.disabled = true;
    previewBtn.addEventListener('click', () => runBulkPreview(textarea));
    confirmBtn.addEventListener('click', runBulkConfirm);
    bulkActions.appendChild(previewBtn);
    bulkActions.appendChild(confirmBtn);
    bulkBox.appendChild(bulkActions);
    bulkBox.appendChild(createEl('p', 'account-bulk-summary', '', { id: 'account-bulk-summary' }));
    bulkBox.appendChild(createEl('ul', 'account-bulk-results', '', { id: 'account-bulk-results' }));
    addDetails.appendChild(bulkBox);
    containerEl.appendChild(addDetails);

    loadAccountsForSource(sourceKey)
        .then(() => {
            // 缓存命中也会异步返回；展开区若已收起/切换则放弃渲染
            if (state.accountSource !== sourceKey) return;
            if (!document.getElementById('accounts-body')) return;
            renderAccountList();
            refreshSourcesAccountBadges();
        })
        .catch((error) => {
            const tbody = document.getElementById('accounts-body');
            if (!tbody) return;
            clearEl(tbody);
            const row = createEl('tr');
            row.appendChild(createEl('td', 'settings-source-empty',
                `账号加载失败：${error.message}`, { colspan: '3' }));
            tbody.appendChild(row);
        });
}
