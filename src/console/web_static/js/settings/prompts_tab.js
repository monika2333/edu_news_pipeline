// 系统设置页 - 提示词页签：左侧提示词列表、右侧模板编辑器与历史版本。
// 内容来自独立的 /api/admin/prompts 负载（不进 app_settings）；保存即追加一个
// revision，版本号由服务端分配，前端只负责展示与在冲突时保留草稿。
'use strict';

const PROMPT_SOURCE_LABELS = { database: '数据库', file: '文件默认' };
const PROMPT_HISTORY_PAGE_SIZE = 20;
// 与后端 PROMPT_HISTORY_MAX_LIMIT 保持一致，避免「加载更多」到上限后原地打转。
const PROMPT_HISTORY_MAX_PAGE = 100;
const PROMPT_KIND_LABELS = { score: '0-100 评分', decision: 'JSON 判定' };

function promptItems() {
    return (state.prompts.payload && state.prompts.payload.items) || [];
}

function promptItem(key) {
    return promptItems().find((item) => item.key === key) || null;
}

function selectedPromptItem() {
    const items = promptItems();
    if (!items.length) return null;
    return promptItem(state.prompts.selectedKey) || items[0];
}

function promptHasDraft(key) {
    if (!Object.prototype.hasOwnProperty.call(state.prompts.drafts, key)) return false;
    const item = promptItem(key);
    return !!item && state.prompts.drafts[key] !== item.content;
}

function promptDraft(key) {
    if (Object.prototype.hasOwnProperty.call(state.prompts.drafts, key)) {
        return state.prompts.drafts[key];
    }
    const item = promptItem(key);
    return item ? item.content : '';
}

function promptDraftsDirty() {
    return promptItems().some((item) => promptHasDraft(item.key));
}

function promptSourceLabel(item) {
    return PROMPT_SOURCE_LABELS[item.source] || item.source || '-';
}

function promptUpdatedLine(item) {
    if (item.source === 'file') {
        return '当前来自 config/prompts 默认文件；保存后会写入数据库并生成新版本。';
    }
    const name = (item.updated_by && item.updated_by.display_name) || '未知';
    return `最后修改：${name} · ${formatLocalDateTime(item.updated_at)}`;
}

function capturePromptDraft() {
    const key = state.prompts.selectedKey;
    if (!key) return;
    const editor = document.getElementById('prompt-editor');
    if (!editor) return;
    const item = promptItem(key);
    if (!item) return;
    if (editor.value !== item.content) {
        state.prompts.drafts[key] = editor.value;
    } else {
        delete state.prompts.drafts[key];
    }
}

function syncPromptNavDirty(key) {
    elements.panels.prompts
        .querySelectorAll('.prompt-nav-item')
        .forEach((button) => {
            if (button.dataset.promptKey !== key) return;
            const dirty = promptHasDraft(key);
            button.classList.toggle('is-dirty', dirty);
            const badge = button.querySelector('.prompt-nav-dirty');
            if (badge) badge.hidden = !dirty;
        });
}

function replacePromptItem(updated) {
    const items = promptItems();
    const index = items.findIndex((item) => item.key === updated.key);
    if (index >= 0) items[index] = updated;
}

async function loadPrompts() {
    state.prompts.loading = true;
    try {
        const { response, payload } = await apiRequest('/api/admin/prompts');
        if (!response.ok) {
            throw new Error(formatApiError(payload, '加载提示词失败'));
        }
        state.prompts.payload = payload;
        state.prompts.error = null;
        state.prompts.history = {};
        const items = promptItems();
        if (!state.prompts.selectedKey || !items.some((item) => item.key === state.prompts.selectedKey)) {
            state.prompts.selectedKey = items.length ? items[0].key : null;
        }
        Object.keys(state.prompts.drafts).forEach((key) => {
            if (!items.some((item) => item.key === key)) delete state.prompts.drafts[key];
        });
        return true;
    } catch (error) {
        state.prompts.error = error.message || '加载提示词失败';
        return false;
    } finally {
        state.prompts.loading = false;
    }
}

