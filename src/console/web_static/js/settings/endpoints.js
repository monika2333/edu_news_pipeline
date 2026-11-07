// 系统设置页 - 接入点块：只读清单 + 「管理接入点」编辑态（新增/删除/字段编辑、
// 分区独立保存）。数据来源：已保存分区 sections.llm_endpoints（步骤下拉同样只读
// 已保存值，不读编辑态草稿）；allowed_hosts、已配置 Key 变量名名单
// （configured_api_key_envs）与各接入点的 host_allowed 来自顶层 endpoints 负载。
// 本文件在 models_tab.js 之前加载；接入点的标签、地址全部来自用户输入，
// 一律经 createEl/textContent 渲染，禁止拼接 innerHTML。
'use strict';

const ENDPOINT_STYLE_LABELS = {
    openrouter: 'OpenRouter',
    thinking: 'thinking（DeepSeek / 智谱 GLM）',
};

// 编辑态是接入点块自己的 UI 状态：管理开关 + 草稿。保存/放弃成功后重置；
// 整个模型页签重渲染时保留（renderModelsTab({ keepDraft: true }) 不经过这里重置）。
let endpointsEditorOpen = false;
let endpointsDraft = null;
let endpointsClientSeq = 0;

function endpointsPayloadInfo() {
    return (state.payload && state.payload.endpoints)
        || { default: null, allowed_hosts: [], configured_api_key_envs: [], items: [] };
}

// 服务器当前已配置的 Key 环境变量名名单（后端按「值非空 + 名字合法」过滤）。
// 判断任意变量名——包括草稿里还没保存的新接入点——配没配都以它为准；
// 逐接入点的「已保存状态」只有一个来源，不再各存一份。
function configuredApiKeyEnvs() {
    return endpointsPayloadInfo().configured_api_key_envs || [];
}

function apiKeyEnvConfigured(name) {
    return configuredApiKeyEnvs().includes(String(name || '').trim());
}

// 与后端 LLM_API_KEY_ENV_PATTERN 一致：大写字母开头、以 _API_KEY 结尾
const ENDPOINT_KEY_ENV_PATTERN = /^[A-Z][A-Z0-9_]*_API_KEY$/;

// Key 环境变量输入框的实时提示状态：empty（不提示）/ invalid（格式警告）/
// ok（已配置）/ missing（未配置，写出完整步骤）
function apiKeyEnvHintState(name) {
    const trimmed = String(name || '').trim();
    if (!trimmed) return { kind: 'empty', message: '' };
    if (!ENDPOINT_KEY_ENV_PATTERN.test(trimmed)) {
        return {
            kind: 'invalid',
            message: '变量名必须是大写字母开头、以 _API_KEY 结尾，例如 GLM_API_KEY。',
        };
    }
    if (apiKeyEnvConfigured(trimmed)) {
        return { kind: 'ok', message: 'Key 已配置' };
    }
    return {
        kind: 'missing',
        message: `该环境变量当前未配置：先在服务器的 .env.local 里添加 ${trimmed}，`
            + '改好后直接回来保存。',
    };
}

// 最终请求地址 = base_url 去掉尾斜杠 + /chat/completions。
// 各家路径深度不同（DeepSeek 无 /v1、GLM 带 /api/paas/v4），把拼好的 URL 摆出来
// 是避免手滑多补一层 /v1 的最有效办法。
function chatCompletionsUrl(baseUrl) {
    return `${String(baseUrl || '').trim().replace(/\/+$/, '')}/chat/completions`;
}

function makeEndpointsDraft() {
    const saved = savedEndpointsValue();
    if (!saved) return null;
    return {
        default: saved.default,
        items: (saved.items || []).map((item) => ({
            clientId: item.key,
            isNew: false,
            key: item.key,
            label: item.label,
            base_url: item.base_url,
            api_key_env: item.api_key_env,
            api_style: item.api_style,
            default_model: item.default_model || '',
            temperature_override: item.temperature_override === null
                || item.temperature_override === undefined
                ? ''
                : String(item.temperature_override),
        })),
    };
}

