// 原文抽屉头部「分类徽章 + 该分类下的重要性分」行为测试。
// 由 tests/test_content_drawer_category_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
//
// 覆盖两套抽屉实现（同一内容接口、同一共享辅助 contentDrawerCategoryBadge）：
// - 筛选页（manual_filter/content_drawer.js）：点卡片「原文」经事件委托打开；
// - 汇总/存档页（submission_archive/content_drawer.js）：直接调 loadArchiveDrawerArticle。
//
// 行为约定（改动这些行为时必须同步更新本文件）：
// 1. 徽章渲染在抽屉头部元信息行最前面：分类在前、分数「N 分」紧随，无悬浮提示；
// 2. 元信息行不展示正文字数（对值班无参考价值，2026-10-20 用户拍板删除）；
// 3. 分类口径与检索卡片一致：京内/京外 × 正/负，情感非 negative 一律按正面；
// 4. 分数经 formatScore 四舍五入取整展示；
// 5. 地域或情感任一未就绪（null/undefined/空）：徽章与分数都不渲染，
//    分数不脱离分类单独出现；
// 6. 已归类但分数缺失：只渲染徽章，不渲染分数。
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { bootPage, waitFor, unhandledRejections } = require('./manual_filter_harness');
const {
    bootPage: bootSummaryPage,
    waitFor: waitForSummary,
    unhandledRejections: summaryRejections,
    shiftRow,
} = require('./duty_summary_harness');

const CONTENT_BASE = {
    title: '抽屉分类测试文章',
    source: '测试来源',
    created_at: '2026-10-13T01:00:00Z',
    content_markdown: '第一段正文。\n\n第二段正文。',
    url: 'https://example.com/x',
};

function drawerMeta(page) {
    return page.document.getElementById('content-drawer-meta');
}

async function openFilterDrawer(articleContents, body) {
    const page = await bootPage('admin', { articleCount: 1, articleContents });
    try {
        page.card('a00').querySelector('.content-drawer-trigger').click();
        // 正文段落渲染出来即头部已就绪（同一函数内先渲染头部再渲染正文）
        await waitFor(() => page.document.querySelectorAll('#content-drawer-body p').length > 0);
        await body(page);
    } finally {
        page.close();
    }
}

test('F1 京内正面 + 重要性分：徽章在元信息行最前、分数紧随并带全称悬浮', async () => {
    await openFilterDrawer({
        a00: { ...CONTENT_BASE, is_beijing_related: true, sentiment_label: 'positive', external_importance_score: 58 },
    }, async (page) => {
        const badge = drawerMeta(page).querySelector('.search-category-badge');
        assert.ok(badge, '应渲染分类徽章');
        assert.equal(badge.textContent, '京内正面');
        assert.ok(badge.className.includes('positive'), '正面应带 positive 样式类');
        assert.equal(drawerMeta(page).firstElementChild, badge, '徽章应在元信息行最前面');

        const score = drawerMeta(page).querySelector('.content-drawer-score');
        assert.ok(score, '应渲染分数');
        assert.equal(score.textContent, '58 分');
        assert.equal(score.getAttribute('title'), null, '分数不挂悬浮提示');
        assert.equal(drawerMeta(page).textContent.includes('正文'), false, '不展示正文字数');
        assert.deepEqual(unhandledRejections, []);
    });
});

test('F2 京外负面：negative 样式类，分数四舍五入取整', async () => {
    await openFilterDrawer({
        a00: { ...CONTENT_BASE, is_beijing_related: false, sentiment_label: 'negative', external_importance_score: 61.4 },
    }, async (page) => {
        const badge = drawerMeta(page).querySelector('.search-category-badge');
        assert.equal(badge.textContent, '京外负面');
        assert.ok(badge.className.includes('negative'), '负面应带 negative 样式类');

        const score = drawerMeta(page).querySelector('.content-drawer-score');
        assert.equal(score.textContent, '61 分');
        assert.equal(score.getAttribute('title'), null);
    });
});

test('F3 分类字段未就绪：徽章与分数都不渲染，元信息其余内容正常', async () => {
    await openFilterDrawer({
        a00: { ...CONTENT_BASE },
    }, async (page) => {
        const meta = drawerMeta(page);
        assert.equal(meta.querySelector('.search-category-badge'), null);
        assert.equal(meta.querySelector('.content-drawer-score'), null);
        assert.ok(meta.textContent.includes('来源: 测试来源'), '来源等其余元信息不受影响');
    });
});

test('F4 已归类但分数缺失：只渲染徽章', async () => {
    await openFilterDrawer({
        // 故意不给 external_importance_score 键（undefined 路径，与显式 null 等价处理）
        a00: { ...CONTENT_BASE, is_beijing_related: true, sentiment_label: 'negative' },
    }, async (page) => {
        const meta = drawerMeta(page);
        const badge = meta.querySelector('.search-category-badge');
        assert.equal(badge.textContent, '京内负面');
        assert.equal(meta.querySelector('.content-drawer-score'), null);
    });
});

test('F5 情感非 negative 一律按正面：neutral 样式类', async () => {
    await openFilterDrawer({
        a00: { ...CONTENT_BASE, is_beijing_related: true, sentiment_label: 'neutral', external_importance_score: 40 },
    }, async (page) => {
        const badge = drawerMeta(page).querySelector('.search-category-badge');
        assert.equal(badge.textContent, '京内正面');
        assert.ok(badge.className.includes('neutral'), '非正非负应带 neutral 样式类');
    });
});

test('F6 地域就绪但情感缺失：不归类，分数也不单独出现', async () => {
    await openFilterDrawer({
        a00: { ...CONTENT_BASE, is_beijing_related: true, sentiment_label: null, external_importance_score: 58 },
    }, async (page) => {
        const meta = drawerMeta(page);
        assert.equal(meta.querySelector('.search-category-badge'), null);
        assert.equal(meta.querySelector('.content-drawer-score'), null);
    });
});

test('D1 汇总/存档版抽屉同样渲染分类徽章与分数', async () => {
    const page = await bootSummaryPage({
        shifts: [shiftRow({ id: 's-today', displayName: '测试值班', endDayOffset: 0 })],
        articleContents: {
            x1: {
                ...CONTENT_BASE,
                is_beijing_related: false,
                sentiment_label: 'negative',
                external_importance_score: 61.4,
            },
        },
    });
    try {
        page.window.loadArchiveDrawerArticle('x1');
        await waitForSummary(() => drawerMeta(page).querySelector('.search-category-badge'));
        const badge = drawerMeta(page).querySelector('.search-category-badge');
        assert.equal(badge.textContent, '京外负面');
        assert.ok(badge.className.includes('negative'));
        assert.equal(drawerMeta(page).firstElementChild, badge);

        const score = drawerMeta(page).querySelector('.content-drawer-score');
        assert.equal(score.textContent, '61 分');
        assert.equal(score.getAttribute('title'), null);
        assert.deepEqual(summaryRejections, []);
    } finally {
        page.close();
    }
});
