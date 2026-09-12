// 系统设置页 - 数据源页签：默认视图（已启用/未启用/每日任务分组、来源启停即时保存、
// 需要账号的来源就地展开管理）与排序模式（上移/下移草稿 + 带版本号的保存）。
'use strict';

function moveSourceInDraft(key, delta) {
    const index = state.sourcesDraft.indexOf(key);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= state.sourcesDraft.length) return;
    const next = [...state.sourcesDraft];
    [next[index], next[target]] = [next[target], next[index]];
    state.sourcesDraft = next;
    markDirty('crawl_sources');
    renderSourcesTab();
}

function sourceRequiresAccounts(key) {
    return accountSources().some((source) => source.key === key);
}

// 账号数量徽标只更新文本，不重建列表，避免打断进行中的账号编辑。
function refreshSourcesAccountBadges() {
    elements.panels.sources
        .querySelectorAll('li[data-source] .source-account-badge')
        .forEach((badge) => {
            const item = badge.closest('li[data-source]');
            applyAccountBadge(item, badge, item.dataset.source);
        });
}

function applyAccountBadge(itemEl, badge, sourceKey) {
    const count = state.accountCounts[sourceKey];
    if (!sourceRequiresAccounts(sourceKey)) {
        badge.hidden = true;
        return;
    }
    badge.hidden = false;
    const countText = count === null || count === undefined ? '未知' : String(count);
    const skip = count === 0;
    badge.textContent = skip
        ? '启用账号 0 · 本轮会跳过该来源'
        : `启用账号 ${countText}`;
    badge.classList.toggle('is-warning', skip);
    if (skip) {
        badge.title = '点击展开账号管理';
        badge.dataset.expandAccounts = sourceKey;
    } else {
        badge.removeAttribute('title');
        delete badge.dataset.expandAccounts;
    }
}

function syncSortModeButton() {
    const btn = document.getElementById('btn-sources-sort-mode');
    if (btn) btn.disabled = state.sourceToggleInflight > 0;
}

// 启停请求进行中锁住面板内所有来源开关：第二个请求会基于过期列表计算，
// 且成功后 renderSourcesTab 重建面板会让失败分支的错误写进已销毁的节点。
// 只锁来源行开关；展开区内的账号启停/编辑走独立接口，不受牵连。
function syncSourceToggles() {
    const locked = state.sourceToggleInflight > 0;
    elements.panels.sources
        .querySelectorAll('.source-enabled-toggle')
        .forEach((toggle) => {
            toggle.disabled = locked;
        });
}

// 展开/收起账号管理区：手风琴（同时最多展开一个），切换时重置筛选词与批量粘贴状态，
// 避免上一个来源的预览结果串到下一个。展开本身不发请求，账号已在初始化时缓存。
function collapseSourceExpansion({ updateHash = false } = {}) {
    const had = !!state.accountSource;
    const panel = elements.panels.sources.querySelector('.source-accounts-panel');
    if (panel) panel.remove();
    elements.panels.sources
        .querySelectorAll('.source-expand-toggle[aria-expanded="true"]')
        .forEach((btn) => btn.setAttribute('aria-expanded', 'false'));
    state.accountSource = null;
    state.accountFilter = '';
    resetBulkState();
    if (updateHash && had) {
        writeSettingsHash('sources');
    }
}

function insertAccountsPanel(row, key) {
    const panel = createEl('li', 'source-accounts-panel', '', { dataset: { accountsFor: key } });
    row.after(panel);
    const arrow = row.querySelector('.source-expand-toggle');
    if (arrow) arrow.setAttribute('aria-expanded', 'true');
    renderSourceAccounts(key, panel);
    // 重渲染恢复展开区时还原「添加账号」的开合状态（renderSourcesTab 重建前从 DOM 捕获）
    const addDetails = panel.querySelector('.account-add-details');
    if (addDetails && state.accountAddDetailsOpen) addDetails.open = true;
}

