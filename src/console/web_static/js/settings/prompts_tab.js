// 系统设置页 - 提示词页签：左侧提示词列表、右侧模板编辑器与历史版本。
// 内容来自独立的 /api/admin/prompts 负载（不进 app_settings）；保存即追加一个
// revision，版本号由服务端分配，前端只负责展示与在冲突时保留草稿。
'use strict';

const PROMPT_SOURCE_LABELS = { database: '数据库', file: '文件默认' };
const PROMPT_HISTORY_PAGE_SIZE = 20;
// 与后端 PROMPT_HISTORY_MAX_LIMIT 保持一致，避免「加载更多」到上限后原地打转。
const PROMPT_HISTORY_MAX_PAGE = 100;
const PROMPT_KIND_LABELS = { score: '0-100 评分', decision: 'JSON 判定' };
const PROMPT_TEST_CATEGORY_LABELS = {
    external_positive: '京外正面',
    external_negative: '京外负面',
    internal_positive: '京内正面',
    internal_negative: '京内负面',
    external: '京外',
    internal: '京内',
};
// 自动优化案例按结论分组，顺序即展示顺序与「默认展开第一组」的优先级。
const PROMPT_OPTIMIZATION_CASE_GROUPS = [
    { key: 'fixed', label: '已修好', status: '达标', statusClass: 'is-fixed' },
    { key: 'broken', label: '改坏', status: '改坏', statusClass: 'is-broken' },
    { key: 'fluctuation', label: '波动', status: '波动', statusClass: 'is-fluctuation' },
    { key: 'unchanged', label: '未变化', status: '未达标', statusClass: 'is-unchanged' },
    { key: 'skipped', label: '跳过', status: '跳过', statusClass: 'is-skipped' },
];
// 运行记录里的 fix_state 只会是这四个值（跳过的案例不带 fix_state）。
const PROMPT_OPTIMIZATION_FIX_STATES = ['fixed', 'broken', 'fluctuation', 'unchanged'];
// 失败原因最多直接展示 3 条，其余收进「还有 N 条」的就地展开入口。
const PROMPT_OPTIMIZATION_VISIBLE_FAILURES = 3;

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

function promptOptimizationStatusText(item) {
    const status = item && item.optimization_status;
    if (item && item.kind !== 'score') return '';
    if (!status) return '尚无自动优化记录';
    const time = formatLocalDateTime(status.finished_at);
    let detail;
    switch (status.outcome) {
        case 'revision_created':
            detail = `生成 ${status.new_version || '新版本'}`;
            break;
        case 'no_correction':
            detail = '未改动：无需修正';
            break;
        case 'model_declined':
            detail = '未改动：模型判断不改';
            break;
        case 'check_failed':
            detail = `未通过检查：修好 ${status.fixed_count || 0} 条、`
                + `改坏 ${status.broken_count || 0} 条`;
            break;
        case 'error': {
            const reason = String(status.error || '未知错误');
            detail = `出错：${reason.length > 36 ? `${reason.slice(0, 36)}…` : reason}`;
            break;
        }
        default:
            detail = status.outcome_label || status.outcome || '状态未知';
    }
    return `${time} · ${detail}`;
}

function promptRevisionActorLabel(revision) {
    if (revision.origin === 'auto_optimized') return '自动优化';
    if (revision.origin === 'file_baseline') return '系统导入';
    return (revision.created_by && revision.created_by.display_name) || '系统导入';
}

function promptUpdatedLine(item) {
    if (item.source === 'file') {
        return '当前来自 config/prompts 默认文件；保存后会写入数据库并生成新版本。';
    }
    const name = (item.updated_by && item.updated_by.display_name) || '未知';
    return `最后修改：${name} · ${formatLocalDateTime(item.updated_at)}`;
}

function promptTestCategoryKey(item) {
    const base = item && item.is_beijing_related ? 'internal' : 'external';
    const sentiment = String((item && item.sentiment_label) || '').toLowerCase();
    if (sentiment === 'positive' || sentiment === 'negative') {
        return `${base}_${sentiment}`;
    }
    return base;
}

