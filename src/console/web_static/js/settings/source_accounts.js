// 系统设置页 - 来源账号管理：数据源页签行内展开区的账号芯片网格（默认态芯片即启用开关，
// 管理态芯片提供打开主页与标记删除，删除为「标记 + 串行批量提交」）、单个新增与批量粘贴
// 预览（收进「添加账号」<details>，默认折叠），以及「刷新账号名称」的分批串行刷新。
// 账号名称由后端解析，前端不提供名称输入；所有用户输入经 textContent 渲染。
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

function accountDisplayName(item) {
    return item.display_name || item.normalized_identifier;
}

// 芯片区下方的共享错误行（单个节点）：表格行内错误在芯片布局下没有落点，
// 改为显示最近一次失败的原因，下一次操作成功时清空。
function accountPanelError(message) {
    const errorEl = document.getElementById('accounts-panel-error');
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
            accountPanelError(`状态切换失败：${formatApiError(payload, '请重试')}`);
            return;
        }
        item.enabled = !!payload.item.enabled;
        toggle.checked = item.enabled;
        accountPanelError('');
        state.accountCounts[item.source] = currentAccountItems()
            .filter((account) => account.enabled).length;
        refreshSourcesAccountBadges();
    } catch (error) {
        toggle.checked = !target;
        accountPanelError(`状态切换失败：${error.message || '网络错误'}`);
    } finally {
        toggle.disabled = false;
    }
}

function isAccountMarked(id) {
    return state.accountDeleteMarks.includes(id);
}

// 管理态进出。进入时捕获「添加账号」开合状态并锁定折叠区、禁用名称刷新——管理态
// 只负责「打开主页」和「删除」两件事，不让新增、名称刷新和待删标记三种状态互相纠缠；
// 退出时丢弃全部待删标记并恢复折叠区开合。DOM 一律由 renderAccountList +
// syncManageModeUI 重建，保证与 state 一致。
function setManageMode(on) {
    if (state.accountDeleteSubmitting) return;
    if (state.accountManageMode === on) return;
    const panel = elements.panels.sources.querySelector('.source-accounts-panel');
    const details = panel && panel.querySelector('.account-add-details');
    if (on) {
        if (details) state.accountAddDetailsOpen = details.open;
        state.accountManageMode = true;
    } else {
        state.accountManageMode = false;
        state.accountDeleteMarks = [];
    }
    renderAccountList();
    syncManageModeUI();
    if (details) details.open = !on && state.accountAddDetailsOpen;
}

// 标记/撤回都只动 state 与前端外观，不发请求；撤销必须是零成本的
function toggleAccountDeleteMark(item) {
    if (state.accountDeleteSubmitting) return;
    const index = state.accountDeleteMarks.indexOf(item.id);
    if (index >= 0) {
        state.accountDeleteMarks.splice(index, 1);
    } else {
        state.accountDeleteMarks.push(item.id);
    }
    renderAccountList();
    syncManageModeUI();
}

// 管理态相关控件的统一同步：管理按钮按下态、名称刷新禁用、「添加账号」锁定、
// 待提交条（数量取全部标记，不按筛选后的可见芯片统计——被筛选隐藏的标记仍然算数）。
function syncManageModeUI() {
    const panel = elements.panels.sources.querySelector('.source-accounts-panel');
    if (!panel) return;
    const manageBtn = panel.querySelector('#btn-account-manage');
    if (manageBtn) {
        manageBtn.setAttribute('aria-pressed', state.accountManageMode ? 'true' : 'false');
        manageBtn.classList.toggle('is-active', state.accountManageMode);
        manageBtn.disabled = state.accountDeleteSubmitting;
    }
    const refreshBtn = panel.querySelector('#btn-account-refresh-names');
    if (refreshBtn) {
        refreshBtn.disabled = state.accountManageMode || state.accountRefreshInflight;
    }
    const details = panel.querySelector('.account-add-details');
    if (details) details.classList.toggle('is-locked', state.accountManageMode);

    const wrap = panel.querySelector('#accounts-delete-bar-wrap');
    if (!wrap) return;
    clearEl(wrap);
    if (!state.accountManageMode || state.accountDeleteMarks.length === 0) return;
    const bar = createEl('div', 'accounts-delete-bar');
    bar.appendChild(createEl('span', 'accounts-delete-count',
        `将删除 ${state.accountDeleteMarks.length} 个账号`));
    const cancelBtn = createEl('button', 'btn btn-secondary', '取消', {
        id: 'btn-account-delete-cancel',
        type: 'button',
    });
    cancelBtn.disabled = state.accountDeleteSubmitting;
    cancelBtn.addEventListener('click', () => setManageMode(false));
    const confirmBtn = createEl('button', 'btn admin-confirm-delete', '确认删除', {
        id: 'btn-account-delete-confirm',
        type: 'button',
    });
    confirmBtn.disabled = state.accountDeleteSubmitting;
    confirmBtn.addEventListener('click', submitAccountDeletions);
    bar.appendChild(cancelBtn);
    bar.appendChild(confirmBtn);
    wrap.appendChild(bar);
}

