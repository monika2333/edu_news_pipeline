// 放弃页（批次筛选 + 扩展选择批量恢复）的浏览器端行为测试。
// 由 tests/test_manual_filter_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
//
// 覆盖的行为约定（改动这些行为时必须同步更新本文件）：
// 1. 条件（分类/放弃时间/分数）变化后立即回第 1 页重新加载，请求携带对应参数；
// 2. 「放弃批次」是独立下拉（与「放弃时间」分离）：选中后 batch_decided_at 原样
//    回传并清掉其他条件；当前批次不在最新批次列表里时，下拉仍如实显示该批次；
// 3. 选择范围与操作只有一套控件：本页勾选 → /decide；可扩展到「全部匹配」，
//    此时只能恢复到待处理，走 /bulk-restore 的 dry_run 预览 + confirm 执行；
// 4. 翻页、改条件、列表重新加载都会退出全部匹配模式并清空选择；
// 5. 值班端放弃列表一律走服务端 /reviews?decision=discarded 分页；
// 6. 条件行常驻显示，无折叠开关。
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { bootPage, waitFor, unhandledRejections } = require('./manual_filter_harness');

const MODES = ['duty', 'admin'];
const MODE_LABELS = { duty: '值班', admin: '管理员' };

const BATCH_DECIDED_AT = '2026-10-05T06:32:05.123456Z';
// 往年批次（按 Asia/Shanghai 与当前年不同年，下拉与行内时间都要带年份）
const LAST_YEAR_BATCH_DECIDED_AT = '2025-11-03T01:05:00.000001Z';
const DISCARD_PAGE_SIZE = 30;