function renderPromptsLoadError(panel) {
    const notice = createEl('div', 'settings-import-notice');
    notice.appendChild(createEl('p', 'settings-import-title', '提示词加载失败'));
    notice.appendChild(createEl('p', '', state.prompts.error || '请稍后重试。'));
    const retry = createEl('button', 'btn btn-secondary', '重新加载', { type: 'button' });
    retry.addEventListener('click', async () => {
        retry.disabled = true;
        await loadPrompts();
        renderPromptsTab();
    });
    notice.appendChild(retry);
    panel.appendChild(notice);
}

function buildPromptNavItem(item) {
    const button = createEl('button', 'prompt-nav-item', '', {
        type: 'button',
        dataset: { promptKey: item.key },
        'aria-pressed': String(item.key === state.prompts.selectedKey),
    });
    if (item.key === state.prompts.selectedKey) button.classList.add('is-active');
    const name = createEl('span', 'prompt-nav-name', item.label);
    const meta = createEl('span', 'prompt-nav-meta');
    meta.append(
        createEl('span', 'prompt-badge prompt-version', item.version),
        createEl('span', 'prompt-badge prompt-source', promptSourceLabel(item)),
    );
    const dirty = createEl('span', 'prompt-nav-dirty', '未保存');
    dirty.hidden = !promptHasDraft(item.key);
    if (!dirty.hidden) button.classList.add('is-dirty');
    button.append(name, meta, dirty);
    button.addEventListener('click', () => selectPrompt(item.key));
    return button;
}

function selectPrompt(key) {
    if (state.prompts.selectedKey === key) return;
    capturePromptDraft();
    state.prompts.selectedKey = key;
    renderPromptsTab({ capture: false });
}