// 引用指定接入点的步骤中文名（去重）。来源有两处，删之前都要看：
// 已保存的 llm_models（与后端删除校验同源）和当前模型草稿——用户把步骤改指向
// 某接入点还没保存时，只查已保存值会放行删除，随后保存模型配置才被 422 挡住，
// 那时已经看不出是这两步操作在互相打架。
function stepsReferencingEndpoint(key) {
    const names = [];
    const seen = new Set();
    const collect = (steps) => {
        stepList().forEach(({ key: stepKey, display_name }) => {
            const step = steps && steps[stepKey];
            if (step && step.endpoint === key && !seen.has(stepKey)) {
                seen.add(stepKey);
                names.push(display_name);
            }
        });
    };
    const modelsSection = settingsSection('llm_models');
    if (modelsSection && modelsSection.value) {
        collect(modelsSection.value.steps);
    }
    if (state.modelsDraft) {
        collect(state.modelsDraft.steps);
    }
    return names;
}

function refreshEndpointsDirtyNote() {
    const note = document.querySelector('.endpoints-dirty-note');
    if (note) note.hidden = !state.dirty.llm_endpoints;
}

// 步骤表格上方的一行提示：接入点分区为脏时提醒「保存后才能在步骤里选择」。
// 节点常驻渲染、hidden 切换，因为脏标记变化不伴随模型页签重渲染。
function buildEndpointsDirtyNote() {
    const note = createEl(
        'p',
        'settings-effect-note settings-endpoints-note endpoints-dirty-note',
        '接入点有未保存修改，保存后才能在步骤里选择。',
    );
    // hidden 必须走属性赋值：createEl 的 setAttribute('hidden', false) 按存在性生效，
    // 传 false 也会把节点藏掉
    note.hidden = !state.dirty.llm_endpoints;
    return note;
}

// 前端预校验：地址必须 https 且主机在 allowed_hosts 内。后端仍是最终权威，
// 这里拦只是为了省一次往返并把改法说得更具体：白名单 = 内置默认列表 +
// LLM_ALLOWED_HOSTS 追加的域名，所以只需追加新域名，不用重复写默认的。
function endpointBaseUrlError(rawBaseUrl, allowedHosts) {
    const trimmed = String(rawBaseUrl || '').trim();
    if (!trimmed) return '地址不能为空。';
    let parsed;
    try {
        parsed = new URL(trimmed);
    } catch (error) {
        return '地址不是合法的 URL。';
    }
    if (parsed.protocol !== 'https:') return '地址必须使用 https。';
    const host = parsed.hostname.toLowerCase();
    const allowed = allowedHosts.map((item) => String(item).trim().toLowerCase());
    if (!allowed.includes(host)) {
        return `该域名 ${host} 不在允许列表内：请在服务器 .env.local 的 `
            + 'LLM_ALLOWED_HOSTS 里追加该域名（不用重复写默认的），再重新保存。';
    }
    return '';
}

// 接入点地址的主机名；解析失败返回空串
function endpointBaseUrlHost(baseUrl) {
    try {
        return new URL(String(baseUrl || '').trim()).hostname.toLowerCase();
    } catch (error) {
        return '';
    }
}

// 新增接入点的 key 不让用户起名：从地址主机名派生（open.bigmodel.cn →
// open-bigmodel-cn）。后端 key 规则是小写字母/数字开头 + [a-z0-9_-]，
// 派生结果按构造必然合规；保存时再对已保存与同批草稿做唯一性去重。
function endpointKeyFromBaseUrl(baseUrl) {
    let host = '';
    try {
        host = new URL(String(baseUrl || '').trim()).hostname.toLowerCase();
    } catch (error) {
        host = '';
    }
    let slug = host.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    if (!slug) slug = 'endpoint';
    if (/^[0-9]/.test(slug)) slug = `ep-${slug}`;
    return slug.slice(0, 32);
}

