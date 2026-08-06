// Manual Filter JS - Sidebar Collapse
//
// 分类侧栏折叠：筛选页与已选结果页共用同一份状态（localStorage），
// 折叠后侧栏收成窄条，仅保留当前桶按钮（竖排显示，仍可见当前所在桶）。
// 折叠/展开复用 layout_anchor.js 的锚定机制，避免列表跳动。

const SIDEBAR_COLLAPSED_KEY = 'sidebar_collapsed';

function readSidebarCollapsed() {
    try {
        return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true';
    } catch (error) {
        return false; // 读取失败时默认展开
    }
}

function persistSidebarCollapsed(collapsed) {
    try {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? 'true' : 'false');
    } catch (error) {
        // 写入失败仅影响持久化，不影响本次切换
    }
}

function applySidebarCollapsedUI(collapsed) {
    document.querySelectorAll('.filter-layout').forEach(layout => {
        layout.classList.toggle('sidebar-collapsed', collapsed);
    });
    document.querySelectorAll('.sidebar-collapse-toggle').forEach(btn => {
        btn.setAttribute('aria-expanded', String(!collapsed));
        btn.textContent = collapsed ? '»' : '«';
        const label = collapsed ? '展开分类侧栏' : '折叠分类侧栏';
        btn.setAttribute('aria-label', label);
        btn.title = label;
    });
}

function setSidebarCollapsed(collapsed, { persist = true, anchor = true } = {}) {
    const anchorInfo = anchor ? findTopVisibleArticleCard() : null;
    applySidebarCollapsedUI(collapsed);
    if (persist) persistSidebarCollapsed(collapsed);
    if (anchor) {
        // 无可见卡片时 anchorInfo 为 null，仅重算高度、跳过滚动补偿
        relayoutListsAfterWidthChange(
            anchorInfo ? anchorInfo.card : null,
            anchorInfo ? anchorInfo.top : null
        );
    }
}

function setupSidebarCollapse() {
    const toggles = document.querySelectorAll('.sidebar-collapse-toggle');
    if (!toggles.length) return;
    toggles.forEach(btn => {
        btn.addEventListener('click', () => {
            setSidebarCollapsed(btn.getAttribute('aria-expanded') === 'true');
        });
    });
    // 初始化只应用状态，不做锚定补偿（页面尚未滚动）
    setSidebarCollapsed(readSidebarCollapsed(), { persist: false, anchor: false });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupSidebarCollapse, { once: true });
} else {
    setupSidebarCollapse();
}