// 生成指定批次的条目；idPrefix 避免不同批次撞 id
function makeBatchItems(prefix, count, decidedAt, overrides = {}) {
    return Array.from({ length: count }, (_, index) => ({
        article_id: `${prefix}${String(index).padStart(2, '0')}`,
        title: `已放弃${prefix}${index}`,
        source: `来源${index}`,
        version: 2,
        decided_at: decidedAt,
        is_beijing_related: true,
        sentiment_label: 'negative',
        external_importance_score: 40 - index,
        ...overrides,
    }));
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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

async function waitForBatchOption(page) {
    assert.ok(
        await waitFor(
            () => page.document.querySelectorAll('#discard-batch-select option[value]').length > 0
        ),
        '「放弃批次」下拉未加载批次项'
    );
}

// 选中「放弃批次」下拉中的某一批
function selectBatch(page, decidedAt) {
    const select = page.document.getElementById('discard-batch-select');
    select.value = decidedAt;
    fireChange(page, select);
}

// 选中「分类」下拉中的某一类
function selectBucket(page, bucketKey) {
    const select = page.document.getElementById('discard-bucket-select');
    select.value = bucketKey;
    fireChange(page, select);
}

// J1（改写）：分类下拉携带 region/sentiment，选回「全部」后不再携带；
// 页面上已没有分类分段按钮
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}分类下拉：请求携带 region/sentiment，选回全部后移除`, async () => {
        await withPage(mode, { discardedItems: makeDiscardedItems(3) }, async (page) => {
            const { server } = page;
            await openDiscardTab(page);
            const baseline = discardRequests(server).length;

            assert.equal(
                page.document.querySelectorAll('.discard-filter-bucket').length,
                0,
                '分类分段按钮应已删除'
            );
            assert.ok(
                page.document.getElementById('discard-bucket-select'),
                '分类下拉应存在'
            );

            selectBucket(page, 'internal_negative');
            assert.ok(await waitFor(() => discardRequests(server).length > baseline), '分类请求未发出');
            let request = lastDiscardRequest(server);
            assert.equal(request.search.region, 'internal');
            assert.equal(request.search.sentiment, 'negative');
            assert.equal(request.search.offset, '0');

            selectBucket(page, 'external_positive');
            assert.ok(
                await waitFor(() => {
                    const search = lastDiscardRequest(server).search;
                    return search.region === 'external' && search.sentiment === 'positive';
                }),
                '切换分类后参数应跟随'
            );

            selectBucket(page, 'all');
            assert.ok(
                await waitFor(() => {
                    const search = lastDiscardRequest(server).search;
                    return search.region === undefined && search.sentiment === undefined;
                }),
                '选回全部后不得再携带分类参数'
            );

            // 其余两个条件控件的参数行为保持不变
            const since = page.document.getElementById('discard-since-select');
            since.value = 'today';
            fireChange(page, since);
            const sinceExpected = new Intl.DateTimeFormat('en-CA', {
                timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
            }).format(new Date());
            assert.ok(
                await waitFor(() => lastDiscardRequest(server).search.decided_since === sinceExpected),
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

// J2（改写）：选中「放弃批次」下拉中的某一批
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}选中放弃批次：batch_decided_at 逐字回传且其他条件被清空`, async () => {
        await withPage(mode, { discardedItems: makeDiscardedItems(3) }, async (page) => {
            const { server } = page;
            await openDiscardTab(page);
            await waitForBatchOption(page);

            // 先叠加其他条件（分类 + 分数 + 关键词 + 时间预设）
            selectBucket(page, 'internal_negative');
            assert.ok(await waitFor(() => lastDiscardRequest(server).search.region === 'internal'));
            const minScore = page.document.getElementById('discard-min-score');
            minScore.value = '5';
            fireChange(page, minScore);
            const searchInput = page.document.getElementById('discard-search-input');
            searchInput.value = '已放弃';
            page.document.getElementById('btn-discard-search').click();
            assert.ok(await waitFor(() => lastDiscardRequest(server).search.q === '已放弃'));
            const since = page.document.getElementById('discard-since-select');
            since.value = '3d';
            fireChange(page, since);
            assert.ok(await waitFor(() => lastDiscardRequest(server).search.decided_since !== undefined));

            const batchOption = page.document.querySelector(
                `#discard-batch-select option[value="${BATCH_DECIDED_AT}"]`
            );
            assert.ok(batchOption, '「放弃批次」下拉里应有批次选项');
            selectBatch(page, BATCH_DECIDED_AT);

            assert.ok(
                await waitFor(() => lastDiscardRequest(server).search.batch_decided_at !== undefined),
                '批次请求未发出'
            );
            const search = lastDiscardRequest(server).search;
            assert.equal(search.batch_decided_at, BATCH_DECIDED_AT, '批次参数必须与原始字符串逐字相同');
            assert.equal(search.region, undefined, '选中批次后其他条件应被清空');
            assert.equal(search.sentiment, undefined, '选中批次后其他条件应被清空');
            assert.equal(search.min_score, undefined, '选中批次后其他条件应被清空');
            assert.equal(search.q, undefined, '选中批次后其他条件应被清空');
            assert.equal(search.offset, '0');
            // 「放弃时间」被重置为「全部」，且该下拉里不再有任何批次选项
            assert.equal(since.value, '', '「放弃时间」应显示为全部');
            assert.equal(
                page.document.querySelectorAll('#discard-since-select option').length,
                4,
                '「放弃时间」下拉应只有全部+三个预设'
            );
            assert.equal(
                page.document.querySelector('#discard-since-select optgroup'),
                null,
                '「放弃时间」下拉不得再有批次分组'
            );

            // 选回「全部」后，请求不再带批次参数
            selectBatch(page, '');
            assert.ok(
                await waitFor(() => lastDiscardRequest(server).search.batch_decided_at === undefined),
                '选回全部后批次参数应被移除'
            );
        });
    });
}

// J9（改写）：当前生效的批次不在最新批次列表里时，「放弃批次」下拉仍显示该批次
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}批次被挤出列表：下拉仍如实显示当前批次`, async () => {
        await withPage(mode, { discardedItems: makeDiscardedItems(3) }, async (page) => {
            const { server } = page;
            await openDiscardTab(page);
            await waitForBatchOption(page);

            selectBatch(page, BATCH_DECIDED_AT);
            assert.ok(
                await waitFor(() => lastDiscardRequest(server).search.batch_decided_at === BATCH_DECIDED_AT),
                '批次条件未生效'
            );

            // 让该批次掉出「最近批次」列表（只剩 1 条，不再满足 >= 2）后重新加载
            server.discardedItems = server.discardedItems.slice(0, 1);
            page.window.changePage('discard', 1);
            assert.ok(
                await waitFor(() => page.window.eval('discardBatches.length') === 0),
                '批次应已从最近批次列表中掉出'
            );
            assert.equal(
                page.document.getElementById('discard-batch-select').value,
                BATCH_DECIDED_AT,
                '「放弃批次」下拉必须仍显示当前生效批次，绝不能显示成「全部」'
            );
        });
    });
}

// J3：本页勾选恢复到待处理（管理员与值班）
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}本页勾选恢复到待处理：一次 /decide 携带全部 id 与版本号`, async () => {
        const items = makeDiscardedItems(3);
        await withPage(mode, { discardedItems: items }, async (page) => {
            const { server, window } = page;
            await openDiscardTab(page);

            const selectAll = page.document.getElementById('discard-select-all');
            selectAll.checked = true;
            fireChange(page, selectAll);
            assert.ok(
                await waitFor(() => !page.document.getElementById('discard-bulk-target').disabled),
                '有选择时「恢复到」应可用'
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
                await waitFor(() => discardRequests(server).length >= 2),
                '恢复后列表应重新加载'
            );
        });
    });
}

