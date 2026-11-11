// 高级设置：各块独立保存、重载和脏标记。用户输入仅经 createEl/textContent 渲染。
'use strict';

const ADVANCED_SECTIONS = {
    education_keywords: ['教育关键词', '抓取时正文不含任何一个词的文章会被直接丢弃，不进入后续流程。',
        '教育关键词不能为空：为空会让所有文章直接放行。'],
    beijing_keywords: ['京内关键词', '用于判断稿件是否与北京相关，没有命中的会被判为京外。',
        '京内关键词不能为空：为空会把所有稿件判为京外。'],
    source_aliases: ['来源别名',
        '统一来源名称，并设置哪些识别来源直接回退到抓取来源。'
        + '三项按编号顺序处理，所有项都可留空。'],
    // 自动排序关键词只影响审阅页展示顺序，立即生效，不参与爬取与打分流程。
    review_sort_keywords: ['自动排序关键词',
        '审阅页「自动排序」按 市教委 → 中小学 → 高校 的固定优先级给条目归类排序，'
        + '未命中任何词的落入「其他」。每类一行一个词，允许留空；'
        + '同一个词只能出现在一个类别里，分类按优先级首个命中。'
        + '保存后下次点击「自动排序」立即生效，不影响已保存的顺序。', ''],
};

function settingsTextLines(text) {
    return text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
}

function advancedLineEntries(text) {
    return text.split(/\r?\n/)
        .map((raw, index) => ({ raw, index, text: raw.trim() }))
        .filter((item) => item.text);
}

function splitAliasLine(line) {
    const separator = line.search(/[=＝]/);
    if (separator < 0) return null;
    const key = line.slice(0, separator).trim();
    const target = line.slice(separator + 1).trim();
    return key && target ? { key, target } : null;
}

