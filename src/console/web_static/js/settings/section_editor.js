// 二期分区共用保存栏。用户输入仅经 createEl/textContent 渲染。
'use strict';

function buildSectionSaveBar(panel, section, label, readValue, render) {
    // 版本与当前 DOM 草稿绑定；其他页签整体刷新 payload 不能偷偷升级此版本。
    const sectionSnapshot = settingsSection(section);
    const bar = createEl('div', 'settings-save-bar');
    const saveBtn = createEl('button', 'btn btn-primary section-save', `保存${label}`, { type: 'button' });
    const discardBtn = createEl('button', 'btn btn-secondary section-discard', '放弃修改', { type: 'button' });
    const reloadBtn = createEl('button', 'btn btn-secondary section-reload', '载入最新配置', { type: 'button' });
    const statusEl = createEl('span', 'settings-save-status', '', { role: 'status', 'aria-live': 'polite' });
    reloadBtn.hidden = true;
    bar.append(saveBtn, discardBtn, reloadBtn, statusEl);
    panel.append(bar, buildLastModifiedLine(sectionSnapshot));
    const lock = (busy) => panel.querySelectorAll('input, textarea, button')
        .forEach((control) => { control.disabled = busy; });
    saveBtn.addEventListener('click', async () => {
        if (state.saving[section]) return;
        const value = readValue(statusEl);
        if (value === undefined) return;
        lock(true);
        try {
            await saveSettingsSection(section, value, {
                sectionSnapshot, saveBtn, reloadBtn, statusEl,
                onSaved: () => {
                    render();
                    setSettingsStatus(panel.querySelector('.settings-save-status'), '保存成功。', 'ok');
                },
            });
        } finally {
            lock(false);
        }
    });
    discardBtn.addEventListener('click', () => {
        // 放弃回到此编辑器最后见到的快照，不借用其他块刷新得到的值。
        state.payload.sections[section] = sectionSnapshot;
        clearDirty(section);
        render();
    });
    reloadBtn.addEventListener('click', async () => {
        lock(true);
        try {
            const { response, payload } = await apiRequest('/api/admin/settings');
            if (!response.ok) throw new Error(formatApiError(payload, '加载设置失败'));
            // 只应用目标分区；其他 DOM、脏标记和版本均保留。
            state.payload.sections[section] = payload.sections[section];
            clearDirty(section);
            render();
        } catch (error) {
            setSettingsStatus(statusEl, `载入失败：${error.message}`, 'error');
        } finally {
            lock(false);
        }
    });
}