// 确认删除：按标记顺序串行发 DELETE——串行而非并发，保证请求顺序确定、失败归属清晰。
// 404 视为成功：前端标记与库中实际状态可能不同步（另一处已删除），此时目的已经达成。
// 部分失败不回滚已成功的删除：失败的 id 保留标记并停留在管理态。
// 来源守卫与 refreshAccountNames 同一写法：开头捕获来源，每次 await 返回后检查，
// 切走则停止后续请求；收尾刷新针对捕获的来源，面板写入全部跳过，toast 照常。
async function submitAccountDeletions() {
    if (state.accountDeleteSubmitting) return;
    const ids = [...state.accountDeleteMarks];
    if (!ids.length) return;
    const source = state.accountSource;
    const panel = elements.panels.sources.querySelector('.source-accounts-panel');
    const confirmBtn = panel && panel.querySelector('#btn-account-delete-confirm');
    const cancelBtn = panel && panel.querySelector('#btn-account-delete-cancel');
    const manageBtn = panel && panel.querySelector('#btn-account-manage');
    state.accountDeleteSubmitting = true;
    if (confirmBtn) confirmBtn.disabled = true;
    if (cancelBtn) cancelBtn.disabled = true;
    if (manageBtn) manageBtn.disabled = true;
    // 提交进行中芯片区不接受任何操作（checkbox 在管理态本已禁用）
    if (panel) {
        panel.querySelectorAll('.account-chip-delete')
            .forEach((btn) => { btn.disabled = true; });
    }
    let succeeded = 0;
    const failures = [];
    let processed = 0;
    try {
        for (const id of ids) {
            // 提交中途切走了来源：停止后续请求（展开箭头在面板外，仍可点击）
            if (state.accountSource !== source) break;
            processed += 1;
            if (confirmBtn && confirmBtn.isConnected) {
                confirmBtn.textContent = `删除中… ${processed}/${ids.length}`;
            }
            try {
                const { response, payload } = await apiRequest(
                    `/api/admin/crawl-accounts/${encodeURIComponent(id)}`,
                    { method: 'DELETE' },
                );
                if (response.ok || response.status === 404) {
                    succeeded += 1;
                    state.accountDeleteMarks = state.accountDeleteMarks
                        .filter((mark) => mark !== id);
                } else {
                    failures.push(formatApiError(payload, '请重试'));
                }
            } catch (error) {
                failures.push(error.message || '网络错误');
            }
        }
    } finally {
        state.accountDeleteSubmitting = false;
    }
    // 无论成功与否、是否中途切走，都按开头捕获的来源重新拉取账号并同步来源行徽标
    await refreshAccountsAndList(source);
    const switchedAway = state.accountSource !== source;
    if (!failures.length) {
        state.accountManageMode = false;
        state.accountDeleteMarks = [];
        if (!switchedAway) {
            renderAccountList();
            syncManageModeUI();
            const details = elements.panels.sources
                .querySelector('.source-accounts-panel .account-add-details');
            if (details) details.open = state.accountAddDetailsOpen;
        }
        showSettingsToast(`已删除 ${succeeded} 个账号`);
        return;
    }
    const message = `已删除 ${succeeded} 个，${failures.length} 个失败：${failures[0]}`;
    if (!switchedAway) {
        renderAccountList();
        syncManageModeUI();
        accountPanelError(message);
    }
    // toast 是全局的，切走了也要提示
    showSettingsToast(message, 'error');
}

