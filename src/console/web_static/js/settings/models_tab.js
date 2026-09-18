// 系统设置页 - 模型页签：环境信息、接入点块、默认模型、七个步骤的
// 接入点与模型来源、reasoning、逐步测试与带版本号的保存。
'use strict';

function modelsDraftFromSection(section) {
    const savedEndpoints = savedEndpointsValue();
    // 旧数据允许「指定了模型但 endpoint 为 null」，界面上归一为「指定 + 默认接入点」：
    // 与后端 resolve（endpoint 缺省落到 default）语义一致，且保持「跟随默认 = 双 null」
    const fallbackEndpoint = savedEndpoints ? savedEndpoints.default : null;
    const steps = {};
    stepList().forEach(({ key }) => {
        const saved = (section.value.steps && section.value.steps[key]) || {};
        let model = saved.model === undefined ? null : saved.model;
        let endpoint = saved.endpoint === undefined ? null : saved.endpoint;
        if (model !== null && endpoint === null) endpoint = fallbackEndpoint;
        steps[key] = {
            model,
            reasoning: !!saved.reasoning,
            endpoint,
        };
    });
    return { default: section.value.default || '', steps };
}

function buildModelsEnvBlock() {
    const env = (state.payload && state.payload.environment) || {};
    const endpoints = (state.payload && state.payload.endpoints) || {};
    const block = createEl('div', 'settings-env-block');
    block.appendChild(createEl('h3', 'settings-env-heading', '环境（只读）'));
    const list = createEl('dl', 'settings-env-list');
    const addItem = (term, value) => {
        list.appendChild(createEl('dt', '', term));
        list.appendChild(createEl('dd', '', value));
    };
    addItem('向量模型', env.embedding_model || '-');
    addItem('允许的接入点主机', (endpoints.allowed_hosts || []).join(', ') || '-');
    block.appendChild(list);
    block.appendChild(createEl(
        'p',
        'settings-env-note',
        '超时、预算、reasoning 强度等参数在服务器环境变量中配置，修改需重启服务。',
    ));
    return block;
}

function modelsFollowHintText() {
    const savedEndpoints = savedEndpointsValue();
    const endpointLabel = savedEndpoints && savedEndpoints.default
        ? endpointDisplayLabel(savedEndpoints.default)
        : '未设置';
    return `跟随默认（接入点：${endpointLabel} · 模型：${state.modelsDraft.default || '未填写'}）`;
}

function updateModelsFollowHints() {
    elements.panels.models
        .querySelectorAll('tr[data-step]')
        .forEach((row) => {
            const step = row.dataset.step;
            const draft = state.modelsDraft.steps[step];
            const hint = row.querySelector('.step-follow-hint');
            const input = row.querySelector('.step-model-input');
            const select = row.querySelector('.step-endpoint-select');
            // 跟随默认 = 接入点与模型双空；指定 = 两者都有值
            const follow = draft.endpoint === null;
            hint.hidden = !follow;
            input.hidden = follow;
            select.hidden = follow;
            if (follow) hint.textContent = modelsFollowHintText();
        });
}

function clearStepTestResult(step) {
    const row = elements.panels.models.querySelector(`tr[data-step="${step}"]`);
    if (!row) return;
    const result = row.querySelector('.step-test-result');
    result.classList.remove('is-ok', 'is-error');
    result.textContent = '';
}

function clearFollowStepTestResults() {
    Object.entries(state.modelsDraft.steps).forEach(([step, draft]) => {
        if (draft.endpoint === null) clearStepTestResult(step);
    });
}

async function runStepModelTest(step) {
    const row = elements.panels.models.querySelector(`tr[data-step="${step}"]`);
    if (!row || !state.modelsDraft) return;
    const draft = state.modelsDraft.steps[step];
    // 测试使用页面当前值：跟随默认时取默认模型输入框的当前内容。
    const model = (draft.endpoint === null ? state.modelsDraft.default : draft.model).trim();
    const reasoning = !!draft.reasoning;
    const resultEl = row.querySelector('.step-test-result');
    const button = row.querySelector('.step-test-btn');
    if (!model) {
        setSettingsStatus(resultEl, '失败：模型名不能为空', 'error');
        return;
    }
    button.disabled = true;
    const stopTicker = startElapsedTicker(resultEl, '测试中');
    try {
        // 指定接入点的步骤带上 endpoint 由后端落到该接入点；跟随默认时不带该字段
        const body = { step, model, reasoning };
        if (draft.endpoint !== null) body.endpoint = draft.endpoint;
        const { response, payload } = await apiRequest('/api/admin/settings/llm_models/test', {
            method: 'POST',
            body,
        });
        if (!response.ok) {
            setSettingsStatus(resultEl, `失败：${formatApiError(payload, '测试请求失败')}`, 'error');
        } else if (payload.success) {
            const seconds = (Number(payload.elapsed_ms) / 1000).toFixed(1);
            setSettingsStatus(resultEl, `成功 · 耗时 ${seconds} 秒`, 'ok');
        } else {
            setSettingsStatus(resultEl, `失败：${payload.error || '未知原因'}`, 'error');
        }
    } catch (error) {
        setSettingsStatus(resultEl, `失败：${error.message || '网络错误'}`, 'error');
    } finally {
        stopTicker();
        button.disabled = false;
    }
}