// J4（改写）：扩展选择 + 全部恢复到待处理（管理员与值班）
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}扩展选择全部恢复：扩展条件、目标禁用、预览+confirm+执行`, async () => {
        const items = makeDiscardedItems(DISCARD_PAGE_SIZE + 1);
        await withPage(mode, { discardedItems: items }, async (page) => {
            const { server, window } = page;
            await openDiscardTab(page);

            // 无任何条件时：本页全选也不出现「选择全部」
            const selectAll = page.document.getElementById('discard-select-all');
            selectAll.checked = true;
            fireChange(page, selectAll);
            assert.ok(
                page.document.getElementById('btn-discard-select-all-matched').hidden,
                '无筛选条件时不得出现「选择全部」'
            );

            // 设置批次条件（经「放弃批次」下拉）：总数 31 > 本页 30 行；
            // 恢复 body 与列表参数的同源性必须覆盖 batch_decided_at（M8 的目标）
            await waitForBatchOption(page);
            selectBatch(page, BATCH_DECIDED_AT);
            assert.ok(
                await waitFor(() => lastDiscardRequest(server).search.batch_decided_at === BATCH_DECIDED_AT),
                '批次条件未生效'
            );
            await waitFor(() => server.inflight === 0);
            // 条件变化会清空选择，重新全选本页
            selectAll.checked = true;
            fireChange(page, selectAll);
            assert.ok(
                await waitFor(() => !page.document.getElementById('btn-discard-select-all-matched').hidden),
                '本页全选 + 总数超一页 + 有条件时应出现「选择全部」'
            );
            assert.equal(
                page.document.getElementById('btn-discard-select-all-matched').textContent,
                `选择全部 ${DISCARD_PAGE_SIZE + 1} 条`
            );

            // 扩展到全部匹配：只允许「待处理」
            page.document.getElementById('btn-discard-select-all-matched').click();
            assert.equal(window.eval('discardSelectionMode'), 'all');
            assert.ok(
                page.document.getElementById('discard-select-all-label').textContent.includes(
                    `已选全部 ${DISCARD_PAGE_SIZE + 1} 条`
                )
            );
            const target = page.document.getElementById('discard-bulk-target');
            const enabledTargets = [...target.querySelectorAll('option[data-bulk-target]')]
                .filter((option) => !option.disabled)
                .map((option) => option.value);
            assert.deepEqual(enabledTargets, ['pending'], '全部匹配模式下只有「待处理」可选');

            // 选「待处理」：先预览；confirm 取消时不发执行请求
            const listRequest = lastDiscardRequest(server);
            window.confirm = () => false;
            target.value = 'pending';
            fireChange(page, target);
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

            // 确认后执行（每次点击都会先发 dry_run 预览：预览×2 + 执行×1）
            window.confirm = () => true;
            target.value = 'pending';
            fireChange(page, target);
            assert.ok(
                await waitFor(() => server.requests('bulk-restore').length === 3),
                '确认后未发执行请求'
            );
            const execute = server.requests('bulk-restore')[2];
            assert.equal(execute.body.dry_run, false);
            assert.equal(execute.body.batch_decided_at, BATCH_DECIDED_AT);

            assert.ok(
                await waitFor(() => page.toastText().includes(`已恢复 ${DISCARD_PAGE_SIZE + 1} 条到待处理`)),
                page.toastText()
            );
            // 恢复成功后退出全部模式，回第 1 页
            assert.equal(window.eval('discardSelectionMode'), 'page');
            const reload = lastDiscardRequest(server);
            assert.equal(reload.search.offset, '0');
        });
    });
}

// J10：总数不超过一页时，全选本页即全部，走 /decide
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}总数不超过一页：无「选择全部」，五目标可用走 /decide`, async () => {
        const items = makeDiscardedItems(2);
        await withPage(mode, { discardedItems: items }, async (page) => {
            const { server } = page;
            await openDiscardTab(page);

            const selectAll = page.document.getElementById('discard-select-all');
            selectAll.checked = true;
            fireChange(page, selectAll);
            await waitFor(() => server.inflight === 0);

            assert.ok(
                page.document.getElementById('btn-discard-select-all-matched').hidden,
                '总数不超过一页时不应出现「选择全部」'
            );
            const target = page.document.getElementById('discard-bulk-target');
            assert.ok(!target.disabled, '「恢复到」应可用');
            const disabledTargets = [...target.querySelectorAll('option[data-bulk-target]')]
                .filter((option) => option.disabled);
            assert.deepEqual(disabledTargets, [], '五个目标都应可用');

            target.value = 'zongbao:selected';
            fireChange(page, target);
            assert.ok(
                await waitFor(() => server.requests('decide').length === 1),
                '应走一次 /decide'
            );
            const body = server.requests('decide')[0].body;
            assert.deepEqual(body.selected_ids, ['d00', 'd01']);
        });
    });
}