// 删除/新增后重新拉取账号并同步来源行徽标。source 默认取当前展开来源；
// 批量删除这类跨多次请求的流程应传入开头捕获的来源——即使中途切走，
// 被删来源的缓存与徽标也要更新。徽标无条件刷新（否则就是切走后徽标停在旧值
// 的 bug），只有写回面板 DOM 的部分需要来源守卫。
async function refreshAccountsAndList(source = state.accountSource) {
    if (!source) {
        refreshSourcesAccountBadges();
        return;
    }
    await loadAccountsForSource(source, { force: true });
    // 请求返回时展开区可能已切换到其他来源，不要覆盖别人的列表
    if (state.accountSource === source) renderAccountList();
    refreshSourcesAccountBadges();
}

// 名称刷新结果就地落到单个芯片：同步 state 缓存，只重绘该芯片的名称区，
// 不整块重建——重建会打断进行中的启停操作，且会让失败分支的错误写进已销毁的节点。
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
    const chip = document.querySelector(
        `#accounts-body .account-chip[data-account-id="${item.id}"]`);
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
        const marked = isAccountMarked(item.id);
        deleteBtn.setAttribute('aria-label', marked
            ? `撤回删除 ${accountDisplayName(item)}`
            : `删除 ${accountDisplayName(item)}`);
    }
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
            button.textContent = originalText;
            // 可用性由 syncManageModeUI 统一裁决（管理态下保持禁用）
            syncManageModeUI();
        }
    }
}