function buildPromptEditorCard(item) {
    const card = buildSettingsCard();
    const head = createEl('div', 'prompt-head');
    const heading = createEl('div', 'prompt-heading');
    heading.appendChild(createEl('h3', 'prompt-title', item.label));
    heading.appendChild(createEl('p', 'prompt-desc', item.description));
    const meta = createEl('div', 'prompt-head-meta');
    meta.append(
        createEl('span', 'prompt-badge prompt-version', item.version),
        createEl('span', 'prompt-badge prompt-source', promptSourceLabel(item)),
        createEl(
            'span',
            'prompt-badge',
            PROMPT_KIND_LABELS[item.kind] || item.kind,
        ),
    );
    head.append(heading, meta);
    card.appendChild(head);
    card.appendChild(createEl('p', 'prompt-updated', promptUpdatedLine(item)));
    card.appendChild(createEl(
        'p',
        'prompt-hint',
        '只有 <prompt> 标签内的正文会发送给模型；文章标题、来源、摘要与正文摘录'
            + '由系统自动追加，不需要在提示词里写占位符。',
    ));

    const textarea = createEl('textarea', 'prompt-textarea', '', {
        rows: '18',
        spellcheck: 'false',
        id: 'prompt-editor',
        'aria-label': `${item.label}提示词正文`,
    });
    textarea.value = promptDraft(item.key);
    card.appendChild(textarea);

    const noteRow = createEl('div', 'prompt-note-row');
    const noteInput = createEl('input', 'prompt-note-input', '', {
        type: 'text',
        id: 'prompt-note-input',
        maxlength: '200',
        placeholder: '例如：收紧 Tier 1 的判定口径（可选，记入历史）',
        'aria-label': '修改说明',
    });
    noteRow.append(
        createEl('label', 'prompt-note-label', '修改说明', { for: 'prompt-note-input' }),
        noteInput,
    );
    card.appendChild(noteRow);

    const footer = createEl('div', 'prompt-editor-foot');
    const count = createEl('span', 'prompt-char-count');
    const status = createEl('span', 'settings-save-status prompt-save-status', '', {
        role: 'status',
        'aria-live': 'polite',
        id: 'prompt-save-status',
    });
    const actions = createEl('div', 'prompt-actions');
    const saveBtn = createEl('button', 'btn btn-primary', '保存为新版本', { type: 'button' });
    const discardBtn = createEl('button', 'btn btn-secondary', '放弃修改', { type: 'button' });
    const reloadBtn = createEl('button', 'btn btn-secondary', '载入最新配置', { type: 'button' });
    reloadBtn.hidden = true;
    actions.append(saveBtn, discardBtn, reloadBtn);
    footer.append(count, status, actions);
    card.appendChild(footer);

    const refreshEditorState = () => {
        const dirty = textarea.value !== item.content;
        count.textContent = `${textarea.value.length} 字符`;
        saveBtn.disabled = !dirty || state.prompts.saving;
        discardBtn.disabled = !dirty || state.prompts.saving;
    };
    refreshEditorState();

    textarea.addEventListener('input', () => {
        if (textarea.value === item.content) {
            delete state.prompts.drafts[item.key];
        } else {
            state.prompts.drafts[item.key] = textarea.value;
        }
        refreshEditorState();
        syncPromptNavDirty(item.key);
        setSettingsStatus(
            status,
            textarea.value === item.content ? '' : '有未保存的修改',
        );
    });

    saveBtn.addEventListener('click', async () => {
        if (state.prompts.saving) return;
        const value = textarea.value;
        if (!value.trim()) {
            setSettingsStatus(status, '提示词内容不能为空。', 'error');
            textarea.focus();
            return;
        }
        if (value === item.content) {
            setSettingsStatus(status, '内容未变化。');
            return;
        }
        state.prompts.saving = true;
        saveBtn.disabled = true;
        discardBtn.disabled = true;
        reloadBtn.disabled = true;
        const stopTicker = startElapsedTicker(status, '保存中');
        try {
            const { response, payload } = await apiRequest(
                `/api/admin/prompts/${encodeURIComponent(item.key)}`,
                {
                    method: 'PUT',
                    body: {
                        content: value,
                        expected_revision_id: item.revision_id,
                        note: noteInput.value.trim() || null,
                    },
                },
            );
            if (response.ok) {
                const updated = payload.item;
                replacePromptItem(updated);
                delete state.prompts.drafts[item.key];
                delete state.prompts.history[item.key];
                state.prompts.selectedKey = item.key;
                state.prompts.saving = false;
                stopTicker();
                renderPromptsTab({ capture: false });
                const nextStatus = document.getElementById('prompt-save-status');
                if (nextStatus) {
                    setSettingsStatus(nextStatus, `已保存为 ${updated.version}。`, 'ok');
                }
                showSettingsToast(`提示词已保存为 ${updated.version}`);
                return;
            }
            if (response.status === 409) {
                setSettingsStatus(
                    status,
                    `保存冲突：${formatApiError(payload, '提示词已在别处被修改')}。`
                        + '本地修改已保留，可点击「载入最新配置」后再调整。',
                    'error',
                );
                reloadBtn.hidden = false;
            } else {
                setSettingsStatus(
                    status,
                    `保存失败：${formatApiError(payload, '请检查内容')}`,
                    'error',
                );
            }
        } catch (error) {
            setSettingsStatus(status, `保存失败：${error.message || '网络错误'}`, 'error');
        } finally {
            stopTicker();
            state.prompts.saving = false;
            saveBtn.disabled = false;
            discardBtn.disabled = false;
            reloadBtn.disabled = false;
            refreshEditorState();
        }
    });

    discardBtn.addEventListener('click', () => {
        delete state.prompts.drafts[item.key];
        renderPromptsTab({ capture: false });
        const nextStatus = document.getElementById('prompt-save-status');
        if (nextStatus) setSettingsStatus(nextStatus, '已放弃未保存的修改。');
    });

    reloadBtn.addEventListener('click', async () => {
        capturePromptDraft();
        const loaded = await loadPrompts();
        if (!loaded) {
            setSettingsStatus(status, `载入失败：${state.prompts.error}`, 'error');
            return;
        }
        state.prompts.selectedKey = item.key;
        renderPromptsTab({ capture: false });
        const nextStatus = document.getElementById('prompt-save-status');
        if (nextStatus) {
            setSettingsStatus(nextStatus, '已载入最新配置；未保存的草稿仍保留。', 'ok');
        }
    });

    return card;
}

