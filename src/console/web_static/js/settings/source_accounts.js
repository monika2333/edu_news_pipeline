// 系统设置页 - 来源账号管理：数据源页签展开面板里的账号芯片网格。
// 芯片本身就是启用开关，同时常驻「打开主页」↗ 与「标记删除」×；没有页面级
// 管理模式——展开某个来源即进入该来源的编辑面。删除为「面板内标记 + 确认条」，
// 确认条只处理本来源的标记。芯片网格末尾的虚线「＋ 添加账号」就地输入，
// 「刷新账号名称」是来源行内小按钮，仅账号面板展开时可见。
// 添加请求本身不解析名称（头条解析要起无头浏览器，不能让添加卡着等）：
// 成功后芯片先带「名称解析中」出现，前端按来源串行补发 refresh-names，
// 结果就地落回芯片（见 queueNewAccountNameResolution）。
// 账号名称由后端解析，前端不提供名称输入；所有用户输入经 textContent 渲染。
// 面板内 DOM 不发唯一 id，一律 class + 面板作用域查询（锚点是面板根的
// data-accounts-for）。
'use strict';

// 后端 refresh-names 接口单批上限 20（Pydantic max_length），前端切片必须与之对齐
const REFRESH_NAMES_BATCH_SIZE = 20;

// 新增账号的自动解析失败时弹 toast：具体原因写在面板错误行与失败徽章的 title 里，
// toast 只负责「失败了、去哪重试」，避免长错误串在全局提示里放不下
const NAME_RESOLVE_FAILED_TOAST = '账号名称获取失败，可点击「刷新账号名称」重试';

// 面板 UI 状态按来源隔离、懒创建，只剩 refreshInflight 一项（名称刷新按钮是按来源的）；
// 管理模式、待删标记、删除提交都是页面级 state（见 core.js）
function accountPanelState(source) {
    if (!state.accountPanels[source]) {
        state.accountPanels[source] = { refreshInflight: false };
    }
    return state.accountPanels[source];
}

function currentAccountItems(source) {
    return state.accounts[source] || [];
}

function accountDisplayName(item) {
    return item.display_name || item.normalized_identifier;
}

// 芯片区下方的共享错误行（每个面板各一个）：表格行内错误在芯片布局下没有落点，
// 改为显示最近一次失败的原因，下一次操作成功时清空。
function accountPanelError(source, message) {
    const panel = accountPanelEl(source);
    const errorEl = panel && panel.querySelector('.accounts-panel-error');
    if (errorEl) errorEl.textContent = message || '';
}

async function toggleAccountEnabled(item, toggle) {
    const target = toggle.checked;
    toggle.disabled = true;
    try {
        const { response, payload } = await apiRequest(
            `/api/admin/crawl-accounts/${encodeURIComponent(item.id)}`,
            { method: 'PATCH', body: { enabled: target } },
        );
        if (!response.ok) {
            toggle.checked = !target;
            accountPanelError(item.source, `状态切换失败：${formatApiError(payload, '请重试')}`);
            return;
        }
        item.enabled = !!payload.item.enabled;
        toggle.checked = item.enabled;
        accountPanelError(item.source, '');
        state.accountCounts[item.source] = currentAccountItems(item.source)
            .filter((account) => account.enabled).length;
        refreshSourcesAccountBadges();
    } catch (error) {
        toggle.checked = !target;
        accountPanelError(item.source, `状态切换失败：${error.message || '网络错误'}`);
    } finally {
        toggle.disabled = false;
    }
}

function isAccountMarked(source, id) {
    return state.deleteMarks.some((mark) => mark.source === source && mark.id === id);
}

function panelDeleteMarks(source) {
    return state.deleteMarks.filter((mark) => mark.source === source);
}

// 标记/撤回都只动 state 与前端外观，不发请求；撤销必须是零成本的。
// 重绘芯片网格 + 本面板的确认条，其他来源的标记不受影响
function toggleAccountDeleteMark(item) {
    if (state.deleteSubmitting) return;
    const index = state.deleteMarks
        .findIndex((mark) => mark.source === item.source && mark.id === item.id);
    if (index >= 0) {
        state.deleteMarks.splice(index, 1);
    } else {
        state.deleteMarks.push({ id: item.id, source: item.source });
    }
    renderAccountList(item.source);
    syncPanelDeleteBar(item.source);
}