// J11：全部模式下，改条件 / 翻页 / 取消一行都会退出全部模式
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}全部模式的退出路径：改条件、翻页、取消一行`, async () => {
        const items = makeDiscardedItems(DISCARD_PAGE_SIZE + 1);
        await withPage(mode, { discardedItems: items }, async (page) => {
            const { server } = page;
            await openDiscardTab(page);

            const enterAllMode = async () => {
                await waitForBatchOption(page);
                selectBatch(page, BATCH_DECIDED_AT);
                assert.ok(
                    await waitFor(() => lastDiscardRequest(server).search.batch_decided_at === BATCH_DECIDED_AT),
                    '批次条件未生效'
                );
                await waitFor(() => server.inflight === 0);
                const selectAll = page.document.getElementById('discard-select-all');
                selectAll.checked = true;
                fireChange(page, selectAll);
                assert.ok(
                    await waitFor(() => !page.document.getElementById('btn-discard-select-all-matched').hidden),
                    '应出现「选择全部」'
                );
                page.document.getElementById('btn-discard-select-all-matched').click();
                assert.equal(page.window.eval('discardSelectionMode'), 'all');
            };

            // ① 改条件退出（切回预设「今天」）
            await enterAllMode();
            const sinceSelect = page.document.getElementById('discard-since-select');
            sinceSelect.value = 'today';
            fireChange(page, sinceSelect);
            assert.ok(
                await waitFor(() => page.window.eval('discardSelectionMode') === 'page'),
                '改条件后应退出全部模式'
            );
            assert.ok(
                await waitFor(() => lastDiscardRequest(server).search.decided_since !== undefined),
                '条件请求应照常发出'
            );

            // ② 翻页退出
            await enterAllMode();
            page.window.changePage('discard', 1);
            assert.ok(
                await waitFor(() => page.window.eval('discardSelectionMode') === 'page'),
                '翻页后应退出全部模式'
            );

            // ③ 取消任意一行退回本页选择模式
            await enterAllMode();
            const firstRowCheck = page.document.querySelector('#discard-list .discard-row-check');
            firstRowCheck.checked = false;
            fireChange(page, firstRowCheck);
            assert.equal(page.window.eval('discardSelectionMode'), 'page');
            assert.equal(page.window.eval('discardSelection.size'), DISCARD_PAGE_SIZE - 1);
            assert.ok(
                page.document.getElementById('btn-discard-exit-all').hidden,
                '退回本页模式后「取消选择」应消失'
            );
            assert.ok(!firstRowCheck.checked, '被取消的行不应勾选');
        });
    });
}

// J12：页面上没有「全部恢复到待处理」按钮
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}独立的「全部恢复到待处理」按钮已移除`, async () => {
        await withPage(mode, { discardedItems: makeDiscardedItems(2) }, async (page) => {
            await openDiscardTab(page);
            assert.equal(
                page.document.getElementById('btn-discard-bulk-restore'),
                null,
                '不应存在独立的「全部恢复到待处理」按钮'
            );
        });
    });
}

