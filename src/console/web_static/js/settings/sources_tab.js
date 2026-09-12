// 系统设置页 - 数据源页签：每小时来源的有序启停、每日任务只读分组、
// 账号数量提醒与带版本号的保存。
'use strict';

function sourcesDraftContains(key) {
    return state.sourcesDraft.includes(key);
}

function moveSourceInDraft(key, delta) {
    const index = state.sourcesDraft.indexOf(key);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= state.sourcesDraft.length) return;
    const next = [...state.sourcesDraft];
    [next[index], next[target]] = [next[target], next[index]];
    state.sourcesDraft = next;
    markDirty('crawl_sources');
    renderSourcesLists();
}

function disableSourceInDraft(key) {
    state.sourcesDraft = state.sourcesDraft.filter((item) => item !== key);
    markDirty('crawl_sources');
    renderSourcesLists();
}

function enableSourceInDraft(key) {
    if (sourcesDraftContains(key)) return;
    state.sourcesDraft = [...state.sourcesDraft, key];
    markDirty('crawl_sources');
    renderSourcesLists();
}

// 账号数量徽标只更新文本，不重建列表，避免清掉未保存的顺序调整。
function refreshSourcesAccountBadges() {
    elements.panels.sources
        .querySelectorAll('#sources-hourly-list li[data-source]')
        .forEach((item) => {
            const badge = item.querySelector('.source-account-badge');
            if (!badge) return;
            applyAccountBadge(item, badge, item.dataset.source);
        });
}

function applyAccountBadge(itemEl, badge, sourceKey) {
    const count = state.accountCounts[sourceKey];
    const needsAccounts = accountSources().some((source) => source.key === sourceKey);
    if (!needsAccounts) {
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
        badge.title = '点击前往抓取账号页签配置';
        badge.dataset.gotoAccounts = sourceKey;
    } else {
        badge.removeAttribute('title');
        delete badge.dataset.gotoAccounts;
    }
}

function buildHourlyListItem(key, index) {
    const item = createEl('li', 'settings-source-item', '', { dataset: { source: key } });
    item.appendChild(createEl('span', 'settings-source-name', sourceDisplayName(key)));
    const badge = createEl('button', 'source-account-badge', '', { type: 'button' });
    applyAccountBadge(item, badge, key);
    badge.addEventListener('click', () => {
        if (badge.dataset.gotoAccounts) {
            activateSettingsTab('accounts', badge.dataset.gotoAccounts);
        }
    });
    item.appendChild(badge);

    const actions = createEl('span', 'settings-source-actions');
    const upBtn = createEl('button', 'btn btn-secondary', '上移', { type: 'button' });
    upBtn.disabled = index === 0;
    upBtn.addEventListener('click', () => moveSourceInDraft(key, -1));
    const downBtn = createEl('button', 'btn btn-secondary', '下移', { type: 'button' });
    downBtn.disabled = index === state.sourcesDraft.length - 1;
    downBtn.addEventListener('click', () => moveSourceInDraft(key, 1));
    const disableBtn = createEl('button', 'btn btn-secondary', '停用', { type: 'button' });
    disableBtn.addEventListener('click', () => disableSourceInDraft(key));
    actions.appendChild(upBtn);
    actions.appendChild(downBtn);
    actions.appendChild(disableBtn);
    item.appendChild(actions);
    return item;
}

function renderSourcesLists() {
    const hourlyList = document.getElementById('sources-hourly-list');
    const availableList = document.getElementById('sources-available-list');
    if (!hourlyList || !availableList) return;
    clearEl(hourlyList);
    state.sourcesDraft.forEach((key, index) => {
        hourlyList.appendChild(buildHourlyListItem(key, index));
    });
    if (!state.sourcesDraft.length) {
        hourlyList.appendChild(createEl(
            'li',
            'settings-source-empty',
            '每小时来源不能为空，请至少保留一个来源。',
        ));
    }

    clearEl(availableList);
    const available = sourceCatalog().filter(
        (item) => !item.daily_only && !sourcesDraftContains(item.key),
    );
    if (!available.length) {
        availableList.appendChild(createEl('li', 'settings-source-empty', '没有可启用的来源。'));
    }
    available.forEach((source) => {
        const item = createEl('li', 'settings-source-item', '', { dataset: { source: source.key } });
        item.appendChild(createEl('span', 'settings-source-name', source.display_name));
        const enableBtn = createEl('button', 'btn btn-secondary source-enable-btn', '启用', { type: 'button' });
        enableBtn.addEventListener('click', () => enableSourceInDraft(source.key));
        item.appendChild(enableBtn);
        availableList.appendChild(item);
    });
}

function renderSourcesTab() {
    const panel = elements.panels.sources;
    clearEl(panel);
    const section = settingsSection('crawl_sources');
    if (!section) {
        renderImportNotice(panel, '数据源配置');
        return;
    }
    state.sourcesDraft = [...section.value];

    panel.appendChild(createEl('p', 'settings-effect-note', '生效时间：下一轮抓取生效。'));
    panel.appendChild(buildLastModifiedLine(section));
    panel.appendChild(createEl(
        'p',
        'settings-sources-note',
        '各来源按顺序抓取，并共用每轮的处理上限，排在前面的先消耗额度。',
    ));

    panel.appendChild(createEl('h3', 'settings-group-heading', '每小时来源（按抓取顺序）'));
    panel.appendChild(createEl('ol', 'settings-source-list', '', { id: 'sources-hourly-list' }));
    panel.appendChild(createEl('h3', 'settings-group-heading', '未启用的来源'));
    panel.appendChild(createEl('ul', 'settings-source-list', '', { id: 'sources-available-list' }));

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

    renderSourcesLists();

    const saveBar = createEl('div', 'settings-save-bar');
    const saveBtn = createEl('button', 'btn btn-primary', '保存数据源配置', {
        id: 'btn-sources-save',
        type: 'button',
    });
    const discardBtn = createEl('button', 'btn btn-secondary', '放弃修改', {
        id: 'btn-sources-discard',
        type: 'button',
    });
    const reloadBtn = createEl('button', 'btn btn-secondary', '载入最新配置', {
        id: 'btn-sources-reload',
        type: 'button',
    });
    reloadBtn.hidden = true;
    const status = createEl('span', 'settings-save-status', '', { id: 'sources-save-status' });
    saveBar.appendChild(saveBtn);
    saveBar.appendChild(discardBtn);
    saveBar.appendChild(reloadBtn);
    saveBar.appendChild(status);
    panel.appendChild(saveBar);

    saveBtn.addEventListener('click', async () => {
        if (!state.sourcesDraft.length) {
            setSettingsStatus(status, '每小时来源不能为空，请先启用至少一个来源。', 'error');
            return;
        }
        await saveSettingsSection('crawl_sources', [...state.sourcesDraft], {
            statusEl: status,
            saveBtn,
            reloadBtn,
            onSaved: () => {
                renderSourcesTab();
                showSettingsToast('数据源配置已保存');
            },
        });
    });
    discardBtn.addEventListener('click', () => {
        clearDirty('crawl_sources');
        renderSourcesTab();
        showSettingsToast('已放弃修改');
    });
    reloadBtn.addEventListener('click', async () => {
        reloadBtn.disabled = true;
        try {
            await reloadSettingsPayload();
            clearDirty('crawl_sources');
            renderSourcesTab();
            showSettingsToast('已载入最新配置');
        } catch (error) {
            setSettingsStatus(status, `载入失败：${error.message}`, 'error');
        } finally {
            reloadBtn.disabled = false;
        }
    });
}
