// 系统设置页 - 来源账号管理：数据源页签平铺的账号芯片网格（默认态芯片即启用开关；
// 页面级管理模式下芯片提供打开主页与标记删除，删除为「标记 + 串行批量提交」，
// 标记跨来源共存、一次确认全部处理），芯片网格末尾的虚线「＋ 添加账号」就地输入，
// 以及「刷新账号名称」（来源行内小按钮，仅管理模式出现）的分批串行刷新。
// 账号名称由后端解析，前端不提供名称输入；所有用户输入经 textContent 渲染。
// 账号面板始终平铺，没有展开/收起；面板内 DOM 不发唯一 id，一律 class +
// 面板作用域查询（锚点是面板根的 data-accounts-for）。
'use strict';

// 后端 refresh-names 接口单批上限 20（Pydantic max_length），前端切片必须与之对齐
const REFRESH_NAMES_BATCH_SIZE = 20;

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

// 页面级管理模式进出。退出时丢弃全部待删标记（撤销必须零成本，不弹确认）；
// 删除提交进行中锁死出口。DOM 一律由 renderSourcesTab 按 state 重建，
// 保证页面级状态落回每一个来源行与芯片网格。
function setManageMode(on) {
    if (state.deleteSubmitting) return;
    if (state.manageMode === on) return;
    state.manageMode = on;
    if (!on) state.deleteMarks = [];
    renderSourcesTab();
}

// 标记/撤回都只动 state 与前端外观，不发请求；撤销必须是零成本的
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
    syncManageBar();
}

// 页面级操作条的统一同步：管理模式开关按下态与锁定、待提交条。
// 待提交条计数取全页所有标记的总数（跨来源汇总），有标记时出现在吸顶条下方。
function syncManageBar() {
    const panel = elements.panels.sources;
    if (!panel) return;
    const manageBtn = panel.querySelector('.accounts-manage-btn');
    if (manageBtn) {
        manageBtn.setAttribute('aria-pressed', state.manageMode ? 'true' : 'false');
        manageBtn.classList.toggle('is-active', state.manageMode);
        manageBtn.disabled = state.deleteSubmitting;
    }
    const wrap = panel.querySelector('.accounts-delete-bar-wrap');
    if (!wrap) return;
    clearEl(wrap);
    if (!state.manageMode || state.deleteMarks.length === 0) return;
    const bar = createEl('div', 'accounts-delete-bar');
    bar.appendChild(createEl('span', 'accounts-delete-count',
        `将删除 ${state.deleteMarks.length} 个账号`));
    const cancelBtn = createEl('button', 'btn btn-secondary btn-account-delete-cancel', '取消', {
        type: 'button',
    });
    cancelBtn.disabled = state.deleteSubmitting;
    cancelBtn.addEventListener('click', () => setManageMode(false));
    const confirmBtn = createEl('button', 'btn admin-confirm-delete btn-account-delete-confirm',
        '确认删除', { type: 'button' });
    confirmBtn.disabled = state.deleteSubmitting;
    confirmBtn.addEventListener('click', () => submitAccountDeletions());
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

// 确认删除：跨来源按标记顺序串行发 DELETE——串行而非并发，保证请求顺序确定、
// 失败归属清晰。404 视为成功：前端标记与库中实际状态可能不同步（另一处已删除），
// 此时目的已经达成。部分失败不回滚已成功的删除：失败的标记保留并停留在管理模式；
// 全部成功后自动退出管理模式。
async function submitAccountDeletions() {
    if (state.deleteSubmitting) return;
    const marks = [...state.deleteMarks];
    if (!marks.length) return;
    const panel = elements.panels.sources;
    const confirmBtn = panel.querySelector('.btn-account-delete-confirm');
    const cancelBtn = panel.querySelector('.btn-account-delete-cancel');
    const manageBtn = panel.querySelector('.accounts-manage-btn');
    state.deleteSubmitting = true;
    if (confirmBtn) confirmBtn.disabled = true;
    if (cancelBtn) cancelBtn.disabled = true;
    if (manageBtn) manageBtn.disabled = true;
    syncRefreshButtons();
    // 提交进行中芯片区不接受任何操作（checkbox 在管理模式本已禁用）
    panel.querySelectorAll('.account-chip-delete')
        .forEach((btn) => { btn.disabled = true; });
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
    }
    // 无论成功与否，涉及的来源都要重新拉取账号并同步来源行徽标
    const affected = [...new Set(marks.map((mark) => mark.source))];
    for (const source of affected) {
        await refreshAccountsAndList(source);
    }
    if (!failures.length) {
        state.manageMode = false;
        state.deleteMarks = [];
        renderSourcesTab();
        showSettingsToast(`已删除 ${succeeded} 个账号`);
        return;
    }
    const message = `已删除 ${succeeded} 个，${failures.length} 个失败：${failures[0].message}`;
    renderSourcesTab();
    accountPanelError(failures[0].source, message);
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
    if (isAccountPanelOpen(source)) renderAccountList(source);
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

// 芯片名称区的三种状态：已解析显示名称；未解析显示截断标识（CSS 省略号，
// 完整值放 title）加弱化标记；非头条来源解析失败时标记变为「名称获取失败」（警示色）。
// 头条依赖下一轮抓取补名称，即使带错误原因也保持中性的「名称待获取」外观；
// 只要有错误原因都放进标记的 title。
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
        const hasError = !!item.display_name_error;
        const showError = hasError && item.source !== 'toutiao';
        const badge = createEl('span',
            `account-name-badge${showError ? ' is-error' : ''}`,
            showError ? '名称获取失败' : '名称待获取');
        if (hasError) badge.title = item.display_name_error;
        label.appendChild(badge);
    }
}