function buildEndpointsSaveValue() {
    const saved = savedEndpointsValue();
    const usedKeys = new Set();
    if (saved && saved.items) {
        saved.items.forEach((item) => usedKeys.add(item.key));
    }
    const keyByClientId = new Map();
    endpointsDraft.items.forEach((item) => {
        if (!item.isNew) {
            keyByClientId.set(item.clientId, item.key.trim());
            return;
        }
        // 同主机的新接入点依次加 -2、-3…，后缀挤占不超过 32 位上限
        let key = endpointKeyFromBaseUrl(item.base_url);
        let n = 2;
        while (usedKeys.has(key)) {
            const suffix = `-${n}`;
            key = `${key.slice(0, 32 - suffix.length)}${suffix}`;
            n += 1;
        }
        usedKeys.add(key);
        keyByClientId.set(item.clientId, key);
    });
    const defaultItem = endpointsDraft.items.find(
        (item) => item.clientId === endpointsDraft.default,
    ) || endpointsDraft.items[0];
    return {
        default: keyByClientId.get(defaultItem.clientId),
        items: endpointsDraft.items.map((item) => ({
            key: keyByClientId.get(item.clientId),
            label: item.label.trim(),
            base_url: item.base_url.trim().replace(/\/+$/, ''),
            api_key_env: item.api_key_env.trim(),
            api_style: item.api_style,
            default_model: item.default_model.trim(),
            temperature_override: item.temperature_override === ''
                ? null
                : Number(item.temperature_override),
        })),
    };
}

function buildEndpointField(nameText, control, hintEl) {
    const field = createEl('label', 'endpoint-field');
    field.appendChild(createEl('span', 'endpoint-field-name', nameText));
    field.appendChild(control);
    if (hintEl) field.appendChild(hintEl);
    return field;
}

