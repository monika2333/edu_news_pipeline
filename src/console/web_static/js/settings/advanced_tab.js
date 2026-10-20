// 高级设置：各块独立保存、重载和脏标记。用户输入仅经 createEl/textContent 渲染。
'use strict';

const ADVANCED_SECTIONS = {
    education_keywords: ['教育关键词', '抓取时正文不含任何一个词的文章会被直接丢弃，不进入后续流程。',
        '教育关键词不能为空：为空会让所有文章直接放行。'],
    beijing_keywords: ['京内关键词', '用于判断稿件是否与北京相关，没有命中的会被判为京外。',
        '京内关键词不能为空：为空会把所有稿件判为京外。'],
    source_aliases: ['来源别名', '统一来源名称，后缀和别名都可留空，表示不做归一化。'],
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
function buildSettingsTextarea(panel, label, value, section, className, options = {}) {
    const mode = options.mode || 'list';
    const unit = options.unit || '个词';
    const field = createEl('div', 'advanced-field');
    const head = createEl('div', 'advanced-field-head');
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
            let removed = false;
            const removeChip = (event) => {
                if (removed) return;
                removed = true;
                if (event.type === 'mousedown') event.preventDefault();
                const lines = input.value.split(/\r?\n/);
                lines.splice(item.index, 1);
                setValue(lines.join('\n'));
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
    // 失焦时提交，避免「打完一个词直接点保存」把词丢掉；但不把焦点抢回来，
    // 否则点「保存」「批量编辑」或跳到另一个字段都会被打断
    addInput.addEventListener('blur', () => {
        if (addInput.value.trim()) addLine({ focus: false });
    });
    chips.addEventListener('click', (event) => {
        if (event.target.closest('.keyword-chip-remove')) return;
        addInput.focus();
    });

    toggle.addEventListener('click', () => {
        const open = input.hidden;
        input.hidden = !open;
        toggle.setAttribute('aria-expanded', String(open));
        toggle.textContent = open ? '收起批量' : '批量编辑';
        if (open) {
            input.focus();
            input.setSelectionRange(input.value.length, input.value.length);
        }
    });
    input.openBatch = () => {
        if (input.hidden) toggle.click();
    };

    input.addEventListener('input', () => {
        markDirty(section);
        updateCount();
        renderChips();
    });

    updateCount();
    renderChips();
    return input;
}

function revealAdvancedBatch(input) {
    if (typeof input.openBatch === 'function') input.openBatch();
}

const REVIEW_SORT_CATEGORIES = ['市教委', '中小学', '高校'];

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
    let readValue;
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
                unit: '条',
                addPlaceholder: '添加后缀，回车确认',
                hint: '归一化时按从上到下的顺序，只剥离第一个匹配到的后缀。',
            },
        );
        const aliases = buildSettingsTextarea(
            fields,
            '别名',
            Object.entries(saved.value.aliases).map(([key, value]) => `${key}=${value}`).join('\n'),
            section,
            'alias-mappings',
            {
                mode: 'aliases',
                unit: '条',
                addPlaceholder: '原名称=标准名称，回车添加',
                hint: '按第一个等号拆分；支持半角 = 和全角 ＝。',
            },
        );
        readValue = (status) => {
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
                    revealAdvancedBatch(aliases);
                    setSettingsStatus(status, `别名第 ${index + 1} 行：${error}`, 'error');
                    return undefined;
                }
                seen.add(key);
                entries.push([key, target]);
            }
            return { suffixes: settingsTextLines(suffixes.value), aliases: Object.fromEntries(entries) };
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
                    addPlaceholder: `添加${category}关键词，回车确认`,
                },
            );
        });
        readValue = (status) => {
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
                        revealAdvancedBatch(input);
                        setSettingsStatus(status, message, 'error');
                        return undefined;
                    }
                    seen.set(lowered, category);
                }
                value[category] = lines;
            }
            if (REVIEW_SORT_CATEGORIES.every((category) => !value[category].length)
                && !window.confirm('三个类别的关键词都为空，自动排序将不会调整条目顺序。确定保存吗？')) {
                return undefined;
            }
            return value;
        };
    } else {
        const input = buildSettingsTextarea(
            panel,
            '关键词',
            saved.value.join('\n'),
            section,
            'keyword-list',
            {
                addPlaceholder: '添加关键词，回车确认',
            },
        );
        readValue = (status) => {
            const value = settingsTextLines(input.value);
            input.setAttribute('aria-invalid', String(!value.length));
            if (!value.length) {
                revealAdvancedBatch(input);
                setSettingsStatus(status, emptyMessage, 'error');
                return undefined;
            }
            return value;
        };
        panel.appendChild(createEl('p', 'settings-effect-note', '只对之后入库的文章生效，已有的文章不会重新处理。'));
    }
    buildSectionSaveBar(panel, section, label, readValue, () => renderAdvancedSection(section, panel));
}

function renderAdvancedTab() {
    const panel = elements.panels.advanced;
    clearEl(panel);
    Object.keys(ADVANCED_SECTIONS).forEach((section) => {
        const block = createEl('section', 'settings-card advanced-section', '', { dataset: { section } });
        panel.appendChild(block);
        renderAdvancedSection(section, block);
    });
}