function expandSourceRow(key, { updateHash = true } = {}) {
    if (state.sourcesMode !== 'default') return false;
    collapseSourceExpansion();
    state.accountSource = key;
    state.accountFilter = '';
    state.accountAddDetailsOpen = false;
    resetBulkState();
    const row = elements.panels.sources
        .querySelector(`li[data-source="${key}"]`);
    if (!row || !sourceRequiresAccounts(key)) {
        state.accountSource = null;
        return false;
    }
    insertAccountsPanel(row, key);
    if (updateHash) {
        writeSettingsHash('sources', key);
    }
    return true;
}

function toggleSourceExpand(key) {
    if (state.accountSource === key) {
        collapseSourceExpansion({ updateHash: true });
    } else {
        expandSourceRow(key);
    }
}

// 来源启停即时保存：切换开关即写库（完整有序列表 + 当前版本号），不进草稿。
// 失败时回滚开关视觉状态、行内显示错误、本地列表不动；成功用响应重排。
async function toggleSourceEnabled(key, target, toggle, errorEl) {
    const section = settingsSection('crawl_sources');
    if (!section) {
        toggle.checked = !target;
        return;
    }
    const current = section.value;
    // 停用最后一个启用来源在前端拦截，不发请求
    if (!target && current.includes(key) && current.length <= 1) {
        toggle.checked = true;
        errorEl.textContent = '每小时来源不能为空，请至少保留一个来源。';
        return;
    }
    const next = target
        ? [...current, key]
        : current.filter((item) => item !== key);
    toggle.disabled = true;
    state.sourceToggleInflight += 1;
    syncSourceToggles();
    syncSortModeButton();
    try {
        const { response, payload } = await apiRequest('/api/admin/settings/crawl_sources', {
            method: 'PUT',
            body: { value: next, expected_version: section.version },
        });
        if (response.ok) {
            applySectionItem('crawl_sources', payload.item);
            showSettingsToast(target ? `已启用 ${sourceDisplayName(key)}` : `已停用 ${sourceDisplayName(key)}`);
            renderSourcesTab();
            return;
        }
        toggle.checked = !target;
        if (response.status === 409) {
            errorEl.textContent = `保存冲突：${formatApiError(payload, '配置已在别处被修改')}。`
                + '启停即时生效，本地没有待保留的修改，请载入最新配置后再操作。';
            const reloadBtn = createEl('button', 'btn btn-secondary source-row-reload', '载入最新配置', {
                type: 'button',
            });
            reloadBtn.addEventListener('click', async () => {
                reloadBtn.disabled = true;
                try {
                    await reloadSettingsPayload();
                    renderSourcesTab();
                    showSettingsToast('已载入最新配置');
                } catch (error) {
                    errorEl.textContent = `载入失败：${error.message}`;
                    reloadBtn.disabled = false;
                }
            });
            errorEl.appendChild(reloadBtn);
            return;
        }
        errorEl.textContent = `${target ? '启用' : '停用'}失败：${formatApiError(payload, '请重试')}`;
    } catch (error) {
        toggle.checked = !target;
        errorEl.textContent = `${target ? '启用' : '停用'}失败：${error.message || '网络错误'}`;
    } finally {
        state.sourceToggleInflight -= 1;
        syncSourceToggles();
        toggle.disabled = false;
        syncSortModeButton();
    }
}

function buildSourceRow(key, { index = null, enabled }) {
    const item = createEl('li', 'settings-source-item', '', { dataset: { source: key } });
    if (index !== null) {
        item.appendChild(createEl('span', 'settings-source-index', `${index + 1}`));
    }
    item.appendChild(createEl('span', 'settings-source-name', sourceDisplayName(key)));

    if (sourceRequiresAccounts(key)) {
        const badge = createEl('button', 'source-account-badge', '', { type: 'button' });
        applyAccountBadge(item, badge, key);
        badge.addEventListener('click', () => {
            if (badge.dataset.expandAccounts) {
                expandSourceRow(badge.dataset.expandAccounts);
            }
        });
        item.appendChild(badge);
    }

    const toggle = createEl('input', 'source-enabled-toggle', '', {
        type: 'checkbox',
        'aria-label': `${enabled ? '停用' : '启用'} ${sourceDisplayName(key)}`,
    });
    toggle.checked = enabled;
    // 重渲染发生在启停请求进行中时，新建的行也要处于锁定态
    toggle.disabled = state.sourceToggleInflight > 0;
    const errorEl = createEl('span', 'source-row-error');
    toggle.addEventListener('change', () => {
        errorEl.textContent = '';
        toggleSourceEnabled(key, toggle.checked, toggle, errorEl);
    });
    item.appendChild(toggle);

    if (sourceRequiresAccounts(key)) {
        const arrow = createEl('button', 'source-expand-toggle', '▸', {
            type: 'button',
            'aria-label': `展开 ${sourceDisplayName(key)} 的账号管理`,
            'aria-expanded': 'false',
        });
        arrow.addEventListener('click', () => toggleSourceExpand(key));
        item.appendChild(arrow);
    }

    item.appendChild(errorEl);
    return item;
}