function accountToggleLabel(item) {
    return `启用 ${accountDisplayName(item)}`;
}

// 芯片的两种模式共用完全相同的子节点结构（label + ↗ + ×），切换模式网格不重排：
// - 默认态：芯片 = 启用开关（label 包裹视觉隐藏的 checkbox，整个芯片即点击区），
//   操作节点由 CSS visibility 隐藏、位置照常占用，JS 侧同步做成不可达——
//   删除按钮 disabled，主页链接移出 Tab 序并对读屏隐藏、点击拦截；
// - 管理模式：checkbox 置 disabled（label 点击自然失效——管理模式确实不能启停），
//   右侧的「打开主页」↗ 与「标记删除」×（已标记为 ↩ 撤回）可用。
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
    toggle.disabled = state.manageMode;
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
    if (!state.manageMode) {
        openLink.setAttribute('tabindex', '-1');
        openLink.setAttribute('aria-hidden', 'true');
    }
    openLink.addEventListener('click', (event) => {
        if (!state.manageMode || state.deleteSubmitting) event.preventDefault();
    });
    chip.appendChild(openLink);
    const deleteBtn = createEl('button', 'account-chip-delete', marked ? '↩' : '×', {
        type: 'button',
        'aria-label': marked
            ? `撤回删除 ${accountDisplayName(item)}`
            : `删除 ${accountDisplayName(item)}`,
    });
    deleteBtn.disabled = !state.manageMode || state.deleteSubmitting;
    deleteBtn.addEventListener('click', () => toggleAccountDeleteMark(item));
    chip.appendChild(deleteBtn);
    return chip;
}

// 管理模式下图标网格末尾的虚线「＋ 添加账号」芯片；默认态不渲染
function buildAccountAddChip(source) {
    const chip = createEl('button', 'account-chip account-add-chip', '＋ 添加账号', {
        type: 'button',
    });
    chip.addEventListener('click', () => {
        const form = buildAccountAddForm(source);
        chip.replaceWith(form);
        form.querySelector('.account-add-input').focus();
    });
    return chip;
}

// 虚线芯片点击后就地变成的输入框：回车或点「添加」提交，Esc 取消退回虚线形态。
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
    form.appendChild(input);
    form.appendChild(addBtn);
    const close = () => {
        if (form.isConnected) form.replaceWith(buildAccountAddChip(source));
    };
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

async function submitAccountAdd(source, input, addBtn) {
    accountPanelError(source, '');
    const text = input.value.trim();
    if (!text) {
        accountPanelError(source, '请输入主页链接或 ID。');
        return;
    }
    addBtn.disabled = true;
    try {
        // 名称由后端同步解析，响应里的账号行直接可用，不再额外刷新
        const { response, payload } = await apiRequest('/api/admin/crawl-accounts', {
            method: 'POST',
            body: { source, text },
        });
        if (!response.ok) {
            accountPanelError(source, formatApiError(payload, '添加失败，请重试'));
            return;
        }
        input.value = '';
        showSettingsToast('账号已添加');
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
    grid.classList.toggle('is-manage', state.manageMode);
    const items = currentAccountItems(source);
    if (!items.length) {
        grid.appendChild(createEl('p', 'settings-source-empty', '该来源还没有抓取账号。'));
    }
    items.forEach((item) => grid.appendChild(buildAccountChip(item)));
    if (state.manageMode) {
        if (addForm) {
            grid.appendChild(addForm);
            if (addHadFocus) addInput.focus();
        } else {
            grid.appendChild(buildAccountAddChip(source));
        }
    }
}

// 渲染某个来源的账号面板。账号列表在页面初始化时已全量缓存，渲染本身不发新请求；
// 新增/删除后由 refreshAccountsAndList 强制刷新。布局：芯片网格 + 共享错误行。
// 面板内不发唯一 id，全靠 data-accounts-for 作用域区分；管理模式与待删标记读自
// 页面级 state，来源启停触发的整块重建后自然恢复。
function renderSourceAccounts(sourceKey, containerEl) {
    clearEl(containerEl);
    accountPanelState(sourceKey);

    containerEl.appendChild(createEl('div', 'accounts-chip-grid'));
    containerEl.appendChild(createEl('p', 'accounts-panel-error'));

    loadAccountsForSource(sourceKey)
        .then(() => {
            // 缓存命中也会异步返回；面板若已随整块重建移除则放弃渲染
            if (!isAccountPanelOpen(sourceKey)) return;
            renderAccountList(sourceKey);
            refreshSourcesAccountBadges();
        })
        .catch((error) => {
            if (!isAccountPanelOpen(sourceKey)) return;
            const panel = accountPanelEl(sourceKey);
            const grid = panel && panel.querySelector('.accounts-chip-grid');
            if (!grid) return;
            clearEl(grid);
            grid.appendChild(createEl('p', 'settings-source-empty',
                `账号加载失败：${error.message}`));
        });
}