// 编辑卡片布局：卡片头（标签标题 + key 身份 + 删除）在上，字段走固定四列网格，
// 两张卡片之间同名字段垂直对齐；行内报错独占卡片底部一行。
function buildEndpointEditRow(item, saved, rerender) {
    const row = createEl('div', 'endpoint-edit-row', '', { dataset: { clientId: item.clientId } });
    const markEndpointsDirty = () => {
        markDirty('llm_endpoints');
        refreshEndpointsDirtyNote();
    };

    // 卡片头：已保存卡片显示「标签 + key 芯片」，标签随下方输入框实时同步；
    // 新增卡片不再让用户起 key——头部放实时预览芯片，key 从地址主机名自动生成
    const head = createEl('div', 'endpoint-card-head');
    const title = createEl('div', 'endpoint-card-title');
    let cardName = null;
    let keyPreview = null;
    if (item.isNew) {
        const keyNew = createEl('div', 'endpoint-card-keynew');
        keyPreview = createEl('code', 'endpoint-key-static', endpointKeyFromBaseUrl(item.base_url));
        keyNew.appendChild(keyPreview);
        keyNew.appendChild(createEl(
            'span',
            'endpoint-field-hint',
            'key 按地址自动生成，保存后就地只读',
        ));
        title.appendChild(keyNew);
    } else {
        cardName = createEl('span', 'endpoint-card-name', item.label || item.key);
        title.appendChild(cardName);
        title.appendChild(createEl('code', 'endpoint-key-static', item.key));
    }
    head.appendChild(title);

    const deleteBtn = createEl('button', 'btn btn-secondary endpoint-delete-btn', '删除', {
        type: 'button',
        'aria-label': `删除接入点 ${item.label || item.key}`,
    });
    const rowError = createEl('span', 'endpoint-row-error');
    deleteBtn.addEventListener('click', () => {
        // 删除只改草稿，真正的删除随分区保存提交；这里的拦截是为了不让
        // 必然失败的修改进入草稿。校验顺序：保底 → 默认 → 被步骤引用。
        const label = item.label || item.key;
        if (endpointsDraft.items.length <= 1) {
            rowError.textContent = '至少保留一个接入点。';
            return;
        }
        if (item.clientId === endpointsDraft.default || item.key === saved.default) {
            rowError.textContent = `「${label}」是默认接入点，请先把默认切换到其他接入点。`;
            return;
        }
        const referencing = stepsReferencingEndpoint(item.key);
        if (referencing.length) {
            // 已保存与草稿里的引用共用一句提示：用户要做的是同一件事——
            // 把步骤切走并保存模型配置，不按来源拆成两套文案
            rowError.textContent = `「${label}」正被以下步骤引用：${referencing.join('、')}，`
                + '请先在步骤表格里把这些步骤切换到其他接入点或跟随默认，并保存模型配置。';
            return;
        }
        endpointsDraft.items = endpointsDraft.items
            .filter((entry) => entry.clientId !== item.clientId);
        rerender();
    });
    head.appendChild(deleteBtn);
    row.appendChild(head);

    const fields = createEl('div', 'endpoint-fields');

    const labelInput = createEl('input', 'endpoint-label-input', '', {
        type: 'text',
        'aria-label': '接入点标签',
        placeholder: '如 智谱 GLM',
    });
    labelInput.value = item.label;
    labelInput.addEventListener('input', () => {
        item.label = labelInput.value;
        if (cardName) cardName.textContent = item.label || item.key;
        markEndpointsDirty();
    });
    fields.appendChild(buildEndpointField('标签', labelInput));

    const urlPreview = createEl('span', 'endpoint-url-preview',
        chatCompletionsUrl(item.base_url));
    const urlInput = createEl('input', 'endpoint-base-url-input', '', {
        type: 'text',
        'aria-label': '接入点地址（base_url）',
        placeholder: 'https://…',
    });
    urlInput.value = item.base_url;
    urlInput.addEventListener('input', () => {
        item.base_url = urlInput.value;
        urlPreview.textContent = chatCompletionsUrl(urlInput.value);
        if (keyPreview) keyPreview.textContent = endpointKeyFromBaseUrl(urlInput.value);
        markEndpointsDirty();
    });
    const urlField = buildEndpointField('地址（base_url）', urlInput, urlPreview);
    urlField.classList.add('endpoint-field-wide');
    fields.appendChild(urlField);

    const envInput = createEl('input', 'endpoint-key-env-input', '', {
        type: 'text',
        'aria-label': 'Key 环境变量名',
        placeholder: '如 ZHIPU_API_KEY',
    });
    // 提示跟着**输入框当前值**实时变化（不是已保存接入点的状态）：
    // 新敲的变量名立刻能看出配没配、格式对不对。节点常驻渲染，
    // 空提示靠 textContent 置空，不重建节点（重建会抢焦点）。
    const envHint = createEl('span', 'endpoint-field-hint endpoint-key-env-hint');
    const updateKeyEnvHint = () => {
        const hintState = apiKeyEnvHintState(envInput.value);
        envHint.textContent = hintState.message;
        envHint.classList.toggle('is-warning', hintState.kind === 'invalid'
            || hintState.kind === 'missing');
    };
    envInput.value = item.api_key_env;
    envInput.addEventListener('input', () => {
        item.api_key_env = envInput.value;
        updateKeyEnvHint();
        markEndpointsDirty();
    });
    updateKeyEnvHint();
    fields.appendChild(buildEndpointField('Key 环境变量', envInput, envHint));

    const styleSelect = createEl('select', 'endpoint-style-select', '', {
        'aria-label': '接口风格',
    });
    Object.entries(ENDPOINT_STYLE_LABELS).forEach(([value, text]) => {
        styleSelect.appendChild(createEl('option', '', text, { value }));
    });
    styleSelect.value = item.api_style;
    const styleHint = createEl('span', 'endpoint-field-hint', '这两家的思考模式参数写法相同。');
    styleHint.hidden = item.api_style !== 'thinking';
    styleSelect.addEventListener('change', () => {
        item.api_style = styleSelect.value;
        styleHint.hidden = item.api_style !== 'thinking';
        markEndpointsDirty();
    });
    fields.appendChild(buildEndpointField('接口风格', styleSelect, styleHint));

    // 默认模型必填：步骤「模型」留空时实际调用的就是它。
    // 模型名只在某个服务商处有效，所以归接入点而不是全局。
    const defaultModelInput = createEl('input', 'endpoint-default-model-input', '', {
        type: 'text',
        'aria-label': '默认模型',
        placeholder: '如 deepseek/deepseek-v4-flash',
    });
    defaultModelInput.value = item.default_model;
    defaultModelInput.addEventListener('input', () => {
        item.default_model = defaultModelInput.value;
        markEndpointsDirty();
    });
    fields.appendChild(buildEndpointField('默认模型', defaultModelInput));

    const tempInput = createEl('input', 'endpoint-temp-input', '', {
        type: 'number',
        step: '0.1',
        min: '0',
        max: '2',
        'aria-label': '温度覆盖',
        placeholder: '留空使用全局默认',
    });
    tempInput.value = item.temperature_override;
    tempInput.addEventListener('input', () => {
        item.temperature_override = tempInput.value;
        markEndpointsDirty();
    });
    fields.appendChild(buildEndpointField('温度覆盖', tempInput));

    const defaultRadio = createEl('input', 'endpoint-default-radio', '', {
        type: 'radio',
        name: 'endpoints-default',
        value: item.clientId,
    });
    defaultRadio.checked = endpointsDraft.default === item.clientId;
    defaultRadio.addEventListener('change', () => {
        endpointsDraft.default = item.clientId;
        markEndpointsDirty();
    });
    const defaultLabel = createEl('span', 'endpoint-default-label');
    defaultLabel.appendChild(defaultRadio);
    defaultLabel.appendChild(createEl('span', '', '设为默认'));
    const defaultField = createEl('div', 'endpoint-field');
    defaultField.appendChild(createEl('span', 'endpoint-field-name', '默认接入点'));
    defaultField.appendChild(defaultLabel);
    fields.appendChild(defaultField);

    row.appendChild(fields);
    row.appendChild(rowError);
    return row;
}

