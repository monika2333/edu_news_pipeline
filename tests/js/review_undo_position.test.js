// 审阅页「撤回后回到原位」的 jsdom 行为测试。
// 由 tests/test_review_undo_position_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
//
// 背景：/decide 把条目恢复到采纳/备选时，后端会把它排到桶末尾（管理员端 rank 取
// 最大值 + 1，值班端 rank 置空 NULLS LAST）。夹具的假服务按同样口径模拟。
//
// 覆盖的行为约定（改动这些行为时必须同步更新本文件）：
// 1. 审阅卡片 🗑️ 放弃 / 状态下拉移走后点撤回，条目回到移动前的位置，并经 /order 落库；
// 2. 原来排第一的条目撤回后仍排第一（没有前邻居时插到最前）；
// 3. 批量移动（勾选 + 「移动到」）撤回后，各条回到各自原位；
// 4. 原来就在末尾的条目撤回后不再多发 /order；
// 5. /order 失败只降级为「条目在末尾」，撤回本身仍算成功并给出提示；
// 6. 查重弹窗内放弃后撤回，审阅列表里的条目同样回到原位；
// 7. 管理员 /admin/review 与值班 /duty 两端行为一致。
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { bootPage, waitFor, unhandledRejections } = require('./manual_filter_harness.js');

const MODES = ['admin', 'duty'];
const MODE_LABELS = { duty: '值班', admin: '管理员' };

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitForCondition(predicate, message) {
    const ok = await waitFor(predicate);
    assert.ok(ok, message || '等待条件超时');
}

function reviewItem(id) {
    return {
        article_id: id,
        title: `审阅标题${id}`,
        summary: `摘要${id}`,
        llm_summary: `LLM摘要${id}`,
        llm_source_display: '测试来源',
        source: 'toutiao',
        url: `https://example.com/${id}`,
        report_type: 'zongbao',
        status: 'selected',
        version: 1,
        group_key: 'internal_positive',
        is_beijing_related: true,
        sentiment_label: 'positive',
        bonus_keywords: [],
        llm_source_raw: null,
    };
}

// 每个用例独立构造：假服务会原地修改条目（status/version/顺序）
function makeReviewItems(ids = ['r1', 'r2', 'r3', 'r4']) {
    return ids.map(reviewItem);
}

function reviewCardIds(page) {
    return [...page.document.querySelectorAll('#review-list .article-card')]
        .map((card) => card.dataset.id);
}

function reviewCard(page, id) {
    return page.document.querySelector(`#review-list .article-card[data-id="${id}"]`);
}

// 页面代码里有未被 await 的 loadStats()：关闭 window 前先等在途请求排空
async function drainServer(page) {
    await waitForCondition(() => page.server.inflight === 0, '在途请求未排空');
    await sleep(10);
}

async function bootReviewPage(mode, serverOptions = {}) {
    const reviewItems = serverOptions.reviewItems || makeReviewItems();
    // 值班页默认停在「筛选」；给一条候选新闻让 bootPage 等 init 跑完再切页签
    const page = await bootPage(mode, {
        articleCount: mode === 'duty' ? 1 : 0,
        ...serverOptions,
        reviewItems,
    });
    if (mode === 'duty') {
        page.document.querySelector('.tab-btn[data-tab="review"]').click();
    }
    await waitForCondition(
        () => reviewCardIds(page).length === reviewItems.length && page.server.inflight === 0,
        `${MODE_LABELS[mode]}端审阅列表未渲染`
    );
    return page;
}

async function discardCard(page, id, expectedRemaining) {
    reviewCard(page, id).querySelector('.review-discard-btn').click();
    await waitForCondition(
        () => reviewCardIds(page).length === expectedRemaining && page.server.inflight === 0,
        `放弃 ${id} 后列表未刷新`
    );
}

async function clickUndo(page) {
    page.document.querySelector('#toast .btn-action').click();
}

async function waitForUndo(page, expectedIds) {
    await waitForCondition(
        () => reviewCardIds(page).length === expectedIds.length
            && page.server.inflight === 0
            && page.toastText().startsWith('已撤销操作'),
        '撤回后列表未刷新'
    );
}

function lastOrderBody(page) {
    const requests = page.server.requests('review-order');
    return requests.length ? requests[requests.length - 1].body : null;
}

