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

function buildSettingsTextarea(panel, label, value, section, className) {
    const wrap = createEl('label', 'advanced-field');
    wrap.appendChild(createEl('span', 'advanced-field-label', label));
    const input = createEl('textarea', `settings-textarea ${className}`, '', { rows: '8', spellcheck: 'false' });
    input.value = value;
    input.addEventListener('input', () => markDirty(section));
    wrap.appendChild(input);
    panel.appendChild(wrap);
    return input;
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
    panel.appendChild(createEl('h3', 'settings-group-heading', label));
    panel.appendChild(createEl('p', 'settings-effect-note', description));
    let readValue;
    if (section === 'source_aliases') {
        const fields = createEl('div', 'advanced-alias-fields');
        panel.appendChild(fields);
        const suffixes = buildSettingsTextarea(fields, '后缀 · 一行一个', saved.value.suffixes.join('\n'), section, 'alias-suffixes');
        suffixes.parentElement.appendChild(createEl('p', 'settings-effect-note',
            '归一化时按从上到下的顺序，只剥离第一个匹配到的后缀。'));
        const aliases = buildSettingsTextarea(fields, '别名 · 原名称=标准名称',
            Object.entries(saved.value.aliases).map(([key, value]) => `${key}=${value}`).join('\n'), section, 'alias-mappings');
        aliases.parentElement.appendChild(createEl('p', 'settings-effect-note', '一行一条，按第一个等号拆分；支持半角 = 和全角 ＝。'));
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
            const input = buildSettingsTextarea(
                panel,
                `${category} · 一行一个词`,
                (saved.value[category] || []).join('\n'),
                section,
                'keyword-list',
            );
            const count = createEl('span', 'settings-word-count', '', { 'aria-live': 'polite' });
            const updateCount = () => {
                count.textContent = `当前 ${settingsTextLines(input.value).length} 个词`;
            };
            input.parentElement.appendChild(count);
            input.addEventListener('input', updateCount);
            updateCount();
            inputs[category] = input;
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
        const input = buildSettingsTextarea(panel, '一行一个词', saved.value.join('\n'), section, 'keyword-list');
        const count = createEl('span', 'settings-word-count', '', { 'aria-live': 'polite' });
        const updateCount = () => { count.textContent = `当前 ${settingsTextLines(input.value).length} 个词`; };
        input.parentElement.appendChild(count);
        input.addEventListener('input', updateCount);
        updateCount();
        readValue = (status) => {
            const value = settingsTextLines(input.value);
            input.setAttribute('aria-invalid', String(!value.length));
            if (!value.length) {
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
    panel.appendChild(createEl('p', 'settings-effect-note advanced-warning',
        '这些配置很少需要修改；除「自动排序关键词」只影响展示顺序外，其余改错会影响抓取和筛选的整体结果。'));
    Object.keys(ADVANCED_SECTIONS).forEach((section) => {
        const block = createEl('section', 'advanced-section', '', { dataset: { section } });
        panel.appendChild(block);
        renderAdvancedSection(section, block);
    });
}
