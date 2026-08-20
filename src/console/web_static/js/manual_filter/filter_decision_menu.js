// Manual Filter JS - Filter Decision Context Menu
// 右键点击筛选卡片（或聚类）上的「采纳」「备选」，弹出小菜单，把条目直接归入另一报别，
// 页面当前报别状态保持不变（不走 setReviewReportType）；左键点击仍是归入当前报别的默认逻辑。
// 菜单元素懒创建并挂在 body 上，不进入 elements 注册表。

let filterDecisionMenuEl = null;

function ensureFilterDecisionMenu() {
    if (filterDecisionMenuEl) return filterDecisionMenuEl;
    filterDecisionMenuEl = document.createElement('div');
    filterDecisionMenuEl.className = 'filter-decision-menu';
    filterDecisionMenuEl.setAttribute('role', 'menu');
    filterDecisionMenuEl.innerHTML = '<button type="button" class="filter-decision-menu-item" role="menuitem"></button>';
    // 菜单自身不弹浏览器原生右键菜单
    filterDecisionMenuEl.addEventListener('contextmenu', event => event.preventDefault());
    document.body.appendChild(filterDecisionMenuEl);
    return filterDecisionMenuEl;
}

function closeFilterDecisionMenu() {
    if (filterDecisionMenuEl) filterDecisionMenuEl.classList.remove('active');
}

function openFilterDecisionMenu(x, y, input) {
    const menu = ensureFilterDecisionMenu();
    const otherReportType = state.reviewReportType === 'wanbao' ? 'zongbao' : 'wanbao';
    const actionLabel = input.value === 'selected' ? '采纳' : '备选';
    const reportLabel = otherReportType === 'wanbao' ? '晚报' : '综报';

    const item = menu.querySelector('.filter-decision-menu-item');
    item.textContent = `${actionLabel}到${reportLabel}`;
    item.onclick = () => {
        closeFilterDecisionMenu();
        if (input.disabled) return;
        // 勾选对应 radio 以给出即时视觉反馈；失败时决策处理器会回退勾选状态
        input.checked = true;
        if (input.name.startsWith('cluster-')) {
            handleClusterDecisionChange(input, otherReportType);
        } else {
            handleCardDecisionChange(input, otherReportType);
        }
    };

    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;
    menu.classList.add('active');
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth - 8) {
        menu.style.left = `${Math.max(8, window.innerWidth - rect.width - 8)}px`;
    }
    if (rect.bottom > window.innerHeight - 8) {
        menu.style.top = `${Math.max(8, window.innerHeight - rect.height - 8)}px`;
    }
}

function setupFilterDecisionMenu() {
    if (!elements.filterList) return;

    elements.filterList.addEventListener('contextmenu', event => {
        const option = event.target.closest('.radio-option');
        if (!option || !elements.filterList.contains(option)) return;
        const input = option.querySelector('input[type="radio"]');
        if (!input || input.disabled) return;
        if (!input.name.startsWith('status-') && !input.name.startsWith('cluster-')) return;
        if (input.value !== 'selected' && input.value !== 'backup') return;
        event.preventDefault();
        openFilterDecisionMenu(event.clientX, event.clientY, input);
    });

    document.addEventListener('click', event => {
        if (!filterDecisionMenuEl || !filterDecisionMenuEl.classList.contains('active')) return;
        if (filterDecisionMenuEl.contains(event.target)) return;
        closeFilterDecisionMenu();
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') closeFilterDecisionMenu();
    });
    window.addEventListener('resize', closeFilterDecisionMenu);
    window.addEventListener('scroll', closeFilterDecisionMenu, true);
}
