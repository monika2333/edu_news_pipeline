// 系统设置页 - 数据源页签：顶部吸顶操作条（页面级管理模式开关 + 跨来源待删除提交条）、
// 每小时来源三列网格（需要账号的来源行与其账号面板占满整行，无子账号的来源各占一列）、
// 开关即写库的即时启停（行位置不随启停变化）与每日任务只读分组。
'use strict';

function sourceRequiresAccounts(key) {
    return accountSources().some((source) => source.key === key);
}

// 账号数量徽标只更新文本，不重建列表，避免打断进行中的账号操作。
function refreshSourcesAccountBadges() {
    elements.panels.sources
        .querySelectorAll('li[data-source] .source-account-badge')
        .forEach((badge) => {
            const item = badge.closest('li[data-source]');
            applyAccountBadge(item, badge, item.dataset.source);
        });
}

// 徽标是纯展示（<span>），不再承担「点击展开」：账号面板始终平铺，无处可展开
function applyAccountBadge(itemEl, badge, sourceKey) {
    const count = state.accountCounts[sourceKey];
    const total = state.accountTotals[sourceKey];
    if (count === null || count === undefined || total === null || total === undefined) {
        badge.textContent = '账号数未知';
        badge.classList.remove('is-warning');
        return;
    }
    const skip = count === 0;
    badge.textContent = skip
        ? `启用 0 / 共 ${total} · 本轮会跳过该来源`
        : `启用 ${count} / 共 ${total}`;
    badge.classList.toggle('is-warning', skip);
}

// 启停请求进行中锁住面板内所有来源开关：第二个请求会基于过期列表计算，
// 且成功后 renderSourcesTab 重建面板会让失败分支的错误写进已销毁的节点。
// 只锁来源行开关；账号芯片的启停走独立接口，不受牵连。
function syncSourceToggles() {
    const locked = state.sourceToggleInflight > 0;
    elements.panels.sources
        .querySelectorAll('.source-enabled-toggle')
        .forEach((toggle) => {
            toggle.disabled = locked;
        });
}

// 面板的作用域锚点：面板内不使用唯一 id，所有查找都以面板为作用域
function accountPanelEl(source) {
    return elements.panels.sources
        .querySelector(`.source-accounts-panel[data-accounts-for="${source}"]`);
}

// 面板是否存在于 DOM：账号面板随来源行始终渲染（没有展开/收起），但来源启停成功后的
// 整块重建会暂时移除面板节点，进行中的异步流程据此跳过写入已销毁的节点
function isAccountPanelOpen(source) {
    return !!accountPanelEl(source);
}

// 账号面板作为来源行之后的兄弟 <li> 紧随插入，随来源行始终存在
function insertAccountsPanel(row, key) {
    const panel = createEl('li', 'source-accounts-panel', '', { dataset: { accountsFor: key } });
    row.after(panel);
    renderSourceAccounts(key, panel);
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

    // 开关打头（与账号芯片「开关在最前」一致），名称、账号数徽标依次在后；
    // 「刷新账号名称」按钮仅管理模式下出现，排在徽标之后
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
        // 需要账号的来源行与其账号面板在三列网格中占满整行；无子账号的来源行各占一列。
        // 判定依据是 requires_accounts，不按具体来源硬编码
        item.classList.add('has-accounts');
        const badge = createEl('span', 'source-account-badge');
        applyAccountBadge(item, badge, key);
        item.appendChild(badge);
        if (state.manageMode) {
            const panelState = state.accountPanels[key];
            const refreshBtn = createEl('button', 'btn btn-secondary accounts-refresh-btn',
                '刷新账号名称', { type: 'button' });
            refreshBtn.disabled = state.deleteSubmitting
                || !!(panelState && panelState.refreshInflight);
            refreshBtn.addEventListener('click', () => {
                const ids = currentAccountItems(key).map((account) => account.id);
                refreshAccountNames(key, ids, refreshBtn);
            });
            item.appendChild(refreshBtn);
        }
    }

    item.appendChild(errorEl);
    return item;
}

// 页面级操作条：吸顶固定在数据源面板顶部（平铺后页面变长，开关必须始终够得着），
// 左侧「数据源」，右侧「管理模式」开关；待删除提交条出现在其下方，跟着一起固定
function buildSourcesActionBar() {
    const bar = createEl('div', 'sources-action-bar');
    const head = createEl('div', 'sources-action-head');
    head.appendChild(createEl('span', 'sources-action-title', '数据源'));
    const manageBtn = createEl('button', 'btn btn-secondary accounts-manage-btn', '管理模式', {
        type: 'button',
        'aria-pressed': state.manageMode ? 'true' : 'false',
    });
    manageBtn.addEventListener('click', () => setManageMode(!state.manageMode));
    head.appendChild(manageBtn);
    bar.appendChild(head);
    bar.appendChild(createEl('div', 'accounts-delete-bar-wrap'));
    return bar;
}

function renderSourcesPanel(panel, section) {
    panel.appendChild(buildSourcesActionBar());
    syncManageBar();

    panel.appendChild(createEl('p', 'settings-effect-note', '生效时间：下一轮抓取生效。'));
    panel.appendChild(buildLastModifiedLine(section));

    // 单一来源列表：全部非每日来源按 payload 目录序渲染（后端返回的启用列表也是目录序，
    // 前端不再排序），开关状态即启停状态，启停不改变行位置。
    // 需要账号的来源，其账号面板作为紧随的兄弟 <li> 始终平铺渲染
    panel.appendChild(createEl('h3', 'settings-group-heading', '每小时来源'));
    const list = createEl('ul', 'settings-source-list', '', { id: 'sources-list' });
    sourceCatalog()
        .filter((item) => !item.daily_only)
        .forEach((source) => {
            const row = buildSourceRow(source.key, {
                enabled: section.value.includes(source.key),
            });
            list.appendChild(row);
            if (sourceRequiresAccounts(source.key)) {
                insertAccountsPanel(row, source.key);
            }
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
}

// 整块重建只读 state，不写回：页面级管理模式、全部待删标记与各来源进行中的
// 名称刷新状态在重建后自然落回 DOM
function renderSourcesTab() {
    const panel = elements.panels.sources;
    clearEl(panel);
    const section = settingsSection('crawl_sources');
    if (!section) {
        state.accountPanels = {};
        renderImportNotice(panel, '数据源配置');
        return;
    }
    renderSourcesPanel(panel, section);
}