for (const mode of MODES) {
    const label = MODE_LABELS[mode];

    test(`${label}审阅：放弃中间一条后撤回，回到原来的位置`, async () => {
        const page = await bootReviewPage(mode);
        try {
            await discardCard(page, 'r2', 3);
            assert.deepEqual(reviewCardIds(page), ['r1', 'r3', 'r4']);

            await clickUndo(page);
            await waitForUndo(page, ['r1', 'r2', 'r3', 'r4']);

            assert.deepEqual(reviewCardIds(page), ['r1', 'r2', 'r3', 'r4'], '撤回后应回到原位，而不是排到末尾');
            assert.equal(page.toastText().trim(), '已撤销操作');
            const body = lastOrderBody(page);
            assert.ok(body, '撤回后应经 /order 保存原顺序');
            assert.deepEqual(body.selected_order, ['r1', 'r2', 'r3', 'r4']);
            assert.deepEqual(body.backup_order, []);
            // 服务端顺序也已恢复：再次加载仍是原顺序
            assert.deepEqual(
                page.server.reviewItems.filter((item) => item.status === 'selected').map((item) => item.article_id),
                ['r1', 'r2', 'r3', 'r4']
            );
            assert.deepEqual(unhandledRejections, []);
        } finally {
            await drainServer(page);
            page.close();
        }
    });

    test(`${label}审阅：原来排第一的条目撤回后仍排第一`, async () => {
        const page = await bootReviewPage(mode);
        try {
            await discardCard(page, 'r1', 3);
            await clickUndo(page);
            await waitForUndo(page, ['r1', 'r2', 'r3', 'r4']);

            assert.deepEqual(reviewCardIds(page), ['r1', 'r2', 'r3', 'r4']);
        } finally {
            await drainServer(page);
            page.close();
        }
    });

    test(`${label}审阅：原来就在末尾的条目撤回后不再多发 /order`, async () => {
        const page = await bootReviewPage(mode);
        try {
            await discardCard(page, 'r4', 3);
            await clickUndo(page);
            await waitForUndo(page, ['r1', 'r2', 'r3', 'r4']);

            assert.deepEqual(reviewCardIds(page), ['r1', 'r2', 'r3', 'r4']);
            assert.equal(page.server.requests('review-order').length, 0, '顺序没变时不应写 /order');
            assert.equal(page.toastText().trim(), '已撤销操作');
        } finally {
            await drainServer(page);
            page.close();
        }
    });

    test(`${label}审阅：批量放弃两条后撤回，各自回到原位`, async () => {
        const page = await bootReviewPage(mode, { reviewItems: makeReviewItems(['r1', 'r2', 'r3', 'r4', 'r5']) });
        try {
            ['r2', 'r4'].forEach((id) => {
                const checkbox = reviewCard(page, id).querySelector('.review-select');
                checkbox.checked = true;
                page.fireChange(checkbox);
            });
            const bulkSelect = page.document.getElementById('review-bulk-status');
            bulkSelect.value = 'discarded';
            page.fireChange(bulkSelect);
            await waitForCondition(
                () => reviewCardIds(page).length === 3 && page.server.inflight === 0,
                '批量放弃后列表未刷新'
            );

            await clickUndo(page);
            await waitForUndo(page, ['r1', 'r2', 'r3', 'r4', 'r5']);

            assert.deepEqual(reviewCardIds(page), ['r1', 'r2', 'r3', 'r4', 'r5']);
        } finally {
            await drainServer(page);
            page.close();
        }
    });
}

test('管理员审阅：下拉移到备选后撤回，回到采纳桶原位', async () => {
    const page = await bootReviewPage('admin');
    try {
        const select = reviewCard(page, 'r2').querySelector('.status-select');
        select.value = 'zongbao:backup';
        page.fireChange(select);
        await waitForCondition(
            () => reviewCardIds(page).length === 3 && page.server.inflight === 0,
            '移到备选后列表未刷新'
        );

        await clickUndo(page);
        await waitForUndo(page, ['r1', 'r2', 'r3', 'r4']);

        assert.deepEqual(reviewCardIds(page), ['r1', 'r2', 'r3', 'r4']);
        assert.deepEqual(lastOrderBody(page).selected_order, ['r1', 'r2', 'r3', 'r4']);
        assert.equal(lastOrderBody(page).report_type, 'zongbao');
    } finally {
        await drainServer(page);
        page.close();
    }
});

test('管理员审阅：/order 失败时撤回仍成功，条目留在末尾并提示', async () => {
    const page = await bootReviewPage('admin');
    try {
        await discardCard(page, 'r2', 3);
        page.server.fail('review-order');
        await clickUndo(page);
        await waitForUndo(page, ['r1', 'r3', 'r4', 'r2']);

        assert.deepEqual(reviewCardIds(page), ['r1', 'r3', 'r4', 'r2'], '回原位失败时条目留在末尾');
        assert.equal(page.toastText().trim(), '已撤销操作，但未能恢复原来的位置');
        assert.equal(page.server.reviewItems.find((item) => item.article_id === 'r2').status, 'selected');
    } finally {
        await drainServer(page);
        page.close();
    }
});

for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}查重弹窗：放弃后撤回，审阅列表里的条目回到原位`, async () => {
        const page = await bootReviewPage(mode, { duplicateGroups: [['r1', 'r2', 'r3']] });
        try {
            page.document.getElementById('btn-check-duplicates').click();
            const modalItem = (id) => page.document.querySelector(
                `#duplicate-review-results .duplicate-review-item[data-id="${id}"]`
            );
            await waitForCondition(() => modalItem('r2'), '查重结果弹窗未渲染');

            modalItem('r2').querySelector('.duplicate-review-discard').click();
            await waitForCondition(
                () => modalItem('r2').hidden && page.server.inflight === 0,
                '弹窗内放弃后条目未隐藏'
            );
            assert.deepEqual(reviewCardIds(page), ['r1', 'r3', 'r4']);

            await clickUndo(page);
            await waitForUndo(page, ['r1', 'r2', 'r3', 'r4']);

            assert.deepEqual(reviewCardIds(page), ['r1', 'r2', 'r3', 'r4']);
            assert.deepEqual(lastOrderBody(page).selected_order, ['r1', 'r2', 'r3', 'r4']);
        } finally {
            await drainServer(page);
            page.close();
        }
    });
}
