// 系统设置页 - init：启动逻辑。加载设置负载与各账号来源概览后渲染三个页签，
// 页签与账号来源从 URL hash 恢复（#models / #sources / #accounts:toutiao）。
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
    if (tab === 'accounts' && sub && accountSources().some((item) => item.key === sub)) {
        state.accountSource = sub;
    }

    await loadAllAccountOverviews();

    renderModelsTab();
    renderSourcesTab();
    renderAccountsTab();
    activateSettingsTab(tab, sub, { updateHash: false });
});
