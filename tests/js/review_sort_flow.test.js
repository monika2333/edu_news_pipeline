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

async function bootReviewPage(reviewItems, mode = 'admin') {
    // 值班页默认停在「筛选」，且要先让 bootPage 等到 init 跑完（articleCount
    // 给 1 就是那个信号），否则切页签的点击落在 setupTabs() 之前没人响应。
    const page = mode === 'duty'
        ? await bootPage('duty', { articleCount: 1, reviewItems })
        : await bootPage('admin', { reviewItems });
    if (mode === 'duty') {
        page.document.querySelector('.tab-btn[data-tab="review"]').click();
    }
    await waitForCondition(() => reviewCardIds(page).length === reviewItems.length);
    return page;
}

// 替换 Sortable 构造函数，记录 initReviewSortable 传进去的选项：
// 手势参数（手柄、长按门槛）只能在真机/浏览器里感知，jsdom 里靠这个断言。
function captureSortableOptions(page) {
    page.window.eval(`
        window.__sortableCalls = [];
        window.Sortable = function (element, options) {
            window.__sortableCalls.push(options);
            return { destroy() {} };
        };
    `);
}

function setViewportWidth(page, width) {
    page.window.eval(
        `Object.defineProperty(window, 'innerWidth', { value: ${width}, configurable: true });`
    );
}

async function toggleSortAndReadOptions(page) {
    page.document.getElementById('btn-toggle-sort').click();
    await waitForCondition(() => page.window.__sortableCalls.length > 0);
    return page.window.__sortableCalls[0];
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

// 手机端排序模式的手势契约：只有 ⋮ 手柄能起拖，手指落在卡片正文上不进拖拽态
// （过去整卡是把手，列表因此滚不动）。两个页面共用同一份模板与模块，两端都测。
for (const mode of ['duty', 'admin']) {
    test(`R5（${mode}）：手机端只有 ⋮ 手柄可起拖，并带长按门槛`, async () => {
        const page = await bootReviewPage(
            [reviewItem('r1', '市教委发布寒假安排'), reviewItem('r2', '本市K12课后服务扩面')],
            mode,
        );
        try {
            setViewportWidth(page, 390);
            captureSortableOptions(page);
            const options = await toggleSortAndReadOptions(page);

            assert.equal(options.handle, '.drag-handle');
            assert.equal(options.delay, 120);
            assert.equal(options.delayOnTouchOnly, true);
            assert.ok(options.touchStartThreshold >= 4);
            // 跨组拖拽语义不变：仍可把条目拖进另一个分组（会改写该条分类并落库）
            assert.equal(options.group.pull, true);
            assert.equal(options.group.put, true);
        } finally { page.close(); }
    });
}

test('R6：桌面端排序手势不受手机端参数影响', async () => {
    const page = await bootReviewPage([reviewItem('r1', '市教委发布寒假安排')]);
    try {
        setViewportWidth(page, 1280);
        captureSortableOptions(page);
        const options = await toggleSortAndReadOptions(page);

        assert.equal(options.handle, '.drag-handle');
        assert.equal(options.delay, 0);
        assert.equal(options.delayOnTouchOnly, false);
        assert.equal(options.touchStartThreshold, 1);
    } finally { page.close(); }
});

test('R9：整组测试无未处理的 Promise 拒绝', async () => {
    assert.deepEqual(unhandledRejections, []);
});