// 「管理接入点 / 退出管理」开关按钮：只读态与编辑态各渲染一个，切换只重渲染
// 接入点块本身，模型页签其他部分不受影响。退出管理不丢弃草稿与脏标记，
// 重新进入可继续编辑；丢弃走块内的「放弃修改」。
function buildEndpointsManageToggle() {
    const btn = createEl(
        'button',
        'btn btn-secondary endpoints-manage-btn',
        endpointsEditorOpen ? '退出管理' : '管理接入点',
        {
            type: 'button',
            'aria-pressed': endpointsEditorOpen ? 'true' : 'false',
        },
    );
    btn.addEventListener('click', () => {
        endpointsEditorOpen = !endpointsEditorOpen;
        if (endpointsEditorOpen && !endpointsDraft) endpointsDraft = makeEndpointsDraft();
        renderEndpointsBlock();
    });
    return btn;
}

// 已保存 llm_models 里解析到指定接入点的步骤名（endpoint 显式选择，或跟随
// 默认而这次保存后的默认接入点就是它）。只读已保存的模型分区：保存接入点时
// 后端用的就是已保存 llm_models，步骤草稿的未保存改动不影响这次会实测谁。
function savedStepsResolvingTo(endpointKey, newDefaultKey) {
    const modelsSection = settingsSection('llm_models');
    const steps = modelsSection && modelsSection.value ? modelsSection.value.steps : {};
    return stepList()
        .filter(({ key }) => {
            const step = steps[key];
            return step && (step.endpoint || newDefaultKey) === endpointKey;
        })
        .map(({ display_name }) => display_name);
}