// 账号面板底部的确认条：只统计本来源的标记，取消/确认都只作用于本面板。
// 没有标记时不渲染任何东西，展开面板保持安静
function syncPanelDeleteBar(source) {
    const panel = accountPanelEl(source);
    const wrap = panel && panel.querySelector('.accounts-delete-bar-wrap');
    if (!wrap) return;
    clearEl(wrap);
    const marks = panelDeleteMarks(source);
    if (!marks.length) return;
    const bar = createEl('div', 'accounts-delete-bar');
    bar.appendChild(createEl('span', 'accounts-delete-count',
        `将删除 ${marks.length} 个账号`));
    const cancelBtn = createEl('button', 'btn btn-secondary btn-account-delete-cancel', '取消', {
        type: 'button',
    });
    cancelBtn.disabled = state.deleteSubmitting;
    cancelBtn.addEventListener('click', () => {
        state.deleteMarks = state.deleteMarks.filter((mark) => mark.source !== source);
        renderAccountList(source);
        syncPanelDeleteBar(source);
    });
    const confirmBtn = createEl('button', 'btn admin-confirm-delete btn-account-delete-confirm',
        '确认删除', { type: 'button' });
    confirmBtn.disabled = state.deleteSubmitting;
    confirmBtn.addEventListener('click', () => submitAccountDeletions(source));
    bar.appendChild(cancelBtn);
    bar.appendChild(confirmBtn);
    wrap.appendChild(bar);
}

// 全部来源行内的「刷新账号名称」按钮统一同步禁用态：
// 各自的 refreshInflight 之外，删除提交进行中全部置灰（唯一的互斥）
function syncRefreshButtons() {
    elements.panels.sources
        .querySelectorAll('li[data-source] .accounts-refresh-btn')
        .forEach((btn) => {
            const key = btn.closest('li[data-source]').dataset.source;
            const panelState = state.accountPanels[key];
            btn.disabled = state.deleteSubmitting || !!(panelState && panelState.refreshInflight);
        });
}

// 删除/新增按钮的全局锁定：删除提交进行中，所有面板的删除与添加都不可点
function setAccountDeleteControlsDisabled(disabled) {
    const panel = elements.panels.sources;
    if (!panel) return;
    panel.querySelectorAll('.account-chip-delete, .account-add-chip')
        .forEach((btn) => { btn.disabled = disabled; });
}

// 确认删除：只处理本来源的标记，按标记顺序串行发 DELETE——串行而非并发，
// 保证请求顺序确定、失败归属清晰。404 视为成功：前端标记与库中实际状态可能
// 不同步（另一处已删除），此时目的已经达成。部分失败不回滚已成功的删除：
// 失败的标记保留，面板确认条继续显示剩余数量，可重试。
async function submitAccountDeletions(source) {
    if (state.deleteSubmitting) return;
    const marks = panelDeleteMarks(source);
    if (!marks.length) return;
    const panel = accountPanelEl(source);
    const confirmBtn = panel && panel.querySelector('.btn-account-delete-confirm');
    const cancelBtn = panel && panel.querySelector('.btn-account-delete-cancel');
    state.deleteSubmitting = true;
    if (confirmBtn) confirmBtn.disabled = true;
    if (cancelBtn) cancelBtn.disabled = true;
    syncRefreshButtons();
    setAccountDeleteControlsDisabled(true);
    let succeeded = 0;
    const failures = [];
    let processed = 0;
    try {
        for (const mark of marks) {
            processed += 1;
            // 来源启停成功后的整块重建随时可能发生：进度文案只写回仍在文档里的按钮
            if (confirmBtn && confirmBtn.isConnected) {
                confirmBtn.textContent = `删除中… ${processed}/${marks.length}`;
            }
            try {
                const { response, payload } = await apiRequest(
                    `/api/admin/crawl-accounts/${encodeURIComponent(mark.id)}`,
                    { method: 'DELETE' },
                );
                if (response.ok || response.status === 404) {
                    succeeded += 1;
                    state.deleteMarks = state.deleteMarks
                        .filter((m) => !(m.id === mark.id && m.source === mark.source));
                } else {
                    failures.push({ source: mark.source, message: formatApiError(payload, '请重试') });
                }
            } catch (error) {
                failures.push({ source: mark.source, message: error.message || '网络错误' });
            }
        }
    } finally {
        state.deleteSubmitting = false;
        syncRefreshButtons();
        setAccountDeleteControlsDisabled(false);
    }
    // 重新拉取账号并同步来源行徽标；renderAccountList 会一并刷新本面板确认条
    await refreshAccountsAndList(source);
    if (!failures.length) {
        state.deleteMarks = state.deleteMarks.filter((mark) => mark.source !== source);
        renderAccountList(source);
        showSettingsToast(`已删除 ${succeeded} 个账号`);
        return;
    }
    const message = `已删除 ${succeeded} 个，${failures.length} 个失败：${failures[0].message}`;
    renderAccountList(source);
    accountPanelError(source, message);
    // toast 是全局的，不受面板重建影响
    showSettingsToast(message, 'error');
}

