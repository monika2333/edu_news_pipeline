// 汇总审阅页「清理旧新闻」弹窗的浏览器端行为测试。
// 由 tests/test_review_cleanup_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
//
// 覆盖的行为约定（改动这些行为时必须同步更新本文件）：
// 1. 选日期后按四个桶分别 dry_run 预览条数并带上 created_before；
// 2. 点「确认清理」必须先经过 window.confirm 二次确认，取消时不得发出清理请求，
//    确认文案包含将清理的条数；
// 3. 只清理勾选的桶；执行后提示浮窗提供撤销，撤销按桶带回各自版本号与报别
//    回到原状态（selected/backup）。
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { bootPage, waitFor, unhandledRejections } = require('./manual_filter_harness');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function reviewItem(id, reportType, status) {
    return {
        article_id: id,
        title: `标题${id}`,
        summary: '摘要内容',
        llm_summary: 'LLM摘要',
        llm_source_display: '测试来源',
        source: 'toutiao',
        url: `https://example.com/${id}`,
        report_type: reportType,
        status,
        version: 1,
        group_key: 'internal_positive',
        bonus_keywords: [],
        llm_source_raw: null,
    };
}

function reviewCardIds(page) {
    return [...page.document.querySelectorAll('#review-list .article-card')]
        .map((card) => card.dataset.id);
}

// 每个用例独立构造：假服务会原地修改条目（status/version），
// 共享同一份对象会把上一个用例的变更带进下一个
function makeReviewItems() {
    return {
        selected: [
            reviewItem('r1', 'zongbao', 'selected'),
            reviewItem('r2', 'zongbao', 'selected'),
            reviewItem('r4', 'wanbao', 'selected'),
        ],
        backup: [
            reviewItem('r3', 'zongbao', 'backup'),
        ],
    };
}

async function bootReviewPage(serverOptions) {
    const page = await bootPage('admin', serverOptions);
    // 两个桶的 /review 请求都返回且在途请求清零即认为页面数据就绪，
    // 不依赖具体卡片数（空桶用例一张卡都不会渲染）
    const booted = await waitFor(() => page.server.requests('review-list').length >= 2
        && page.server.inflight === 0);
    assert.ok(booted, '审阅页数据未加载');
    return page;
}

async function openCleanupWithPreview(page, expectedTotal) {
    page.window.confirm = () => true;
    page.document.getElementById('btn-open-review-cleanup').click();
    const dateInput = page.document.getElementById('review-cleanup-date-input');
    dateInput.value = '2025-06-01';
    page.fireChange(dateInput);
    return waitFor(() => page.document.getElementById('review-cleanup-modal-stats').textContent
        .includes(`将清理 ${expectedTotal} 条`));
}

// 页面代码里有未被 await 的 loadStats()：关闭 window 前先等在途请求排空，
// 否则关闭后到达的响应会让 showToast 撞上已销毁的 document，留下未处理 rejection
async function drainServer(page) {
    await waitFor(() => page.server.inflight === 0);
    await sleep(10);
}

test('清理旧新闻：选日期后按四个桶分别预览并携带日期', async () => {
    const page = await bootReviewPage({ reviewItems: makeReviewItems() });
    try {
        const { document, server } = page;
        page.document.getElementById('btn-open-review-cleanup').click();
        const dateInput = document.getElementById('review-cleanup-date-input');
        dateInput.value = '2025-06-01';
        page.fireChange(dateInput);
        assert.ok(
            await waitFor(() => document.getElementById('review-cleanup-modal-stats').textContent
                .includes('将清理 4 条')),
            document.getElementById('review-cleanup-modal-stats').textContent
        );
        const counts = {};
        document.querySelectorAll('#review-cleanup-bucket-list .cleanup-category-row')
            .forEach((row) => {
                counts[row.dataset.bucket] = row.querySelector('.cleanup-category-count').textContent;
            });
        assert.deepEqual(counts, {
            'zongbao:selected': '2 条',
            'zongbao:backup': '1 条',
            'wanbao:selected': '1 条',
            'wanbao:backup': '0 条',
        });
        const previews = server.requests('cleanup-review-buckets');
        assert.equal(previews.length, 4);
        previews.forEach((entry) => {
            assert.equal(entry.body.created_before, '2025-06-01');
            assert.equal(entry.body.dry_run, true);
        });
    } finally {
        await drainServer(page);
        page.close();
    }
});