// 这次保存后端 _run_save_time_model_tests 会不会实测该接入点（前端镜像）。
// 只用于「Key 未配置要不要拦」：拦截范围必须**不比后端宽**——宁可少拦，
// 交给后端实测报错；一个没改动、也不会被实测的旧接入点即使没配 Key，
// 也不能卡住无关的修改。判定依据与后端一致：
// - 新增接入点 → 接入点级实测它的默认模型；
// - default_model 改了 → 接入点级实测；
// - 连接信息（地址 / Key 变量名 / 接口风格）改了且有步骤解析到它 → 步骤级实测；
// - 这次默认切到它且有跟随默认的步骤 → 步骤组合变化触发实测。
function endpointWillBeTested(item, newDefaultKey) {
    const saved = savedEndpointsValue();
    if (item.isNew || !saved) return true;
    const savedItem = (saved.items || []).find((entry) => entry.key === item.key);
    if (!savedItem) return true;
    if ((savedItem.default_model || '') !== String(item.default_model || '').trim()) {
        return true;
    }
    const connectionChanged = savedItem.base_url !== String(item.base_url || '').trim().replace(/\/+$/, '')
        || savedItem.api_key_env !== String(item.api_key_env || '').trim()
        || savedItem.api_style !== item.api_style;
    const referenced = savedStepsResolvingTo(item.key, newDefaultKey).length > 0;
    if (connectionChanged) return referenced;
    if (!referenced) return false;
    // 连接信息没变但仍被解析到：只有「这次默认切到它」会改变跟随默认步骤的组合
    return newDefaultKey === item.key && saved.default !== item.key;
}

