// 系统设置页 - core：共享状态、DOM 引用、请求封装、页签与未保存守卫。
// 加载顺序最前；页签逻辑见 models_tab.js / sources_tab.js / source_accounts.js，
// 启动逻辑在 init.js。用户输入一律经 createEl/textContent 渲染，禁止拼接 innerHTML。
'use strict';

const SETTINGS_TABS = ['models', 'sources', 'bonuses', 'advanced'];

// 页签短标签用于导航，页头标题与一句话说明用于内容区。两者分开：
// 导航要短（将来加页签也放得下），页头负责解释当前分区在管什么。
const SETTINGS_TAB_META = {
    models: {
        nav: '模型与接入点',
        title: '模型与接入点',
        description: '设置默认模型、流水线各步骤使用的模型，以及外部模型服务的接入点。',
    },
    sources: {
        nav: '来源与账号',
        title: '数据源',
        description: '管理每小时抓取的来源与其抓取账号',
    },
    bonuses: {
        nav: '关键词与排除',
        title: '加分词典',
        description: '命中关键词即加减分；排除名单命中的文章直接淘汰，不进入评分。',
    },
    advanced: {
        nav: '低频配置',
        title: '高级',
        description: '低频配置：抓取关键词、来源别名与审阅自动排序。关键词可逐条增删，整段修改用「批量编辑」；改动影响全局，请谨慎修改。',
    },
};

const state = {
    payload: null,
    activeTab: 'models',
    accounts: {},
    // 启用数与总数成对维护：徽标文案「启用 X / 共 Y」；加载失败时两者都置 null（未知态）
    accountCounts: {},
    accountTotals: {},
    // 当前展开账号面板的来源 key（手风琴，同时只开一个）；跨来源启停重渲染时保留。
    // 账号面板节点始终在 DOM 里，折叠只切 hidden，进行中的异步写入不会丢
    expandedSource: null,
    // 待删标记按来源归到展开面板：元素形如 { id, source }，面板底部的确认条
    // 只提交本来源的标记，不再有页面级「管理模式」或跨来源批量条
    deleteMarks: [],
    // 删除提交进行中：锁死全部删除/确认/取消与刷新按钮，防重复提交
    deleteSubmitting: false,
    // 各来源账号面板的 UI 状态，按来源 key 懒创建（见 source_accounts.js 的
    // accountPanelState）：只剩 refreshInflight（名称刷新按钮是按来源的）。
    // 账号面板始终平铺，没有收起即重置的语义
    accountPanels: {},
    // 新增账号的后台名称解析中（含排队）的账号 id：纯前端瞬态，不落库；
    // 页面刷新即丢，重新加载后未解析账号回到「名称待获取」中性外观
    accountNameResolving: new Set(),
    // 数据源即时保存；其余分区各自维护未保存标记。
    dirty: { llm_models: false, llm_endpoints: false, score_keyword_bonuses: false,
        score_exclusion_keywords: false, education_keywords: false,
        beijing_keywords: false, source_aliases: false,
        review_sort_keywords: false },
    saving: { llm_models: false, llm_endpoints: false, score_keyword_bonuses: false,
        score_exclusion_keywords: false, education_keywords: false,
        beijing_keywords: false, source_aliases: false,
        review_sort_keywords: false },
    modelsDraft: null,
    // 进行中的来源启停请求数；非零时禁用面板内全部来源开关
    sourceToggleInflight: 0,
};

const elements = {};

function cacheSettingsElements() {
    elements.alert = document.getElementById('settings-alert');
    elements.toast = document.getElementById('toast');
    elements.tabButtons = Array.from(document.querySelectorAll('[data-settings-tab]'));
    elements.tabButtons.forEach((btn) => {
        const meta = SETTINGS_TAB_META[btn.dataset.settingsTab];
        // 侧栏每项的副标题只做视觉提示，用 data-desc + CSS ::after 渲染：
        // 不进入无障碍名称，也不把按钮文案写长
        btn.dataset.desc = meta ? meta.nav : '';
    });
    elements.panels = {
        models: document.getElementById('settings-panel-models'),
        sources: document.getElementById('settings-panel-sources'),
        bonuses: document.getElementById('settings-panel-bonuses'),
        advanced: document.getElementById('settings-panel-advanced'),
    };
    elements.pageTitle = document.getElementById('settings-page-title');
    elements.pageDesc = document.getElementById('settings-page-desc');
    state.currentUserName = document.body.dataset.currentUserName || '';
}