function loadPromptHistory(key, { limit = PROMPT_HISTORY_PAGE_SIZE, force = false } = {}) {
    const existing = state.prompts.history[key];
    if (existing && existing.loading) return existing.promise;
    if (existing && existing.loaded && !force && existing.limit >= limit) {
        return Promise.resolve(existing);
    }
    const cache = existing || {};
    cache.loading = true;
    cache.error = null;
    cache.limit = limit;
    state.prompts.history[key] = cache;
    const promise = (async () => {
        try {
            const { response, payload } = await apiRequest(
                `/api/admin/prompts/${encodeURIComponent(key)}/versions?limit=${limit}`,
            );
            if (!response.ok) {
                throw new Error(formatApiError(payload, '加载历史版本失败'));
            }
            cache.items = payload.items || [];
            cache.has_more = !!payload.has_more;
            cache.loaded = true;
        } catch (error) {
            cache.error = error.message || '加载历史版本失败';
            cache.loaded = true;
        } finally {
            cache.loading = false;
        }
        return cache;
    })();
    cache.promise = promise;
    return promise;
}

async function fetchPromptRevisionContent(key, revision) {
    if (revision._content !== undefined) return revision._content;
    const { response, payload } = await apiRequest(
        `/api/admin/prompts/${encodeURIComponent(key)}/versions/${revision.id}`,
    );
    if (!response.ok) {
        throw new Error(formatApiError(payload, '加载版本内容失败'));
    }
    revision._content = payload.content || '';
    return revision._content;
}

function buildPromptHistoryItem(item, revision, preview) {
    const row = createEl('li', 'prompt-history-item');
    const main = createEl('div', 'prompt-history-main');
    const title = createEl('div', 'prompt-history-title');
    title.append(
        createEl('span', 'prompt-badge prompt-version', revision.version),
        createEl('span', 'prompt-history-time', formatLocalDateTime(revision.created_at)),
        createEl(
            'span',
            'prompt-history-user',
            (revision.created_by && revision.created_by.display_name) || '系统导入',
        ),
    );
    main.appendChild(title);
    const note = String(revision.note || '').trim();
    if (note) main.appendChild(createEl('p', 'prompt-history-note', note));
    main.appendChild(createEl('p', 'prompt-history-size', `${revision.char_count} 字符`));

    const actions = createEl('div', 'prompt-history-actions');
    const viewBtn = createEl('button', 'btn btn-secondary btn-sm', '查看', { type: 'button' });
    const restoreBtn = createEl('button', 'btn btn-secondary btn-sm', '恢复', { type: 'button' });
    actions.append(viewBtn, restoreBtn);
    row.append(main, actions);

    viewBtn.addEventListener('click', async () => {
        viewBtn.disabled = true;
        try {
            if (!preview.hidden && preview.dataset.revisionId === String(revision.id)) {
                preview.hidden = true;
                viewBtn.textContent = '查看';
                return;
            }
            const content = await fetchPromptRevisionContent(item.key, revision);
            preview.textContent = content;
            preview.dataset.revisionId = String(revision.id);
            preview.hidden = false;
            viewBtn.textContent = '收起';
            if (typeof preview.scrollIntoView === 'function') {
                preview.scrollIntoView({ block: 'nearest' });
            }
        } catch (error) {
            showSettingsAlert(error.message || '加载版本内容失败');
        } finally {
            viewBtn.disabled = false;
        }
    });

    restoreBtn.addEventListener('click', async () => {
        restoreBtn.disabled = true;
        try {
            const content = await fetchPromptRevisionContent(item.key, revision);
            state.prompts.selectedKey = item.key;
            if (content === item.content) {
                delete state.prompts.drafts[item.key];
            } else {
                state.prompts.drafts[item.key] = content;
            }
            renderPromptsTab({ capture: false });
            const noteInput = document.getElementById('prompt-note-input');
            if (noteInput) noteInput.value = `恢复自 ${revision.version}`;
            const status = document.getElementById('prompt-save-status');
            if (status) {
                setSettingsStatus(
                    status,
                    `已载入 ${revision.version} 的内容，保存后生成新版本。`,
                    'ok',
                );
            }
            const editor = document.getElementById('prompt-editor');
            if (editor) editor.focus();
        } catch (error) {
            showSettingsAlert(error.message || '恢复版本失败');
        } finally {
            restoreBtn.disabled = false;
        }
    });

    return row;
}