function buildEndpointsEditor(saved) {
    const wrap = createEl('div', 'endpoints-editor');
    const head = createEl('div', 'endpoints-head');
    head.appendChild(createEl(
        'span',
        'endpoints-hint',
        '删除被步骤引用或默认的接入点会被拦截；修改完成后点右下角「保存接入点」提交。'
            + '新增接入点前，先在 .env.local 配好 Key，新服务商还要把域名加入 '
            + 'LLM_ALLOWED_HOSTS，改好后直接保存即可。',
    ));
    head.appendChild(buildEndpointsManageToggle());
    wrap.appendChild(head);

    const list = createEl('div', 'endpoints-edit-list');
    endpointsDraft.items.forEach((item) => {
        list.appendChild(buildEndpointEditRow(item, saved, () => renderEndpointsBlock()));
    });
    wrap.appendChild(list);

    // 底部一条操作栏：新增在左，保存/放弃在右，不再各自成行
    const footer = createEl('div', 'endpoints-footer');
    const addBtn = createEl('button', 'btn btn-secondary endpoint-add-btn', '＋ 新增接入点', {
        type: 'button',
    });
    addBtn.addEventListener('click', () => {
        endpointsClientSeq += 1;
        endpointsDraft.items.push({
            clientId: `new-${endpointsClientSeq}`,
            isNew: true,
            key: '',
            label: '',
            base_url: '',
            api_key_env: '',
            api_style: 'openrouter',
            default_model: '',
            temperature_override: '',
        });
        renderEndpointsBlock();
    });
    footer.appendChild(addBtn);

    const saveBar = createEl('div', 'settings-save-bar endpoints-save-bar');
    const saveBtn = createEl('button', 'btn btn-primary btn-endpoints-save', '保存接入点', {
        type: 'button',
    });
    const discardBtn = createEl('button', 'btn btn-secondary btn-endpoints-discard', '放弃修改', {
        type: 'button',
    });
    const reloadBtn = createEl('button', 'btn btn-secondary btn-endpoints-reload', '载入最新配置', {
        type: 'button',
    });
    reloadBtn.hidden = true;
    const status = createEl('span', 'settings-save-status endpoints-save-status');
    saveBar.appendChild(saveBtn);
    saveBar.appendChild(discardBtn);
    saveBar.appendChild(reloadBtn);
    saveBar.appendChild(status);
    footer.appendChild(saveBar);
    wrap.appendChild(footer);

    saveBtn.addEventListener('click', async () => {
        // 用户常常开着设置页去改 .env.local：点保存先重拉设置负载（后端处理
        // 请求前会自动读取 env 文件），再用最新名单做拦截判断——不能用打开
        // 页面时的旧名单，否则用户改好 Key 回来仍会被误拦。拉取失败就用
        // 现有负载继续（保存请求本身还带版本号，冲突有 409 兜底）。
        try {
            await reloadSettingsPayload();
        } catch (error) {
            // 静默：名单刷新失败不拦保存
        }
        // 行内预校验：任一行不通过就在该行报错，不发请求。
        // 保存接入点可能触发后端模型测试（默认模型变了要实测），耗时变长，
        // 进行中提示由 saveSettingsSection 的计时器承担，失败时原样展示后端报错。
        const allowedHosts = endpointsPayloadInfo().allowed_hosts;
        const saveValue = buildEndpointsSaveValue();
        const keyByClientId = new Map();
        endpointsDraft.items.forEach((item, index) => {
            keyByClientId.set(item.clientId, saveValue.items[index].key);
        });
        const newDefaultKey = keyByClientId.get(
            endpointsDraft.items.find((item) => item.clientId === endpointsDraft.default)
                ? endpointsDraft.default
                : endpointsDraft.items[0].clientId,
        );
        let hasError = false;
        endpointsDraft.items.forEach((item) => {
            const row = wrap.querySelector(`[data-client-id="${item.clientId}"]`);
            if (!row) return;
            const messages = [];
            let blocking = false;
            const baseError = endpointBaseUrlError(item.base_url, allowedHosts);
            if (baseError) {
                messages.push(baseError);
                blocking = true;
            }
            if (!item.default_model.trim()) {
                const name = item.label.trim() || item.key || '新接入点';
                messages.push(`「${name}」的默认模型不能为空。`);
                blocking = true;
            }
            const hintState = apiKeyEnvHintState(item.api_key_env);
            if (hintState.kind === 'invalid') {
                // 格式不对后端必然拒绝，就地拦下并写明规则
                messages.push(hintState.message);
                blocking = true;
            } else if (hintState.kind === 'missing') {
                if (endpointWillBeTested(item, newDefaultKey)) {
                    // 后端会实测的接入点必须有 Key（实测要拿真实 Key 发请求）：
                    // 新增的、连接信息或默认模型改了的、这次刚被设为默认的
                    messages.push(hintState.message);
                    blocking = true;
                } else {
                    // 没改动、也不会被实测的接入点即使没 Key 也只警告不拦——
                    // 一个暂时没 Key 的旧接入点不该卡住无关的修改
                    messages.push(`警告：${hintState.message}`);
                }
            }
            const message = messages.filter(Boolean).join(' ');
            const rowError = row.querySelector('.endpoint-row-error');
            rowError.textContent = message;
            rowError.classList.toggle('is-warning', !!message && !blocking);
            if (blocking && message) hasError = true;
        });
        if (hasError) {
            setSettingsStatus(status, '接入点未通过校验，请按行内提示修改。', 'error');
            return;
        }
        await saveSettingsSection('llm_endpoints', saveValue, {
            statusEl: status,
            saveBtn,
            reloadBtn,
            onSaved: () => {
                endpointsEditorOpen = false;
                endpointsDraft = null;
                // 只重建 DOM，不重建模型草稿：步骤表格里未保存的修改必须保留
                renderModelsTab({ keepDraft: true });
                showSettingsToast('接入点配置已保存');
            },
        });
    });
    discardBtn.addEventListener('click', () => {
        endpointsDraft = makeEndpointsDraft();
        clearDirty('llm_endpoints');
        refreshEndpointsDirtyNote();
        renderEndpointsBlock();
        showSettingsToast('已放弃接入点修改');
    });
    reloadBtn.addEventListener('click', async () => {
        reloadBtn.disabled = true;
        let reloaded = false;
        try {
            await reloadSettingsPayload();
            clearDirty('llm_endpoints');
            endpointsDraft = makeEndpointsDraft();
            refreshEndpointsDirtyNote();
            renderEndpointsBlock();
            showSettingsToast('已载入最新配置');
            reloaded = true;
        } catch (error) {
            // 失败分支不重渲染：行内报错就挂在当前编辑器的节点上，
            // 重渲染会把报错连同节点一起换掉，用户就看不到原因了
            setSettingsStatus(status, `载入失败：${error.message}`, 'error');
        } finally {
            reloadBtn.disabled = false;
        }
        // 重渲染必须放在 try/finally 之后：renderModelsTab 会重建整个模型页签，
        // reloadBtn / status 随之脱离文档；finally 已先在原节点上完成复位，
        // 之后不再有任何代码引用这些游离节点，对它们缺席的操作全部安全。
        // 成功分支要重渲染是因为步骤下拉读的是已保存接入点值——payload 重拉后
        // 不重建步骤表格，下拉就还是旧选项，可能选中已不存在的 key。
        if (reloaded) {
            renderModelsTab({ keepDraft: true });
        }
    });
    return wrap;
}

