// 查重结果弹窗（#duplicate-review-modal）的滚动位置行为测试。
// 由 tests/test_duplicate_review_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
//
// 覆盖的行为约定（改动这些行为时必须同步更新本文件）：
// 1. 组内放弃条目（🗑️ 按钮 / 下拉选「放弃」/ 批量放弃）后保留当前滚动位置，
//    与筛选、审阅页决定后「停留在改动位置」一致；
// 2. 当前组被清空后自动切到下一组，并从顶部开始显示（切组不保位）；
// 3. 撤销恢复到当前组时保留滚动位置；从已被切走的空组恢复时切回该组并从顶部显示；
// 4. 上一组 / 下一组翻页仍回到顶部（分页行为不能因保位改动而退化）。
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { bootPage, waitFor, unhandledRejections } = require('./manual_filter_harness');

const MODES = ['admin', 'duty'];
const GROUP_TWO_FIRST_INDEX = '1';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function reviewItem(id, reportType, status) {
    return {
        article_id: id,
        title: `标题${id}`,
        summary: `摘要${id}`,
        llm_summary: `LLM摘要${id}`,
        llm_source_display: '测试来源',
        source: 'toutiao',
        url: `https://example.com/${id}`,
        report_type: reportType,
        status,
        version: 1,
        group_key: 'internal_positive',
        is_beijing_related: true,
        bonus_keywords: [],
        llm_source_raw: null,
    };
}

// 每个用例独立构造：假服务会原地修改条目（status/version），
// 共享同一份对象会把上一个用例的变更带进下一个
function makeReviewItems() {
    return {
        selected: ['a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7', 'a8']
            .map((id) => reviewItem(id, 'zongbao', 'selected')),
        backup: [],
    };
}

function makeDuplicateGroups() {
    return [
        ['a1', 'a2', 'a3', 'a4', 'a5', 'a6'],
        ['a7', 'a8'],
    ];
}

// 页面代码里有未被 await 的 loadStats()：关闭 window 前先等在途请求排空，
// 否则关闭后到达的响应会让 showToast 撞上已销毁的 document，留下未处理 rejection
async function drainServer(page) {
    assert.ok(await waitFor(() => page.server.inflight === 0), '在途请求未排空');
    await sleep(10);
}

async function bootDuplicateReview(mode, serverOptions = {}) {
    const page = await bootPage(mode, {
        reviewItems: makeReviewItems(),
        duplicateGroups: makeDuplicateGroups(),
        // 值班页默认停在「筛选」；给一条候选新闻让 bootPage 等 init 跑完再切页签
        articleCount: mode === 'duty' ? 1 : 0,
        ...serverOptions,
    });
    if (mode === 'duty') {
        page.document.querySelector('.tab-btn[data-tab="review"]').click();
    }
    const ready = await waitFor(() => page.server.requests('review-list').length >= 2
        && page.document.querySelectorAll('#review-list .article-card').length > 0
        && page.server.inflight === 0);
    assert.ok(ready, `${mode}: 审阅列表未就绪`);
    page.document.getElementById('btn-check-duplicates').click();
    const modalReady = await waitFor(() => (
        page.document.getElementById('duplicate-review-modal').classList.contains('active')
        && page.document.querySelectorAll('#duplicate-review-results .duplicate-review-item').length > 0
    ));
    assert.ok(modalReady, `${mode}: 查重结果弹窗未渲染`);
    return page;
}

function resultBody(page) {
    return page.document.getElementById('duplicate-review-results');
}

function activeGroup(page) {
    return page.document.querySelector(
        '#duplicate-review-results .duplicate-review-group:not([hidden])'
    );
}

function modalItem(page, id) {
    return page.document.querySelector(
        `#duplicate-review-results .duplicate-review-item[data-id="${id}"]`
    );
}

function setScrollTop(page, value) {
    const body = resultBody(page);
    body.scrollTop = value;
    assert.equal(body.scrollTop, value, 'jsdom 未记住 scrollTop，断言将失去意义');
}

async function waitForDecision(page, ids) {
    const settled = await waitFor(() => ids.every((id) => modalItem(page, id).hidden)
        && page.server.inflight === 0);
    assert.ok(settled, `放弃后条目未隐藏或请求未结束：${ids.join(',')}`);
}

async function discardViaButton(page, id) {
    modalItem(page, id).querySelector('.duplicate-review-discard').click();
    await waitForDecision(page, [id]);
}

