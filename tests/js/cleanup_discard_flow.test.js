// 清理旧新闻弹窗与批量放弃的浏览器端行为测试。
// 由 tests/test_manual_filter_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
//
// 覆盖的行为约定（改动这些行为时必须同步更新本文件）：
// 1. 清理旧新闻点「确认放弃」必须先经过 window.confirm 二次确认，取消时不得发出
//    放弃请求，确认文案包含将放弃的条数；
// 2. 清理旧新闻执行后提示浮窗提供撤销，撤销把整批条目带放弃时返回的版本号回退
//    pending（与单条撤回同路径，版本不符时服务端 409）；
// 3. 工具栏「批量放弃」执行后同样提供撤销，两端（管理员/值班）行为一致。
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { bootPage, waitFor, unhandledRejections } = require('./manual_filter_harness');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function withPage(mode, serverOptions, body) {
    const page = await bootPage(mode, serverOptions);
    try {
        await body(page);
    } finally {
        page.close();
    }
}

function openCleanupWithPreview(page, articleCount) {
    const { document } = page;
    page.window.confirm = () => true;
    document.getElementById('btn-open-cleanup').click();
    const dateInput = document.getElementById('cleanup-date-input');
    dateInput.value = '2025-06-01';
    page.fireChange(dateInput);
    return waitFor(() => document.getElementById('cleanup-modal-stats').textContent
        .includes(`将放弃 ${articleCount} 条`));
}

test('清理旧新闻：二次确认取消时不发出放弃请求，确认文案含条数', async () => {
    await withPage('admin', { articleCount: 3 }, async (page) => {
        const { document, server } = page;
        const prompts = [];
        page.window.confirm = (message) => {
            prompts.push(message);
            return false;
        };
        document.getElementById('btn-open-cleanup').click();
        const dateInput = document.getElementById('cleanup-date-input');
        dateInput.value = '2025-06-01';
        page.fireChange(dateInput);
        assert.ok(
            await waitFor(() => document.getElementById('cleanup-modal-stats').textContent
                .includes('将放弃 3 条')),
            document.getElementById('cleanup-modal-stats').textContent
        );
        document.getElementById('btn-cleanup-confirm').click();
        await sleep(20);
        const applies = server.requests('bulk-discard').filter((entry) => !entry.body.dry_run);
        assert.equal(applies.length, 0, '取消二次确认后不应发出放弃请求');
        assert.match(prompts[0] || '', /确定放弃这 3 条旧新闻吗/);
    });
});

test('清理旧新闻：确认后放弃，提示中可整批撤销回 pending', async () => {
    await withPage('admin', { articleCount: 3 }, async (page) => {
        const { document, server } = page;
        assert.ok(await openCleanupWithPreview(page, 3), '预览统计未出现');
        document.getElementById('btn-cleanup-confirm').click();
        assert.ok(await waitFor(() => page.toastText().includes('已放弃 3 条新闻')), page.toastText());
        assert.ok(document.querySelector('#toast button'), '提示中应有撤销按钮');
        document.querySelector('#toast button').click();
        assert.ok(await waitFor(() => page.toastText().includes('已撤销')), page.toastText());
        assert.ok(await waitFor(() => page.cardIds().length === 3), page.cardIds().join(','));
        const decides = server.requests('decide');
        const undoCall = decides[decides.length - 1];
        assert.equal(undoCall.status, 200);
        assert.deepEqual([...undoCall.body.pending_ids].sort(), ['a00', 'a01', 'a02']);
        assert.equal(Object.keys(undoCall.body.versions).length, 3, '撤销必须携带版本号');
    });
});

for (const mode of ['admin', 'duty']) {
    test(`${mode}「批量放弃」：提示中可整批撤销回 pending`, async () => {
        await withPage(mode, { articleCount: 2 }, async (page) => {
            const { document, server } = page;
            page.window.confirm = () => true;
            document.getElementById('btn-filter-bulk-discard').click();
            assert.ok(await waitFor(() => page.toastText().includes('已放弃 2 条新闻')), page.toastText());
            assert.ok(document.querySelector('#toast button'), '提示中应有撤销按钮');
            document.querySelector('#toast button').click();
            assert.ok(await waitFor(() => page.toastText().includes('已撤销')), page.toastText());
            assert.ok(await waitFor(() => page.cardIds().length === 2), page.cardIds().join(','));
            const undoCall = server.requests('decide')[0];
            assert.deepEqual([...undoCall.body.pending_ids].sort(), ['a00', 'a01']);
        });
    });
}

test('以上场景没有产生未处理的 Promise rejection', () => {
    assert.deepEqual(unhandledRejections, []);
});