// J8：行内放弃时间不可点击，点击后不发出任何列表请求
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}行内放弃时间为普通文本：不可点击`, async () => {
        await withPage(mode, { discardedItems: makeDiscardedItems(2) }, async (page) => {
            const { server } = page;
            await openDiscardTab(page);

            assert.equal(
                page.document.querySelector('#discard-list .discard-batch-btn'),
                null,
                '行内不应再有批次点击入口'
            );
            const time = page.document.querySelector('#discard-list .discard-item-time');
            assert.ok(time, '行内应保留放弃时间文本');
            const baseline = discardRequests(server).length;
            time.click();
            await sleep(200);
            assert.equal(discardRequests(server).length, baseline, '点击行内时间不得发出列表请求');
            assert.equal(
                lastDiscardRequest(server).search.batch_decided_at,
                undefined,
                '行内点击不得触发批次条件'
            );
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
        const items = makeDiscardedItems(DISCARD_PAGE_SIZE + 1);
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
                await waitFor(() => page.window.eval('discardSelection.size') === 0),
                '失败后选择应被清空'
            );
        });
    });
}

// J4（分类+分数+关键词驱动）：扩展选择全部恢复，预览与执行 body 与列表参数一致。
// 与批次驱动的用例并存：body 同源性必须覆盖 q / min_score（M19 的目标）。
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}扩展选择全部恢复（分类+分数+关键词）：body 与列表同源`, async () => {
        const items = makeDiscardedItems(DISCARD_PAGE_SIZE + 1);
        await withPage(mode, { discardedItems: items }, async (page) => {
            const { server, window } = page;
            await openDiscardTab(page);

            // 分类（下拉）
            selectBucket(page, 'internal_negative');
            assert.ok(await waitFor(() => lastDiscardRequest(server).search.region === 'internal'));
            // 分数
            const minScore = page.document.getElementById('discard-min-score');
            minScore.value = '5';
            fireChange(page, minScore);
            assert.ok(
                await waitFor(() => lastDiscardRequest(server).search.min_score === '5'),
                'min_score 条件未生效'
            );
            // 关键词（走「检索」按钮，与用户操作一致）
            const searchInput = page.document.getElementById('discard-search-input');
            searchInput.value = '已放弃';
            page.document.getElementById('btn-discard-search').click();
            assert.ok(
                await waitFor(() => lastDiscardRequest(server).search.q === '已放弃'),
                '关键词条件未生效'
            );
            await waitFor(() => server.inflight === 0);

            const listRequest = lastDiscardRequest(server);

            // 本页全选 → 扩展到全部匹配
            const selectAll = page.document.getElementById('discard-select-all');
            selectAll.checked = true;
            fireChange(page, selectAll);
            assert.ok(
                await waitFor(() => !page.document.getElementById('btn-discard-select-all-matched').hidden),
                '本页全选 + 总数超一页 + 有条件时应出现「选择全部」'
            );
            page.document.getElementById('btn-discard-select-all-matched').click();
            assert.equal(page.window.eval('discardSelectionMode'), 'all');

            const target = page.document.getElementById('discard-bulk-target');
            window.confirm = () => false;
            target.value = 'pending';
            fireChange(page, target);
            assert.ok(
                await waitFor(() => server.requests('bulk-restore').length === 1),
                '预览请求未发出'
            );
            const preview = server.requests('bulk-restore')[0];
            assert.equal(preview.body.dry_run, true);
            // body 条件与列表查询参数一致（同一份状态生成），关键词也不例外
            assert.equal(preview.body.region, listRequest.search.region);
            assert.equal(preview.body.sentiment, listRequest.search.sentiment);
            // body 中分数是 Number、查询参数是字符串，按数值比较
            assert.equal(preview.body.min_score, Number(listRequest.search.min_score));
            assert.equal(preview.body.q, listRequest.search.q);
            assert.equal(preview.body.q, '已放弃');
            await sleep(300);
            assert.equal(server.requests('bulk-restore').length, 1, 'confirm 取消后不得发执行请求');

            window.confirm = () => true;
            target.value = 'pending';
            fireChange(page, target);
            // 每次点击都会先发 dry_run 预览：预览×2 + 执行×1
            assert.ok(
                await waitFor(() => server.requests('bulk-restore').length === 3),
                '确认后未发执行请求'
            );
            const execute = server.requests('bulk-restore')[2];
            assert.equal(execute.body.dry_run, false);
            assert.equal(execute.body.region, listRequest.search.region);
            assert.equal(execute.body.min_score, Number(listRequest.search.min_score));
            assert.equal(execute.body.q, '已放弃');

            assert.ok(
                await waitFor(() => page.toastText().includes(`已恢复 ${DISCARD_PAGE_SIZE + 1} 条到待处理`)),
                page.toastText()
            );
        });
    });
}

