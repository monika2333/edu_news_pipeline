// 系统设置页 - 抓取账号页签：来源切换、账号列表（就地改备注、启停、删除）、
// 单个新增与批量粘贴预览。所有用户输入经 textContent 渲染。
'use strict';

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

async function saveAccountName(item, row, input) {
    const next = input.value.trim();
    const previous = item.display_name || '';
    if (next === previous) {
        input.value = previous;
        return;
    }
    input.disabled = true;
    try {
        const { response, payload } = await apiRequest(
            `/api/admin/crawl-accounts/${encodeURIComponent(item.id)}`,
            { method: 'PATCH', body: { display_name: next } },
        );
        if (!response.ok) {
            input.value = previous;
            accountRowError(row, `备注保存失败：${formatApiError(payload, '请重试')}`);
            return;
        }
        item.display_name = payload.item.display_name;
        input.value = payload.item.display_name || '';
        input.dataset.savedValue = payload.item.display_name || '';
        accountRowError(row, '');
        showSettingsToast('备注已保存');
    } catch (error) {
        input.value = previous;
        accountRowError(row, `备注保存失败：${error.message || '网络错误'}`);
    } finally {
        input.disabled = false;
    }
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
    await loadAccountsForSource(state.accountSource, { force: true });
    renderAccountList();
    refreshSourcesAccountBadges();
}

function buildAccountRow(item) {
    const row = createEl('tr', '', '', { dataset: { accountId: item.id } });

    const nameCell = createEl('td', 'account-name-cell');
    const nameInput = createEl('input', 'account-name-input', '', {
        type: 'text',
        'aria-label': '备注名',
        placeholder: '未设置备注名',
    });
    nameInput.value = item.display_name || '';
    nameInput.dataset.savedValue = item.display_name || '';
    nameInput.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            nameInput.blur();
        } else if (event.key === 'Escape') {
            nameInput.value = nameInput.dataset.savedValue;
            nameInput.blur();
        }
    });
    nameInput.addEventListener('blur', () => {
        saveAccountName(item, row, nameInput);
    });
    nameCell.appendChild(nameInput);
    nameCell.appendChild(createEl('span', 'account-row-error'));
    row.appendChild(nameCell);

    row.appendChild(createEl('td', 'account-identifier', item.normalized_identifier));
    row.appendChild(createEl('td', 'account-original', item.original_input));

    const linkCell = createEl('td');
    linkCell.appendChild(createEl('a', 'account-profile-link', '主页', {
        href: item.profile_url,
        target: '_blank',
        rel: 'noopener noreferrer',
    }));
    row.appendChild(linkCell);

    const enabledCell = createEl('td');
    const toggle = createEl('input', 'account-enabled-toggle', '', {
        type: 'checkbox',
        'aria-label': `启用 ${item.display_name || item.normalized_identifier}`,
    });
    toggle.checked = !!item.enabled;
    toggle.addEventListener('change', () => toggleAccountEnabled(item, row, toggle));
    enabledCell.appendChild(toggle);
    row.appendChild(enabledCell);

    const creator = item.created_by_display_name || '未知';
    row.appendChild(createEl(
        'td',
        'account-created',
        `${creator} · ${formatLocalDateTime(item.created_at)}`,
    ));

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
                colspan: '7',
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
        showSettingsToast('批量添加完成，可在列表中补充备注名');
        await refreshAccountsAndList();
    } catch (error) {
        showSettingsToast(`批量添加失败：${error.message || '网络错误'}`, 'error');
        syncBulkConfirmButton();
    }
}

