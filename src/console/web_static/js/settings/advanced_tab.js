// 系统设置页 - 高级页签：教育关键词、京内关键词、来源别名三个低频分区的
// 朴素文本框编辑。三块各自独立保存、独立脏标记、各自带自己分区的版本号；
// 某一块 409 后点「载入最新配置」只重建该块——reloadSettingsPayload 会整体
// 替换 state.payload，所以重渲染必须按块进行，不能把其他块未保存的文本框冲掉。
// 保存前的前端拦截规则与后端 business_config 的校验一致；保存成功后按服务器
// 返回的规范化值回填。用户输入一律经 createEl/textContent 渲染，禁止拼接 innerHTML。
'use strict';

const ADVANCED_BLOCKS = [
    {
        section: 'education_keywords',
        kind: 'keywords',
        title: '教育关键词',
        description: '抓取时正文不含任何一个词的文章会被直接丢弃，不进入后续流程。一行一个词。',
        emptyHint: '教育关键词为空会让所有文章直接放行',
    },
    {
        section: 'beijing_keywords',
        kind: 'keywords',
        title: '京内关键词',
        description: '用于判断稿件是否与北京相关，没有命中的会被判为京外。一行一个词。',
        emptyHint: '京内关键词为空会把所有稿件判为京外',
    },
    {
        section: 'source_aliases',
        kind: 'aliases',
        title: '来源别名',
        description: '把抓取来源名归一化为标准名称。',
        emptyHint: '',
    },
];

// 按行拆分，去掉首尾空白，丢弃空行；重复的词由后端静默去重，前端不处理
function parseKeywordLines(text) {
    return String(text)
        .split('\n')
        .map((line) => line.trim())
        .filter((line) => line.length > 0);
}

function buildAdvancedSaveBar(block, container) {
    const saveBar = createEl('div', 'settings-save-bar');
    const saveBtn = createEl('button', 'btn btn-primary advanced-save-btn', '保存', {
        type: 'button',
    });
    const reloadBtn = createEl('button', 'btn btn-secondary advanced-reload-btn',
        '载入最新配置', { type: 'button' });
    reloadBtn.hidden = true;
    const status = createEl('span', 'settings-save-status advanced-save-status');
    saveBar.appendChild(saveBtn);
    saveBar.appendChild(reloadBtn);
    saveBar.appendChild(status);

    saveBtn.addEventListener('click', () => {
        block.onSave({ statusEl: status, saveBtn, reloadBtn, container });
    });
    reloadBtn.addEventListener('click', async () => {
        reloadBtn.disabled = true;
        let reloaded = false;
        try {
            await reloadSettingsPayload();
            clearDirty(block.section);
            showSettingsToast('已载入最新配置');
            reloaded = true;
        } catch (error) {
            // 失败分支不重渲染：错误就挂在当前块的状态行上
            setSettingsStatus(status, `载入失败：${error.message}`, 'error');
        } finally {
            reloadBtn.disabled = false;
        }
        // 重渲染放在 try/finally 之后且只重建本块：reloadBtn / status 随之脱离
        // 文档，finally 已先在原节点上完成复位；其他块未保存的文本不受影响
        if (reloaded) {
            renderAdvancedBlock(container, block);
        }
    });
    return saveBar;
}

function renderKeywordListBlock(container, block) {
    clearEl(container);
    const section = settingsSection(block.section);
    if (!section) {
        renderImportNotice(container, block.title);
        return;
    }
    container.appendChild(createEl('h3', 'settings-env-heading', block.title));
    container.appendChild(createEl('p', 'settings-env-note', block.description));

    const textarea = createEl('textarea', 'advanced-textarea', '', {
        'aria-label': block.title,
        rows: '12',
    });
    textarea.value = (section.value || []).join('\n');
    const count = createEl('p', 'advanced-count');
    const updateCount = () => {
        count.textContent = `当前词数：${parseKeywordLines(textarea.value).length}`;
    };
    updateCount();
    textarea.addEventListener('input', () => {
        updateCount();
        markDirty(block.section);
    });
    container.appendChild(textarea);
    container.appendChild(count);
    container.appendChild(createEl(
        'p',
        'settings-effect-note',
        '只对之后入库的文章生效，已有的文章不会重新处理。',
    ));
    container.appendChild(buildLastModifiedLine(section));

    const onSave = async ({ statusEl, saveBtn, reloadBtn }) => {
        const words = parseKeywordLines(textarea.value);
        // 拆分后为空时前端直接拦截：后端同样会拒绝，这里拦是为了把后果说得更直接
        if (!words.length) {
            setSettingsStatus(
                statusEl,
                `保存已取消：词表拆分后为空。${block.emptyHint}，请至少保留一个词。`,
                'error',
            );
            return;
        }
        await saveSettingsSection(block.section, words, {
            statusEl,
            saveBtn,
            reloadBtn,
            onSaved: () => {
                // 按服务器返回的规范化列表回填文本框
                renderKeywordListBlock(container, block);
                showSettingsToast(`${block.title}已保存`);
            },
        });
    };
    container.appendChild(buildAdvancedSaveBar({ ...block, onSave }, container));
}