// J13：批次生效时选「今天」→ 带 decided_since、不带批次；之前设的分类保留
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}批次与时间预设互斥：选今天清批次但保留分类`, async () => {
        await withPage(mode, { discardedItems: makeDiscardedItems(3) }, async (page) => {
            const { server } = page;
            await openDiscardTab(page);
            await waitForBatchOption(page);

            // 先选分类（要验证它在选「今天」后保留）
            selectBucket(page, 'internal_negative');
            assert.ok(await waitFor(() => lastDiscardRequest(server).search.region === 'internal'));

            // 再选批次（会清掉分类——这是批次自己的规则）
            selectBatch(page, BATCH_DECIDED_AT);
            assert.ok(
                await waitFor(() => lastDiscardRequest(server).search.batch_decided_at === BATCH_DECIDED_AT),
                '批次条件未生效'
            );

            // 批次生效时改分类：允许叠加，批次保持
            selectBucket(page, 'internal_negative');
            assert.ok(
                await waitFor(() => {
                    const search = lastDiscardRequest(server).search;
                    return search.region === 'internal'
                        && search.batch_decided_at === BATCH_DECIDED_AT;
                }),
                '批次生效时修改分类应叠加且保持批次'
            );

            // 选「今天」：带 decided_since、不带批次，「放弃批次」显示「全部」，
            // 且之前设置的分类保留
            const since = page.document.getElementById('discard-since-select');
            since.value = 'today';
            fireChange(page, since);

            const sinceExpected = new Intl.DateTimeFormat('en-CA', {
                timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
            }).format(new Date());
            assert.ok(
                await waitFor(() => {
                    const search = lastDiscardRequest(server).search;
                    return search.decided_since === sinceExpected
                        && search.batch_decided_at === undefined;
                }),
                `选今天后应带 decided_since 且不带批次：${JSON.stringify(lastDiscardRequest(server).search)}`
            );
            assert.equal(
                lastDiscardRequest(server).search.region,
                'internal',
                '选今天不得清掉之前设置的分类'
            );
            assert.equal(
                page.document.getElementById('discard-batch-select').value,
                '',
                '「放弃批次」下拉应显示为「全部」'
            );
        });
    });
}

// J14：日期格式——今年批次 MM-DD HH:mm，往年批次 YYYY-MM-DD HH:mm（下拉+行内）
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}跨年批次：下拉选项与行内时间按年决定格式`, async () => {
        const items = [
            ...makeBatchItems('c', 2, BATCH_DECIDED_AT),
            ...makeBatchItems('o', 2, LAST_YEAR_BATCH_DECIDED_AT),
        ];
        await withPage(mode, { discardedItems: items }, async (page) => {
            const { server } = page;
            await openDiscardTab(page);
            await waitForBatchOption(page);

            // 上海时区显示：今年批次 06:32Z → 14:32；往年批次 01:05Z → 09:05
            const thisYearOption = page.document.querySelector(
                `#discard-batch-select option[value="${BATCH_DECIDED_AT}"]`
            );
            const lastYearOption = page.document.querySelector(
                `#discard-batch-select option[value="${LAST_YEAR_BATCH_DECIDED_AT}"]`
            );
            assert.ok(thisYearOption && lastYearOption, '两个批次的选项都应存在');
            assert.equal(thisYearOption.textContent, '10-05 14:32 · 2 条',
                '今年批次选项不得带年份');
            assert.equal(lastYearOption.textContent, '2025-11-03 09:05 · 2 条',
                '往年批次选项必须带年份');

            // 行内时间：两个批次的行格式一致
            const thisYearRowTime = page.document.querySelector(
                '#discard-list .article-card[data-id="c00"] .discard-item-time'
            );
            const lastYearRowTime = page.document.querySelector(
                '#discard-list .article-card[data-id="o00"] .discard-item-time'
            );
            assert.ok(thisYearRowTime && lastYearRowTime, '两批的行都应渲染');
            assert.equal(thisYearRowTime.textContent, '10-05 14:32',
                '今年批次的行内时间不得带年份');
            assert.equal(lastYearRowTime.textContent, '2025-11-03 09:05',
                '往年批次的行内时间必须带年份');
        });
    });
}

