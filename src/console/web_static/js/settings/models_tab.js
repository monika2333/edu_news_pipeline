// 系统设置页 - 模型页签：环境信息、八个步骤的接入点与模型（模型留空跟随
// 所选接入点的默认模型）、reasoning、逐步测试与带版本号的保存。
// 默认模型归各接入点（接入点卡片里维护），本页签不再有全局默认模型输入框。
'use strict';

function modelsDraftFromSection(section) {
    // 迁移后后端保证「model 非空 ⇒ endpoint 非空」；双 null 表示跟随默认
    // 接入点并用它的默认模型。草稿里 model 统一存字符串，null 记空串。
    const steps = {};
    stepList().forEach(({ key }) => {
        const saved = (section.value.steps && section.value.steps[key]) || {};
        steps[key] = {
            model: typeof saved.model === 'string' ? saved.model : '',
            reasoning: !!saved.reasoning,
            endpoint: saved.endpoint || null,
        };
    });
    return { steps };
}

function buildModelsEnvBlock() {
    const env = (state.payload && state.payload.environment) || {};
    const endpoints = (state.payload && state.payload.endpoints) || {};
    const card = buildSettingsCard(
        '环境（只读）',
        '超时、预算、reasoning 强度等参数在服务器环境变量中配置。修改服务器上的'
            + ' .env.local 后点「重新加载环境变量」即可生效，无需重启；'
            + '数据库连接等少数配置仍需重启服务。',
    );
    const block = createEl('div', 'settings-env-block');
    const list = createEl('dl', 'settings-env-list');
    const addItem = (term, value) => {
        list.appendChild(createEl('dt', '', term));
        list.appendChild(createEl('dd', '', value));
    };
    addItem('向量模型', env.embedding_model || '-');
    addItem('允许的接入点主机', (endpoints.allowed_hosts || []).join(', ') || '-');
    block.appendChild(list);

    // 环境变量在进程内是一次性快照（src.config 的 _ENV_LOADED）；
    // 重载按钮让「改完 .env.local」不必再登录服务器重启进程，接入点的
    // Key 配置状态随之刷新。失败分支不重渲染，错误就挂在当前块上。
    const reloadRow = createEl('div', 'settings-env-reload');
    const reloadBtn = createEl('button', 'btn btn-secondary env-reload-btn',
        '重新加载环境变量', { type: 'button' });
    const status = createEl('span', 'settings-save-status env-reload-status');
    reloadRow.appendChild(reloadBtn);
    reloadRow.appendChild(status);
    block.appendChild(reloadRow);

    reloadBtn.addEventListener('click', async () => {
        reloadBtn.disabled = true;
        let reloaded = false;
        try {
            const { response, payload } = await apiRequest(
                '/api/admin/settings/environment/reload',
                { method: 'POST' },
            );
            if (!response.ok) {
                setSettingsStatus(
                    status,
                    `重载失败：${formatApiError(payload, '请稍后重试')}`,
                    'error',
                );
            } else {
                await reloadSettingsPayload();
                showSettingsToast('环境变量已重新加载');
                reloaded = true;
            }
        } catch (error) {
            setSettingsStatus(status, `重载失败：${error.message || '网络错误'}`, 'error');
        } finally {
            reloadBtn.disabled = false;
        }
        // 重渲染放在 try/finally 之后：renderModelsTab 重建整个模型页签，
        // 按钮 / 状态节点随之脱离文档（与接入点「载入最新配置」同一时序约定）；
        // keepDraft 保留模型草稿与接入点编辑态，只重建 DOM。
        if (reloaded) {
            renderModelsTab({ keepDraft: true });
        }
    });
    card.appendChild(block);
    return card;
}

function clearStepTestResult(step) {
    const row = elements.panels.models.querySelector(`tr[data-step="${step}"]`);
    if (!row) return;
    const result = row.querySelector('.step-test-result');
    result.classList.remove('is-ok', 'is-error');
    result.textContent = '';
}

// 模型留空时灰字提示实际会用的模型（所选接入点——或默认接入点——的默认模型）。
// 为空串时 CSS :empty 把节点藏掉，不给行内流留占位。
function stepModelHintText(draft) {
    if ((draft.model || '').trim()) return '';
    const savedEndpoints = savedEndpointsValue();
    if (!savedEndpoints) return '';
    const model = endpointDefaultModel(draft.endpoint || savedEndpoints.default);
    return model ? `留空使用 ${model}` : '';
}

function updateStepModelHint(row, draft) {
    const hint = row.querySelector('.step-model-hint');
    if (!hint) return;
    hint.textContent = stepModelHintText(draft);
}

