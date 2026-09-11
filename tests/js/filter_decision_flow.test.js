// 筛选页决定流程的浏览器端行为测试。
// 由 tests/test_manual_filter_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
//
// 覆盖的行为约定（改动这些行为时必须同步更新本文件）：
// 1. 值班端做出决定后立即显示提示与撤销，补页重载在后台进行，不阻塞提示；
// 2. loadFilterData 最新请求获胜，先发后到的旧响应不得覆盖新列表；
// 3. 决定操作只保存与「最近一次被服务端确认的值」不同的卡片；
// 4. 决定前等待进行中的编辑保存完成，不得带同一版本号并发写入（否则 409）。
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { bootPage, waitFor, unhandledRejections } = require('./manual_filter_harness');

async function withPage(mode, serverOptions, body) {
    const page = await bootPage(mode, serverOptions);
    try {
        await body(page);
    } finally {
        page.close();
    }
}

test('值班「放弃本页剩余内容」：补页请求未返回时提示与撤销已出现，放行后显示下一页', async () => {
    await withPage('duty', { articleCount: 13 }, async (page) => {
        const { window, server } = page;
        server.hold('list');
        const action = window.discardRemainingItems();
        assert.ok(
            await waitFor(() => page.toastText().includes('已放弃 10 条新闻')),
            `提示未在补页返回前出现：${page.toastText()}`
        );
        assert.equal(server.heldCount('list'), 1, '补页请求应仍被扣住');
        assert.ok(page.document.querySelector('#toast button'), '提示中应有撤销按钮');
        server.release('list');
        await action;
        assert.ok(await waitFor(() => page.cardIds().length === 3), page.cardIds().join(','));
    });
});

test('值班单条决定清空本页：补页请求未返回时提示已出现', async () => {
    await withPage('duty', { articleCount: 1 }, async (page) => {
        const { server } = page;
        server.hold('list');
        page.chooseRadio(page.card('a00').querySelector('input[type="radio"][value="discarded"]'));
        assert.ok(await waitFor(() => page.toastText().includes('已放弃')), page.toastText());
        assert.equal(server.heldCount('list'), 1);
        server.release('list');
    });
});

test('值班整簇决定清空本页：补页请求未返回时提示已出现，且未编辑时不发 /edit', async () => {
    await withPage('duty', { articleCount: 2, clusters: [['a00', 'a01']] }, async (page) => {
        const { document, server } = page;
        server.hold('list');
        page.chooseRadio(document.querySelector('#filter-list .cluster-radio input[value="discarded"]'));
        assert.ok(await waitFor(() => page.toastText().includes('已放弃 2 条新闻')), page.toastText());
        assert.equal(server.heldCount('list'), 1);
        assert.equal(server.requests('edit').length, 0);
        server.release('list');
    });
});

test('最新请求获胜：先发后到的旧响应不覆盖新列表，被取代的失败请求不显示错误', async () => {
    await withPage('duty', { articleCount: 5 }, async (page) => {
        const { window, server } = page;
        server.hold('list');
        const olderLoad = window.loadFilterData(); // 发出时快照为 5 条，被扣住
        await waitFor(() => server.heldCount('list') === 1);
        server.articles.get('a00').decision = 'discarded';
        server.articles.get('a01').decision = 'discarded';
        await window.loadFilterData(); // 新请求：3 条
        assert.deepEqual(page.cardIds(), ['a02', 'a03', 'a04']);
        server.release('list');
        await olderLoad;
        assert.deepEqual(page.cardIds(), ['a02', 'a03', 'a04'], '旧响应覆盖了新列表');

        server.hold('list');
        server.failNext.list = 1; // 失败落在被扣住的这次（较早发出）请求上
        const failingLoad = window.loadFilterData();
        await waitFor(() => server.heldCount('list') === 1);
        await window.loadFilterData();
        server.release('list');
        await failingLoad;
        assert.ok(!page.listHtml().includes('加载数据失败'), '被取代的失败请求渲染了错误');
        assert.deepEqual(page.cardIds(), ['a02', 'a03', 'a04']);
    });
});