function renderPromptHistory(mount, item) {
    clearEl(mount);
    let cache = state.prompts.history[item.key];
    if (!cache) {
        loadPromptHistory(item.key);
        cache = state.prompts.history[item.key];
    }
    if (cache.loading) {
        mount.appendChild(createEl('p', 'prompt-history-loading', '历史版本加载中…'));
        if (cache.promise) {
            cache.promise.then(() => {
                if (mount.isConnected && state.prompts.selectedKey === item.key) {
                    renderPromptHistory(mount, item);
                }
            });
        }
        return;
    }
    if (cache.error) {
        const error = createEl('div', 'settings-import-notice');
        error.appendChild(createEl('p', 'settings-import-title', '历史版本加载失败'));
        error.appendChild(createEl('p', '', cache.error));
        const retry = createEl('button', 'btn btn-secondary', '重试', { type: 'button' });
        retry.addEventListener('click', async () => {
            retry.disabled = true;
            await loadPromptHistory(item.key, { force: true });
            if (mount.isConnected && state.prompts.selectedKey === item.key) {
                renderPromptHistory(mount, item);
            }
        });
        error.appendChild(retry);
        mount.appendChild(error);
        return;
    }

    const card = buildSettingsCard(
        '历史版本',
        '每次保存都会追加一个版本；「恢复」把旧内容载入编辑器，保存后成为新版本。',
    );
    if (!cache.items.length) {
        card.appendChild(createEl(
            'p',
            'prompt-history-empty',
            '还没有数据库版本。保存一次后，这里会记录版本、时间、修改人与说明。',
        ));
        mount.appendChild(card);
        return;
    }
    const preview = createEl('pre', 'prompt-history-preview');
    preview.hidden = true;
    const list = createEl('ul', 'prompt-history-list');
    cache.items.forEach((revision) => {
        list.appendChild(buildPromptHistoryItem(item, revision, preview));
    });
    card.append(list, preview);
    if (cache.has_more && cache.items.length < PROMPT_HISTORY_MAX_PAGE) {
        const more = createEl('button', 'btn btn-secondary btn-sm prompt-history-more', '加载更多', {
            type: 'button',
        });
        more.addEventListener('click', async () => {
            more.disabled = true;
            await loadPromptHistory(item.key, {
                limit: Math.min(
                    cache.items.length + PROMPT_HISTORY_PAGE_SIZE,
                    PROMPT_HISTORY_MAX_PAGE,
                ),
                force: true,
            });
            if (mount.isConnected && state.prompts.selectedKey === item.key) {
                renderPromptHistory(mount, item);
            }
        });
        card.appendChild(more);
    }
    mount.appendChild(card);
}

function renderPromptsTab({ capture = true } = {}) {
    const panel = elements.panels.prompts;
    if (!panel) return;
    if (capture) capturePromptDraft();
    clearEl(panel);

    if (!state.prompts.payload) {
        if (state.prompts.error) {
            renderPromptsLoadError(panel);
        } else {
            panel.appendChild(createEl('p', 'prompt-history-loading', '提示词加载中…'));
        }
        return;
    }
    if (state.prompts.error) {
        renderPromptsLoadError(panel);
        return;
    }
    const items = promptItems();
    if (!items.length) {
        panel.appendChild(createEl('p', 'prompt-history-empty', '没有可管理的提示词。'));
        return;
    }
    const item = selectedPromptItem();
    const layout = createEl('div', 'prompt-layout');
    const nav = createEl('aside', 'prompt-nav', '', { 'aria-label': '提示词列表' });
    nav.appendChild(createEl('p', 'prompt-nav-hint', '选择要编辑的提示词'));
    const list = createEl('div', 'prompt-nav-list');
    items.forEach((entry) => list.appendChild(buildPromptNavItem(entry)));
    nav.appendChild(list);
    const main = createEl('div', 'prompt-main');
    main.appendChild(buildPromptEditorCard(item));
    const historyMount = createEl('div', 'prompt-history-mount');
    main.appendChild(historyMount);
    layout.append(nav, main);
    panel.appendChild(layout);
    renderPromptHistory(historyMount, item);
}
