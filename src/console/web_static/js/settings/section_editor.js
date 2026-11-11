// 高级页各分区的即时保存栏：焦点离开整块（或芯片删除这类显式动作）时校验并
// 写库，卡片底部没有保存/放弃按钮。用户输入仅经 createEl/textContent 渲染。
'use strict';

// 返回 { commit }：commit() 立即校验并保存当前草稿。焦点离开整块才触发的绑定
// 在 advanced_tab.js（同一分区里换字段不算离开，避免一份草稿被反复 PUT）。
// options.render：还原时整块重绘；options.applyValue：把服务端规范化后的值同步
// 回控件（服务端会 trim/去重，不同步就会让本地脏文本反复触发保存）。
function buildSectionAutoSaveBar(panel, section, readValue, options = {}) {
    const { render, applyValue } = options;
    // 版本冻结在本编辑器上，只被自己的保存成功推进。窗口重新聚焦时页面会整体
    // 重拉 payload，这里若跟着读最新版本，就会拿别处升级过的版本静默覆盖别人的
    // 修改，409 永远不会出现。
    let saved = { ...settingsSection(section) };
    const bar = createEl('div', 'settings-save-bar is-autosave');
    const statusEl = createEl('span', 'settings-save-status', '', {
        role: 'status', 'aria-live': 'polite',
    });
    const updatedEl = buildLastModifiedLine(saved);
    const revertBtn = createEl('button', 'btn btn-secondary section-revert',
        '还原为已保存内容', { type: 'button' });
    const reloadBtn = createEl('button', 'btn btn-secondary section-reload',
        '载入最新配置', { type: 'button' });
    revertBtn.hidden = true;
    reloadBtn.hidden = true;
    // 保存成功不重绘卡片：重绘会把批量编辑收起、焦点丢掉。状态与「最后修改」
    // 就地刷新，只有「还原」/「载入最新配置」才重绘。
    bar.append(statusEl, updatedEl, revertBtn, reloadBtn);
    panel.append(bar);

    const showEscape = (revert, reload) => {
        revertBtn.hidden = !revert;
        reloadBtn.hidden = !reload;
    };
    const refreshUpdated = () => {
        const name = (saved.updated_by && saved.updated_by.display_name) || '未知';
        updatedEl.textContent = `最后修改：${name} · ${formatLocalDateTime(saved.updated_at)}`;
    };
    const sameValue = (value) => JSON.stringify(value) === JSON.stringify(saved.value);
    const successMessage = (warning) => (warning ? `已自动保存。${warning}` : '已自动保存。');

    let saveQueued = false;

    const flush = async () => {
        if (state.saving[section]) {
            saveQueued = true;
            return;
        }
        if (!settingsSection(section)) return;
        const result = readValue();
        if (result.error) {
            // 校验不过就不写库：草稿留在 DOM 里，标红并给一个回滚入口
            state.dirty[section] = true;
            showEscape(true, false);
            setSettingsStatus(statusEl, result.error, 'error');
            if (result.reveal) revealAdvancedBatch(result.reveal);
            return;
        }
        if (sameValue(result.value)) {
            state.dirty[section] = false;
            showEscape(false, false);
            setSettingsStatus(statusEl, successMessage(result.warning), 'ok');
            return;
        }
        const sent = result.value;
        state.saving[section] = true;
        revertBtn.disabled = true;
        reloadBtn.disabled = true;
        setSettingsStatus(statusEl, '保存中…');
        let savedOk = false;
        try {
            const { response, payload } = await apiRequest(`/api/admin/settings/${section}`, {
                method: 'PUT',
                body: { value: sent, expected_version: saved.version },
            });
            if (response.ok) {
                applySectionItem(section, payload.item);
                saved = { ...settingsSection(section) };
                refreshUpdated();
                // 请求期间可能又被改过：按「当前草稿 vs 已保存值」重新判定。
                const current = readValue();
                if (current.error) {
                    state.dirty[section] = true;
                    showEscape(true, false);
                    setSettingsStatus(statusEl, current.error, 'error');
                    if (current.reveal) revealAdvancedBatch(current.reveal);
                } else {
                    const sentUnchanged = JSON.stringify(current.value) === JSON.stringify(sent);
                    // 服务器会 trim/去重：草稿与提交值一致时把规范化结果写回控件，
                    // 否则本地脏文本会让每次失焦都重复提交同一个值、把版本号越推越高。
                    if (sentUnchanged && applyValue) applyValue(saved.value);
                    const pending = !sameValue(readValue().value);
                    state.dirty[section] = pending;
                    showEscape(pending, false);
                    setSettingsStatus(
                        statusEl,
                        pending ? '已自动保存；还有未保存的修改。' : successMessage(current.warning),
                        'ok',
                    );
                }
                savedOk = true;
            } else if (response.status === 409) {
                state.dirty[section] = true;
                showEscape(true, true);
                setSettingsStatus(
                    statusEl,
                    `保存冲突：${formatApiError(payload, '配置已在别处被修改')}。`
                        + '本地修改已保留，可「还原为已保存内容」或「载入最新配置」后再调整。',
                    'error',
                );
            } else {
                state.dirty[section] = true;
                showEscape(true, false);
                setSettingsStatus(
                    statusEl,
                    `保存失败：${formatApiError(payload, '请检查填写内容')}`,
                    'error',
                );
            }
        } catch (error) {
            state.dirty[section] = true;
            showEscape(true, false);
            setSettingsStatus(statusEl, `保存失败：${error.message || '网络错误'}`, 'error');
        } finally {
            state.saving[section] = false;
            revertBtn.disabled = false;
            reloadBtn.disabled = false;
        }
        if (savedOk && saveQueued) {
            saveQueued = false;
            await flush();
            return;
        }
        saveQueued = false;
    };

    // 还原到本编辑器最后保存成功的值；payload 里的版本也一起回退，后续保存不会
    // 拿别处升级过的版本静默覆盖（会照常 409）。
    revertBtn.addEventListener('click', () => {
        state.payload.sections[section] = { ...saved };
        clearDirty(section);
        if (render) render();
        showSettingsToast('已还原为保存时的内容');
    });
    reloadBtn.addEventListener('click', async () => {
        reloadBtn.disabled = true;
        try {
            const { response, payload } = await apiRequest('/api/admin/settings');
            if (!response.ok) throw new Error(formatApiError(payload, '加载设置失败'));
            state.payload.sections[section] = payload.sections[section];
            clearDirty(section);
            if (render) render();
            showSettingsToast('已载入最新配置');
        } catch (error) {
            setSettingsStatus(statusEl, `载入失败：${error.message}`, 'error');
            reloadBtn.disabled = false;
        }
    });

    return { commit: flush };
}
