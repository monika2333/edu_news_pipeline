// 系统设置页 - 模型页签：环境信息、默认模型、七个步骤的模型来源与 reasoning、
// 逐步测试与带版本号的保存。
'use strict';

function modelsDraftFromSection(section) {
    const steps = {};
    stepList().forEach(({ key }) => {
        const saved = (section.value.steps && section.value.steps[key]) || {};
        steps[key] = {
            model: saved.model === undefined ? null : saved.model,
            reasoning: !!saved.reasoning,
        };
    });
    return { default: section.value.default || '', steps };
}

function buildModelsEnvBlock() {
    const env = (state.payload && state.payload.environment) || {};
    const block = createEl('div', 'settings-env-block');
    block.appendChild(createEl('h3', 'settings-env-heading', '环境（只读）'));
    const list = createEl('dl', 'settings-env-list');
    const addItem = (term, value) => {
        list.appendChild(createEl('dt', '', term));
        list.appendChild(createEl('dd', '', value));
    };
    addItem('API Key', env.llm_api_key_configured ? '已配置' : '未配置');
    addItem('API 地址', env.llm_api_base_url || '-');
    addItem('向量模型', env.embedding_model || '-');
    block.appendChild(list);
    block.appendChild(createEl(
        'p',
        'settings-env-note',
        '超时、预算、reasoning 强度等参数在服务器环境变量中配置，修改需重启服务。',
    ));
    return block;
}

function modelsFollowHintText() {
    return `跟随默认（当前：${state.modelsDraft.default || '未填写'}）`;
}

function updateModelsFollowHints() {
    elements.panels.models
        .querySelectorAll('tr[data-step]')
        .forEach((row) => {
            const step = row.dataset.step;
            const draft = state.modelsDraft.steps[step];
            const hint = row.querySelector('.step-follow-hint');
            const input = row.querySelector('.step-model-input');
            const follow = draft.model === null;
            hint.hidden = !follow;
            input.hidden = follow;
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
        if (draft.model === null) clearStepTestResult(step);
    });
}

async function runStepModelTest(step) {
    const row = elements.panels.models.querySelector(`tr[data-step="${step}"]`);
    if (!row || !state.modelsDraft) return;
    const draft = state.modelsDraft.steps[step];
    // 测试使用页面当前值：跟随默认时取默认模型输入框的当前内容。
    const model = (draft.model === null ? state.modelsDraft.default : draft.model).trim();
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
        const { response, payload } = await apiRequest('/api/admin/settings/llm_models/test', {
            method: 'POST',
            body: { step, model, reasoning },
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
    const row = createEl('tr', '', '', { dataset: { step } });

    row.appendChild(createEl('td', 'settings-step-name', displayName));

    const modelCell = createEl('td', 'settings-step-model');
    const modeSelect = createEl('select', 'step-model-mode', '', {
        'aria-label': `${displayName}模型来源`,
    });
    modeSelect.appendChild(createEl('option', '', '跟随默认', { value: 'follow' }));
    modeSelect.appendChild(createEl('option', '', '指定', { value: 'custom' }));
    modeSelect.value = draft.model === null ? 'follow' : 'custom';
    const followHint = createEl('span', 'step-follow-hint', modelsFollowHintText());
    const modelInput = createEl('input', 'step-model-input', '', {
        type: 'text',
        'aria-label': `${displayName}模型名`,
        placeholder: '模型名，如 deepseek/deepseek-v4-flash',
    });
    modelInput.value = draft.model === null ? state.modelsDraft.default : draft.model;
    modelCell.appendChild(modeSelect);
    modelCell.appendChild(followHint);
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
            modelInput.value = state.modelsDraft.default;
            draft.model = modelInput.value.trim();
        } else {
            draft.model = null;
        }
        updateModelsFollowHints();
        clearStepTestResult(step);
        markDirty('llm_models');
    });
    modelInput.addEventListener('input', () => {
        if (draft.model !== null) draft.model = modelInput.value;
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
            model: draft.model === null ? null : draft.model.trim(),
            reasoning: !!draft.reasoning,
        };
    });
    return value;
}

function renderModelsTab() {
    const panel = elements.panels.models;
    clearEl(panel);
    const section = settingsSection('llm_models');
    if (!section) {
        renderImportNotice(panel, '模型配置');
        return;
    }
    state.modelsDraft = modelsDraftFromSection(section);

    panel.appendChild(buildModelsEnvBlock());
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

    const tableWrap = createEl('div', 'admin-table-wrap');
    const table = createEl('table', 'admin-table settings-steps-table');
    const thead = createEl('thead');
    const headRow = createEl('tr');
    ['步骤', '模型', 'reasoning', '测试'].forEach((text) => {
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