function promptTestArticleFromSearch(item) {
    const badge = typeof articleCategoryBadge === 'function'
        ? articleCategoryBadge(item)
        : null;
    const score = item && item.external_importance_score;
    return {
        articleId: item.article_id,
        title: item.title || '（无标题）',
        source: item.source || '',
        score: typeof score === 'number' ? score : null,
        category: promptTestCategoryKey(item),
        categoryLabel: badge ? badge.label : '',
        ingestedAt: (item.attribution && item.attribution.ingested_at) || null,
    };
}

// 搜索抽屉选择模式回调：只更新试跑卡片，不触碰编辑器草稿。
window.onSearchArticlePicked = function onSearchArticlePicked(item) {
    capturePromptDraft();
    state.prompts.testArticle = promptTestArticleFromSearch(item);
    state.prompts.testResult = null;
    state.prompts.testError = null;
    if (state.prompts.payload) {
        renderPromptsTab({ capture: false });
    }
};

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
        state.prompts.testResult = null;
        state.prompts.testError = null;
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
    const statusText = promptOptimizationStatusText(item);
    if (statusText) {
        button.appendChild(createEl(
            'span',
            'prompt-auto-status',
            statusText,
            { title: statusText },
        ));
    }
    button.addEventListener('click', () => selectPrompt(item.key));
    return button;
}

