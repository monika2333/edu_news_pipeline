// Manual Filter JS - Filter Decision Context Menu
// 桌面右键、触屏长按筛选卡片（或聚类）上的「采纳」「备选」，弹出小菜单，把条目直接归入另一报别，
// 「采纳/备选归入」报别状态保持不变（不走 setFilterAssignReportType）；左键点击/轻点仍是归入当前报别的默认逻辑。
// 手机没有右键，长按是右键的触屏等价物：抬手后浏览器可能补发一次 click，必须吃掉，否则长按会先弹菜单再被当成轻点决定。
// 菜单元素懒创建并挂在 body 上，不进入 elements 注册表。

const FILTER_DECISION_LONG_PRESS_MS = 500;
// 长按期间手指移动超过这个距离按滚动/拖拽处理，取消长按
const FILTER_DECISION_LONG_PRESS_MOVE_PX = 10;
// 长按（或触屏上弹出的原生 contextmenu）之后，这段时间内的 click 视为手势残留
const FILTER_DECISION_CLICK_GUARD_MS = 800;

let filterDecisionMenuEl = null;
// 当前按下的长按照手势：{ input, startX, startY, x, y, timer }
let filterDecisionPress = null;
// 长按已经弹出菜单，等待 touchend/touchcancel 收尾
let filterDecisionLongPressFired = false;
// 非 null 表示该 radio 在 guard.until 之前收到的 click 是长按手势残留，不能当成轻点决定
let filterDecisionClickGuard = null;

function cancelFilterDecisionPress() {
    if (filterDecisionPress) {
        window.clearTimeout(filterDecisionPress.timer);
        filterDecisionPress = null;
    }
}

function armFilterDecisionClickGuard(input) {
    filterDecisionClickGuard = { input, until: Date.now() + FILTER_DECISION_CLICK_GUARD_MS };
}

// 事件目标是否落在可决定（采纳/备选）的 radio 选项上，右键与长按共用同一判定
function filterDecisionInputFromTarget(target) {
    if (!target || typeof target.closest !== 'function') return null;
    const option = target.closest('.radio-option');
    if (!option || !elements.filterList || !elements.filterList.contains(option)) return null;
    const input = option.querySelector('input[type="radio"]');
    if (!input || input.disabled) return null;
    if (!input.name.startsWith('status-') && !input.name.startsWith('cluster-')) return null;
    if (input.value !== 'selected' && input.value !== 'backup') return null;
    return input;
}

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
    cancelFilterDecisionPress();
    const menu = ensureFilterDecisionMenu();
    const otherReportType = state.filterAssignReportType === 'wanbao' ? 'zongbao' : 'wanbao';
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
        const input = filterDecisionInputFromTarget(event.target);
        if (!input) return;
        // 触屏上系统可能在长按计时器到点前后补发 contextmenu：这次手势同样不能被当成轻点
        const fromTouchPress = filterDecisionPress !== null;
        event.preventDefault();
        openFilterDecisionMenu(event.clientX, event.clientY, input);
        if (fromTouchPress) {
            filterDecisionLongPressFired = true;
            armFilterDecisionClickGuard(input);
        }
    });

    elements.filterList.addEventListener('touchstart', event => {
        cancelFilterDecisionPress();
        filterDecisionLongPressFired = false;
        filterDecisionClickGuard = null;
        if (event.touches.length !== 1) return;
        const input = filterDecisionInputFromTarget(event.target);
        if (!input) return;
        const touch = event.touches[0];
        const press = {
            input,
            startX: touch.clientX,
            startY: touch.clientY,
            x: touch.clientX,
            y: touch.clientY,
            timer: null,
        };
        press.timer = window.setTimeout(() => {
            if (filterDecisionPress !== press) return;
            filterDecisionPress = null;
            if (!press.input.isConnected) return;
            filterDecisionLongPressFired = true;
            armFilterDecisionClickGuard(press.input);
            openFilterDecisionMenu(press.x, press.y, press.input);
        }, FILTER_DECISION_LONG_PRESS_MS);
        filterDecisionPress = press;
    }, { passive: true });

    elements.filterList.addEventListener('touchmove', event => {
        if (!filterDecisionPress) return;
        if (event.touches.length !== 1) {
            cancelFilterDecisionPress();
            return;
        }
        const touch = event.touches[0];
        const dx = touch.clientX - filterDecisionPress.startX;
        const dy = touch.clientY - filterDecisionPress.startY;
        if (dx * dx + dy * dy > FILTER_DECISION_LONG_PRESS_MOVE_PX * FILTER_DECISION_LONG_PRESS_MOVE_PX) {
            cancelFilterDecisionPress();
            return;
        }
        filterDecisionPress.x = touch.clientX;
        filterDecisionPress.y = touch.clientY;
    }, { passive: true });

    elements.filterList.addEventListener('touchend', event => {
        if (!filterDecisionLongPressFired) {
            cancelFilterDecisionPress();
            return;
        }
        // 长按已接管这次手势：阻止浏览器把抬手补发成一次轻点
        filterDecisionLongPressFired = false;
        if (event.cancelable) event.preventDefault();
    }, { passive: false });

    elements.filterList.addEventListener('touchcancel', () => {
        filterDecisionLongPressFired = false;
        cancelFilterDecisionPress();
    });

    // 兜底：部分浏览器即便 touchend 被 preventDefault 仍会补发 click
    elements.filterList.addEventListener('click', event => {
        if (!filterDecisionClickGuard) return;
        if (Date.now() > filterDecisionClickGuard.until) {
            filterDecisionClickGuard = null;
            return;
        }
        if (filterDecisionInputFromTarget(event.target) !== filterDecisionClickGuard.input) return;
        filterDecisionClickGuard = null;
        event.preventDefault();
        event.stopPropagation();
    }, true);

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