// 别名行按第一个等号拆分，半角 = 与全角 ＝ 都接受（中文输入法下很容易打出全角）。
// 重复的原名称必须由前端拦截：别名存为 JSON 对象，重复的键在序列化时会被静默
// 吞掉一个，后端根本看不到。
function parseAliasDraft(suffixText, aliasText) {
    const suffixes = parseKeywordLines(suffixText);
    const aliases = {};
    const errors = [];
    String(aliasText).split('\n').forEach((rawLine, index) => {
        const line = rawLine.trim();
        if (!line) return;
        const eq = line.search(/[=＝]/);
        if (eq === -1) {
            errors.push(`第 ${index + 1} 行缺少等号（格式：原名称=标准名称）`);
            return;
        }
        const from = line.slice(0, eq).trim();
        const to = line.slice(eq + 1).trim();
        if (!from || !to) {
            errors.push(`第 ${index + 1} 行等号两侧都不能为空`);
            return;
        }
        if (Object.prototype.hasOwnProperty.call(aliases, from)) {
            errors.push(`第 ${index + 1} 行原名称「${from}」重复`);
            return;
        }
        aliases[from] = to;
    });
    return { value: { suffixes, aliases }, errors };
}

function renderAliasesBlock(container, block) {
    clearEl(container);
    const section = settingsSection(block.section);
    if (!section) {
        renderImportNotice(container, block.title);
        return;
    }
    const value = section.value || {};
    container.appendChild(createEl('h3', 'settings-env-heading', block.title));
    container.appendChild(createEl('p', 'settings-env-note', block.description));

    container.appendChild(createEl('p', 'advanced-field-label', '后缀（一行一个，顺序有意义）'));
    container.appendChild(createEl(
        'p',
        'settings-env-note',
        '归一化时按从上到下的顺序，只剥离第一个匹配到的后缀。',
    ));
    const suffixTextarea = createEl('textarea', 'advanced-textarea alias-suffixes-input', '', {
        'aria-label': '来源别名后缀',
        rows: '5',
    });
    suffixTextarea.value = (value.suffixes || []).join('\n');
    container.appendChild(suffixTextarea);

    container.appendChild(createEl('p', 'advanced-field-label', '别名（一行一条）'));
    container.appendChild(createEl(
        'p',
        'settings-env-note',
        '格式为 原名称=标准名称，按第一个等号拆分；半角 = 和全角 ＝ 都可以。',
    ));
    const aliasTextarea = createEl('textarea', 'advanced-textarea alias-mappings-input', '', {
        'aria-label': '来源别名映射',
        rows: '8',
    });
    // 回填一律用半角等号输出
    aliasTextarea.value = Object.entries(value.aliases || {})
        .map(([from, to]) => `${from}=${to}`)
        .join('\n');
    container.appendChild(aliasTextarea);

    [suffixTextarea, aliasTextarea].forEach((textarea) => {
        textarea.addEventListener('input', () => {
            textarea.classList.remove('is-invalid');
            markDirty(block.section);
        });
    });

    container.appendChild(createEl(
        'p',
        'settings-effect-note',
        '只对之后入库的文章生效，已有的文章不会重新处理。',
    ));
    container.appendChild(buildLastModifiedLine(section));

    const onSave = async ({ statusEl, saveBtn, reloadBtn }) => {
        const { value: draft, errors } = parseAliasDraft(
            suffixTextarea.value,
            aliasTextarea.value,
        );
        aliasTextarea.classList.toggle('is-invalid', errors.length > 0);
        if (errors.length) {
            setSettingsStatus(statusEl, `保存已取消：${errors.join('；')}`, 'error');
            return;
        }
        await saveSettingsSection(block.section, draft, {
            statusEl,
            saveBtn,
            reloadBtn,
            onSaved: () => {
                renderAliasesBlock(container, block);
                showSettingsToast(`${block.title}已保存`);
            },
        });
    };
    container.appendChild(buildAdvancedSaveBar({ ...block, onSave }, container));
}

function renderAdvancedBlock(container, block) {
    if (block.kind === 'aliases') {
        renderAliasesBlock(container, block);
    } else {
        renderKeywordListBlock(container, block);
    }
}

function renderAdvancedTab() {
    const panel = elements.panels.advanced;
    clearEl(panel);
    panel.appendChild(createEl(
        'p',
        'settings-effect-note',
        '这些配置很少需要修改，改错会影响抓取和筛选的整体结果。',
    ));
    ADVANCED_BLOCKS.forEach((block) => {
        const container = createEl('div', 'advanced-block', '', {
            dataset: { advancedBlock: block.section },
        });
        renderAdvancedBlock(container, block);
        panel.appendChild(container);
    });
}