async function runStepModelTest(step) {
    const row = elements.panels.models.querySelector(`tr[data-step="${step}"]`);
    if (!row || !state.modelsDraft) return;
    const draft = state.modelsDraft.steps[step];
    const reasoning = !!draft.reasoning;
    const resultEl = row.querySelector('.step-test-result');
    const button = row.querySelector('.step-test-btn');
    const savedEndpoints = savedEndpointsValue();
    if (!savedEndpoints || !savedEndpoints.default) {
        setSettingsStatus(resultEl, '失败：接入点配置尚未保存', 'error');
        return;
    }
    // 模型框有值就测该值；留空就测所选（或默认）接入点已保存的默认模型。
    // 负载里的 endpoint 与实际解析一致：跟随默认也发解析出的默认接入点 key。
    const typed = (draft.model || '').trim();
    const resolvedEndpoint = draft.endpoint || savedEndpoints.default;
    const model = typed || endpointDefaultModel(resolvedEndpoint) || '';
    if (!model) {
        setSettingsStatus(resultEl, '失败：模型名不能为空', 'error');
        return;
    }
    button.disabled = true;
    const stopTicker = startElapsedTicker(resultEl, '测试中');
    try {
        const body = { step, model, reasoning, endpoint: resolvedEndpoint };
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

// 接入点下拉第一项：跟随默认（默认接入点显示名）；其后是各已保存接入点。
function buildStepEndpointSelect(displayName, draft) {
    const savedEndpoints = savedEndpointsValue();
    const select = createEl('select', 'step-endpoint-select', '', {
        'aria-label': `${displayName}接入点`,
    });
    const defaultLabel = savedEndpoints && savedEndpoints.default
        ? endpointDisplayLabel(savedEndpoints.default)
        : '未设置';
    select.appendChild(createEl('option', '', `跟随默认（${defaultLabel}）`, { value: '' }));
    ((savedEndpoints && savedEndpoints.items) || []).forEach((item) => {
        select.appendChild(createEl('option', '', item.label, { value: item.key }));
    });
    select.value = draft.endpoint || '';
    return select;
}

function buildStepRow(step, displayName) {
    const draft = state.modelsDraft.steps[step];
    const savedEndpoints = savedEndpointsValue();
    const row = createEl('tr', '', '', { dataset: { step, stepLabel: displayName } });

    row.appendChild(createEl('td', 'settings-step-name', displayName));

    const modelCell = createEl('td', 'settings-step-model');
    const controls = createEl('div', 'step-controls');
    const endpointSelect = buildStepEndpointSelect(displayName, draft);
    const modelInput = createEl('input', 'step-model-input', '', {
        type: 'text',
        'aria-label': `${displayName}模型名`,
        placeholder: '留空使用所选接入点的默认模型',
    });
    modelInput.value = draft.model || '';
    controls.appendChild(endpointSelect);
    controls.appendChild(modelInput);
    modelCell.appendChild(controls);
    const hint = createEl('span', 'step-model-hint');
    modelCell.appendChild(hint);
    row.appendChild(modelCell);
    // 挂载后才能按行查找：updateStepModelHint 用 row.querySelector 找灰字节点
    updateStepModelHint(row, draft);

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

    endpointSelect.addEventListener('change', () => {
        draft.endpoint = endpointSelect.value || null;
        // 模型名与接入点服务商绑定，跨服务商保留旧值必然是错的：
        // 换接入点（含切回跟随默认）一律清空模型输入，灰字换成新接入点的默认模型
        draft.model = '';
        modelInput.value = '';
        updateStepModelHint(row, draft);
        clearStepTestResult(row.dataset.step);
        markDirty('llm_models');
    });
    modelInput.addEventListener('input', () => {
        draft.model = modelInput.value;
        if (draft.endpoint === null && (draft.model || '').trim() && savedEndpoints) {
            // 「跟随默认」下开始输入模型：模型名只属于某个服务商，
            // 当场切到默认接入点本身，用户看得见这个变化，不是保存时悄悄改
            draft.endpoint = savedEndpoints.default;
            endpointSelect.value = savedEndpoints.default;
        }
        updateStepModelHint(row, draft);
        clearStepTestResult(row.dataset.step);
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
    const value = { steps: {} };
    stepList().forEach(({ key }) => {
        const draft = state.modelsDraft.steps[key];
        const model = (draft.model || '').trim();
        value.steps[key] = {
            // 跟随默认必然双 null；选了接入点则模型留空也合法（用它的默认模型）
            model: draft.endpoint === null ? null : (model || null),
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
    // 步骤表格里未保存的修改不能丢；灰字提示随之反映新的默认模型
    if (!keepDraft || !state.modelsDraft) {
        state.modelsDraft = modelsDraftFromSection(section);
    }

    const mainCard = buildSettingsCard(
        '流水线步骤模型',
        '接入点选「跟随默认」时使用默认接入点；模型留空时使用所选接入点的默认模型。'
            + '保存后下一轮流水线生效，查重复核立即生效。',
    );

    // 接入点分区为脏时的提示（节点常驻、hidden 切换，见 endpoints.js）
    mainCard.appendChild(buildEndpointsDirtyNote());

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
    mainCard.appendChild(tableWrap);

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
    mainCard.appendChild(buildLastModifiedLine(section));
    mainCard.appendChild(saveBar);
    panel.appendChild(mainCard);

    const endpointsCard = buildSettingsCard('接入点');
    endpointsCard.appendChild(buildEndpointsBlock());
    panel.appendChild(endpointsCard);

    panel.appendChild(buildModelsEnvBlock());

    saveBtn.addEventListener('click', async () => {
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