// 删除/新增后重新拉取账号并同步来源行徽标。只有写回面板 DOM 的部分需要
// 面板存在性守卫（整块重建的间隙面板暂时不存在），缓存与徽标无条件刷新。
async function refreshAccountsAndList(source) {
    if (!source) {
        refreshSourcesAccountBadges();
        return;
    }
    await loadAccountsForSource(source, { force: true });
    if (hasAccountPanel(source)) renderAccountList(source);
    refreshSourcesAccountBadges();
}

// 名称刷新结果就地落到单个芯片：同步 state 缓存，只重绘该芯片的名称区，
// 不整块重建——重建会丢掉该芯片上共存的管理态标记外观。is-marked 与
// 操作节点都不在这里触碰，标记外观自然保持。
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
    const panel = accountPanelEl(source);
    if (!panel) return;
    const chip = panel.querySelector(`.account-chip[data-account-id="${item.id}"]`);
    if (!chip) return;
    chip.classList.toggle('is-unresolved', !item.display_name);
    renderChipNameContent(chip.querySelector('.account-chip-label'), item);
    const toggle = chip.querySelector('.account-enabled-toggle');
    if (toggle) toggle.setAttribute('aria-label', accountToggleLabel(item));
    const openLink = chip.querySelector('.account-chip-open');
    if (openLink) {
        openLink.setAttribute('aria-label', `打开 ${accountDisplayName(item)} 的主页`);
    }
    const deleteBtn = chip.querySelector('.account-chip-delete');
    if (deleteBtn) {
        const marked = isAccountMarked(source, item.id);
        deleteBtn.setAttribute('aria-label', marked
            ? `撤回删除 ${accountDisplayName(item)}`
            : `删除 ${accountDisplayName(item)}`);
    }
}