function renderSettingsPageHead(tab) {
    const meta = SETTINGS_TAB_META[tab];
    if (!meta) return;
    elements.pageTitle.textContent = meta.title;
    elements.pageDesc.textContent = meta.description;
}

// 设置内容统一放在卡片里：标题 + 一句话说明 + 内容，卡片之间留白。
// 页签负责「去哪」，卡片负责「这一块是什么」，避免一整个大白面板里
// 平铺表格、清单和表单而看不出分组。
function buildSettingsCard(title, description) {
    const card = createEl('section', 'settings-card');
    if (title || description) {
        const head = createEl('div', 'settings-card-head');
        const heading = createEl('div', 'settings-card-heading');
        if (title) heading.appendChild(createEl('h3', 'settings-card-title', title));
        if (description) heading.appendChild(createEl('p', 'settings-card-desc', description));
        head.appendChild(heading);
        card.appendChild(head);
    }
    return card;
}

async function apiRequest(path, { method = 'GET', body } = {}) {
    const init = { method };
    if (body !== undefined) {
        init.headers = { 'Content-Type': 'application/json' };
        init.body = JSON.stringify(body);
    }
    const response = await window.fetch(path, init);
    const payload = await response.json().catch(() => ({}));
    return { response, payload };
}

function showSettingsAlert(message) {
    elements.alert.textContent = message;
    elements.alert.hidden = false;
}

function showSettingsToast(message, type = 'success') {
    showToastAt(elements.toast, message, type);
}

function setSettingsStatus(el, message, kind = '') {
    el.classList.remove('is-ok', 'is-error');
    if (kind === 'ok') el.classList.add('is-ok');
    if (kind === 'error') el.classList.add('is-error');
    el.textContent = message;
}

// 保存/测试进行中的已用秒数提示；返回停止函数，停止后由调用方写最终文案。
function startElapsedTicker(el, label) {
    const startedAt = Date.now();
    const render = () => {
        const seconds = Math.floor((Date.now() - startedAt) / 1000);
        el.textContent = `${label}… ${seconds} 秒`;
    };
    el.classList.remove('is-ok', 'is-error');
    render();
    const timer = setInterval(render, 1000);
    return () => clearInterval(timer);
}

function markDirty(section) {
    state.dirty[section] = true;
}

function clearDirty(section) {
    state.dirty[section] = false;
}

function hasUnsavedChanges() {
    return Object.values(state.dirty).some(Boolean);
}

function registerUnsavedGuard() {
    window.addEventListener('beforeunload', (event) => {
        if (!hasUnsavedChanges()) return;
        event.preventDefault();
        event.returnValue = '';
    });
}

function stepList() {
    return (state.payload && state.payload.steps) || [];
}

function sourceCatalog() {
    return (state.payload && state.payload.sources) || [];
}

function accountSources() {
    return sourceCatalog().filter((item) => item.requires_accounts);
}

function sourceDisplayName(key) {
    const found = sourceCatalog().find((item) => item.key === key);
    return found ? found.display_name : key;
}

function settingsSection(section) {
    return (state.payload && state.payload.sections)
        ? state.payload.sections[section]
        : null;
}

// 已保存的接入点分区值（{ default, items }）；未导入时为 null。
// 步骤表格的接入点下拉等只读展示必须读这里，而不是接入点的编辑态草稿：
// 后端校验步骤引用时查的也是库里的接入点，草稿里的新接入点存不进去。
function savedEndpointsValue() {
    const section = settingsSection('llm_endpoints');
    return section ? section.value : null;
}

function endpointDisplayLabel(key) {
    const saved = savedEndpointsValue();
    const found = saved && saved.items
        ? saved.items.find((item) => item.key === key)
        : null;
    return found ? found.label : (key || '');
}

// 分区缺失（服务器尚未运行 import-settings）时的占位提示：不渲染任何编辑控件。
function renderImportNotice(panel, label) {
    clearEl(panel);
    const notice = createEl('div', 'settings-import-notice');
    notice.appendChild(createEl('p', 'settings-import-title', `${label}尚未导入数据库。`));
    notice.appendChild(createEl(
        'p',
        '',
        '请先在服务器上运行 python -m src.cli.main import-settings 完成导入，然后刷新本页。',
    ));
    panel.appendChild(notice);
}

function buildLastModifiedLine(section) {
    const name = (section.updated_by && section.updated_by.display_name) || '未知';
    return createEl(
        'p',
        'settings-updated',
        `最后修改：${name} · ${formatLocalDateTime(section.updated_at)}`,
    );
}

