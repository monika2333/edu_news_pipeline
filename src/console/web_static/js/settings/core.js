// 系统设置页 - core：共享状态、DOM 引用、请求封装、页签与未保存守卫。
// 加载顺序最前；页签逻辑见 models_tab.js / sources_tab.js / accounts_tab.js，
// 启动逻辑在 init.js。用户输入一律经 createEl/textContent 渲染，禁止拼接 innerHTML。
'use strict';

const SETTINGS_TABS = ['models', 'sources', 'accounts'];

const state = {
    payload: null,
    activeTab: 'models',
    accountSource: null,
    accounts: {},
    accountCounts: {},
    dirty: { llm_models: false, crawl_sources: false },
    saving: { llm_models: false, crawl_sources: false },
    modelsDraft: null,
    sourcesDraft: null,
    accountFilter: '',
    bulk: { text: '', items: null, stale: false },
};

const elements = {};

function cacheSettingsElements() {
    elements.alert = document.getElementById('settings-alert');
    elements.toast = document.getElementById('toast');
    elements.tabButtons = Array.from(document.querySelectorAll('[data-settings-tab]'));
    elements.panels = {
        models: document.getElementById('settings-panel-models'),
        sources: document.getElementById('settings-panel-sources'),
        accounts: document.getElementById('settings-panel-accounts'),
    };
    elements.deleteModal = document.getElementById('delete-account-modal');
    elements.deleteName = document.getElementById('delete-account-name');
    elements.deleteConfirm = document.getElementById('btn-confirm-delete-account');
    elements.deleteCancel = document.getElementById('btn-cancel-delete-account');
    state.currentUserName = document.body.dataset.currentUserName || '';
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

// 模型与数据源分区共用的保存流程：版本乐观锁、409 保留修改 + 手动载入最新、
// 422 展示服务端原因、进行中防重复提交。
async function saveSettingsSection(section, value, controls) {
    if (state.saving[section]) return;
    const current = settingsSection(section);
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
    return state.accounts[source];
}

async function loadAllAccountOverviews() {
    await Promise.all(accountSources().map(async (item) => {
        try {
            await loadAccountsForSource(item.key);
        } catch (error) {
            state.accountCounts[item.key] = null;
        }
    }));
}

function parseSettingsHash() {
    const raw = String(window.location.hash || '').replace(/^#/, '');
    const [tab, sub] = raw.split(':');
    return { tab, sub };
}

function writeSettingsHash(tab, sub) {
    const target = `#${tab}${sub ? `:${sub}` : ''}`;
    if (window.location.hash === target) return;
    window.history.replaceState(null, '', target);
}

// 切换页签只隐藏面板，不重渲染，未保存的修改随 DOM 保留。
function activateSettingsTab(tab, sub, { updateHash = true } = {}) {
    const normalized = SETTINGS_TABS.includes(tab) ? tab : 'models';
    state.activeTab = normalized;
    if (normalized === 'accounts') {
        if (sub && accountSources().some((item) => item.key === sub)) {
            state.accountSource = sub;
        }
        if (!state.accountSource) {
            const first = accountSources()[0];
            state.accountSource = first ? first.key : null;
        }
        renderAccountsTab();
    }
    elements.tabButtons.forEach((btn) => {
        const active = btn.dataset.settingsTab === normalized;
        btn.classList.toggle('is-active', active);
        btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    Object.entries(elements.panels).forEach(([name, panel]) => {
        panel.hidden = name !== normalized;
    });
    if (updateHash) {
        writeSettingsHash(normalized, normalized === 'accounts' ? state.accountSource : '');
    }
}
