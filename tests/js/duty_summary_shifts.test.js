// 汇总审阅页「班次」列表显示值班人名字的浏览器端行为测试。
// 由 tests/test_duty_summary_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
// 复用 duty_summary_harness 启动 /admin/duty-summary 页。
//
// 覆盖的行为约定（改动这些行为时必须同步更新本文件）：
// 1. 每张班次卡片在日期右侧显示当天值班人（display_name），带「·」分隔；
// 2. display_name 为空时名字与分隔点整体不渲染，不留孤零零的「·」；
// 3. 名字按纯文本转义，HTML 字符串不产生元素；
// 4. 点击班次卡片切换班次的行为不受影响：选中卡片带 active，
//    顶部上下文条同步为「名字 · 日期」。
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { bootPage, waitFor, unhandledRejections, shiftRow } = require('./duty_summary_harness');

function expectedShiftDate(endsAt) {
    return new Intl.DateTimeFormat('zh-CN', {
        timeZone: 'Asia/Shanghai',
        month: 'long',
        day: 'numeric',
    }).format(new Date(endsAt));
}

async function withPage(serverOptions, body) {
    const page = await bootPage(serverOptions);
    try {
        await body(page);
    } finally {
        page.close();
    }
}

test('班次卡片在日期右侧显示当天值班人名字', async () => {
    await withPage({
        shifts: [
            shiftRow({ id: 's-today', displayName: '张三', endDayOffset: 0 }),
            shiftRow({ id: 's-yesterday', displayName: '李四', endDayOffset: -1 }),
            shiftRow({ id: 's-earlier', displayName: '王五', endDayOffset: -2 }),
        ],
    }, async (page) => {
        const cards = page.shiftCards();
        assert.equal(cards.length, 3);
        // 列表按结束时间倒序：今天在前
        assert.deepEqual(
            cards.map((card) => card.id),
            ['s-today', 's-yesterday', 's-earlier']
        );
        // 初始选中今天（ends_at 落在上海时区的今天）
        assert.equal(cards[0].active, true);
        assert.equal(cards[1].active, false);
        // 日期与值班人并列展示
        for (const [card, name] of [
            [cards[0], '张三'],
            [cards[1], '李四'],
            [cards[2], '王五'],
        ]) {
            assert.equal(card.date, expectedShiftDate(
                page.server.shifts.find((shift) => shift.shift_id === card.id).ends_at
            ));
            assert.equal(card.owner, `· ${name}`);
        }
        // 顶部上下文条同样使用值班人名字
        assert.match(page.contextText(), /^张三 · /);
    });
});

test('值班人名字缺失时不渲染名字与分隔点', async () => {
    await withPage({
        shifts: [
            shiftRow({ id: 's-named', displayName: '张三', endDayOffset: 0 }),
            shiftRow({ id: 's-blank', displayName: '', endDayOffset: -1 }),
            shiftRow({ id: 's-null', displayName: null, endDayOffset: -2 }),
        ],
    }, async (page) => {
        const cards = page.shiftCards();
        assert.equal(cards.length, 3);
        assert.equal(cards[0].owner, '· 张三');
        for (const card of cards.slice(1)) {
            assert.equal(card.ownerElement, null, '不应渲染名字元素');
            assert.ok(!card.text.includes('·'), '不应残留孤立分隔点');
        }
    });
});

test('值班人名字按纯文本转义', async () => {
    await withPage({
        shifts: [
            shiftRow({ id: 's-html', displayName: '<b>张三</b>', endDayOffset: 0 }),
        ],
    }, async (page) => {
        const [card] = page.shiftCards();
        assert.equal(card.owner, '· <b>张三</b>');
        assert.equal(card.ownerElement.querySelectorAll('b').length, 0);
    });
});

test('点击班次卡片切换班次的行为不受影响', async () => {
    await withPage({
        shifts: [
            shiftRow({ id: 's-today', displayName: '张三', endDayOffset: 0 }),
            shiftRow({ id: 's-yesterday', displayName: '李四', endDayOffset: -1 }),
        ],
    }, async (page) => {
        page.clickShift('s-yesterday');
        // renderShifts 在点击处理器里同步重渲染：选中态立即移动
        const cards = page.shiftCards();
        assert.equal(cards.find((card) => card.id === 's-yesterday').active, true);
        assert.equal(cards.find((card) => card.id === 's-today').active, false);
        // 上下文条在 loadResults 完成后同步为「名字 · 日期」
        await waitFor(() => page.contextText().startsWith('李四'));
        assert.match(page.contextText(), /^李四 · /);
        // 切换后按选择加载该班次条目
        const reviewRequest = page.server.log.find((entry) => (
            entry.path === '/api/admin/duty-summary/s-yesterday/reviews'
        ));
        assert.ok(reviewRequest, '切换班次应请求对应条目列表');
    });
});

test('页面无未处理的脚本错误', async () => {
    await withPage({
        shifts: [shiftRow({ id: 's-today', displayName: '张三', endDayOffset: 0 })],
    }, async () => {
        assert.deepEqual(unhandledRejections, []);
    });
});
