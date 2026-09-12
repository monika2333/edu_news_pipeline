// 系统设置页 - init：启动逻辑。加载设置负载与各账号来源概览后渲染两个页签，
// 页签与展开的来源从 URL hash 恢复（#models / #sources / #sources:toutiao；
// 旧 hash #accounts[:key] 由 activateSettingsTab 兼容改写）。
'use strict';

document.addEventListener('DOMContentLoaded', async () => {
    cacheSettingsElements();
    registerUnsavedGuard();
    bindDeleteAccountModal();
    elements.tabButtons.forEach((btn) => {
        btn.addEventListener('click', () => activateSettingsTab(btn.dataset.settingsTab));
    });

    try {
        await reloadSettingsPayload();
    } catch (error) {
        showSettingsAlert(`加载系统设置失败：${error.message}`);
        return;
    }

    const { tab, sub } = parseSettingsHash();

    await loadAllAccountOverviews();

    renderModelsTab();
    renderSourcesTab();
    activateSettingsTab(tab, sub, { updateHash: false });
});