test('清理旧新闻：二次确认取消时不发出清理请求，确认文案含条数', async () => {
    const page = await bootReviewPage({ reviewItems: makeReviewItems() });
    try {
        const { document, server } = page;
        const prompts = [];
        page.window.confirm = (message) => {
            prompts.push(message);
            return false;
        };
        // 不经 openCleanupWithPreview：它会把 confirm 固定为 true
        document.getElementById('btn-open-review-cleanup').click();
        const dateInput = document.getElementById('review-cleanup-date-input');
        dateInput.value = '2025-06-01';
        page.fireChange(dateInput);
        assert.ok(
            await waitFor(() => document.getElementById('review-cleanup-modal-stats').textContent
                .includes('将清理 4 条')),
            document.getElementById('review-cleanup-modal-stats').textContent
        );
        document.getElementById('btn-review-cleanup-confirm').click();
        await sleep(20);
        const applies = server.requests('cleanup-review-buckets')
            .filter((entry) => !entry.body.dry_run);
        assert.equal(applies.length, 0, '取消二次确认后不应发出清理请求');
        assert.match(prompts[0] || '', /确定清理这 4 条旧新闻吗/);
    } finally {
        await drainServer(page);
        page.close();
    }
});

test('清理旧新闻：只清理勾选的桶，提示中可按桶撤销回原状态', async () => {
    const page = await bootReviewPage({ reviewItems: makeReviewItems() });
    try {
        const { document, server } = page;
        assert.ok(await openCleanupWithPreview(page, 4), '预览统计未出现');
        // 取消勾选「综报备选」（r3 所在桶）
        const backupRow = document.querySelector(
            '#review-cleanup-bucket-list .cleanup-category-row[data-bucket="zongbao:backup"]'
        );
        backupRow.querySelector('.cleanup-category-check').checked = false;
        backupRow.querySelector('.cleanup-category-check').dispatchEvent(
            new page.window.Event('change', { bubbles: true })
        );
        assert.ok(
            await waitFor(() => document.getElementById('review-cleanup-modal-stats').textContent
                .includes('将清理 3 条')),
            document.getElementById('review-cleanup-modal-stats').textContent
        );
        document.getElementById('btn-review-cleanup-confirm').click();
        assert.ok(
            await waitFor(() => page.toastText().includes('已清理 3 条旧新闻')),
            page.toastText()
        );
        const applies = server.requests('cleanup-review-buckets')
            .filter((entry) => !entry.body.dry_run);
        assert.equal(applies.length, 2, '只应发出两个勾选桶的清理请求');
        const applyBodies = applies.map((entry) => entry.body);
        assert.ok(applyBodies.some((body) => body.report_type === 'zongbao' && body.status === 'selected'));
        assert.ok(applyBodies.some((body) => body.report_type === 'wanbao' && body.status === 'selected'));
        assert.ok(
            !applyBodies.some((body) => body.status === 'backup'),
            '未勾选的备选桶不应被清理'
        );

        assert.ok(document.querySelector('#toast button'), '提示中应有撤销按钮');
        document.querySelector('#toast button').click();
        assert.ok(await waitFor(() => page.toastText().includes('已撤销')), page.toastText());
        const undoes = server.requests('decide').slice(-2);
        const zongbaoUndo = undoes.find((entry) => entry.body.report_type === 'zongbao');
        const wanbaoUndo = undoes.find((entry) => entry.body.report_type === 'wanbao');
        assert.ok(zongbaoUndo && wanbaoUndo, '撤销必须按桶分别回退');
        assert.deepEqual([...zongbaoUndo.body.selected_ids].sort(), ['r1', 'r2']);
        assert.deepEqual(zongbaoUndo.body.backup_ids, []);
        assert.deepEqual([...wanbaoUndo.body.selected_ids], ['r4']);
        assert.equal(Object.keys(zongbaoUndo.body.versions).length, 2, '撤销必须携带版本号');
        assert.equal(Object.keys(wanbaoUndo.body.versions).length, 1);
        // 撤销后综报采纳桶恢复 r1、r2（初始视图只显示该桶）
        assert.ok(
            await waitFor(() => reviewCardIds(page).length === 2),
            reviewCardIds(page).join(',')
        );
    } finally {
        await drainServer(page);
        page.close();
    }
});

test('清理旧新闻：四个桶都为空时确认保持禁用', async () => {
    const page = await bootReviewPage({ reviewItems: { selected: [], backup: [] } });
    try {
        const { document } = page;
        page.document.getElementById('btn-open-review-cleanup').click();
        const dateInput = document.getElementById('review-cleanup-date-input');
        dateInput.value = '2025-06-01';
        page.fireChange(dateInput);
        assert.ok(
            await waitFor(() => document.getElementById('review-cleanup-modal-stats').textContent
                .includes('将清理 0 条')),
            document.getElementById('review-cleanup-modal-stats').textContent
        );
        const confirmBtn = document.getElementById('btn-review-cleanup-confirm');
        assert.ok(confirmBtn.disabled, '空桶时确认按钮应禁用');
    } finally {
        await drainServer(page);
        page.close();
    }
});

test('以上场景没有产生未处理的 Promise rejection', () => {
    assert.deepEqual(unhandledRejections, []);
});
