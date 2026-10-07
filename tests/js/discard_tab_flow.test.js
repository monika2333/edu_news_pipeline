// 放弃页（筛选 + 批量恢复）的浏览器端行为测试。
// 由 tests/test_manual_filter_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
//
// 覆盖的行为约定（改动这些行为时必须同步更新本文件）：
// 1. 条件（分类/放弃时间/分数）变化后立即回第 1 页重新加载，请求携带对应参数；
// 2. 「只看这一批」原样回传行上的 decided_at 字符串（不经 Date 转换），并清掉其他条件；
// 3. 本页勾选恢复只发一次 /decide，带全部 id 与 DOM 收集的版本号，成功后清空选择；
// 4. 「全部恢复到待处理」先 dry_run 预览再执行，body 条件与列表查询参数同源；
// 5. 值班端放弃列表一律走服务端 /reviews?decision=discarded 分页，无前端全量分支。
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { bootPage, waitFor, unhandledRejections } = require('./manual_filter_harness');

const MODES = ['duty', 'admin'];
const MODE_LABELS = { duty: '值班', admin: '管理员' };

const BATCH_DECIDED_AT = '2026-10-05T06:32:05.123456Z';

function makeDiscardedItems(count, overrides = {}) {
    return Array.from({ length: count }, (_, index) => ({
        article_id: `d${String(index).padStart(2, '0')}`,
        title: `已放弃${index}`,
        source: `来源${index}`,
        version: 2,
        decided_at: BATCH_DECIDED_AT,
        is_beijing_related: true,
        sentiment_label: 'negative',
        external_importance_score: 40 - index,
        ...overrides,
    }));
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function withPage(mode, serverOptions, body) {
    const page = await bootPage(mode, serverOptions);
    try {
        await body(page);
    } finally {
        // 等在途请求落定再关窗，避免关闭后的渲染回调变成未处理拒绝
        await waitFor(() => page.server.inflight === 0).catch(() => {});
        await sleep(50);
        page.close();
    }
}

// bootPage 可能在 init.js 的 DOMContentLoaded（值班端还要先异步加载班次）完成前返回，
// tab 监听器尚未绑定，因此点击需要重试直到列表真正渲染
async function openDiscardTab(page) {
    const deadline = Date.now() + 3000;
    while (Date.now() < deadline) {
        page.document.querySelector('.tab-btn[data-tab="discard"]')?.click();
        const rendered = await waitFor(
            () => page.document.querySelectorAll('#discard-list .article-card').length > 0,
            300
        );
        if (rendered) {
            await waitFor(() => page.server.inflight === 0);
            return;
        }
        await sleep(20);
    }
    throw new Error('放弃列表未渲染');
}

function discardRequests(server) {
    return server.requests('discard-list');
}

function lastDiscardRequest(server) {
    const requests = discardRequests(server);
    assert.ok(requests.length, '没有放弃列表请求');
    return requests[requests.length - 1];
}

function fireChange(page, element) {
    element.dispatchEvent(new page.window.Event('change', { bubbles: true }));
}

// J1：条件变化后回第 1 页并携带参数（管理员与值班）
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}放弃页条件变化：请求携带分类/时间/分数参数且 offset=0`, async () => {
        await withPage(mode, { discardedItems: makeDiscardedItems(3) }, async (page) => {
            const { server } = page;
            await openDiscardTab(page);
            const baseline = discardRequests(server).length;

            page.document.querySelector('[data-discard-bucket="internal_negative"]').click();
            assert.ok(await waitFor(() => discardRequests(server).length > baseline), '分类请求未发出');
            let request = lastDiscardRequest(server);
            assert.equal(request.search.region, 'internal');
            assert.equal(request.search.sentiment, 'negative');
            assert.equal(request.search.offset, '0');

            const since = page.document.getElementById('discard-since-select');
            since.value = 'today';
            fireChange(page, since);
            const sinceExpected = new Intl.DateTimeFormat('en-CA', {
                timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
            }).format(new Date());
            assert.ok(
                await waitFor(() => {
                    const search = lastDiscardRequest(server).search;
                    return search.decided_since === sinceExpected;
                }),
                `decided_since 未携带：${JSON.stringify(lastDiscardRequest(server).search)}`
            );
            assert.equal(lastDiscardRequest(server).search.offset, '0');

            const minScore = page.document.getElementById('discard-min-score');
            minScore.value = '10';
            fireChange(page, minScore);
            const maxScore = page.document.getElementById('discard-max-score');
            maxScore.value = '30';
            fireChange(page, maxScore);
            assert.ok(
                await waitFor(() => lastDiscardRequest(server).search.min_score === '10'),
                'min_score 未携带'
            );
            assert.ok(
                await waitFor(() => lastDiscardRequest(server).search.max_score === '30'),
                'max_score 未携带'
            );
        });
    });
}

// J2：「只看这一批」原样回传 decided_at、清掉其他条件，✕ 可取消（管理员与值班）
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}只看这一批：批次参数逐字回传且其他条件被清空`, async () => {
        await withPage(mode, { discardedItems: makeDiscardedItems(3) }, async (page) => {
            const { server } = page;
            await openDiscardTab(page);

            // 先叠加其他条件
            page.document.querySelector('[data-discard-bucket="internal_negative"]').click();
            assert.ok(await waitFor(() => lastDiscardRequest(server).search.region === 'internal'));
            // 等条件请求的响应渲染完成，再取行上的批次按钮
            assert.ok(
                await waitFor(
                    () => page.document.querySelectorAll('#discard-list .article-card').length > 0
                ),
                '条件请求后列表未重新渲染'
            );

            const batchBtn = page.document.querySelector('#discard-list .discard-batch-btn');
            assert.equal(batchBtn.dataset.decidedAt, BATCH_DECIDED_AT, '行上应保存原始 decided_at');
            batchBtn.click();

            assert.ok(
                await waitFor(() => lastDiscardRequest(server).search.batch_decided_at !== undefined),
                '批次请求未发出'
            );
            const search = lastDiscardRequest(server).search;
            assert.equal(search.batch_decided_at, BATCH_DECIDED_AT, '批次参数必须与原始字符串逐字相同');
            assert.equal(search.region, undefined, '点击批次后其他条件应被清空');
            assert.equal(search.sentiment, undefined, '点击批次后其他条件应被清空');
            assert.equal(search.offset, '0');

            // meta 行出现可移除的批次 chip
            assert.ok(
                await waitFor(() => page.document.querySelector('.discard-batch-chip')),
                'meta 行应显示批次 chip'
            );

            page.document.querySelector('.discard-batch-clear').click();
            assert.ok(
                await waitFor(() => lastDiscardRequest(server).search.batch_decided_at === undefined),
                '点 ✕ 后批次参数应被移除'
            );
        });
    });
}