for (const mode of MODES) {
    test(`查重弹窗（${mode}）：组内点「放弃新闻」后保留滚动位置`, async () => {
        const page = await bootDuplicateReview(mode);
        try {
            setScrollTop(page, 240);
            await discardViaButton(page, 'a1');

            assert.equal(resultBody(page).scrollTop, 240, '放弃后滚动位置不应回到顶部');
            // 当前组还在，只是条数与页码计数更新
            const heading = activeGroup(page).querySelector('.duplicate-review-group-heading span');
            assert.equal(heading.textContent, '5 条新闻');
            assert.match(
                page.document.getElementById('duplicate-review-page-indicator').textContent,
                /第 1 \/ 2 组/
            );
        } finally {
            await drainServer(page);
            page.close();
        }
    });

    test(`查重弹窗（${mode}）：下拉选择「放弃」后保留滚动位置`, async () => {
        const page = await bootDuplicateReview(mode);
        try {
            setScrollTop(page, 180);
            const select = modalItem(page, 'a2').querySelector('.duplicate-review-status');
            select.value = 'discarded';
            page.fireChange(select);
            await waitForDecision(page, ['a2']);

            assert.equal(resultBody(page).scrollTop, 180, '下拉放弃后滚动位置不应回到顶部');
        } finally {
            await drainServer(page);
            page.close();
        }
    });

    test(`查重弹窗（${mode}）：批量放弃（组内仍有条目）后保留滚动位置`, async () => {
        const page = await bootDuplicateReview(mode);
        try {
            setScrollTop(page, 300);
            ['a3', 'a4'].forEach((id) => {
                const checkbox = modalItem(page, id).querySelector('.duplicate-review-select');
                checkbox.checked = true;
                page.fireChange(checkbox);
            });
            const bulkSelect = page.document.getElementById('duplicate-review-bulk-status');
            bulkSelect.value = 'discarded';
            page.fireChange(bulkSelect);
            await waitForDecision(page, ['a3', 'a4']);

            assert.equal(resultBody(page).scrollTop, 300, '批量放弃后滚动位置不应回到顶部');
            assert.equal(
                activeGroup(page).querySelector('.duplicate-review-group-heading span').textContent,
                '4 条新闻'
            );
        } finally {
            await drainServer(page);
            page.close();
        }
    });

    test(`查重弹窗（${mode}）：撤销放弃后保留滚动位置`, async () => {
        const page = await bootDuplicateReview(mode);
        try {
            setScrollTop(page, 260);
            await discardViaButton(page, 'a5');
            assert.equal(modalItem(page, 'a5').hidden, true);

            page.document.querySelector('#toast .btn-action').click();
            const restored = await waitFor(() => !modalItem(page, 'a5').hidden
                && page.server.inflight === 0);
            assert.ok(restored, '撤销后条目未恢复');

            assert.equal(resultBody(page).scrollTop, 260, '撤销后滚动位置不应变化');
        } finally {
            await drainServer(page);
            page.close();
        }
    });

    test(`查重弹窗（${mode}）：整组清空后自动切下一组并回到顶部，撤销后切回原组顶部`, async () => {
        const page = await bootDuplicateReview(mode);
        try {
            setScrollTop(page, 320);
            const selectAll = page.document.getElementById('duplicate-review-select-all');
            selectAll.checked = true;
            page.fireChange(selectAll);
            const bulkSelect = page.document.getElementById('duplicate-review-bulk-status');
            bulkSelect.value = 'discarded';
            page.fireChange(bulkSelect);

            const advanced = await waitFor(() => {
                const group = activeGroup(page);
                return group
                    && group.dataset.groupIndex === GROUP_TWO_FIRST_INDEX
                    && page.server.inflight === 0;
            });
            assert.ok(advanced, '整组清空后未切到下一组');
            assert.equal(resultBody(page).scrollTop, 0, '切组后应从顶部开始显示');
            assert.match(
                page.document.getElementById('duplicate-review-page-indicator').textContent,
                /第 1 \/ 1 组/
            );

            // 撤销整批放弃：恢复的组已不在当前显示位，切回该组并从顶部开始
            page.document.querySelector('#toast .btn-action').click();
            const restored = await waitFor(() => {
                const group = activeGroup(page);
                return group
                    && group.dataset.groupIndex === '0'
                    && !modalItem(page, 'a1').hidden
                    && page.server.inflight === 0;
            });
            assert.ok(restored, '撤销后未切回原组');
            assert.equal(resultBody(page).scrollTop, 0, '切回原组后应从顶部开始显示');
        } finally {
            await drainServer(page);
            page.close();
        }
    });

    test(`查重弹窗（${mode}）：上一组/下一组翻页仍回到顶部`, async () => {
        const page = await bootDuplicateReview(mode);
        try {
            setScrollTop(page, 200);
            page.document.getElementById('btn-duplicate-next-group').click();
            assert.equal(
                activeGroup(page).dataset.groupIndex,
                GROUP_TWO_FIRST_INDEX,
                '未切到下一组'
            );
            assert.equal(resultBody(page).scrollTop, 0, '翻到下一组应回到顶部');

            setScrollTop(page, 200);
            page.document.getElementById('btn-duplicate-prev-group').click();
            assert.equal(activeGroup(page).dataset.groupIndex, '0', '未切回上一组');
            assert.equal(resultBody(page).scrollTop, 0, '翻回上一组应回到顶部');
        } finally {
            await drainServer(page);
            page.close();
        }
    });
}

test('以上场景没有产生未处理的 Promise rejection', () => {
    assert.deepEqual(unhandledRejections, []);
});