test('值班放弃后立即撤销：后台补页先发后到，不覆盖撤销后的列表', async () => {
    await withPage('duty', { articleCount: 13 }, async (page) => {
        const { window, document, server } = page;
        server.hold('list');
        const action = window.discardRemainingItems();
        assert.ok(await waitFor(() => page.toastText().includes('已放弃')));
        document.querySelector('#toast button').click();
        assert.ok(await waitFor(() => page.toastText().includes('已撤销')), page.toastText());
        assert.ok(await waitFor(() => page.cardIds().length === 10), page.cardIds().join(','));
        server.release('list');
        await action;
        await waitFor(() => server.inflight === 0);
        assert.equal(page.cardIds().length, 10, page.cardIds().join(','));
        assert.equal(page.cardIds()[0], 'a00');
    });
});

for (const mode of ['duty', 'admin']) {
    test(`${mode === 'duty' ? '值班' : '管理员'}未编辑整页放弃：不发 /edit`, async () => {
        await withPage(mode, { articleCount: 4 }, async (page) => {
            await page.window.discardRemainingItems();
            const { server } = page;
            assert.equal(server.requests('edit').length, 0);
            assert.equal(server.requests('decide')[0]?.status, 200);
        });
    });
}

test('单条决定未编辑时不发 /edit', async () => {
    await withPage('duty', { articleCount: 3 }, async (page) => {
        const { server } = page;
        page.chooseRadio(page.card('a01').querySelector('input[type="radio"][value="selected"]'));
        assert.ok(await waitFor(() => server.requests('decide').some((entry) => entry.done)));
        assert.equal(server.requests('edit').length, 0);
        assert.ok(await waitFor(() => !page.cardIds().includes('a01')));
    });
});

test('change 保存失败的卡片在决定前被重新保存', async () => {
    await withPage('duty', { articleCount: 4 }, async (page) => {
        const { window, server } = page;
        const box = page.setEditValue('a02', '.summary-box', '人工改写');
        server.failNext.edit = 1;
        page.fireChange(box);
        assert.ok(await waitFor(() => server.requests('edit').some((entry) => entry.done)));
        await window.discardRemainingItems();
        const edits = server.requests('edit');
        assert.deepEqual(edits.map((entry) => entry.status), [500, 200]);
        assert.deepEqual(Object.keys(edits[1].body.edits), ['a02'], '只应重存改过的卡片');
        assert.ok(server.log.indexOf(edits[1]) < server.log.indexOf(server.requests('decide')[0]));
        assert.equal(server.articles.get('a02').manual_summary, '人工改写');
    });
});

test('编辑保存进行中时点决定：等待保存完成，无 409，只发一次 /edit', async () => {
    await withPage('duty', { articleCount: 4 }, async (page) => {
        const { window, server } = page;
        const box = page.setEditValue('a01', '.summary-box', '改后摘要');
        box.focus();
        server.hold('edit');
        page.fireChange(box); // 浏览器在按下按钮、编辑框失焦时触发
        const action = window.discardRemainingItems();
        assert.equal(
            await waitFor(() => server.requests('decide').length > 0, 200),
            false,
            '编辑保存尚未完成就提交了决定'
        );
        server.release('edit');
        await action;
        const statuses = server.log.map((entry) => `${entry.kind}:${entry.status}`).join(' ');
        assert.ok(!server.log.some((entry) => entry.status === 409), statuses);
        assert.equal(server.requests('edit').length, 1, statuses);
        assert.equal(server.requests('decide')[0]?.status, 200, statuses);
        assert.equal(server.articles.get('a01').manual_summary, '改后摘要');
    });
});

test('焦点仍在编辑框、尚未触发 change 时点决定：编辑不丢失', async () => {
    await withPage('duty', { articleCount: 3 }, async (page) => {
        const { window, server } = page;
        const box = page.setEditValue('a00', '.source-box', '人工来源');
        box.focus();
        await window.discardRemainingItems();
        assert.ok(!server.log.some((entry) => entry.status === 409));
        assert.equal(server.articles.get('a00').manual_source, '人工来源');
    });
});

test('change 保存成功后再决定：不重复发 /edit', async () => {
    await withPage('duty', { articleCount: 4 }, async (page) => {
        const { window, server } = page;
        page.fireChange(page.setEditValue('a03', '.summary-box', '已保存的改写'));
        assert.ok(await waitFor(() => page.toastText().includes('已保存')));
        await window.discardRemainingItems();
        assert.equal(server.requests('edit').length, 1);
    });
});

test('以上场景没有产生未处理的 Promise rejection', () => {
    assert.deepEqual(unhandledRejections, []);
});