function renderAccountsTab() {
    const panel = elements.panels.accounts;
    clearEl(panel);
    const sources = accountSources();
    if (!sources.length) {
        panel.appendChild(createEl('p', 'settings-source-empty', '没有需要抓取账号的来源。'));
        return;
    }
    if (!state.accountSource || !sources.some((item) => item.key === state.accountSource)) {
        state.accountSource = sources[0].key;
    }

    panel.appendChild(createEl('p', 'settings-effect-note', '生效时间：下一轮抓取生效。'));

    const switcher = createEl('div', 'accounts-source-switch', '', { id: 'accounts-source-switch' });
    sources.forEach((source) => {
        const btn = createEl('button', 'accounts-source-btn', source.display_name, {
            type: 'button',
            dataset: { accountSource: source.key },
        });
        btn.classList.toggle('is-active', source.key === state.accountSource);
        btn.setAttribute('aria-pressed', source.key === state.accountSource ? 'true' : 'false');
        btn.addEventListener('click', () => {
            if (state.accountSource === source.key) return;
            state.accountSource = source.key;
            state.accountFilter = '';
            resetBulkState();
            writeSettingsHash('accounts', source.key);
            renderAccountsTab();
        });
        switcher.appendChild(btn);
    });
    panel.appendChild(switcher);

    const filter = createEl('input', 'accounts-filter', '', {
        id: 'accounts-filter',
        type: 'search',
        placeholder: '按备注名、标识或原始输入筛选…',
        'aria-label': '筛选账号',
    });
    filter.value = state.accountFilter;
    filter.addEventListener('input', () => {
        state.accountFilter = filter.value;
        renderAccountList();
    });
    panel.appendChild(filter);

    const tableWrap = createEl('div', 'admin-table-wrap');
    const table = createEl('table', 'admin-table accounts-table');
    const thead = createEl('thead');
    const headRow = createEl('tr');
    ['备注名', '标识', '原始输入', '主页', '启用', '添加', '操作'].forEach((text) => {
        headRow.appendChild(createEl('th', '', text));
    });
    thead.appendChild(headRow);
    table.appendChild(thead);
    table.appendChild(createEl('tbody', '', '', { id: 'accounts-body' }));
    tableWrap.appendChild(table);
    panel.appendChild(tableWrap);

    const addBox = createEl('div', 'account-add-box');
    addBox.appendChild(createEl('h3', 'settings-group-heading', '新增账号'));
    const addRow = createEl('div', 'account-add-row');
    const addInput = createEl('input', 'account-add-input', '', {
        id: 'account-add-input',
        type: 'text',
        placeholder: '主页链接或 ID',
        'aria-label': '账号链接或 ID',
    });
    const addName = createEl('input', 'account-add-name', '', {
        id: 'account-add-name',
        type: 'text',
        placeholder: '备注名（可选）',
        'aria-label': '备注名（可选）',
    });
    const addBtn = createEl('button', 'btn btn-primary', '添加', {
        id: 'btn-account-add',
        type: 'button',
    });
    addRow.appendChild(addInput);
    addRow.appendChild(addName);
    addRow.appendChild(addBtn);
    addBox.appendChild(addRow);
    addBox.appendChild(createEl('p', 'account-add-error', '', { id: 'account-add-error' }));
    panel.appendChild(addBox);

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
            const body = { source: state.accountSource, text };
            const displayName = addName.value.trim();
            if (displayName) body.display_name = displayName;
            const { response, payload } = await apiRequest('/api/admin/crawl-accounts', {
                method: 'POST',
                body,
            });
            if (!response.ok) {
                errorEl.textContent = formatApiError(payload, '添加失败，请重试');
                return;
            }
            addInput.value = '';
            addName.value = '';
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
        '每行一个主页链接或 ID；批量添加的账号没有备注名，可在列表中补充。'));
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
    panel.appendChild(bulkBox);

    loadAccountsForSource(state.accountSource)
        .then(() => {
            renderAccountList();
            refreshSourcesAccountBadges();
        })
        .catch((error) => {
            const tbody = document.getElementById('accounts-body');
            if (!tbody) return;
            clearEl(tbody);
            const row = createEl('tr');
            row.appendChild(createEl('td', 'settings-source-empty',
                `账号加载失败：${error.message}`, { colspan: '7' }));
            tbody.appendChild(row);
        });
}