// J3：全选本页并恢复到待处理（管理员与值班）
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}全选本页恢复到待处理：一次 /decide 携带全部 id 与版本号`, async () => {
        const items = makeDiscardedItems(3);
        await withPage(mode, { discardedItems: items }, async (page) => {
            const { server, window } = page;
            await openDiscardTab(page);

            const selectAll = page.document.getElementById('discard-select-all');
            selectAll.checked = true;
            fireChange(page, selectAll);
            assert.ok(
                await waitFor(() => !page.document.getElementById('discard-bulk-bar').hidden),
                '批量栏未出现'
            );

            const target = page.document.getElementById('discard-bulk-target');
            target.value = 'pending';
            // 挂 toast 钩子：toast 在「清空选择」之后发出，可确定性地捕获
            // 清空时刻的选择数（不依赖列表重载的清除副作用）
            window.eval(`
                const __origShowToast = showToast;
                window.__selectionSizeAtToast = null;
                showToast = function (...args) {
                    window.__selectionSizeAtToast = discardSelection.size;
                    return __origShowToast.apply(this, args);
                };
            `);
            fireChange(page, target);

            assert.ok(
                await waitFor(() => server.requests('decide').length > 0),
                '批量 /decide 未发出'
            );
            assert.ok(
                await waitFor(() => window.eval('window.__selectionSizeAtToast') !== null),
                '成功提示未出现'
            );
            assert.equal(
                window.eval('window.__selectionSizeAtToast'),
                0,
                '恢复成功后应立即清空选择（不依赖列表重载）'
            );
            const decideRequests = server.requests('decide');
            assert.equal(decideRequests.length, 1, '应只发一次 /decide');
            const body = decideRequests[0].body;
            assert.deepEqual(
                [...body.pending_ids].sort(),
                ['d00', 'd01', 'd02'],
                'pending_ids 应包含全部 id'
            );
            assert.deepEqual(body.versions, { d00: 2, d01: 2, d02: 2 }, '应携带 DOM 收集的版本号');

            assert.ok(
                await waitFor(() => page.toastText().includes('已恢复 3 条到待处理')),
                page.toastText()
            );
            assert.ok(
                await waitFor(() => discardRequests(server).length >= 2, ),
                '恢复后列表应重新加载'
            );
            assert.ok(
                await waitFor(() => page.document.getElementById('discard-bulk-bar').hidden),
                '恢复后选择应被清空'
            );
        });
    });
}

// J7：批量 /decide 返回 409（管理员与值班）
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}批量恢复 409：错误提示并重新加载列表`, async () => {
        const items = makeDiscardedItems(2);
        await withPage(mode, { discardedItems: items }, async (page) => {
            const { server } = page;
            await openDiscardTab(page);

            const selectAll = page.document.getElementById('discard-select-all');
            selectAll.checked = true;
            fireChange(page, selectAll);

            // 注入一次 409
            server.fail('decide', 1, 409);

            const target = page.document.getElementById('discard-bulk-target');
            target.value = 'pending';
            fireChange(page, target);

            assert.ok(
                await waitFor(() => server.requests('decide').length > 0),
                '批量 /decide 未发出'
            );
            assert.ok(
                await waitFor(() => page.toastText().includes('已被其他操作更新')),
                `应出现 409 错误提示：${page.toastText()}`
            );
            assert.ok(
                await waitFor(() => discardRequests(server).length >= 2),
                '失败后列表应重新加载以刷新版本号'
            );
            assert.ok(
                await waitFor(() => page.document.getElementById('discard-bulk-bar').hidden),
                '失败后选择应被清空'
            );
        });
    });
}