// 「刷新账号名称」：全部账号按每批最多 20 个切片串行请求，skipped（后端 30 秒预算
// 耗尽）的 id 重新排队；某批完全没有推进时停止兜底，否则 skipped 一直回队会成为死循环。
// 进度写在按钮文案上；失败与「部分账号未能刷新」写进面板的共享错误行。
async function refreshAccountNames(source, ids, button) {
    const panelState = accountPanelState(source);
    if (panelState.refreshInflight) return;
    const queue = [...ids];
    const total = queue.length;
    if (!total) return;
    panelState.refreshInflight = true;
    const originalText = button.textContent;
    let processed = 0;
    let resolved = 0;
    let failed = 0;
    let stalled = false;
    button.disabled = true;
    const syncProgress = () => {
        // 来源启停成功后的整块重建会让旧按钮脱离文档，进度只写回仍在文档里的那个
        if (button.isConnected) button.textContent = `刷新中… ${processed}/${total}`;
    };
    accountPanelError(source, '');
    syncProgress();
    try {
        while (queue.length) {
            const batch = queue.splice(0, REFRESH_NAMES_BATCH_SIZE);
            const { response, payload } = await apiRequest(
                '/api/admin/crawl-accounts/refresh-names',
                { method: 'POST', body: { account_ids: batch } },
            );
            if (!response.ok) {
                accountPanelError(source, `刷新失败：${formatApiError(payload, '请重试')}`);
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
            accountPanelError(source, '部分账号未能刷新，请稍后重试');
        } else {
            showSettingsToast(`已更新 ${resolved} 个名称，${failed} 个获取失败`);
        }
    } catch (error) {
        accountPanelError(source, `刷新失败：${error.message || '网络错误'}`);
    } finally {
        panelState.refreshInflight = false;
        // 按钮可能已随整块重建脱离文档，文案只能写回仍在文档里的那个；
        // 控件状态必须无条件重新同步，否则重建后的刷新按钮会一直停在禁用态
        if (button.isConnected) button.textContent = originalText;
        syncRefreshButtons();
    }
}

// 芯片名称区的四种状态：已解析显示名称；新增后解析进行中显示「名称解析中」
// （纯前端瞬态，见 core.js 的 accountNameResolving）；其余未解析显示截断标识
// （CSS 省略号，完整值放 title）加弱化标记「名称待获取」；解析失败时标记变为
// 「名称获取失败」（警示色），失败原因放进标记的 title。
// 名称文本一律经 textContent 写入，禁止 innerHTML。
// 只重绘名称区（名称、标记），不动 label 里的 checkbox——刷新进行中
// 该芯片的其他状态（标记外观、操作节点）都保持原样。
function renderChipNameContent(label, item) {
    label.querySelectorAll('.account-chip-name, .account-name-badge')
        .forEach((node) => node.remove());
    const nameEl = createEl('span', 'account-chip-name');
    if (item.display_name) {
        nameEl.textContent = item.display_name;
        nameEl.title = item.display_name;
    } else {
        nameEl.textContent = item.normalized_identifier;
        nameEl.title = item.normalized_identifier;
    }
    label.appendChild(nameEl);
    if (!item.display_name) {
        let badgeText = '名称待获取';
        let badgeTitle = '';
        if (state.accountNameResolving.has(item.id)) {
            badgeText = '名称解析中';
        } else if (item.display_name_error) {
            badgeText = '名称获取失败';
            badgeTitle = item.display_name_error;
        }
        const badge = createEl('span',
            `account-name-badge${badgeTitle ? ' is-error' : ''}`,
            badgeText);
        if (badgeTitle) badge.title = badgeTitle;
        label.appendChild(badge);
    }
}

function accountToggleLabel(item) {
    return `启用 ${accountDisplayName(item)}`;
}

// 芯片 = 启用开关 + 打开主页 + 标记删除，三者同时可用；展开面板就是编辑面，
// 没有第二种模式。删除提交进行中临时禁用相关控件，结束后恢复。
function buildAccountChip(item) {
    const marked = isAccountMarked(item.source, item.id);
    const chip = createEl('span',
        `account-chip${item.display_name ? '' : ' is-unresolved'}${marked ? ' is-marked' : ''}`,
        '', { dataset: { accountId: item.id } });

    const label = createEl('label', 'account-chip-label');
    const toggle = createEl('input', 'account-enabled-toggle account-chip-checkbox', '', {
        type: 'checkbox',
        'aria-label': accountToggleLabel(item),
    });
    toggle.checked = !!item.enabled;
    toggle.disabled = state.deleteSubmitting;
    toggle.addEventListener('change', () => toggleAccountEnabled(item, toggle));
    label.appendChild(toggle);
    renderChipNameContent(label, item);
    chip.appendChild(label);

    const openLink = createEl('a', 'account-chip-open', '↗', {
        href: item.profile_url,
        target: '_blank',
        rel: 'noopener noreferrer',
        'aria-label': `打开 ${accountDisplayName(item)} 的主页`,
    });
    openLink.addEventListener('click', (event) => {
        if (state.deleteSubmitting) event.preventDefault();
    });
    chip.appendChild(openLink);
    const deleteBtn = createEl('button', 'account-chip-delete', marked ? '↩' : '×', {
        type: 'button',
        'aria-label': marked
            ? `撤回删除 ${accountDisplayName(item)}`
            : `删除 ${accountDisplayName(item)}`,
    });
    deleteBtn.disabled = state.deleteSubmitting;
    deleteBtn.addEventListener('click', () => toggleAccountDeleteMark(item));
    chip.appendChild(deleteBtn);
    return chip;
}

// 账号网格末尾常驻的虚线「＋ 添加账号」芯片
function buildAccountAddChip(source) {
    const chip = createEl('button', 'account-chip account-add-chip', '＋ 添加账号', {
        type: 'button',
    });
    chip.disabled = state.deleteSubmitting;
    chip.addEventListener('click', () => {
        const form = buildAccountAddForm(source);
        chip.replaceWith(form);
        form.querySelector('.account-add-input').focus();
    });
    return chip;
}

// 虚线芯片点击后就地变成的输入框：回车或点「添加」提交，「×」或 Esc 取消退回
// 虚线形态（Esc 是快捷键，「×」是可见入口，二者走同一个 close）。
// 添加成功后输入框保持打开并清空、焦点保留（renderAccountList 重建网格时
// 保留该节点），方便连续添加下一个；失败时输入内容保留，错误写进共享错误行。
function buildAccountAddForm(source) {
    const form = createEl('span', 'account-add-inline');
    const input = createEl('input', 'account-add-input', '', {
        type: 'text',
        placeholder: '主页链接或 ID',
        'aria-label': '账号链接或 ID',
    });
    const addBtn = createEl('button', 'btn btn-primary btn-account-add', '添加', {
        type: 'button',
    });
    const cancelBtn = createEl('button', 'account-add-cancel', '×', {
        type: 'button',
        'aria-label': '取消添加',
        title: '取消添加',
    });
    form.appendChild(input);
    form.appendChild(addBtn);
    form.appendChild(cancelBtn);
    const close = () => {
        if (form.isConnected) form.replaceWith(buildAccountAddChip(source));
    };
    cancelBtn.addEventListener('click', close);
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            close();
        } else if (event.key === 'Enter') {
            event.preventDefault();
            submitAccountAdd(source, input, addBtn);
        }
    });
    addBtn.addEventListener('click', () => submitAccountAdd(source, input, addBtn));
    return form;
}

