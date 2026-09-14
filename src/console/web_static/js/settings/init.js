// 系统设置页 - init：启动逻辑。加载设置负载与各账号来源概览后渲染两个页签，
// 页签从 URL hash 恢复（#models / #sources；hash 只到页签级，子锚点一律忽略；
// 旧 hash #accounts[:任意] 由 activateSettingsTab 兼容改写为 #sources）。
'use strict';

document.addEventListener('DOMContentLoaded', async () => {
    cacheSettingsElements();
    registerUnsavedGuard();
    elements.tabButtons.forEach((btn) => {
        btn.addEventListener('click', () => activateSettingsTab(btn.dataset.settingsTab));
    });

    try {
        await reloadSettingsPayload();
    } catch (error) {
        showSettingsAlert(`加载系统设置失败：${error.message}`);
        return;
    }

    const { tab } = parseSettingsHash();

    await loadAllAccountOverviews();

    renderModelsTab();
    renderSourcesTab();
    activateSettingsTab(tab, { updateHash: false });
});
