// 系统设置页 - 加分词典页签（score_keyword_bonuses）：每条规则一行
// （关键词 + 分值 + 删除），按库里存的顺序展示、不排序，「添加一行」追加到末尾。
// 批量保存走 core.js 的 saveSettingsSection（整个分区一次 PUT + 版本号），
// 不做逐行即时保存：逐行保存会产生大量版本、互相冲突，改到一半的中间状态
// 也会被写进线上。保存前的前端拦截规则与后端 validate_score_keyword_bonuses
// 一致（关键词去空白后非空、不重复，分值为 -100~100 的整数），出错在对应行
// 标红并在状态行汇总，不发请求。
// 用户输入一律经 createEl/textContent 渲染，禁止拼接 innerHTML。
'use strict';

// 与后端 business_config.SCORE_BONUS_MIN/MAX 一致
const BONUS_VALUE_MIN = -100;
const BONUS_VALUE_MAX = 100;

function buildBonusRow(item) {
    const row = createEl('div', 'bonus-row');
    const keywordInput = createEl('input', 'bonus-keyword-input', '', {
        type: 'text',
        'aria-label': '关键词',
        placeholder: '关键词',
    });
    keywordInput.value = item.keyword === undefined || item.keyword === null
        ? ''
        : String(item.keyword);
    const bonusInput = createEl('input', 'bonus-value-input', '', {
        type: 'text',
        inputmode: 'numeric',
        'aria-label': '分值',
        placeholder: '-100 ~ 100',
    });
    bonusInput.value = item.bonus === undefined || item.bonus === null || item.bonus === ''
        ? ''
        : String(item.bonus);
    const deleteBtn = createEl('button', 'btn btn-secondary bonus-delete-btn', '删除', {
        type: 'button',
        'aria-label': '删除该行',
    });
    const rowError = createEl('span', 'bonus-row-error');

    [keywordInput, bonusInput].forEach((input) => {
        input.addEventListener('input', () => {
            row.classList.remove('is-invalid');
            rowError.textContent = '';
            markDirty('score_keyword_bonuses');
        });
    });
    deleteBtn.addEventListener('click', () => {
        row.remove();
        markDirty('score_keyword_bonuses');
    });

    row.appendChild(keywordInput);
    row.appendChild(bonusInput);
    row.appendChild(deleteBtn);
    row.appendChild(rowError);
    return row;
}

// 保存前的前端拦截：逐行校验并在出错行上标红，返回可直接提交的
// [{ keyword, bonus }]（按页面顺序）与错误摘要；有错误时不发请求。
function collectBonusDraft() {
    const rows = [...elements.panels.bonuses.querySelectorAll('.bonus-row')];
    const items = [];
    const errors = [];
    const seen = new Set();
    rows.forEach((row, index) => {
        const keyword = row.querySelector('.bonus-keyword-input').value.trim();
        const bonusText = row.querySelector('.bonus-value-input').value.trim();
        let error = '';
        let bonus = null;
        if (!keyword) {
            error = '关键词不能为空';
        } else if (!bonusText) {
            error = '分值不能为空';
        } else if (!/^-?\d+$/.test(bonusText)) {
            error = '分值必须是整数';
        } else {
            bonus = Number(bonusText);
            if (bonus < BONUS_VALUE_MIN || bonus > BONUS_VALUE_MAX) {
                error = `分值必须在 ${BONUS_VALUE_MIN} 到 ${BONUS_VALUE_MAX} 之间`;
            }
        }
        if (!error && seen.has(keyword)) {
            error = `关键词「${keyword}」与其他行重复`;
        }
        if (error) {
            errors.push(`第 ${index + 1} 行：${error}`);
        } else {
            seen.add(keyword);
            items.push({ keyword, bonus });
        }
        row.classList.toggle('is-invalid', !!error);
        row.querySelector('.bonus-row-error').textContent = error;
    });
    return { items, errors };
}

function renderBonusTab() {
    const panel = elements.panels.bonuses;
    clearEl(panel);
    const section = settingsSection('score_keyword_bonuses');
    if (!section) {
        renderImportNotice(panel, '加分词典');
        return;
    }

    panel.appendChild(createEl(
        'p',
        'settings-effect-note',
        '关键词在标题、正文或已记录的关键词中出现即加分，命中多个时分值累加。'
            + '修改只对保存之后打分的文章生效，已经打过分的不会重算。',
    ));
    panel.appendChild(buildLastModifiedLine(section));

    const rows = createEl('div', 'bonus-rows', '', { id: 'bonus-rows' });
    (section.value || []).forEach((item) => {
        rows.appendChild(buildBonusRow(item));
    });
    panel.appendChild(rows);

    const addBtn = createEl('button', 'btn btn-secondary bonus-add-btn', '添加一行', {
        id: 'btn-bonus-add',
        type: 'button',
    });
    addBtn.addEventListener('click', () => {
        rows.appendChild(buildBonusRow({ keyword: '', bonus: '' }));
        markDirty('score_keyword_bonuses');
    });
    panel.appendChild(addBtn);

    const saveBar = createEl('div', 'settings-save-bar');
    const saveBtn = createEl('button', 'btn btn-primary', '保存加分词典', {
        id: 'btn-bonus-save',
        type: 'button',
    });
    const discardBtn = createEl('button', 'btn btn-secondary', '放弃修改', {
        id: 'btn-bonus-discard',
        type: 'button',
    });
    const reloadBtn = createEl('button', 'btn btn-secondary', '载入最新配置', {
        id: 'btn-bonus-reload',
        type: 'button',
    });
    reloadBtn.hidden = true;
    const status = createEl('span', 'settings-save-status', '', { id: 'bonus-save-status' });
    saveBar.appendChild(saveBtn);
    saveBar.appendChild(discardBtn);
    saveBar.appendChild(reloadBtn);
    saveBar.appendChild(status);
    panel.appendChild(saveBar);

    saveBtn.addEventListener('click', async () => {
        const { items, errors } = collectBonusDraft();
        if (errors.length) {
            setSettingsStatus(status, `保存已取消：${errors.join('；')}`, 'error');
            return;
        }
        // 删光是合法状态（表示不再加分），但更可能是误操作，保存前确认一次
        if (items.length === 0) {
            const confirmed = window.confirm(
                '加分词典将被清空。保存后所有文章都不再有关键词加分，确定继续吗？',
            );
            if (!confirmed) {
                setSettingsStatus(status, '已取消保存。', '');
                return;
            }
        }
        await saveSettingsSection('score_keyword_bonuses', items, {
            statusEl: status,
            saveBtn,
            reloadBtn,
            onSaved: () => {
                // 按服务器返回的规范化值重渲染（后端会去掉关键词首尾空白）
                renderBonusTab();
                showSettingsToast('加分词典已保存');
            },
        });
    });
    discardBtn.addEventListener('click', () => {
        clearDirty('score_keyword_bonuses');
        renderBonusTab();
        showSettingsToast('已放弃修改');
    });
    reloadBtn.addEventListener('click', async () => {
        reloadBtn.disabled = true;
        try {
            await reloadSettingsPayload();
            clearDirty('score_keyword_bonuses');
            renderBonusTab();
            showSettingsToast('已载入最新配置');
        } catch (error) {
            setSettingsStatus(status, `载入失败：${error.message}`, 'error');
        } finally {
            reloadBtn.disabled = false;
        }
    });
}