// 新增账号的名称解析：按来源串行排队，每个新账号单独发一次 refresh-names，
// 结果经 applyRefreshedAccount 就地落到芯片。串行是有意的：头条解析每次要在
// 后端起一个无头浏览器，连续添加时不能并发起一堆；排队期间芯片一直显示
// 「名称解析中」。与来源行的「刷新账号名称」互不互斥，重复解析一次无害。
function queueNewAccountNameResolution(source, accountId) {
    const panelState = accountPanelState(source);
    panelState.resolveChain = (panelState.resolveChain || Promise.resolve())
        .then(() => resolveNewAccountName(source, accountId))
        .catch(() => {}); // 任何意外异常都不能断掉后续排队
}

async function resolveNewAccountName(source, accountId) {
    let failureMessage = null;
    try {
        const { response, payload } = await apiRequest(
            '/api/admin/crawl-accounts/refresh-names',
            { method: 'POST', body: { account_ids: [accountId] } },
        );
        if (!response.ok) {
            failureMessage = formatApiError(payload, '请重试');
        } else {
            // 先摘掉解析中标记再落结果，徽章才不会被打回「名称解析中」
            state.accountNameResolving.delete(accountId);
            const results = payload.items || [];
            results.forEach((result) => applyRefreshedAccount(source, result));
            const failed = results.find((result) => result.status === 'failed' && result.error);
            if (failed) {
                accountPanelError(source, `名称解析失败：${failed.error}`);
                showSettingsToast(NAME_RESOLVE_FAILED_TOAST, 'error');
                return;
            }
            const resolved = results.find((result) => result.account
                && result.account.display_name);
            if (resolved) {
                showSettingsToast(`已获取账号名称：${resolved.account.display_name}`);
            }
            return;
        }
    } catch (error) {
        failureMessage = error.message || '网络错误';
    }
    state.accountNameResolving.delete(accountId);
    accountPanelError(source, `名称解析失败：${failureMessage}`);
    showSettingsToast(NAME_RESOLVE_FAILED_TOAST, 'error');
    // 没有可用的刷新结果时也要把芯片徽章从「名称解析中」恢复成中性待获取
    const item = currentAccountItems(source)
        .find((account) => account.id === accountId);
    const panel = accountPanelEl(source);
    const chip = panel
        && panel.querySelector(`.account-chip[data-account-id="${accountId}"]`);
    if (item && chip) {
        renderChipNameContent(chip.querySelector('.account-chip-label'), item);
    }
}