// 高级页的关键词编辑器：正文默认是芯片列表，逐条增删；「批量编辑」展开
// 原生 textarea 做整段粘贴/替换。textarea 仍是唯一数据源（readValue 只读它），
// 芯片和输入框只是它的视图，输入事件会同时触发脏标记、计数和芯片重绘。
// mode='aliases' 时芯片显示「原名称 → 标准名称」，添加输入也按同一格式拆分。
// options.order 给来源别名这类有处理先后顺序的字段一个编号徽标；
// options.onCommit 是「删除芯片后立刻保存」的回调（添加词等失焦再提交）。
function buildSettingsTextarea(panel, label, value, section, className, options = {}) {
    const mode = options.mode || 'list';
    const unit = options.unit || '个词';
    const field = createEl('div', 'advanced-field');
    const head = createEl('div', 'advanced-field-head');
    if (options.order) {
        head.appendChild(createEl('span', 'advanced-field-step', String(options.order), {
            'aria-hidden': 'true',
        }));
    }
    head.appendChild(createEl('span', 'advanced-field-label', label));
    const count = createEl('span', 'settings-word-count', '', { 'aria-live': 'polite' });
    const toggle = createEl('button', 'keyword-batch-toggle', '批量编辑', {
        type: 'button',
        'aria-expanded': 'false',
        'aria-label': `${label}批量编辑`,
    });
    head.appendChild(count);
    head.appendChild(toggle);
    field.appendChild(head);

    const chips = createEl('div', 'keyword-chips');
    const addInput = createEl('input', 'keyword-add-input', '', {
        type: 'text',
        placeholder: options.addPlaceholder || '添加关键词，回车确认',
        'aria-label': options.addPlaceholder || '添加关键词',
    });
    chips.appendChild(addInput);
    field.appendChild(chips);

    if (options.hint) {
        field.appendChild(createEl('p', 'settings-effect-note keyword-field-hint', options.hint));
    }

    const input = createEl('textarea', `settings-textarea ${className}`, '', {
        rows: String(options.rows || 8),
        spellcheck: 'false',
        hidden: 'hidden',
    });
    input.value = value;
    field.appendChild(input);
    panel.appendChild(field);

    const setValue = (next, { focusAdd = false } = {}) => {
        input.value = next;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        if (focusAdd) addInput.focus();
    };

    const updateCount = () => {
        count.textContent = `当前 ${advancedLineEntries(input.value).length} ${unit}`;
    };

    const chipLabel = (item) => {
        if (mode !== 'aliases') return item.text;
        const parsed = splitAliasLine(item.text);
        return parsed ? `${parsed.key} → ${parsed.target}` : item.text;
    };

    const renderChips = () => {
        chips.querySelectorAll('.keyword-chip').forEach((chip) => chip.remove());
        const fragment = document.createDocumentFragment();
        advancedLineEntries(input.value).forEach((item) => {
            const chip = createEl('span', 'keyword-chip');
            if (mode === 'aliases' && !splitAliasLine(item.text)) {
                chip.classList.add('is-invalid');
            }
            const text = createEl('span', 'keyword-chip-text', chipLabel(item));
            text.title = item.text;
            const remove = createEl('button', 'keyword-chip-remove', '×', {
                type: 'button',
                'aria-label': `移除 ${item.text}`,
            });
            // mousedown 先于输入框 blur：如果添加框里还有没回车的词，
            // blur 会重绘芯片并把当前按钮换掉，click 就丢了。mousedown
            // 先删除并阻止默认行为（不抢焦点），键盘 Enter 仍走 click。
            // 删除是破坏性动作，删完立刻保存（添加词仍等失焦，连续录入不逐个发请求）。
            let removed = false;
            const removeChip = (event) => {
                if (removed) return;
                removed = true;
                if (event.type === 'mousedown') event.preventDefault();
                const lines = input.value.split(/\r?\n/);
                lines.splice(item.index, 1);
                setValue(lines.join('\n'));
                if (typeof options.onCommit === 'function') options.onCommit();
            };
            remove.addEventListener('mousedown', removeChip);
            remove.addEventListener('click', removeChip);
            chip.append(text, remove);
            fragment.appendChild(chip);
        });
        chips.insertBefore(fragment, addInput);
    };

    const addLine = ({ focus = true } = {}) => {
        const raw = addInput.value.trim();
        if (!raw) {
            if (focus) addInput.focus();
            return;
        }
        let line = raw;
        if (mode === 'aliases') {
            const parsed = splitAliasLine(raw);
            if (!parsed) {
                addInput.setAttribute('aria-invalid', 'true');
                return;
            }
            line = `${parsed.key}=${parsed.target}`;
        }
        const current = input.value.replace(/\s+$/, '');
        const next = current ? `${current}\n${line}` : line;
        addInput.value = '';
        addInput.setAttribute('aria-invalid', 'false');
        setValue(next, { focusAdd: focus });
    };

    addInput.addEventListener('input', () => addInput.setAttribute('aria-invalid', 'false'));
    addInput.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            addLine();
        } else if (event.key === 'Escape') {
            addInput.value = '';
            addInput.setAttribute('aria-invalid', 'false');
        }
    });
    // 失焦时提交，避免打完一个词直接点到别处把词丢掉；但不把焦点抢回来，
    // 否则点「批量编辑」或跳到另一个字段都会被打断
    addInput.addEventListener('blur', () => {
        if (addInput.value.trim()) addLine({ focus: false });
    });
    chips.addEventListener('click', (event) => {
        if (event.target.closest('.keyword-chip-remove')) return;
        addInput.focus();
    });

    // 批量编辑的开合：正常点击进入时把焦点放进 textarea；校验失败需要展开
    // 出错位置时不抢焦点（失焦保存中抢焦点会打断用户操作），见 revealAdvancedBatch。
    const setBatchOpen = (open, { focus = true } = {}) => {
        input.hidden = !open;
        toggle.setAttribute('aria-expanded', String(open));
        toggle.textContent = open ? '收起批量' : '批量编辑';
        if (open && focus) {
            input.focus();
            input.setSelectionRange(input.value.length, input.value.length);
        }
    };
    toggle.addEventListener('click', () => setBatchOpen(input.hidden));
    input.openBatch = (openOptions) => {
        if (input.hidden) setBatchOpen(true, openOptions);
    };

    input.addEventListener('input', () => {
        markDirty(section);
        updateCount();
        renderChips();
    });

    // 服务端规范化（trim/去重）后的值回写：只更新视图，不当作一次用户输入。
    // 不同步的话，本地文本里的多余空格或重复词会让每次失焦都重复提交同一个值，
    // 把分区版本号越推越高。
    input.syncValue = (next) => {
        // 内容相同时不重新赋值：写回相等的 value 会把光标移到末尾
        if (input.value !== next) input.value = next;
        updateCount();
        renderChips();
    };

    updateCount();
    renderChips();
    return input;
}

