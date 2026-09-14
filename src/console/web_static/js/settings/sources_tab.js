// 系统设置页 - 数据源页签：每小时来源单列表（开关即写库的即时启停、行位置不随启停变化、
// 需要账号的来源就地展开管理）与每日任务只读分组。
'use strict';

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
    const total = state.accountTotals[sourceKey];
    if (!sourceRequiresAccounts(sourceKey)) {
        badge.hidden = true;
        return;
    }
    badge.hidden = false;
    if (count === null || count === undefined || total === null || total === undefined) {
        badge.textContent = '账号数未知';
        badge.classList.remove('is-warning');
        badge.removeAttribute('title');
        delete badge.dataset.expandAccounts;
        return;
    }
    const skip = count === 0;
    badge.textContent = skip
        ? `启用 0 / 共 ${total} · 本轮会跳过该来源`
        : `启用 ${count} / 共 ${total}`;
    badge.classList.toggle('is-warning', skip);
    if (skip) {
        badge.title = '点击展开账号管理';
        badge.dataset.expandAccounts = sourceKey;
    } else {
        badge.removeAttribute('title');
        delete badge.dataset.expandAccounts;
    }
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

// 展开/收起账号管理区：手风琴（同时最多展开一个），切换时重置筛选词、批量粘贴状态
// 与管理态（含待删标记），避免上一个来源的状态串到下一个。展开本身不发请求，
// 账号已在初始化时缓存。
function collapseSourceExpansion({ updateHash = false } = {}) {
    const had = !!state.accountSource;
    const panel = elements.panels.sources.querySelector('.source-accounts-panel');
    if (panel) panel.remove();
    elements.panels.sources
        .querySelectorAll('.source-expand-toggle[aria-expanded="true"]')
        .forEach((btn) => btn.setAttribute('aria-expanded', 'false'));
    state.accountSource = null;
    state.accountFilter = '';
    state.accountManageMode = false;
    state.accountDeleteMarks = [];
    state.accountDeleteSubmitting = false;
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
    // 重渲染恢复展开区时还原「添加账号」的开合状态（renderSourcesTab 重建前从 DOM 捕获）；
    // 管理态下该折叠区被锁定收起，开合状态由退出管理态时恢复，这里不还原
    const addDetails = panel.querySelector('.account-add-details');
    if (addDetails && !state.accountManageMode && state.accountAddDetailsOpen) {
        addDetails.open = true;
    }
}

function expandSourceRow(key, { updateHash = true } = {}) {
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

// 来源启停即时保存：切换开关即写库（按目录序构造的完整启用列表 + 当前版本号），不进草稿。
// 失败时回滚开关视觉状态、行内显示错误、行位置与本地列表不动；成功后整表重渲染，
// 行位置始终由目录序决定，不随启停变化。
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
    // 请求体按目录序构造：与后端规范化结果、页面渲染顺序三者保持一致
    const enabledKeys = new Set(current);
    if (target) {
        enabledKeys.add(key);
    } else {
        enabledKeys.delete(key);
    }
    const next = sourceCatalog()
        .filter((item) => !item.daily_only && enabledKeys.has(item.key))
        .map((item) => item.key);
    toggle.disabled = true;
    state.sourceToggleInflight += 1;
    syncSourceToggles();
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
    }
}

function buildSourceRow(key, { enabled }) {
    const item = createEl('li', 'settings-source-item', '', { dataset: { source: key } });
    // 停用行只做视觉弱化（名称降灰）：行高、内边距与控件位置不变，开关切换时行不位移
    if (!enabled) item.classList.add('is-disabled');

    // 开关打头（与账号表「开关在最前」一致），名称、账号数标签、展开箭头依次在后
    const toggle = createEl('input', 'settings-switch source-enabled-toggle', '', {
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

    if (sourceRequiresAccounts(key)) {
        const arrow = createEl('button', 'source-expand-toggle', '', {
            type: 'button',
            'aria-label': `展开 ${sourceDisplayName(key)} 的账号管理`,
            'aria-expanded': 'false',
        });
        // 三角放在独立 span 里：展开时靠 CSS 旋转 90°（朝右 → 朝下），不转按钮本体
        arrow.appendChild(createEl('span', 'source-expand-icon', '▸', { 'aria-hidden': 'true' }));
        arrow.addEventListener('click', () => toggleSourceExpand(key));
        item.appendChild(arrow);
    }

    item.appendChild(errorEl);
    return item;
}

function renderSourcesPanel(panel, section) {
    panel.appendChild(createEl('p', 'settings-effect-note', '生效时间：下一轮抓取生效。'));
    panel.appendChild(buildLastModifiedLine(section));

    // 单一来源列表：全部非每日来源按 payload 目录序渲染（后端返回的启用列表也是目录序，
    // 前端不再排序），开关状态即启停状态，启停不改变行位置
    panel.appendChild(createEl('h3', 'settings-group-heading', '每小时来源'));
    const list = createEl('ul', 'settings-source-list', '', { id: 'sources-list' });
    sourceCatalog()
        .filter((item) => !item.daily_only)
        .forEach((source) => {
            list.appendChild(buildSourceRow(source.key, {
                enabled: section.value.includes(source.key),
            }));
        });
    panel.appendChild(list);

    // 每日任务独立分组：只读行，由服务器计划任务调度，不在页面上启停
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
            // 只读行没有开关，补一个与开关等宽的空占位，让两组行的名称左边缘对齐
            item.appendChild(createEl('span', 'settings-source-toggle-placeholder'));
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

    // 重新渲染后恢复展开区（例如启停成功后的重建）
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

function renderSourcesTab() {
    const panel = elements.panels.sources;
    // 重建面板前捕获「添加账号」<details> 的开合状态，insertAccountsPanel 恢复展开区时还原；
    // 管理态下折叠区被锁定收起，DOM 上的 false 是被强制的外观，
    // 真实开合状态已在进入管理态时存入 state，这里不能覆盖
    const openDetails = panel.querySelector('.source-accounts-panel .account-add-details');
    if (openDetails && !state.accountManageMode) {
        state.accountAddDetailsOpen = openDetails.open;
    }
    clearEl(panel);
    const section = settingsSection('crawl_sources');
    if (!section) {
        state.accountSource = null;
        renderImportNotice(panel, '数据源配置');
        return;
    }
    renderSourcesPanel(panel, section);
}
