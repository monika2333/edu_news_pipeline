// 审阅页「自动排序」（review_tab_sort.js）的 jsdom 行为测试。
// 页面由 tests/test_review_sort_js_behavior.py 经真实路由渲染并注入固定词表
// （市教委/中小学/高校），本文件验证分类、组内重排与顺序回存的端到端行为。
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { bootPage, waitFor, unhandledRejections } = require('./manual_filter_harness.js');

async function waitForCondition(predicate) {
    const ok = await waitFor(predicate);
    assert.ok(ok, '等待条件超时');
}

function reviewItem(id, title) {
    return {
        article_id: id,
        title,
        summary: '摘要内容',
        llm_summary: 'LLM摘要',
        llm_source_display: '测试来源',
        source: 'toutiao',
        url: `https://example.com/${id}`,
        report_type: null,
        status: 'selected',
        manual_status: null,
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

async function bootReviewPage(reviewItems) {
    const page = await bootPage('admin', { reviewItems });
    await waitForCondition(() => reviewCardIds(page).length === reviewItems.length);
    return page;
}

async function autoSort(page) {
    page.document.getElementById('btn-toggle-sort').click();
    await waitForCondition(() => page.document.querySelector('.sort-group-body .article-card'));
    page.document.getElementById('btn-auto-reorder').click();
    await waitForCondition(() => page.toastText().includes('自动排序完成'));
}

test('R1：自动排序按固定优先级组内重排并回存顺序', async () => {
    const page = await bootReviewPage([
        reviewItem('r4', '一则城市民生资讯'),
        reviewItem('r3', '大学科研成果转化落地'),
        reviewItem('r2', '小学运动会顺利举行'),
        reviewItem('r1', '市教委发布寒假安排'),
    ]);
    try {
        await autoSort(page);
        assert.deepEqual(reviewCardIds(page), ['r1', 'r2', 'r3', 'r4']);
        const orderCall = page.server.log.find((entry) => entry.path.endsWith('/order'));
        assert.deepEqual(orderCall.body.selected_order, ['r1', 'r2', 'r3', 'r4']);
        assert.match(page.toastText(), /市教委 1，中小学 1，高校 1，其他 1/);
    } finally { page.close(); }
});

test('R2：关键词匹配大小写不敏感且覆盖标题文本', async () => {
    const page = await bootReviewPage([
        reviewItem('r2', '本市K12课后服务扩面'),
        reviewItem('r1', '一条与教育无关的消息'),
    ]);
    try {
        await autoSort(page);
        assert.deepEqual(reviewCardIds(page), ['r2', 'r1']);
        assert.match(page.toastText(), /中小学 1，高校 0，其他 1/);
    } finally { page.close(); }
});

test('R3：未打开排序模式时自动排序仅提示不发请求', async () => {
    const page = await bootReviewPage([reviewItem('r1', '市教委发布寒假安排')]);
    try {
        page.document.getElementById('btn-auto-reorder').click();
        await new Promise((resolve) => setTimeout(resolve, 20));
        assert.equal(page.server.log.some((entry) => entry.path.endsWith('/order')), false);
        assert.match(page.toastText(), /请先打开排序模式/);
    } finally { page.close(); }
});

test('R0：页面嵌入的服务端词表可被解析（模板集成点）', async () => {
    const page = await bootReviewPage([]);
    try {
        // JSON 序列化后解析回 node realm，避免跨窗口原型的 deepEqual 误报
        const rules = JSON.parse(page.window.eval(
            'JSON.stringify(JSON.parse(document.getElementById("review-sort-rules").textContent))',
        ));
        assert.deepEqual(rules, {
            '市教委': ['市教委', '教工委'],
            '中小学': ['中小学', '小学', 'k12'],
            '高校': ['高校', '大学'],
        });
        assert.equal(page.window.eval('typeof classifyCategory'), 'function');
        assert.equal(
            page.window.eval('classifyCategory("市教委组织会议")'),
            '市教委',
        );
        // 等页面内的挂起定时器走完再关窗，避免关窗后的异步回调被记为未处理拒绝
        await new Promise((resolve) => setTimeout(resolve, 50));
    } finally { page.close(); }
});

test('R4：一条同时命中两个类别的条目归入优先级更高的类别', async () => {
    const page = await bootReviewPage([
        reviewItem('r2', '一条与教育无关的消息'),
        reviewItem('r1', '市教委赴大学调研就业工作'),
    ]);
    try {
        await autoSort(page);
        assert.deepEqual(reviewCardIds(page), ['r1', 'r2']);
        assert.match(page.toastText(), /市教委 1，中小学 0，高校 0，其他 1/);
        // 等页面内的挂起定时器走完再关窗，避免关窗后的异步回调被记为未处理拒绝
        await new Promise((resolve) => setTimeout(resolve, 50));
    } finally { page.close(); }
});

test('R9：整组测试无未处理的 Promise 拒绝', async () => {
    assert.deepEqual(unhandledRejections, []);
});