function revealAdvancedBatch(input) {
    if (typeof input.openBatch === 'function') input.openBatch({ focus: false });
}

const REVIEW_SORT_CATEGORIES = ['市教委', '中小学', '高校'];

// 每个块在焦点离开整块（或芯片删除）时即时保存，没有保存/放弃按钮。
// readValue() 只做取值与校验：成功返回 { value, warning? }，失败返回
// { error, reveal? }，由 section_editor.js 的保存栏统一决定状态行与按钮。
function renderAdvancedSection(section, panel) {
    clearEl(panel);
    const [label, description, emptyMessage] = ADVANCED_SECTIONS[section];
    const saved = settingsSection(section);
    if (!saved) {
        renderImportNotice(panel, label);
        return;
    }
    const head = createEl('div', 'settings-card-head');
    const heading = createEl('div', 'settings-card-heading');
    heading.appendChild(createEl('h3', 'settings-card-title', label));
    heading.appendChild(createEl('p', 'settings-card-desc settings-effect-note', description));
    head.appendChild(heading);
    panel.appendChild(head);
    // 删除芯片要立刻保存；保存栏在本函数末尾才建好，用这个间接跳板接上
    let commitNow = () => {};
    const commitOptions = { onCommit: () => commitNow() };
    let readValue;
    let applyValue = null;
    if (section === 'source_aliases') {
        const fields = createEl('div', 'advanced-alias-fields');
        panel.appendChild(fields);
        const suffixes = buildSettingsTextarea(
            fields,
            '后缀',
            saved.value.suffixes.join('\n'),
            section,
            'alias-suffixes',
            {
                ...commitOptions,
                order: 1,
                unit: '条',
                addPlaceholder: '添加后缀，回车确认',
                hint: '去掉识别来源末尾固定的后缀（如「客户端」「网」）。'
                    + '按从上到下的顺序，只剥离第一个命中的后缀。',
            },
        );
        const aliases = buildSettingsTextarea(
            fields,
            '别名',
            Object.entries(saved.value.aliases).map(([key, value]) => `${key}=${value}`).join('\n'),
            section,
            'alias-mappings',
            {
                ...commitOptions,
                order: 2,
                mode: 'aliases',
                unit: '条',
                addPlaceholder: '原名称=标准名称，回车添加',
                hint: '把不同写法归到同一个标准来源名。按第一个等号拆分，支持半角 = 和全角 ＝。',
            },
        );
        // 旧配置行可能没有 fallback_names 字段，渲染时按空名单兜底。
        const fallbackNames = buildSettingsTextarea(
            fields,
            '回退来源',
            (saved.value.fallback_names || []).join('\n'),
            section,
            'alias-fallback-names',
            {
                ...commitOptions,
                order: 3,
                unit: '条',
                addPlaceholder: '添加来源名称，回车确认',
                hint: '识别来源与名单中的名称完全相同时不采用，直接使用抓取来源；'
                    + '归一化前后的名称都参与比对。书名号等符号也要一致。',
            },
        );
        readValue = () => {
            const entries = [];
            const seen = new Set();
            const lines = aliases.value.split(/\r?\n/);
            aliases.setAttribute('aria-invalid', 'false');
            for (let index = 0; index < lines.length; index += 1) {
                const line = lines[index].trim();
                if (!line) continue;
                const separator = line.search(/[=＝]/);
                const key = separator < 0 ? '' : line.slice(0, separator).trim();
                const target = separator < 0 ? '' : line.slice(separator + 1).trim();
                const error = separator < 0 ? '缺少等号。' : !key || !target ? '等号两侧不能为空。' :
                    seen.has(key) ? '原名称重复。' : '';
                if (error) {
                    aliases.setAttribute('aria-invalid', 'true');
                    return { error: `别名第 ${index + 1} 行：${error}`, reveal: aliases };
                }
                seen.add(key);
                entries.push([key, target]);
            }
            return {
                value: {
                    suffixes: settingsTextLines(suffixes.value),
                    aliases: Object.fromEntries(entries),
                    fallback_names: settingsTextLines(fallbackNames.value),
                },
            };
        };
        applyValue = (value) => {
            suffixes.syncValue(value.suffixes.join('\n'));
            aliases.syncValue(
                Object.entries(value.aliases).map(([key, target]) => `${key}=${target}`).join('\n'),
            );
            fallbackNames.syncValue((value.fallback_names || []).join('\n'));
        };
        panel.appendChild(createEl('p', 'settings-effect-note', '只对之后入库的文章生效，已有的文章不会重新处理。'));
    } else if (section === 'review_sort_keywords') {
        const inputs = {};
        REVIEW_SORT_CATEGORIES.forEach((category) => {
            inputs[category] = buildSettingsTextarea(
                panel,
                category,
                (saved.value[category] || []).join('\n'),
                section,
                'keyword-list',
                {
                    ...commitOptions,
                    addPlaceholder: `添加${category}关键词，回车确认`,
                },
            );
        });
        readValue = () => {
            const value = {};
            const seen = new Map();
            for (const category of REVIEW_SORT_CATEGORIES) {
                const input = inputs[category];
                input.setAttribute('aria-invalid', 'false');
                const lines = settingsTextLines(input.value);
                for (let index = 0; index < lines.length; index += 1) {
                    const lowered = lines[index].toLowerCase();
                    const owner = seen.get(lowered);
                    if (owner) {
                        const message = owner === category
                            ? `${category}第 ${index + 1} 行：关键词重复。`
                            : `${category}第 ${index + 1} 行：关键词「${lines[index]}」同时出现在 ${owner} 和 ${category}，分类按优先级首个命中，请只保留一处。`;
                        input.setAttribute('aria-invalid', 'true');
                        return { error: message, reveal: input };
                    }
                    seen.set(lowered, category);
                }
                value[category] = lines;
            }
            // 三类全空是合法配置（自动排序不调整顺序），不再弹 confirm 打断
            // 失焦保存，只用状态行提示后果。
            const warning = REVIEW_SORT_CATEGORIES.every((category) => !value[category].length)
                ? '三个类别都为空，自动排序不会调整条目顺序。'
                : '';
            return { value, warning };
        };
        applyValue = (value) => {
            REVIEW_SORT_CATEGORIES.forEach((category) => {
                inputs[category].syncValue((value[category] || []).join('\n'));
            });
        };
    } else {
        const input = buildSettingsTextarea(
            panel,
            '关键词',
            saved.value.join('\n'),
            section,
            'keyword-list',
            {
                ...commitOptions,
                addPlaceholder: '添加关键词，回车确认',
            },
        );
        readValue = () => {
            const value = settingsTextLines(input.value);
            input.setAttribute('aria-invalid', String(!value.length));
            if (!value.length) {
                return { error: emptyMessage, reveal: input };
            }
            return { value };
        };
        applyValue = (value) => input.syncValue(value.join('\n'));
        panel.appendChild(createEl('p', 'settings-effect-note', '只对之后入库的文章生效，已有的文章不会重新处理。'));
    }
    const bar = buildSectionAutoSaveBar(panel, section, readValue, {
        render: () => renderAdvancedSection(section, panel),
        applyValue,
    });
    commitNow = bar.commit;
    // 焦点离开整块才提交：同一块里从词表跳到添加框、批量编辑等不算离开，
    // 一份草稿只发一次请求。事件委托挂在页签根上，块重绘不需要重绑。
    panel.commitAutoSave = bar.commit;
}

function renderAdvancedTab() {
    const panel = elements.panels.advanced;
    clearEl(panel);
    if (!panel.dataset.autoSaveBound) {
        panel.dataset.autoSaveBound = '1';
        panel.addEventListener('focusout', (event) => {
            const block = event.target.closest ? event.target.closest('.advanced-section') : null;
            if (!block || typeof block.commitAutoSave !== 'function') return;
            const next = event.relatedTarget;
            if (next && block.contains(next)) return;
            block.commitAutoSave();
        });
    }
    Object.keys(ADVANCED_SECTIONS).forEach((section) => {
        const block = createEl('section', 'settings-card advanced-section', '', { dataset: { section } });
        panel.appendChild(block);
        renderAdvancedSection(section, block);
    });
}
