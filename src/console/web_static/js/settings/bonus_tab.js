// 加分词典：有序草稿、逐行校验、整段保存；禁止拼接用户输入到 innerHTML。
'use strict';

function renderBonusesTab() {
    const panel = elements.panels.bonuses;
    const section = 'score_keyword_bonuses';
    clearEl(panel);
    const saved = settingsSection(section);
    if (!saved) {
        renderImportNotice(panel, '加分词典');
        return;
    }
    panel.appendChild(createEl('p', 'settings-effect-note',
        '关键词在标题、正文或已记录的关键词中出现即加分，命中多个时分值累加；修改只对保存之后打分的文章生效，已经打过分的不会重算。'));
    const head = createEl('div', 'bonus-row bonus-heading');
    ['关键词', '分值（−100～100）', '操作'].forEach((text) => head.appendChild(createEl('span', '', text)));
    const list = createEl('div', 'bonus-list');
    panel.append(head, list);
    const addRow = (item) => {
        const row = createEl('div', 'bonus-row');
        const keyword = createEl('input', 'bonus-keyword', '', { type: 'text', 'aria-label': '关键词' });
        const bonus = createEl('input', 'bonus-value', '', {
            type: 'number', min: '-100', max: '100', step: '1', 'aria-label': '加分分值',
        });
        keyword.value = item.keyword;
        bonus.value = item.bonus;
        const remove = createEl('button', 'btn btn-secondary bonus-delete', '删除', { type: 'button' });
        const error = createEl('span', 'bonus-row-error');
        row.append(keyword, bonus, remove, error);
        row.addEventListener('input', () => markDirty(section));
        remove.addEventListener('click', () => {
            const next = row.nextElementSibling || row.previousElementSibling;
            row.remove();
            markDirty(section);
            (next ? next.querySelector('input') : addBtn).focus();
        });
        list.appendChild(row);
        return keyword;
    };
    saved.value.forEach(addRow);
    const addBtn = createEl('button', 'btn btn-secondary bonus-add', '＋ 添加一行', { type: 'button' });
    addBtn.addEventListener('click', () => {
        addRow({ keyword: '', bonus: '' }).focus();
        markDirty(section);
    });
    panel.appendChild(addBtn);
    buildSectionSaveBar(panel, section, '加分词典', (status) => {
        const rows = Array.from(list.children);
        const values = rows.map((row) => ({
            keyword: row.querySelector('.bonus-keyword').value.trim(),
            bonus: Number(row.querySelector('.bonus-value').value),
        }));
        let invalid = false;
        rows.forEach((row, index) => {
            const { keyword, bonus } = values[index];
            const duplicate = values.some((item, other) => other !== index && item.keyword === keyword);
            const badKeyword = !keyword || duplicate;
            const badBonus = !row.querySelector('.bonus-value').value.trim()
                || !Number.isInteger(bonus) || bonus < -100 || bonus > 100;
            const message = !keyword ? '关键词不能为空。' : duplicate ? '关键词重复。' :
                badBonus ? '分值必须是 -100 到 100 之间的整数。' : '';
            row.classList.toggle('is-error', badKeyword || badBonus);
            row.querySelector('.bonus-keyword').setAttribute('aria-invalid', String(badKeyword));
            row.querySelector('.bonus-value').setAttribute('aria-invalid', String(badBonus));
            row.querySelector('.bonus-row-error').textContent = message;
            invalid = invalid || badKeyword || badBonus;
        });
        if (invalid) {
            setSettingsStatus(status, '保存已取消：请检查标红行，关键词不能为空或重复，分值必须是 -100 到 100 之间的整数。', 'error');
            return undefined;
        }
        if (!values.length && !window.confirm('保存后所有文章都不再有关键词加分。确定保存空词典吗？')) return undefined;
        return values;
    }, renderBonusesTab);
}