// J4：「全部恢复到待处理」（管理员与值班）
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}全部恢复：无条件禁用，有条件先预览后执行且 body 与列表同源`, async () => {
        const items = makeDiscardedItems(2);
        await withPage(mode, { discardedItems: items }, async (page) => {
            const { server, window } = page;
            await openDiscardTab(page);

            const bulkBtn = page.document.getElementById('btn-discard-bulk-restore');
            assert.ok(bulkBtn.hidden || bulkBtn.disabled, '无条件时应禁用');

            // 启用批次条件（点击行上的「只看这一批」）
            page.document.querySelector('#discard-list .discard-batch-btn').click();
            assert.ok(
                await waitFor(() => lastDiscardRequest(server).search.batch_decided_at === BATCH_DECIDED_AT),
                '批次条件未生效'
            );
            assert.ok(
                await waitFor(() => !bulkBtn.hidden && !bulkBtn.disabled),
                '有条件且命中时应可用'
            );

            const listRequest = lastDiscardRequest(server);
            window.confirm = () => false;
            bulkBtn.click();

            assert.ok(
                await waitFor(() => server.requests('bulk-restore').length === 1),
                '预览请求未发出'
            );
            const preview = server.requests('bulk-restore')[0];
            assert.equal(preview.body.dry_run, true);
            // body 条件与当时列表请求的查询参数一致（同一份 pairs 生成），
            // 批次参数必须逐字一致（不经 Date 转换）
            assert.equal(preview.body.batch_decided_at, listRequest.search.batch_decided_at);
            assert.equal(preview.body.batch_decided_at, BATCH_DECIDED_AT);
            await sleep(300);
            assert.equal(server.requests('bulk-restore').length, 1, 'confirm 取消后不得发执行请求');

            window.confirm = () => true;
            bulkBtn.click();
            // 每次点击都会先发 dry_run 预览：预览×2 + 执行×1
            assert.ok(
                await waitFor(() => server.requests('bulk-restore').length === 3),
                '确认后未发执行请求'
            );
            const execute = server.requests('bulk-restore')[2];
            assert.equal(execute.body.dry_run, false);
            assert.equal(execute.body.batch_decided_at, BATCH_DECIDED_AT);

            assert.ok(
                await waitFor(() => page.toastText().includes('已恢复 2 条到待处理')),
                page.toastText()
            );
            // 回到第 1 页重新加载
            const reload = lastDiscardRequest(server);
            assert.equal(reload.search.offset, '0');
        });
    });
}

// J5（值班）：无条件时列表也走服务端 /reviews?decision=discarded 分页
test('值班放弃列表：无条件时也走服务端 /reviews 分页', async () => {
    await withPage('duty', { discardedItems: makeDiscardedItems(3) }, async (page) => {
        const { server } = page;
        await openDiscardTab(page);
        const request = lastDiscardRequest(server);
        assert.ok(request.path.endsWith('/reviews'), `应转发到 /reviews：${request.path}`);
        assert.equal(request.search.decision, 'discarded');
        assert.equal(request.search.limit, '30');
        assert.equal(request.search.offset, '0');
    });
});

// J6：恢复后当前页越界时自动退到上一页
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}恢复后翻页越界：自动请求上一页`, async () => {
        const items = makeDiscardedItems(31);
        await withPage(mode, { discardedItems: items }, async (page) => {
            const { server } = page;
            await openDiscardTab(page);

            // 跳到第 2 页（只有 1 条）
            page.window.changePage('discard', 2);
            assert.ok(
                await waitFor(() => lastDiscardRequest(server).search.offset === '30'),
                '未请求第 2 页'
            );
            assert.ok(
                await waitFor(
                    () => page.document.querySelectorAll('#discard-list .article-card').length === 1
                ),
                '第 2 页应只有 1 条'
            );

            const select = page.document.querySelector('#discard-list .discard-restore-select');
            select.value = 'pending';
            fireChange(page, select);

            assert.ok(
                await waitFor(() => server.requests('decide').length > 0),
                '单条恢复未发出'
            );
            // 恢复后总数降到 30，第 2 页越界 → 请求 offset=0
            assert.ok(
                await waitFor(
                    () => {
                        const requests = discardRequests(server);
                        const last = requests[requests.length - 1];
                        return last.search.offset === '0';
                    },
                ),
                '越界后未请求上一页'
            );
        });
    });
}

test('结束后不应有未处理的脚本异常', async () => {
    assert.deepEqual(unhandledRejections, []);
});