function applySectionItem(section, item) {
    const updatedBy = item.updated_by && typeof item.updated_by === 'object'
        ? item.updated_by
        : { display_name: state.currentUserName || '未知' };
    state.payload.sections[section] = {
        value: item.value,
        version: item.version,
        updated_at: item.updated_at,
        updated_by: updatedBy,
    };
}

async function reloadSettingsPayload() {
    const { response, payload } = await apiRequest('/api/admin/settings');
    if (!response.ok) {
        throw new Error(formatApiError(payload, '加载设置失败'));
    }
    state.payload = payload;
}

// 分区保存的通用流程：版本乐观锁、409 保留修改 + 手动载入最新、
// 422 展示服务端原因、进行中防重复提交。由各编辑分区复用。
async function saveSettingsSection(section, value, controls) {
    if (state.saving[section]) return;
    const current = controls.sectionSnapshot || settingsSection(section);
    if (!current) return;
    state.saving[section] = true;
    controls.saveBtn.disabled = true;
    const stopTicker = startElapsedTicker(controls.statusEl, '保存中');
    try {
        const { response, payload } = await apiRequest(`/api/admin/settings/${section}`, {
            method: 'PUT',
            body: { value, expected_version: current.version },
        });
        if (response.ok) {
            applySectionItem(section, payload.item);
            clearDirty(section);
            setSettingsStatus(controls.statusEl, '保存成功。', 'ok');
            if (controls.onSaved) controls.onSaved();
            return;
        }
        if (response.status === 409) {
            setSettingsStatus(
                controls.statusEl,
                `保存冲突：${formatApiError(payload, '配置已在别处被修改')}。`
                    + '本地修改已保留，可点击「载入最新配置」后再调整。',
                'error',
            );
            controls.reloadBtn.hidden = false;
            return;
        }
        setSettingsStatus(
            controls.statusEl,
            `保存失败：${formatApiError(payload, '请检查填写内容')}`,
            'error',
        );
    } catch (error) {
        setSettingsStatus(controls.statusEl, `保存失败：${error.message || '网络错误'}`, 'error');
    } finally {
        stopTicker();
        state.saving[section] = false;
        controls.saveBtn.disabled = false;
    }
}

async function loadAccountsForSource(source, { force = false } = {}) {
    if (!force && state.accounts[source]) return state.accounts[source];
    const { response, payload } = await apiRequest(
        `/api/admin/crawl-accounts?source=${encodeURIComponent(source)}`,
    );
    if (!response.ok) {
        throw new Error(formatApiError(payload, '加载抓取账号失败'));
    }
    state.accounts[source] = payload.items || [];
    state.accountCounts[source] = state.accounts[source]
        .filter((item) => item.enabled).length;
    state.accountTotals[source] = state.accounts[source].length;
    return state.accounts[source];
}

async function loadAllAccountOverviews() {
    await Promise.all(accountSources().map(async (item) => {
        try {
            await loadAccountsForSource(item.key);
        } catch (error) {
            state.accountCounts[item.key] = null;
            state.accountTotals[item.key] = null;
        }
    }));
}

function parseSettingsHash() {
    const raw = String(window.location.hash || '').replace(/^#/, '');
    const [tab, sub] = raw.split(':');
    return { tab, sub };
}

function writeSettingsHash(tab) {
    const target = `#${tab}`;
    if (window.location.hash === target) return;
    window.history.replaceState(null, '', target);
}

// 切换页签只隐藏面板，不重渲染，未保存的修改随 DOM 保留。
// hash 收敛到页签级：只写页签名，解析出的 sub 一律忽略
// （#sources:<来源key> 这类子锚点已随展开抽屉取消而失效）。
// 旧 hash 兼容：#accounts[:任意] 一律 replaceState 改写为 #sources，不报错、不留历史。
function activateSettingsTab(tab, { updateHash = true } = {}) {
    let normalized = tab;
    let forceHashRewrite = false;
    if (normalized === 'accounts') {
        normalized = 'sources';
        forceHashRewrite = true;
    }
    if (!SETTINGS_TABS.includes(normalized)) normalized = 'models';
    state.activeTab = normalized;
    renderSettingsPageHead(normalized);
    elements.tabButtons.forEach((btn) => {
        const active = btn.dataset.settingsTab === normalized;
        btn.classList.toggle('is-active', active);
        btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    Object.entries(elements.panels).forEach(([name, panel]) => {
        panel.hidden = name !== normalized;
    });
    if (updateHash || forceHashRewrite) {
        writeSettingsHash(normalized);
    }
}