function selectPrompt(key) {
    if (state.prompts.selectedKey === key) return;
    capturePromptDraft();
    state.prompts.selectedKey = key;
    // 文章跨提示词保留（便于同一篇对比），上次结果只对旧提示词有效。
    state.prompts.testResult = null;
    state.prompts.testError = null;
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
                state.prompts.testResult = null;
                state.prompts.testError = null;
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
        state.prompts.testResult = null;
        state.prompts.testError = null;
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

function promptTestCategoryLabel(category) {
    return PROMPT_TEST_CATEGORY_LABELS[category] || category || '';
}

function buildPromptTestArticleChip(article) {
    const chip = createEl('div', 'prompt-test-article');
    chip.appendChild(createEl('div', 'prompt-test-article-title', article.title));
    const metaParts = [];
    if (article.source) metaParts.push(article.source);
    if (article.categoryLabel) metaParts.push(article.categoryLabel);
    if (article.score !== null && article.score !== undefined) {
        metaParts.push(`当前生产分 ${article.score}`);
    } else {
        metaParts.push('当前未评分');
    }
    if (article.ingestedAt) {
        metaParts.push(`收录 ${formatLocalDateTime(article.ingestedAt)}`);
    }
    chip.appendChild(createEl('div', 'prompt-test-article-meta', metaParts.join(' · ')));
    return chip;
}

function openPromptTestPicker(statusEl) {
    if (typeof window.openSearchDrawer !== 'function') {
        setSettingsStatus(statusEl, '文章检索尚未加载，无法选择文章。', 'error');
        return;
    }
    window.openSearchDrawer();
}

function buildPromptTestResult(item, result) {
    const box = createEl('div', 'prompt-test-result');
    const head = createEl('div', 'prompt-test-result-head');
    if (result.kind === 'score') {
        const score = result.score;
        const verdictLabel = score === null
            ? '未返回分数'
            : (result.passed ? '通过' : '未通过');
        const verdictClass = score === null
            ? 'is-muted'
            : (result.passed ? 'is-pass' : 'is-fail');
        head.appendChild(createEl(
            'span',
            `prompt-test-verdict ${verdictClass}`,
            verdictLabel,
        ));
        const scoreWrap = createEl('div', 'prompt-test-score-wrap');
        scoreWrap.append(
            createEl('span', 'prompt-test-score', score === null ? '—' : String(score)),
            createEl('span', 'prompt-test-score-unit', '/ 100'),
        );
        head.appendChild(scoreWrap);
        if (result.threshold !== null && result.threshold !== undefined) {
            head.appendChild(createEl(
                'span',
                'prompt-test-threshold',
                `阈值 ${result.threshold}`,
            ));
        }
    } else {
        const decision = result.decision || {};
        const value = decision.is_beijing_related;
        const verdictLabel = value === true
            ? '判为京内'
            : value === false ? '判为京外' : '未能判定';
        const verdictClass = value === true
            ? 'is-pass'
            : value === false ? 'is-fail' : 'is-muted';
        head.appendChild(createEl(
            'span',
            `prompt-test-verdict ${verdictClass}`,
            verdictLabel,
        ));
        if (decision.reason) {
            head.appendChild(createEl('p', 'prompt-test-reason', decision.reason));
        }
    }
    box.appendChild(head);

    const meta = createEl('div', 'prompt-test-meta');
    const versionLabel = result.draft
        ? '草稿（未保存）'
        : (result.prompt_version || '—');
    [
        ['模型', result.model || '—'],
        ['耗时', `${result.elapsed_ms} ms`],
        ['版本', versionLabel],
    ].forEach(([label, value]) => {
        const metaItem = createEl('span', 'prompt-test-meta-item');
        metaItem.append(
            createEl('span', 'prompt-test-meta-label', label),
            document.createTextNode(value),
        );
        meta.appendChild(metaItem);
    });
    box.appendChild(meta);

    if (
        result.kind === 'score'
        && result.article
        && result.article.category
        && result.article.category !== result.prompt_key
    ) {
        box.appendChild(createEl(
            'p',
            'prompt-test-note',
            `这篇文章在生产流程中走「${promptTestCategoryLabel(result.article.category)}」，`
                + `本次试跑的是「${result.prompt_label}」。`,
        ));
    }

    if (result.current) {
        let currentText;
        if (result.kind === 'score') {
            if (result.current.score !== null && result.current.score !== undefined) {
                const version = result.current.prompt_version
                    ? `（${result.current.prompt_version}）`
                    : '';
                const category = result.current.prompt_key
                    ? ` · ${promptTestCategoryLabel(result.current.prompt_key)}`
                    : '';
                currentText = `文章当前生产分数：${result.current.score}${version}${category}`;
            } else {
                currentText = '文章当前没有生产评分';
            }
        } else {
            const value = result.current.is_beijing_related;
            currentText = `文章当前判定：${
                value === true ? '京内' : value === false ? '京外' : '未判定'
            }`;
        }
        box.appendChild(createEl('p', 'prompt-test-current', currentText));
    }

    if (result.error) {
        box.appendChild(createEl('p', 'prompt-test-error', result.error));
    }
    if (result.raw_output) {
        const details = createEl('details', 'prompt-test-raw');
        details.appendChild(createEl('summary', '', '原始输出'));
        details.appendChild(createEl('pre', '', result.raw_output));
        box.appendChild(details);
    }
    box.appendChild(createEl(
        'p',
        'prompt-test-note',
        '试跑不写库，也不改变文章状态；结果对应点击试跑时的编辑器内容。',
    ));
    return box;
}

function buildPromptTestCard(item) {
    const card = buildSettingsCard(
        '试跑',
        '用当前编辑器里的提示词（可以是未保存的草稿）对一篇文章实际调用模型；'
            + '不写库，也不改变文章状态。',
    );
    const article = state.prompts.testArticle;
    const toolbar = createEl('div', 'prompt-test-toolbar');
    const status = createEl('span', 'settings-save-status prompt-test-status', '', {
        role: 'status',
        'aria-live': 'polite',
        id: 'prompt-test-status',
    });

    if (!article) {
        const pickBtn = createEl(
            'button',
            'btn btn-secondary prompt-test-pick',
            '选一篇文章试跑',
            { type: 'button' },
        );
        pickBtn.disabled = state.prompts.testRunning;
        pickBtn.addEventListener('click', () => openPromptTestPicker(status));
        toolbar.append(pickBtn, status);
        card.appendChild(toolbar);
        card.appendChild(createEl(
            'p',
            'prompt-test-hint',
            '从全库检索中选一篇已进入摘要环节的文章；标题、来源、摘要和正文会由系统拼进提示词。',
        ));
    } else {
        toolbar.appendChild(buildPromptTestArticleChip(article));
        const actions = createEl('div', 'prompt-test-actions');
        const pickBtn = createEl(
            'button',
            'btn btn-secondary btn-sm prompt-test-pick',
            '换一篇',
            { type: 'button' },
        );
        const runBtn = createEl(
            'button',
            'btn btn-primary btn-sm prompt-test-run',
            '开始试跑',
            { type: 'button' },
        );
        pickBtn.disabled = state.prompts.testRunning;
        runBtn.disabled = state.prompts.testRunning;
        pickBtn.addEventListener('click', () => openPromptTestPicker(status));
        runBtn.addEventListener('click', async () => {
            if (state.prompts.testRunning) return;
            const editor = document.getElementById('prompt-editor');
            const content = editor ? editor.value : '';
            if (!content.trim()) {
                setSettingsStatus(status, '提示词内容不能为空。', 'error');
                return;
            }
            const requestKey = item.key;
            state.prompts.testRunning = true;
            state.prompts.testResult = null;
            state.prompts.testError = null;
            renderPromptsTab({ capture: false });
            const runningStatus = document.getElementById('prompt-test-status');
            let stopTicker = () => {};
            if (runningStatus) stopTicker = startElapsedTicker(runningStatus, '试跑中');
            try {
                const { response, payload } = await apiRequest(
                    `/api/admin/prompts/${encodeURIComponent(item.key)}/test`,
                    {
                        method: 'POST',
                        body: {
                            article_id: article.articleId,
                            content,
                        },
                    },
                );
                if (state.prompts.selectedKey !== requestKey) return;
                if (response.ok) {
                    state.prompts.testResult = payload;
                    state.prompts.testError = null;
                } else {
                    state.prompts.testResult = null;
                    state.prompts.testError = `试跑失败：${
                        formatApiError(payload, '请稍后重试')
                    }`;
                }
            } catch (error) {
                if (state.prompts.selectedKey === requestKey) {
                    state.prompts.testResult = null;
                    state.prompts.testError = `试跑失败：${error.message || '网络错误'}`;
                }
            } finally {
                stopTicker();
                state.prompts.testRunning = false;
                renderPromptsTab({ capture: false });
            }
        });
        actions.append(pickBtn, runBtn);
        toolbar.append(actions);
        card.append(toolbar, status);
    }

    if (state.prompts.testError) {
        card.appendChild(createEl('p', 'prompt-test-error', state.prompts.testError));
    }
    if (state.prompts.testResult) {
        card.appendChild(buildPromptTestResult(item, state.prompts.testResult));
    }
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

async function fetchPromptRevisionPayload(key, revision) {
    if (revision._payload !== undefined) return revision._payload;
    const { response, payload } = await apiRequest(
        `/api/admin/prompts/${encodeURIComponent(key)}/versions/${revision.id}`,
    );
    if (!response.ok) {
        throw new Error(formatApiError(payload, '加载版本内容失败'));
    }
    revision._payload = payload;
    return revision._payload;
}

async function fetchPromptRevisionContent(key, revision) {
    const payload = await fetchPromptRevisionPayload(key, revision);
    return payload.content || '';
}

function promptScoreText(value) {
    if (value === null || value === undefined || value === '') return '—';
    const number = Number(value);
    return Number.isFinite(number) ? String(Math.round(number)) : '—';
}

// 有确认回放分数时显示成两次的形式（如 58/62）；历史记录缺该字段时退回单次。
function promptReplayPairText(first, second) {
    const base = promptScoreText(first);
    if (second === null || second === undefined || second === '') return base;
    return `${base}/${promptScoreText(second)}`;
}

function buildOptimizationEdits(edits) {
    const wrap = createEl('div', 'prompt-optimization-edits');
    if (!edits.length) {
        wrap.appendChild(createEl('p', 'prompt-optimization-empty', '本次没有文本改动。'));
        return wrap;
    }
    edits.forEach((edit, index) => {
        const block = createEl('div', 'prompt-optimization-edit');
        block.appendChild(createEl(
            'p',
            'prompt-optimization-edit-title',
            `改动 ${index + 1}`,
        ));
        const oldBlock = createEl('div', 'prompt-optimization-diff is-old');
        oldBlock.appendChild(createEl('span', 'prompt-optimization-diff-label', '替换前'));
        oldBlock.appendChild(createEl('pre', '', edit.old || '（空）'));
        const newBlock = createEl('div', 'prompt-optimization-diff is-new');
        newBlock.appendChild(createEl('span', 'prompt-optimization-diff-label', '替换后'));
        newBlock.appendChild(createEl('pre', '', edit.new || '（删除）'));
        block.append(oldBlock, newBlock);
        wrap.appendChild(block);
    });
    return wrap;
}

function buildOptimizationCheck(check) {
    const wrap = createEl('div', 'prompt-optimization-check');
    if (!check) {
        wrap.appendChild(createEl('p', 'prompt-optimization-empty', '没有检查结论。'));
        return wrap;
    }
    const verdict = createEl(
        'span',
        `prompt-badge ${check.passed ? 'is-pass' : 'is-fail'}`,
        check.passed ? '通过' : '未通过',
    );
    wrap.appendChild(verdict);
    let checkMeta = `T 修好 ${check.fixed_count || 0}/${check.t_total || 0} 条`;
    if (
        check.weighted_fixed_score !== null
        && check.weighted_fixed_score !== undefined
    ) {
        checkMeta += `（计权 ${check.weighted_fixed_score} 分）`;
    }
    checkMeta += ` · R 改坏 ${check.broken_count || 0}/${check.r_total || 0} 条`;
    if (check.fluctuation_count) {
        checkMeta += ` · 波动 ${check.fluctuation_count} 条`;
    }
    wrap.appendChild(createEl(
        'span',
        'prompt-optimization-check-meta',
        checkMeta,
    ));
    if (check.unparseable_count) {
        wrap.appendChild(createEl(
            'p',
            'prompt-optimization-failure',
            `${check.unparseable_count} 条回放未解析出分数`,
        ));
    }
    const failures = (check.failures || [])
        .map((failure) => String(failure))
        .filter((failure) => failure.trim().length > 0);
    failures.slice(0, PROMPT_OPTIMIZATION_VISIBLE_FAILURES).forEach((failure) => {
        wrap.appendChild(createEl('p', 'prompt-optimization-failure', failure));
    });
    const hiddenCount = failures.length - PROMPT_OPTIMIZATION_VISIBLE_FAILURES;
    if (hiddenCount > 0) {
        const rest = createEl('div', 'prompt-optimization-failures-rest');
        rest.hidden = true;
        let rendered = false;
        const more = createEl(
            'button',
            'prompt-optimization-more-failures',
            `还有 ${hiddenCount} 条`,
            { type: 'button' },
        );
        more.addEventListener('click', () => {
            // 就地展开：第一次点开时才把剩余条目建进 DOM，再点收起。
            if (rest.hidden) {
                if (!rendered) {
                    failures
                        .slice(PROMPT_OPTIMIZATION_VISIBLE_FAILURES)
                        .forEach((failure) => {
                            rest.appendChild(createEl(
                                'p',
                                'prompt-optimization-failure',
                                failure,
                            ));
                        });
                    rendered = true;
                }
                rest.hidden = false;
                more.textContent = '收起';
            } else {
                rest.hidden = true;
                more.textContent = `还有 ${hiddenCount} 条`;
            }
        });
        wrap.append(rest, more);
    }
    return wrap;
}

// 案例归组：skip_reason 优先，其次用运行记录里的 fix_state；
// 2026-10-31 之前写出的运行记录没有 fix_state，必须按 bucket + 候选判定回退，
// 否则历史记录会被整体误判成「未变化」。
function promptOptimizationCaseGroupKey(item) {
    const caseItem = item || {};
    if (String(caseItem.skip_reason || '').trim()) return 'skipped';
    if (PROMPT_OPTIMIZATION_FIX_STATES.includes(caseItem.fix_state)) {
        return caseItem.fix_state;
    }
    if (caseItem.bucket === 'T') {
        return caseItem.candidate_satisfied ? 'fixed' : 'unchanged';
    }
    if (caseItem.bucket === 'R') {
        return caseItem.candidate_satisfied === false ? 'broken' : 'unchanged';
    }
    return 'unchanged';
}

function promptOptimizationCaseFlow(item) {
    return `${promptScoreText(item.original_score)} → `
        + `${promptReplayPairText(item.current_score, item.current_confirm_score)} → `
        + `${promptReplayPairText(item.candidate_score, item.candidate_confirm_score)}`;
}

// 「未变化」组混着两类语义不同的案例：T 没修好（未达标）与 R 本来就达标、
// 只是没被改坏（达标）。组级文案固定成「未达标」会把后者说错，所以
// unchanged 组按单条 candidate_satisfied 判定；样式仍用中性的 is-unchanged。
function promptOptimizationCaseStatus(item, group) {
    if (group.key === 'unchanged') {
        if (item.candidate_satisfied === true) return '达标';
        if (item.candidate_satisfied === false) return '未达标';
        return '—';
    }
    return group.status;
}

// 卡片式案例行：标题 + 方向/来源 + 分数流 + 状态 一行，原因一行，
// 不再用 6 列表格（宽屏也不至于把分数列挤掉）。
function buildOptimizationCaseRow(item, group) {
    const row = createEl('div', 'prompt-optimization-case');
    const head = createEl('div', 'prompt-optimization-case-head');
    head.appendChild(createEl(
        'span',
        'prompt-optimization-case-title',
        item.title || '（无标题）',
    ));
    const metaParts = [];
    if (item.origin_label) metaParts.push(item.origin_label);
    if (item.direction_label) metaParts.push(item.direction_label);
    if (metaParts.length) {
        head.appendChild(createEl(
            'span',
            'prompt-optimization-case-meta',
            metaParts.join(' · '),
        ));
    }
    head.appendChild(createEl(
        'span',
        'prompt-optimization-case-score',
        promptOptimizationCaseFlow(item),
    ));
    head.appendChild(createEl(
        'span',
        `prompt-optimization-case-status ${group.statusClass}`,
        promptOptimizationCaseStatus(item, group),
    ));
    row.appendChild(head);
    row.appendChild(createEl(
        'p',
        'prompt-optimization-case-reason',
        item.reason || item.skip_reason || '—',
    ));
    return row;
}

function buildOptimizationCases(cases) {
    const wrap = createEl('div', 'prompt-optimization-case-groups');
    const groups = PROMPT_OPTIMIZATION_CASE_GROUPS.map((group) => ({
        group,
        items: [],
    }));
    (cases || []).forEach((item) => {
        const key = promptOptimizationCaseGroupKey(item);
        const entry = groups.find((candidate) => candidate.group.key === key);
        if (entry) entry.items.push(item);
    });
    const nonEmpty = groups.filter((entry) => entry.items.length > 0);
    if (!nonEmpty.length) {
        wrap.appendChild(createEl('p', 'prompt-optimization-empty', '没有案例。'));
        return wrap;
    }
    groups.forEach((entry) => {
        if (!entry.items.length) return;
        // 默认只展开第一个非空组（通常是「已修好」）；其余组只渲染组头，
        // 行在点开组头时才建进 DOM（不是先建好再用 CSS 隐藏）。
        const expanded = entry === nonEmpty[0];
        const section = createEl('div', 'prompt-optimization-case-group');
        section.classList.toggle('is-open', expanded);
        const head = createEl('button', 'prompt-optimization-case-group-head', '', {
            type: 'button',
            'aria-expanded': String(expanded),
        });
        head.classList.toggle('is-open', expanded);
        head.append(
            createEl(
                'span',
                'prompt-optimization-case-group-label',
                `${entry.group.label} ${entry.items.length} 条`,
            ),
            createEl('span', 'prompt-optimization-case-group-caret', '▾', {
                'aria-hidden': 'true',
            }),
        );
        const body = createEl('div', 'prompt-optimization-case-group-body');
        body.hidden = !expanded;
        let rendered = false;
        const renderRows = () => {
            if (rendered) return;
            entry.items.forEach((item) => {
                body.appendChild(buildOptimizationCaseRow(item, entry.group));
            });
            rendered = true;
        };
        if (expanded) renderRows();
        head.addEventListener('click', () => {
            const open = body.hidden;
            if (open) renderRows();
            body.hidden = !open;
            head.classList.toggle('is-open', open);
            head.setAttribute('aria-expanded', String(open));
            section.classList.toggle('is-open', open);
        });
        section.append(head, body);
        wrap.appendChild(section);
    });
    return wrap;
}

// 二级开关的标题行直接给结论摘要；计权/波动字段缺失就不拼对应段落，
// 没有 check 时只显示到 outcome_label。
function promptOptimizationSummary(optimization) {
    const parts = ['自动优化'];
    const outcome = optimization.outcome_label || optimization.outcome;
    if (outcome) parts.push(outcome);
    const attempts = optimization.attempts || [];
    const lastAttempt = attempts.length ? attempts[attempts.length - 1] : null;
    const check = lastAttempt && lastAttempt.check;
    if (check) {
        let summary = `修好 ${check.fixed_count || 0}/${check.t_total || 0} 条`;
        if (
            check.weighted_fixed_score !== null
            && check.weighted_fixed_score !== undefined
        ) {
            summary += `（计权 ${check.weighted_fixed_score} 分）`;
        }
        summary += ` · 改坏 ${check.broken_count || 0}/${check.r_total || 0} 条`;
        if (check.fluctuation_count) {
            summary += ` · 波动 ${check.fluctuation_count} 条`;
        }
        parts.push(summary);
    }
    return parts.join(' · ');
}

function renderPromptOptimizationDetail(mount, optimization) {
    clearEl(mount);
    if (!optimization) {
        mount.hidden = true;
        return;
    }
    mount.hidden = false;
    const head = createEl('div', 'prompt-optimization-detail-head');
    head.appendChild(createEl(
        'span',
        'prompt-badge prompt-version',
        optimization.outcome_label || optimization.outcome || '自动优化',
    ));
    head.appendChild(createEl(
        'span',
        'prompt-optimization-time',
        formatLocalDateTime(optimization.finished_at),
    ));
    head.appendChild(createEl(
        'span',
        'prompt-optimization-model',
        optimization.model || '—',
    ));
    mount.appendChild(head);
    if (optimization.error) {
        mount.appendChild(createEl('p', 'prompt-optimization-error', optimization.error));
    }

    const attempts = optimization.attempts || [];
    const lastAttempt = attempts.length ? attempts[attempts.length - 1] : {};
    mount.appendChild(createEl('h4', 'prompt-optimization-subtitle', '本次改动'));
    mount.appendChild(buildOptimizationEdits(lastAttempt.edits || []));
    mount.appendChild(createEl('h4', 'prompt-optimization-subtitle', '检查结论'));
    mount.appendChild(buildOptimizationCheck(lastAttempt.check));
    mount.appendChild(createEl('h4', 'prompt-optimization-subtitle', '案例'));
    mount.appendChild(buildOptimizationCases(optimization.cases || []));
}

// 展开/收起只是 DOM 局部状态：同一列表内手风琴互斥，重绘后回到全部收起。
function setPromptHistoryItemOpen(row, open) {
    const panel = row.querySelector('.prompt-history-panel');
    const toggle = row.querySelector('.prompt-history-toggle');
    row.classList.toggle('is-open', open);
    if (panel) panel.hidden = !open;
    if (!toggle) return;
    toggle.classList.toggle('is-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    const label = toggle.querySelector('.prompt-history-toggle-label');
    if (label) label.textContent = open ? '收起' : '展开';
}

function buildPromptHistoryItem(item, revision) {
    const row = createEl('li', 'prompt-history-item');
    const header = createEl('div', 'prompt-history-row');
    const main = createEl('div', 'prompt-history-main');
    const title = createEl('div', 'prompt-history-title');
    title.append(
        createEl('span', 'prompt-badge prompt-version', revision.version),
        createEl('span', 'prompt-history-time', formatLocalDateTime(revision.created_at)),
        createEl(
            'span',
            'prompt-history-user',
            promptRevisionActorLabel(revision),
        ),
    );
    main.appendChild(title);
    const note = String(revision.note || '').trim();
    if (note) main.appendChild(createEl('p', 'prompt-history-note', note));
    main.appendChild(createEl('p', 'prompt-history-size', `${revision.char_count} 字符`));

    const actions = createEl('div', 'prompt-history-actions');
    // 整个标题行不做点击区域：只有这个按钮可点，避免误触「恢复」或选中备注文字。
    const toggleBtn = createEl('button', 'btn btn-secondary btn-sm prompt-history-toggle', '', {
        type: 'button',
        'aria-expanded': 'false',
    });
    toggleBtn.append(
        createEl('span', 'prompt-history-toggle-label', '展开'),
        createEl('span', 'prompt-history-caret', '▾', { 'aria-hidden': 'true' }),
    );
    const restoreBtn = createEl('button', 'btn btn-secondary btn-sm', '恢复', { type: 'button' });
    actions.append(toggleBtn, restoreBtn);
    header.append(main, actions);

    // 就地展开区：始终是本条 <li> 的后代、紧随标题行之后。
    const panel = createEl('div', 'prompt-history-panel');
    panel.hidden = true;
    row.append(header, panel);

    let loaded = false;
    let loading = false;

    const renderLoadedPanel = (payload) => {
        clearEl(panel);
        panel.appendChild(createEl('pre', 'prompt-history-preview', payload.content || ''));
        if (payload.optimization) {
            // 自动优化详情是二级开关：默认收起，标题行直接给结论摘要。
            // 手工保存/文件基线版本没有 optimization，不渲染这个开关。
            const section = createEl('div', 'prompt-optimization-section');
            const optToggle = createEl('button', 'prompt-optimization-toggle', '', {
                type: 'button',
                'aria-expanded': 'false',
            });
            optToggle.append(
                createEl(
                    'span',
                    'prompt-optimization-toggle-label',
                    promptOptimizationSummary(payload.optimization),
                ),
                createEl('span', 'prompt-optimization-caret', '▾', {
                    'aria-hidden': 'true',
                }),
            );
            const detail = createEl('div', 'prompt-optimization-detail');
            detail.hidden = true;
            optToggle.addEventListener('click', () => {
                const open = detail.hidden;
                if (open && !detail.dataset.rendered) {
                    renderPromptOptimizationDetail(detail, payload.optimization);
                    detail.dataset.rendered = '1';
                }
                detail.hidden = !open;
                optToggle.classList.toggle('is-open', open);
                optToggle.setAttribute('aria-expanded', String(open));
            });
            section.append(optToggle, detail);
            panel.appendChild(section);
        }
        // 成功后标记已加载：收起再展开直接复用现有 DOM，不重渲染、
        // 不闪「加载中…」，二级展开态也得以保留。
        loaded = true;
    };

    const expand = async () => {
        // 手风琴：展开新条目时收起同一列表里的其它条目。
        const list = row.closest('.prompt-history-list');
        if (list) {
            list.querySelectorAll('.prompt-history-item').forEach((other) => {
                if (other !== row) setPromptHistoryItemOpen(other, false);
            });
        }
        if (loaded) {
            setPromptHistoryItemOpen(row, true);
            return;
        }
        if (loading) return;
        loading = true;
        setPromptHistoryItemOpen(row, true);
        clearEl(panel);
        panel.appendChild(createEl('p', 'prompt-history-panel-loading', '加载中…'));
        toggleBtn.disabled = true;
        try {
            const payload = await fetchPromptRevisionPayload(item.key, revision);
            if (!row.isConnected) return;
            renderLoadedPanel(payload);
        } catch (error) {
            if (row.isConnected) setPromptHistoryItemOpen(row, false);
            showSettingsAlert(error.message || '加载版本内容失败');
        } finally {
            loading = false;
            if (row.isConnected) toggleBtn.disabled = false;
        }
    };

    toggleBtn.addEventListener('click', () => {
        if (row.classList.contains('is-open')) {
            setPromptHistoryItemOpen(row, false);
            return;
        }
        expand();
    });

    restoreBtn.addEventListener('click', async () => {
        restoreBtn.disabled = true;
        try {
            const content = await fetchPromptRevisionContent(item.key, revision);
            state.prompts.selectedKey = item.key;
            state.prompts.testResult = null;
            state.prompts.testError = null;
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
    const list = createEl('ul', 'prompt-history-list');
    cache.items.forEach((revision) => {
        list.appendChild(buildPromptHistoryItem(item, revision));
    });
    card.appendChild(list);
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
    main.appendChild(buildPromptTestCard(item));
    const historyMount = createEl('div', 'prompt-history-mount');
    main.appendChild(historyMount);
    layout.append(nav, main);
    panel.appendChild(layout);
    renderPromptHistory(historyMount, item);
}
