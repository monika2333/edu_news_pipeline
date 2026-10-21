// 筛选页「采纳」「备选」决定菜单（桌面右键 + 触屏长按）的浏览器端行为测试。
// 由 tests/test_manual_filter_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
//
// 覆盖的行为约定（改动这些行为时必须同步更新本文件）：
// 1. 桌面右键仍然打开菜单，并直接把该条归入另一报别（不切换「归入报别」状态）；
// 2. 手机没有右键，长按是等价入口：长按弹出同一菜单，抬手补发的 click 不得被当成轻点决定；
// 3. 短按仍是原来的轻点决定（沿用「归入报别」，不弹菜单）；
// 4. 长按期间手指移动超过容差视为滚动，不打开菜单；
// 5. 「放弃」不参与该菜单，右键与长按同口径。
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { bootPage, waitFor } = require('./manual_filter_harness');

const MODES = ['duty', 'admin'];
const MODE_LABELS = { duty: '值班', admin: '管理员' };

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function withPage(mode, serverOptions, body) {
    const page = await bootPage(mode, serverOptions);
    try {
        await body(page);
    } finally {
        page.close();
    }
}

// jsdom 没有 TouchEvent：用可冒泡的 Event 补上 touches/changedTouches，足以驱动页面里的手势逻辑
function touchEvent(window, type, x, y) {
    const event = new window.Event(type, { bubbles: true, cancelable: true });
    const touch = { clientX: x, clientY: y };
    Object.defineProperty(event, 'touches', {
        value: type === 'touchend' || type === 'touchcancel' ? [] : [touch],
    });
    Object.defineProperty(event, 'changedTouches', { value: [touch] });
    return event;
}

function clickEvent(window) {
    return new window.MouseEvent('click', { bubbles: true, cancelable: true });
}

function menuItem(document) {
    const menu = document.querySelector('.filter-decision-menu.active');
    return menu ? menu.querySelector('.filter-decision-menu-item') : null;
}

// 右键入口：既有桌面行为，重构长按共用判定后仍需保持
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}右键「采纳」：菜单直接归入另一报别，不切换归入报别`, async () => {
        await withPage(mode, { articleCount: 1 }, async (page) => {
            const { window, document, server } = page;
            const label = page.card('a00').querySelector('label[for="sel-a00"]');
            const event = new window.MouseEvent('contextmenu', { bubbles: true, cancelable: true });
            label.dispatchEvent(event);

            const item = menuItem(document);
            assert.ok(item, '右键未打开菜单');
            assert.equal(item.textContent, '采纳到晚报');
            assert.equal(event.defaultPrevented, true, '右键应阻止浏览器原生菜单');
            assert.equal(window.eval('state.filterAssignReportType'), 'zongbao', '右键不得切换归入报别');

            item.click();
            assert.ok(await waitFor(() => server.requests('decide').length === 1), '菜单点击未触发决定');
            const body = server.requests('decide')[0].body;
            assert.equal(body.report_type, 'wanbao');
            assert.deepEqual(body.selected_ids, ['a00']);
            await waitFor(() => server.inflight === 0);
        });
    });
}

// 触屏入口：长按 = 右键
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}长按「备选」打开同一菜单，抬手补发的 click 不算轻点决定`, async () => {
        await withPage(mode, { articleCount: 1 }, async (page) => {
            const { window, document, server } = page;
            const label = page.card('a00').querySelector('label[for="bak-a00"]');

            label.dispatchEvent(touchEvent(window, 'touchstart', 12, 30));
            assert.ok(
                await waitFor(() => menuItem(document)),
                '长按后菜单未出现'
            );
            assert.equal(menuItem(document).textContent, '备选到晚报');

            label.dispatchEvent(touchEvent(window, 'touchend', 12, 30));
            // 部分浏览器在长按抬手后仍会补发一次 click
            label.dispatchEvent(clickEvent(window));
            await sleep(80);
            assert.equal(server.requests('decide').length, 0, '长按抬手不应产生决定请求');
            assert.ok(menuItem(document), '补发的 click 不应关闭菜单');

            menuItem(document).click();
            assert.ok(await waitFor(() => server.requests('decide').length === 1), '菜单点击未触发决定');
            const body = server.requests('decide')[0].body;
            assert.equal(body.report_type, 'wanbao');
            assert.deepEqual(body.backup_ids, ['a00']);
            await waitFor(() => server.inflight === 0);
        });
    });
}

test('短按「采纳」仍是轻点决定：不弹菜单，落在归入报别（综报）', async () => {
    await withPage('admin', { articleCount: 1 }, async (page) => {
        const { window, document, server } = page;
        const label = page.card('a00').querySelector('label[for="sel-a00"]');

        label.dispatchEvent(touchEvent(window, 'touchstart', 12, 30));
        await sleep(150);
        label.dispatchEvent(touchEvent(window, 'touchend', 12, 30));
        assert.equal(menuItem(document), null, '短按不应打开菜单');

        // 轻点由浏览器合成 click，走原有的单条决定逻辑
        label.dispatchEvent(clickEvent(window));
        assert.ok(await waitFor(() => server.requests('decide').length === 1), '轻点未触发决定');
        assert.equal(server.requests('decide')[0].body.report_type, 'zongbao');
        await waitFor(() => server.inflight === 0);
    });
});

test('长按期间移动超过容差视为滚动，不打开菜单', async () => {
    await withPage('admin', { articleCount: 1 }, async (page) => {
        const { window, document } = page;
        const label = page.card('a00').querySelector('label[for="sel-a00"]');

        label.dispatchEvent(touchEvent(window, 'touchstart', 12, 30));
        label.dispatchEvent(touchEvent(window, 'touchmove', 12, 70));
        await sleep(700);
        assert.equal(menuItem(document), null, '滚动中的长按不应打开菜单');
        label.dispatchEvent(touchEvent(window, 'touchend', 12, 70));
    });
});

test('「放弃」不参与决定菜单：右键与长按都不弹', async () => {
    await withPage('admin', { articleCount: 1 }, async (page) => {
        const { window, document } = page;
        const label = page.card('a00').querySelector('label[for="dis-a00"]');

        label.dispatchEvent(new window.MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
        await sleep(80);
        assert.equal(menuItem(document), null, '「放弃」右键不应弹菜单');

        label.dispatchEvent(touchEvent(window, 'touchstart', 12, 30));
        await sleep(700);
        assert.equal(menuItem(document), null, '「放弃」长按不应弹菜单');
        label.dispatchEvent(touchEvent(window, 'touchend', 12, 30));
    });
});

test('聚类标题的长按与右键同口径：整簇直接归入另一报别', async () => {
    await withPage('admin', { articleCount: 2, clusters: [['a00', 'a01']] }, async (page) => {
        const { window, document, server } = page;
        const label = document.querySelector('#cluster-sel-c0 + label')
            || document.querySelector('label[for="cluster-sel-c0"]');
        assert.ok(label, '聚类「采纳」标签未渲染');

        label.dispatchEvent(touchEvent(window, 'touchstart', 20, 40));
        assert.ok(await waitFor(() => menuItem(document)), '长按后菜单未出现');
        assert.equal(menuItem(document).textContent, '采纳到晚报');
        label.dispatchEvent(touchEvent(window, 'touchend', 20, 40));

        menuItem(document).click();
        assert.ok(await waitFor(() => server.requests('decide').length === 1), '菜单点击未触发决定');
        const body = server.requests('decide')[0].body;
        assert.equal(body.report_type, 'wanbao');
        assert.deepEqual(body.selected_ids.sort(), ['a00', 'a01']);
        await waitFor(() => server.inflight === 0);
    });
});
