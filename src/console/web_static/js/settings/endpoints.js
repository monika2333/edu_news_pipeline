// 系统设置页 - 接入点块：只读清单 + 「管理接入点」编辑态（新增/删除/字段编辑、
// 分区独立保存）。数据来源：已保存分区 sections.llm_endpoints（步骤下拉同样只读
// 已保存值，不读编辑态草稿）；allowed_hosts 与 Key 配置状态来自顶层 endpoints 负载。
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
        || { default: null, allowed_hosts: [], items: [] };
}

// 该接入点的 Key 是否已在服务器环境配置；null 表示未知（草稿里新增的接入点）
function endpointKeyConfigured(key) {
    const found = endpointsPayloadInfo().items.find((item) => item.key === key);
    return found ? found.api_key_env_configured : null;
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
// 这里拦只是为了省一次往返并把原因（需要改服务器 .env 并重启）说得更具体。
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
        return `${host} 不在允许的接入点主机列表内：新增服务商需要先在服务器 `
            + '.env 的 LLM_ALLOWED_HOSTS 里加域名并重启服务，再回到本页保存。';
    }
    return '';
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
    envInput.value = item.api_key_env;
    envInput.addEventListener('input', () => {
        item.api_key_env = envInput.value;
        markEndpointsDirty();
    });
    // Key 状态逐个接入点显示：未配置时提示需要在 .env 里添加该变量名并重启服务
    const configured = item.isNew ? null : endpointKeyConfigured(item.key);
    let envHint = null;
    if (configured === false) {
        envHint = createEl('span', 'endpoint-field-hint is-warning',
            `该环境变量当前未配置：需在服务器 .env 里添加 ${item.api_key_env} 并重启服务。`);
    } else if (configured === null) {
        envHint = createEl('span', 'endpoint-field-hint',
            '保存后需在服务器 .env 里配置该变量并重启服务。');
    }
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

function buildEndpointsEditor(saved) {
    const wrap = createEl('div', 'endpoints-editor');
    const head = createEl('div', 'endpoints-head');
    head.appendChild(createEl(
        'span',
        'endpoints-hint',
        '删除被步骤引用或默认的接入点会被拦截；修改完成后点右下角「保存接入点」提交。',
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
        // 行内预校验：任一行不通过就在该行报错，不发请求
        const allowedHosts = endpointsPayloadInfo().allowed_hosts;
        let hasError = false;
        endpointsDraft.items.forEach((item) => {
            const row = wrap.querySelector(`[data-client-id="${item.clientId}"]`);
            if (!row) return;
            const message = endpointBaseUrlError(item.base_url, allowedHosts);
            row.querySelector('.endpoint-row-error').textContent = message;
            if (message) hasError = true;
        });
        if (hasError) {
            setSettingsStatus(status, '接入点未通过校验，请按行内提示修改。', 'error');
            return;
        }
        await saveSettingsSection('llm_endpoints', buildEndpointsSaveValue(), {
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
        li.appendChild(createEl('span', 'endpoint-item-key', `Key：${item.api_key_env}`));
        const configured = endpointKeyConfigured(item.key);
        li.appendChild(createEl(
            'span',
            `endpoint-key-state${configured ? '' : ' is-unset'}`,
            configured ? 'Key 已配置' : 'Key 未配置：需在 .env 里添加该变量并重启服务',
        ));
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
    target.appendChild(createEl('h3', 'settings-env-heading', '接入点'));
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
    const block = createEl('div', 'settings-env-block endpoints-block');
    renderEndpointsBlock(block);
    return block;
}