function renderSourcesDefaultView(panel, section) {
    panel.appendChild(createEl('p', 'settings-effect-note', '生效时间：下一轮抓取生效。'));
    panel.appendChild(buildLastModifiedLine(section));

    const sortBtn = createEl('button', 'btn btn-secondary', '调整抓取顺序', {
        id: 'btn-sources-sort-mode',
        type: 'button',
    });
    sortBtn.disabled = state.sourceToggleInflight > 0;
    sortBtn.addEventListener('click', enterSortMode);
    panel.appendChild(sortBtn);

    panel.appendChild(createEl('h3', 'settings-group-heading', '已启用来源（按抓取顺序）'));
    const enabledList = createEl('ul', 'settings-source-list', '', { id: 'sources-hourly-list' });
    section.value.forEach((key, index) => {
        enabledList.appendChild(buildSourceRow(key, { index, enabled: true }));
    });
    if (!section.value.length) {
        enabledList.appendChild(createEl(
            'li',
            'settings-source-empty',
            '每小时来源不能为空，请至少启用一个来源。',
        ));
    }
    panel.appendChild(enabledList);

    panel.appendChild(createEl('h3', 'settings-group-heading', '未启用来源'));
    const availableList = createEl('ul', 'settings-source-list', '', { id: 'sources-available-list' });
    const available = sourceCatalog().filter(
        (item) => !item.daily_only && !section.value.includes(item.key),
    );
    if (!available.length) {
        availableList.appendChild(createEl('li', 'settings-source-empty', '没有可启用的来源。'));
    }
    available.forEach((source) => {
        availableList.appendChild(buildSourceRow(source.key, { enabled: false }));
    });
    panel.appendChild(availableList);

    const dailySources = sourceCatalog().filter((item) => item.daily_only);
    if (dailySources.length) {
        panel.appendChild(createEl('h3', 'settings-group-heading', '每日任务'));
        const dailyList = createEl('ul', 'settings-source-list settings-daily-list', '', {
            id: 'sources-daily-list',
        });
        dailySources.forEach((source) => {
            const item = createEl('li', 'settings-source-item is-readonly', '', {
                dataset: { source: source.key },
            });
            item.appendChild(createEl('span', 'settings-source-name', source.display_name));
            item.appendChild(createEl(
                'span',
                'settings-daily-note',
                '每日单独任务，由服务器计划任务调度',
            ));
            dailyList.appendChild(item);
        });
        panel.appendChild(dailyList);
    }

    // 重新渲染后恢复展开区（例如启停成功后的重排）
    if (state.accountSource) {
        const key = state.accountSource;
        const row = panel.querySelector(`li[data-source="${key}"]`);
        if (row && sourceRequiresAccounts(key)) {
            insertAccountsPanel(row, key);
        } else {
            state.accountSource = null;
        }
    }
}

function enterSortMode() {
    const section = settingsSection('crawl_sources');
    if (!section || state.sourceToggleInflight > 0) return;
    collapseSourceExpansion();
    state.sourcesMode = 'sort';
    state.sourcesDraft = [...section.value];
    renderSourcesTab();
}

function exitSortMode() {
    state.sourcesMode = 'default';
    state.sourcesDraft = null;
}