// J15：批次列表为空时，「放弃批次」下拉存在且可用，只有「全部」一项
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}无批次时：放弃批次下拉保持可见且可用`, async () => {
        const items = makeDiscardedItems(3).map((item, index) => ({
            ...item,
            // 各不相同 → 不成批次
            decided_at: `2026-10-0${index + 1}T06:00:00.000000Z`,
        }));
        await withPage(mode, { discardedItems: items }, async (page) => {
            const { server } = page;
            await openDiscardTab(page);
            await waitFor(() => server.inflight === 0);

            const batchSelect = page.document.getElementById('discard-batch-select');
            assert.ok(batchSelect, '「放弃批次」下拉应存在');
            assert.equal(batchSelect.hidden, false, '无批次时不得隐藏');
            assert.equal(batchSelect.disabled, false, '无批次时不得禁用');
            assert.equal(batchSelect.value, '');
            assert.deepEqual(
                [...batchSelect.querySelectorAll('option')].map((option) => option.value),
                [''],
                '无批次时只有「放弃批次：全部」一项'
            );
        });
    });
}

// J16：条件行分组——控件 DOM 顺序为时间、批次、分类、最低分、最高分；
// 「或」位于时间与批次之间；范围组与收窄组分属两个容器
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}条件行分组：顺序、「或」位置、两组容器`, async () => {
        await withPage(mode, { discardedItems: makeDiscardedItems(2) }, async (page) => {
            await openDiscardTab(page);
            const row = page.document.getElementById('discard-refine-row');
            assert.ok(row, '条件行应存在');

            // 控件 DOM 顺序（不含「或」等非控件）
            const controlIds = [...row.querySelectorAll('select, input')]
                .map((el) => el.id);
            assert.deepEqual(
                controlIds,
                [
                    'discard-since-select',
                    'discard-batch-select',
                    'discard-bucket-select',
                    'discard-min-score',
                    'discard-max-score',
                ],
                `控件顺序不符：${controlIds.join(', ')}`
            );

            // 两组分属两个容器
            const scope = row.querySelector('.discard-filter-scope');
            const narrow = row.querySelector('.discard-filter-narrow');
            assert.ok(scope, '范围组容器应存在');
            assert.ok(narrow, '收窄组容器应存在');
            assert.equal(scope.parentElement, row);
            assert.equal(narrow.parentElement, row);

            // 「或」位于时间与批次之间，且都在范围组内
            const scopeSequence = [...scope.children].map(
                (el) => el.id || el.textContent.trim()
            );
            assert.deepEqual(
                scopeSequence,
                ['discard-since-select', '或', 'discard-batch-select'],
                `范围组内顺序不符：${scopeSequence.join(', ')}`
            );

            // 收窄组只含分类与分数
            const narrowIds = [...narrow.querySelectorAll('select, input')]
                .map((el) => el.id);
            assert.deepEqual(
                narrowIds,
                ['discard-bucket-select', 'discard-min-score', 'discard-max-score'],
                `收窄组控件不符：${narrowIds.join(', ')}`
            );
        });
    });
}