function buildStepRow(step, displayName) {
    const draft = state.modelsDraft.steps[step];
    const savedEndpoints = savedEndpointsValue();
    const row = createEl('tr', '', '', { dataset: { step } });

    row.appendChild(createEl('td', 'settings-step-name', displayName));

    const modelCell = createEl('td', 'settings-step-model');
    const modeSelect = createEl('select', 'step-model-mode', '', {
        'aria-label': `${displayName}模型来源`,
    });
    modeSelect.appendChild(createEl('option', '', '跟随默认', { value: 'follow' }));
    modeSelect.appendChild(createEl('option', '', '指定', { value: 'custom' }));
    modeSelect.value = draft.endpoint === null ? 'follow' : 'custom';
    const followHint = createEl('span', 'step-follow-hint', modelsFollowHintText());
    // 接入点下拉只列「已保存」的接入点：后端校验步骤引用时查的是库里的接入点，
    // 草稿里的新接入点存不进去；新增接入点先保存，这里立即能选（下拉数据源见
    // core.js 的 savedEndpointsValue）。
    const endpointSelect = createEl('select', 'step-endpoint-select', '', {
        'aria-label': `${displayName}接入点`,
    });
    ((savedEndpoints && savedEndpoints.items) || []).forEach((item) => {
        endpointSelect.appendChild(createEl('option', '', item.label, { value: item.key }));
    });
    endpointSelect.value = draft.endpoint || '';
    endpointSelect.hidden = draft.endpoint === null;
    const modelInput = createEl('input', 'step-model-input', '', {
        type: 'text',
        'aria-label': `${displayName}模型名`,
        placeholder: '模型名，如 deepseek/deepseek-v4-flash',
    });
    modelInput.value = draft.model === null ? '' : draft.model;
    modelInput.hidden = draft.endpoint === null;
    modelCell.appendChild(modeSelect);
    modelCell.appendChild(followHint);
    modelCell.appendChild(endpointSelect);
    modelCell.appendChild(modelInput);
    row.appendChild(modelCell);

    const reasoningCell = createEl('td', 'settings-step-reasoning');
    const reasoningToggle = createEl('input', 'step-reasoning', '', {
        type: 'checkbox',
        'aria-label': `${displayName} reasoning 开关`,
    });
    reasoningToggle.checked = draft.reasoning;
    reasoningCell.appendChild(reasoningToggle);
    row.appendChild(reasoningCell);

    const testCell = createEl('td', 'settings-step-test');
    const testBtn = createEl('button', 'btn btn-secondary step-test-btn', '测试', { type: 'button' });
    const testResult = createEl('span', 'step-test-result');
    testCell.appendChild(testBtn);
    testCell.appendChild(testResult);
    row.appendChild(testCell);

    modeSelect.addEventListener('change', () => {
        if (modeSelect.value === 'custom') {
            // 首次切到「指定」时选中当前默认接入点；此前选过则保留上次选择
            draft.endpoint = endpointSelect.value
                || (savedEndpoints && savedEndpoints.default)
                || '';
            endpointSelect.value = draft.endpoint;
            // 模型名与接入点服务商绑定，跨服务商预填必然是错的（OpenRouter 上是
            // deepseek/xxx，官网是 deepseek-flash）：切到指定一律留空，模型必须现填
            modelInput.value = '';
            draft.model = '';
        } else {
            draft.endpoint = null;
            draft.model = null;
        }
        updateModelsFollowHints();
        clearStepTestResult(step);
        markDirty('llm_models');
    });
    endpointSelect.addEventListener('change', () => {
        if (draft.endpoint === null) return;
        draft.endpoint = endpointSelect.value;
        // 换接入点即换服务商：清空模型输入与该行的测试结果，不留错误的旧值
        modelInput.value = '';
        draft.model = '';
        clearStepTestResult(step);
        markDirty('llm_models');
    });
    modelInput.addEventListener('input', () => {
        if (draft.endpoint !== null) draft.model = modelInput.value;
        clearStepTestResult(step);
        markDirty('llm_models');
    });
    reasoningToggle.addEventListener('change', () => {
        draft.reasoning = reasoningToggle.checked;
        clearStepTestResult(step);
        markDirty('llm_models');
    });
    testBtn.addEventListener('click', () => {
        runStepModelTest(step);
    });

    return row;
}

function buildModelsSaveValue() {
    const value = { default: state.modelsDraft.default.trim(), steps: {} };
    stepList().forEach(({ key }) => {
        const draft = state.modelsDraft.steps[key];
        value.steps[key] = {
            model: draft.endpoint === null ? null : draft.model.trim(),
            reasoning: !!draft.reasoning,
            endpoint: draft.endpoint,
        };
    });
    return value;
}

