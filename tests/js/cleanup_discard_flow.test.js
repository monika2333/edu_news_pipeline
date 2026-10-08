// 清理旧新闻弹窗与批量放弃的浏览器端行为测试。
// 由 tests/test_manual_filter_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
//
// 覆盖的行为约定（改动这些行为时必须同步更新本文件）：
// 1. 清理旧新闻选日期后只发一次预览请求（body 含全部 4 个分类），点「确认放弃」
//    必须先经过 window.confirm 二次确认，取消时不得发出执行请求，确认文案包含
//    将放弃的条数；执行也只发一次请求，body 只含勾选的分类；
// 2. 清理执行失败时弹窗保持打开、toast 提示「清理失败，未做任何改动」，列表不
//    重新加载（整次操作没有生效）；
// 3. 清理旧新闻执行后提示浮窗提供撤销，撤销把整批条目带放弃时返回的版本号一次
//    回退 pending（与单条撤回同路径，版本不符时服务端 409）；
// 4. 工具栏「批量放弃」执行后同样提供撤销，两端（管理员/值班）行为一致。
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

test('清理旧新闻：选日期后只发一次预览请求且 body 含全部 4 个分类', async () => {
    await withPage('admin', { articleCount: 3 }, async (page) => {
        const { document, server } = page;
        document.getElementById('btn-open-cleanup').click();
        const dateInput = document.getElementById('cleanup-date-input');
        dateInput.value = '2025-06-01';
        page.fireChange(dateInput);
        assert.ok(
            await waitFor(() => document.getElementById('cleanup-modal-stats').textContent
                .includes('将放弃 3 条')),
            document.getElementById('cleanup-modal-stats').textContent
        );
        const previews = server.requests('cleanup-candidates');
        assert.equal(previews.length, 1, '选日期后应只发一次预览请求');
        const previewBody = previews[0].body;
        assert.equal(previewBody.created_before, '2025-06-01');
        assert.equal(previewBody.dry_run, true);
        assert.deepEqual(previewBody.buckets, [
            { region: 'internal', sentiment: 'positive' },
            { region: 'internal', sentiment: 'negative' },
            { region: 'external', sentiment: 'positive' },
            { region: 'external', sentiment: 'negative' },
        ]);
    });
});

test('清理旧新闻：二次确认取消时不发出执行请求，确认文案含条数', async () => {
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
        const applies = server.requests('cleanup-candidates')
            .filter((entry) => !entry.body.dry_run);
        assert.equal(applies.length, 0, '取消二次确认后不应发出执行请求');
        assert.match(prompts[0] || '', /确定放弃这 3 条旧新闻吗/);
    });
});

test('清理旧新闻：确认后一次执行请求只含勾选的分类，提示中可整批撤销回 pending', async () => {
    await withPage('admin', { articleCount: 3 }, async (page) => {
        const { document, server } = page;
        assert.ok(await openCleanupWithPreview(page, 3), '预览统计未出现');
        document.getElementById('btn-cleanup-confirm').click();
        assert.ok(await waitFor(() => page.toastText().includes('已放弃 3 条新闻')), page.toastText());
        const applies = server.requests('cleanup-candidates')
            .filter((entry) => !entry.body.dry_run);
        assert.equal(applies.length, 1, '确认后应只发一次执行请求');
        // 夹具数据都在京内正面，其余分类计数为 0、复选框禁用未勾选，
        // 执行 body 里不得出现它们
        assert.deepEqual(applies[0].body.buckets, [
            { region: 'internal', sentiment: 'positive' },
        ]);
        assert.equal(applies[0].body.created_before, '2025-06-01');
        assert.equal(applies[0].body.dry_run, false);
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

test('清理旧新闻：执行失败时弹窗保持打开，提示未做任何改动，列表不重载', async () => {
    await withPage('admin', { articleCount: 3 }, async (page) => {
        const { document, server } = page;
        assert.ok(await openCleanupWithPreview(page, 3), '预览统计未出现');
        const listRequestsBefore = server.requests('list').length;
        server.fail('cleanup-candidates', 1, 500);
        page.window.confirm = () => true;
        document.getElementById('btn-cleanup-confirm').click();
        assert.ok(
            await waitFor(() => page.toastText().includes('清理失败，未做任何改动')),
            page.toastText()
        );
        const modal = document.getElementById('cleanup-modal');
        assert.ok(modal.classList.contains('active'), '失败后弹窗应保持打开');
        assert.equal(
            server.requests('cleanup-candidates').filter((entry) => !entry.body.dry_run).length,
            1,
            '应发出过一次执行请求'
        );
        assert.equal(
            server.requests('list').length,
            listRequestsBefore,
            '失败后列表不得重新加载'
        );
        const confirmBtn = document.getElementById('btn-cleanup-confirm');
        assert.ok(!confirmBtn.disabled, '失败后确认按钮应恢复可用以便重试');
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