// J17：范围切换是 button 元素；本页全选文案「已选本页」；
// 进入全部模式批量栏带强调状态 class，退出后移除
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}范围切换按钮与全部模式状态提示`, async () => {
        const items = makeDiscardedItems(DISCARD_PAGE_SIZE + 1);
        await withPage(mode, { discardedItems: items }, async (page) => {
            const { server, window } = page;
            await openDiscardTab(page);
            await waitForBatchOption(page);

            const bulkBar = page.document.getElementById('discard-bulk-bar');
            const matchedBtn = page.document.getElementById('btn-discard-select-all-matched');
            const exitBtn = page.document.getElementById('btn-discard-exit-all');

            // 两个范围切换入口都是 button 元素
            assert.equal(matchedBtn.tagName, 'BUTTON');
            assert.equal(exitBtn.tagName, 'BUTTON');

            // 批次条件 → 本页全选：文案为「已选本页 N 条」，出现「选择全部」
            selectBatch(page, BATCH_DECIDED_AT);
            assert.ok(
                await waitFor(() => lastDiscardRequest(server).search.batch_decided_at === BATCH_DECIDED_AT),
                '批次条件未生效'
            );
            await waitFor(() => server.inflight === 0);
            const selectAll = page.document.getElementById('discard-select-all');
            selectAll.checked = true;
            fireChange(page, selectAll);
            assert.ok(
                await waitFor(
                    () => page.document.getElementById('discard-select-all-label').textContent.includes('已选本页')
                ),
                `本页全选文案应含「已选本页」：${page.document.getElementById('discard-select-all-label').textContent}`
            );
            assert.ok(
                await waitFor(() => !matchedBtn.hidden),
                '本页全选 + 总数超一页 + 有条件时应出现「选择全部」'
            );

            // 进入全部模式：批量栏带强调状态 class
            matchedBtn.click();
            assert.equal(page.window.eval('discardSelectionMode'), 'all');
            assert.ok(
                bulkBar.classList.contains('is-all-mode'),
                '全部模式应给批量栏加强调状态 class'
            );

            // 退出全部模式：class 移除，文案回「全选本页」
            assert.ok(!exitBtn.hidden, '全部模式下「取消选择」应可见');
            exitBtn.click();
            assert.equal(page.window.eval('discardSelectionMode'), 'page');
            assert.ok(
                !bulkBar.classList.contains('is-all-mode'),
                '退出全部模式后应移除强调状态 class'
            );
            assert.ok(
                await waitFor(
                    () => page.document.getElementById('discard-select-all-label').textContent === '全选本页'
                ),
                '退出后文案应恢复「全选本页」'
            );
        });
    });
}

// 条件行常驻（无折叠开关）：不依赖任何展开动作即可操作条件
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}条件行常驻：无折叠开关，控件直接可用`, async () => {
        const items = makeDiscardedItems(2);
        await withPage(mode, { discardedItems: items }, async (page) => {
            const { server } = page;
            await openDiscardTab(page);

            assert.equal(
                page.document.getElementById('discard-refine-toggle'),
                null,
                '「筛选」折叠开关应已删除'
            );
            const row = page.document.getElementById('discard-refine-row');
            assert.ok(row, '条件行应存在');
            // 夹具不加载样式表，常驻显示由 CSS 评审保证；这里断言结构上
            // 不再依赖折叠开关（无 body 状态类控制显示）
            assert.equal(
                page.document.body.className.includes('discard-refine-row-open'),
                false
            );

            // 条件控件无需展开即可直接设置并生效
            selectBucket(page, 'internal_negative');
            assert.ok(
                await waitFor(() => lastDiscardRequest(server).search.region === 'internal'),
                '常驻条件行应直接生效'
            );
        });
    });
}

// A4：loadDiscardData 请求序号——被取代的旧响应不得渲染列表
test('放弃列表旧响应不渲染：只渲染最新一次请求的响应', async () => {
    await withPage('admin', { discardedItems: makeDiscardedItems(3) }, async (page) => {
        const { server } = page;
        await openDiscardTab(page);

        // 扣住旧条件的列表请求，让新请求先完成
        server.hold('discard-list', 1);
        selectBucket(page, 'internal_negative');
        assert.ok(
            await waitFor(() => server.heldCount('discard-list') === 1),
            '旧条件的列表请求未被扣住'
        );

        // 数据清空后再触发新请求：新响应应把列表渲染为空
        server.discardedItems = [];
        selectBucket(page, 'external_positive');
        assert.ok(
            await waitFor(() => {
                const requests = discardRequests(server);
                return requests.length >= 3 && requests[requests.length - 1].done;
            }),
            '新条件的列表响应未返回'
        );
        assert.equal(
            page.document.querySelectorAll('#discard-list .article-card').length,
            0,
            '清空后的新响应应把列表渲染为空'
        );

        // 放开旧请求：其响应（3 条的旧快照）不得覆盖列表
        server.release('discard-list');
        await sleep(100);
        assert.equal(
            page.document.querySelectorAll('#discard-list .article-card').length,
            0,
            '被取代的旧响应不得渲染列表'
        );
        assert.ok(
            await waitFor(() => server.inflight === 0),
            '旧请求应已结束'
        );
    });
});

test('结束后不应有未处理的脚本异常', async () => {
    assert.deepEqual(unhandledRejections, []);
});