function renderModelsTab({ keepDraft = false } = {}) {
    const panel = elements.panels.models;
    clearEl(panel);
    const section = settingsSection('llm_models');
    if (!section) {
        renderImportNotice(panel, '模型配置');
        return;
    }
    // 接入点保存成功后的重渲染必须保留模型草稿（keepDraft）：只重建 DOM，
    // 步骤表格里未保存的修改不能丢
    if (!keepDraft || !state.modelsDraft) {
        state.modelsDraft = modelsDraftFromSection(section);
    }

    panel.appendChild(buildModelsEnvBlock());
    panel.appendChild(buildEndpointsBlock());
    panel.appendChild(createEl(
        'p',
        'settings-effect-note',
        '生效时间：流水线下一轮生效；查重复核立即生效。',
    ));
    panel.appendChild(buildLastModifiedLine(section));

    const defaultRow = createEl('div', 'settings-default-row');
    const defaultLabel = createEl('label', 'settings-default-label', '默认模型', {
        for: 'models-default-input',
    });
    const defaultInput = createEl('input', 'settings-default-input', '', {
        id: 'models-default-input',
        type: 'text',
        placeholder: '模型名，如 deepseek/deepseek-v4-flash',
    });
    defaultInput.value = state.modelsDraft.default;
    defaultInput.addEventListener('input', () => {
        state.modelsDraft.default = defaultInput.value;
        updateModelsFollowHints();
        clearFollowStepTestResults();
        markDirty('llm_models');
    });
    defaultRow.appendChild(defaultLabel);
    defaultRow.appendChild(defaultInput);
    panel.appendChild(defaultRow);

    // 接入点分区为脏时的提示（节点常驻、hidden 切换，见 endpoints.js）
    panel.appendChild(buildEndpointsDirtyNote());

    const tableWrap = createEl('div', 'admin-table-wrap');
    const table = createEl('table', 'admin-table settings-steps-table');
    const thead = createEl('thead');
    const headRow = createEl('tr');
    ['步骤', '接入点与模型', 'reasoning', '测试'].forEach((text) => {
        headRow.appendChild(createEl('th', '', text));
    });
    thead.appendChild(headRow);
    table.appendChild(thead);
    const tbody = createEl('tbody', '', '', { id: 'models-steps-body' });
    stepList().forEach(({ key, display_name: displayName }) => {
        tbody.appendChild(buildStepRow(key, displayName));
    });
    table.appendChild(tbody);
    tableWrap.appendChild(table);
    panel.appendChild(tableWrap);
    updateModelsFollowHints();

    const saveBar = createEl('div', 'settings-save-bar');
    const saveBtn = createEl('button', 'btn btn-primary', '保存模型配置', {
        id: 'btn-models-save',
        type: 'button',
    });
    const discardBtn = createEl('button', 'btn btn-secondary', '放弃修改', {
        id: 'btn-models-discard',
        type: 'button',
    });
    const reloadBtn = createEl('button', 'btn btn-secondary', '载入最新配置', {
        id: 'btn-models-reload',
        type: 'button',
    });
    reloadBtn.hidden = true;
    const status = createEl('span', 'settings-save-status', '', { id: 'models-save-status' });
    saveBar.appendChild(saveBtn);
    saveBar.appendChild(discardBtn);
    saveBar.appendChild(reloadBtn);
    saveBar.appendChild(status);
    panel.appendChild(saveBar);

    saveBtn.addEventListener('click', async () => {
        if (!state.modelsDraft.default.trim()) {
            setSettingsStatus(status, '默认模型不能为空。', 'error');
            return;
        }
        // 与后端硬规则对齐的前端拦截：指定接入点就必须指定模型——界面上
        // 「只选接入点不填模型」这个状态不该等 422 回来才发现
        const missing = stepList().filter(({ key }) => {
            const draft = state.modelsDraft.steps[key];
            return draft.endpoint !== null && !draft.model.trim();
        });
        if (missing.length) {
            const names = missing.map(({ display_name }) => display_name).join('、');
            setSettingsStatus(
                status,
                `保存已取消：${names} 指定了接入点但模型名为空，请填写模型名或切回跟随默认。`,
                'error',
            );
            return;
        }
        await saveSettingsSection('llm_models', buildModelsSaveValue(), {
            statusEl: status,
            saveBtn,
            reloadBtn,
            onSaved: () => {
                renderModelsTab();
                showSettingsToast('模型配置已保存');
            },
        });
    });
    discardBtn.addEventListener('click', () => {
        clearDirty('llm_models');
        renderModelsTab();
        showSettingsToast('已放弃修改');
    });
    reloadBtn.addEventListener('click', async () => {
        reloadBtn.disabled = true;
        try {
            await reloadSettingsPayload();
            clearDirty('llm_models');
            renderModelsTab();
            showSettingsToast('已载入最新配置');
        } catch (error) {
            setSettingsStatus(status, `载入失败：${error.message}`, 'error');
        } finally {
            reloadBtn.disabled = false;
        }
    });
}