async function submitAccountAdd(source, input, addBtn) {
    accountPanelError(source, '');
    const text = input.value.trim();
    if (!text) {
        accountPanelError(source, '请输入主页链接或 ID。');
        return;
    }
    addBtn.disabled = true;
    try {
        const { response, payload } = await apiRequest('/api/admin/crawl-accounts', {
            method: 'POST',
            body: { source, text },
        });
        if (!response.ok) {
            accountPanelError(source, formatApiError(payload, '添加失败，请重试'));
            return;
        }
        // 创建请求即时返回（不含名称）：芯片先以「名称解析中」出现，
        // 随后排队补一次解析，完成后名称就地补上，添加过程不再阻塞
        const created = payload.item;
        let resolving = false;
        if (created && created.id && !created.display_name) {
            state.accountNameResolving.add(created.id);
            queueNewAccountNameResolution(source, created.id);
            resolving = true;
        }
        input.value = '';
        showSettingsToast(resolving ? '账号已添加，正在获取账号名称…' : '账号已添加');
        await refreshAccountsAndList(source);
    } catch (error) {
        accountPanelError(source, `添加失败：${error.message || '网络错误'}`);
    } finally {
        addBtn.disabled = false;
    }
}

function renderAccountList(source) {
    const panel = accountPanelEl(source);
    const grid = panel && panel.querySelector('.accounts-chip-grid');
    if (!grid) return;
    // 重建网格前保留已打开的就地添加输入框（含焦点）：添加成功后的列表刷新
    // 不应打断连续添加；节点保留意味着已输入内容与焦点都不丢
    const addForm = grid.querySelector('.account-add-inline');
    const addInput = addForm && addForm.querySelector('.account-add-input');
    const addHadFocus = addInput && addInput === panel.ownerDocument.activeElement;
    if (addForm) addForm.remove();
    clearEl(grid);
    const items = currentAccountItems(source);
    if (!items.length) {
        grid.appendChild(createEl('p', 'settings-source-empty', '该来源还没有抓取账号。'));
    }
    items.forEach((item) => grid.appendChild(buildAccountChip(item)));
    if (addForm) {
        grid.appendChild(addForm);
        if (addHadFocus) addInput.focus();
    } else {
        grid.appendChild(buildAccountAddChip(source));
    }
    syncPanelDeleteBar(source);
}

// 渲染某个来源的账号面板。账号列表在页面初始化时已全量缓存，渲染本身不发新请求；
// 新增/删除后由 refreshAccountsAndList 强制刷新。布局：芯片网格 + 共享错误行。
// 面板内不发唯一 id，全靠 data-accounts-for 作用域区分；管理模式与待删标记读自
// 页面级 state，来源启停触发的整块重建后自然恢复。
function renderSourceAccounts(sourceKey, containerEl) {
    clearEl(containerEl);
    accountPanelState(sourceKey);

    containerEl.appendChild(createEl('div', 'accounts-chip-grid'));
    containerEl.appendChild(createEl('div', 'accounts-delete-bar-wrap'));
    containerEl.appendChild(createEl('p', 'accounts-panel-error'));

    loadAccountsForSource(sourceKey)
        .then(() => {
            // 缓存命中也会异步返回；面板若已随整块重建移除则放弃渲染
            if (!hasAccountPanel(sourceKey)) return;
            renderAccountList(sourceKey);
            refreshSourcesAccountBadges();
        })
        .catch((error) => {
            if (!hasAccountPanel(sourceKey)) return;
            const panel = accountPanelEl(sourceKey);
            const grid = panel && panel.querySelector('.accounts-chip-grid');
            if (!grid) return;
            clearEl(grid);
            grid.appendChild(createEl('p', 'settings-source-empty',
                `账号加载失败：${error.message}`));
        });
}