function buildSortListItem(key, index) {
    const item = createEl('li', 'settings-source-item', '', { dataset: { source: key } });
    item.appendChild(createEl('span', 'settings-source-index', `${index + 1}`));
    item.appendChild(createEl('span', 'settings-source-name', sourceDisplayName(key)));
    const actions = createEl('span', 'settings-source-actions');
    const upBtn = createEl('button', 'btn btn-secondary', '上移', { type: 'button' });
    upBtn.disabled = index === 0;
    upBtn.addEventListener('click', () => moveSourceInDraft(key, -1));
    const downBtn = createEl('button', 'btn btn-secondary', '下移', { type: 'button' });
    downBtn.disabled = index === state.sourcesDraft.length - 1;
    downBtn.addEventListener('click', () => moveSourceInDraft(key, 1));
    actions.appendChild(upBtn);
    actions.appendChild(downBtn);
    item.appendChild(actions);
    return item;
}

function renderSourcesSortMode(panel, section) {
    panel.appendChild(createEl('p', 'settings-effect-note', '生效时间：下一轮抓取生效。'));
    panel.appendChild(buildLastModifiedLine(section));
    panel.appendChild(createEl(
        'p',
        'settings-sources-note',
        '各来源按顺序抓取，并共用每轮的处理上限，排在前面的先消耗额度。',
    ));

    panel.appendChild(createEl('h3', 'settings-group-heading', '每小时来源（按抓取顺序）'));
    const list = createEl('ol', 'settings-source-list', '', { id: 'sources-sort-list' });
    state.sourcesDraft.forEach((key, index) => {
        list.appendChild(buildSortListItem(key, index));
    });
    panel.appendChild(list);

    const saveBar = createEl('div', 'settings-save-bar');
    const saveBtn = createEl('button', 'btn btn-primary', '保存顺序', {
        id: 'btn-sources-save',
        type: 'button',
    });
    const cancelBtn = createEl('button', 'btn btn-secondary', '取消', {
        id: 'btn-sources-cancel',
        type: 'button',
    });
    const reloadBtn = createEl('button', 'btn btn-secondary', '载入最新配置', {
        id: 'btn-sources-reload',
        type: 'button',
    });
    reloadBtn.hidden = true;
    const status = createEl('span', 'settings-save-status', '', { id: 'sources-save-status' });
    saveBar.appendChild(saveBtn);
    saveBar.appendChild(cancelBtn);
    saveBar.appendChild(reloadBtn);
    saveBar.appendChild(status);
    panel.appendChild(saveBar);

    saveBtn.addEventListener('click', async () => {
        await saveSettingsSection('crawl_sources', [...state.sourcesDraft], {
            statusEl: status,
            saveBtn,
            reloadBtn,
            onSaved: () => {
                exitSortMode();
                renderSourcesTab();
                showSettingsToast('数据源配置已保存');
            },
        });
    });
    cancelBtn.addEventListener('click', () => {
        clearDirty('crawl_sources');
        exitSortMode();
        renderSourcesTab();
        showSettingsToast('已放弃修改');
    });
    reloadBtn.addEventListener('click', async () => {
        reloadBtn.disabled = true;
        try {
            await reloadSettingsPayload();
            clearDirty('crawl_sources');
            exitSortMode();
            renderSourcesTab();
            showSettingsToast('已载入最新配置');
        } catch (error) {
            setSettingsStatus(status, `载入失败：${error.message}`, 'error');
        } finally {
            reloadBtn.disabled = false;
        }
    });
}

function renderSourcesTab() {
    const panel = elements.panels.sources;
    // 重建面板前捕获「添加账号」<details> 的开合状态，insertAccountsPanel 恢复展开区时还原
    const openDetails = panel.querySelector('.source-accounts-panel .account-add-details');
    if (openDetails) state.accountAddDetailsOpen = openDetails.open;
    clearEl(panel);
    const section = settingsSection('crawl_sources');
    if (!section) {
        state.accountSource = null;
        renderImportNotice(panel, '数据源配置');
        return;
    }
    if (state.sourcesMode === 'sort' && state.sourcesDraft) {
        renderSourcesSortMode(panel, section);
        return;
    }
    state.sourcesMode = 'default';
    renderSourcesDefaultView(panel, section);
}