function buildEndpointsReadonly(saved) {
    const wrap = createEl('div', 'endpoints-readonly');
    const head = createEl('div', 'endpoints-head');
    head.appendChild(createEl(
        'span',
        'endpoints-hint',
        '流水线各步骤经接入点调用大模型；接入点很少改动，编辑入口收在「管理接入点」里。',
    ));
    head.appendChild(buildEndpointsManageToggle());
    wrap.appendChild(head);

    const list = createEl('ul', 'endpoints-list');
    (saved.items || []).forEach((item) => {
        const li = createEl('li', 'endpoint-item', '', { dataset: { endpointKey: item.key } });
        li.appendChild(createEl('span', 'endpoint-item-label', item.label));
        li.appendChild(createEl('code', 'endpoint-item-url', item.base_url));
        li.appendChild(createEl('span', 'endpoint-item-style',
            ENDPOINT_STYLE_LABELS[item.api_style] || item.api_style));
        li.appendChild(createEl('span', 'endpoint-item-model',
            `默认模型：${item.default_model || '-'}`));
        li.appendChild(createEl('span', 'endpoint-item-key', `Key：${item.api_key_env}`));
        const configured = apiKeyEnvConfigured(item.api_key_env);
        li.appendChild(createEl(
            'span',
            `endpoint-key-state${configured ? '' : ' is-unset'}`,
            configured
                ? 'Key 已配置'
                : `Key 未配置：先在服务器 .env.local 里添加 ${item.api_key_env}，改好后回来保存`,
        ));
        // 主机不在白名单内的已存接入点：标红并写明修法——这个接入点现在无法
        // 调用（传输层会拦下所有请求），用户要在控制台里把它修好或删掉。
        // host_allowed 在顶层 endpoints 负载里（分区值是已保存的原始结构）
        const payloadItem = endpointsPayloadInfo().items
            .find((entry) => entry.key === item.key);
        if (payloadItem && payloadItem.host_allowed === false) {
            li.classList.add('is-host-blocked');
            const host = endpointBaseUrlHost(item.base_url);
            li.appendChild(createEl(
                'span',
                'endpoint-host-blocked-note',
                `该地址的域名 ${host || '(无法解析)'} 不在允许列表内，这个接入点现在无法调用；`
                    + '请在 .env.local 的 LLM_ALLOWED_HOSTS 里追加该域名，'
                    + '或者修改地址、删除这个接入点。',
            ));
        }
        if (item.key === saved.default) {
            li.appendChild(createEl('span', 'endpoint-default-badge', '默认'));
        }
        list.appendChild(li);
    });
    wrap.appendChild(list);

    const section = settingsSection('llm_endpoints');
    if (section) wrap.appendChild(buildLastModifiedLine(section));
    return wrap;
}

// 整块重渲染（块容器节点身份不变，模型页签其他部分不受影响）；
// 传入 container 时渲染到该容器（构建期尚未挂载到文档），否则就近查找已挂载的块
function renderEndpointsBlock(container) {
    const target = container || document.querySelector('.endpoints-block');
    if (!target) return;
    clearEl(target);
    const saved = savedEndpointsValue();
    if (!saved) {
        target.appendChild(createEl(
            'p',
            'settings-env-note',
            '接入点配置尚未导入数据库，请先运行 python -m src.cli.main import-settings 完成导入。',
        ));
        return;
    }
    if (endpointsEditorOpen && endpointsDraft) {
        target.appendChild(buildEndpointsEditor(saved));
    } else {
        target.appendChild(buildEndpointsReadonly(saved));
    }
}

function buildEndpointsBlock() {
    const block = createEl('div', 'endpoints-block');
    renderEndpointsBlock(block);
    return block;
}