// 芯片名称区的三种状态：已解析显示名称；未解析显示截断标识（CSS 省略号，
// 完整值放 title）加弱化标记；非头条来源解析失败时标记变为「名称获取失败」（警示色）。
// 头条依赖下一轮抓取补名称，即使带错误原因也保持中性的「名称待获取」外观；
// 只要有错误原因都放进标记的 title。
// 名称文本一律经 textContent 写入，禁止 innerHTML。
// 只重绘名称区（圆点、名称、标记），不动 label 里的 checkbox——刷新进行中
// 该芯片的启停操作不应被打断（芯片节点与开关节点都保持原样）。
function renderChipNameContent(label, item) {
    label.querySelectorAll('.account-chip-dot, .account-chip-name, .account-name-badge')
        .forEach((node) => node.remove());
    // 圆点在名称之前：实心 = 启用，空心 = 停用，不只靠颜色区分启停
    label.appendChild(createEl('span', 'account-chip-dot', '', { 'aria-hidden': 'true' }));
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

// 芯片的两种模式共用一个结构：
// - 默认态：芯片 = 启用开关（label 包裹视觉隐藏的 checkbox，整个芯片即点击区），
//   没有主页链接、没有删除入口；
// - 管理态：checkbox 置 disabled（label 点击自然失效——管理态确实不能启停），
//   右侧长出「打开主页」↗ 与「标记删除」×（已标记为 ↩ 撤回）。
function buildAccountChip(item) {
    const marked = isAccountMarked(item.id);
    const chip = createEl('span',
        `account-chip${item.display_name ? '' : ' is-unresolved'}${marked ? ' is-marked' : ''}`,
        '', { dataset: { accountId: item.id } });

    const label = createEl('label', 'account-chip-label');
    const toggle = createEl('input', 'account-enabled-toggle account-chip-checkbox', '', {
        type: 'checkbox',
        'aria-label': accountToggleLabel(item),
    });
    toggle.checked = !!item.enabled;
    toggle.disabled = state.accountManageMode;
    toggle.addEventListener('change', () => toggleAccountEnabled(item, toggle));
    label.appendChild(toggle);
    renderChipNameContent(label, item);
    chip.appendChild(label);

    if (state.accountManageMode) {
        const openLink = createEl('a', 'account-chip-open', '↗', {
            href: item.profile_url,
            target: '_blank',
            rel: 'noopener noreferrer',
            'aria-label': `打开 ${accountDisplayName(item)} 的主页`,
        });
        openLink.addEventListener('click', (event) => {
            if (state.accountDeleteSubmitting) event.preventDefault();
        });
        chip.appendChild(openLink);
        const deleteBtn = createEl('button', 'account-chip-delete', marked ? '↩' : '×', {
            type: 'button',
            'aria-label': marked
                ? `撤回删除 ${accountDisplayName(item)}`
                : `删除 ${accountDisplayName(item)}`,
        });
        deleteBtn.disabled = state.accountDeleteSubmitting;
        deleteBtn.addEventListener('click', () => toggleAccountDeleteMark(item));
        chip.appendChild(deleteBtn);
    }
    return chip;
}

function renderAccountList() {
    const grid = document.getElementById('accounts-body');
    if (!grid) return;
    clearEl(grid);
    const items = filteredAccountItems();
    if (!items.length) {
        grid.appendChild(createEl('p', 'settings-source-empty',
            state.accountFilter ? '没有匹配的账号。' : '该来源还没有抓取账号。'));
        return;
    }
    items.forEach((item) => grid.appendChild(buildAccountChip(item)));
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
// 布局：工具条（筛选框、管理按钮、刷新账号名称按钮、状态文字）+ 芯片网格
// （#accounts-body）+ 共享错误行 + 待提交条容器 + 「添加账号」折叠区。
// 管理态与待删标记读自 state，来源启停触发的整块重建后自然恢复。
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
        // 待提交条也随筛选重渲染：计数始终取自全部标记（含被筛选隐藏的），
        // 保证用户看到的数字与将提交的集合一致
        syncManageModeUI();
    });
    const manageBtn = createEl('button', 'btn btn-secondary accounts-manage-btn', '管理', {
        id: 'btn-account-manage',
        type: 'button',
        'aria-pressed': state.accountManageMode ? 'true' : 'false',
    });
    manageBtn.addEventListener('click', () => setManageMode(!state.accountManageMode));
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
    toolbar.appendChild(manageBtn);
    toolbar.appendChild(refreshBtn);
    toolbar.appendChild(createEl('span', 'accounts-refresh-status', '', {
        id: 'accounts-refresh-status',
    }));
    containerEl.appendChild(toolbar);

    containerEl.appendChild(createEl('div', 'accounts-chip-grid', '', { id: 'accounts-body' }));
    containerEl.appendChild(createEl('p', 'accounts-panel-error', '', {
        id: 'accounts-panel-error',
    }));
    containerEl.appendChild(createEl('div', 'accounts-delete-bar-wrap', '', {
        id: 'accounts-delete-bar-wrap',
    }));

    const addDetails = createEl('details', 'account-add-details');
    const addSummary = createEl('summary', 'account-add-summary', '添加账号');
    addSummary.addEventListener('click', (event) => {
        // 管理态下「添加账号」锁定收起：管理态只负责打开主页与删除，
        // 不让新增和待删标记两种状态互相纠缠
        if (state.accountManageMode) {
            event.preventDefault();
            event.stopPropagation();
        }
    });
    addDetails.appendChild(addSummary);

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

    // 展开区可能是来源启停重建后的恢复：管理态、待删标记、刷新进行中状态都要落回 DOM
    syncManageModeUI();

    loadAccountsForSource(sourceKey)
        .then(() => {
            // 缓存命中也会异步返回；展开区若已收起/切换则放弃渲染
            if (state.accountSource !== sourceKey) return;
            if (!document.getElementById('accounts-body')) return;
            renderAccountList();
            refreshSourcesAccountBadges();
        })
        .catch((error) => {
            const grid = document.getElementById('accounts-body');
            if (!grid) return;
            clearEl(grid);
            grid.appendChild(createEl('p', 'settings-source-empty',
                `账号加载失败：${error.message}`));
        });
}
