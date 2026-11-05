// 审阅页单条卡片的加分词浅红高亮行为测试（renderReviewCard）。
// 由 tests/test_manual_filter_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
//
// 覆盖的行为约定（改动这些行为时必须同步更新本文件）：
// 1. 与筛选页同口径：命中加分词的浅红高亮（has-bonus）只用于京内
//    （is_beijing_related 为 true）；京外与未归类命中也保持白底；
// 2. meta 行「Bonus: 关键词」列表不受京内外限制，命中照常显示；
// 3. 审阅页与管理员汇总审阅共用 renderReviewCard，两端行为必须一致。
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { bootPage, waitFor, unhandledRejections } = require('./manual_filter_harness.js');

const MODES = ['duty', 'admin'];
const MODE_LABELS = { duty: '值班', admin: '管理员' };

async function waitForCondition(predicate, message) {
    const ok = await waitFor(predicate);
    assert.ok(ok, message || '等待条件超时');
}

function reviewItem(id, extra = {}) {
    return {
        article_id: id,
        title: `审阅标题${id}`,
        summary: '摘要内容',
        llm_summary: 'LLM摘要',
        llm_source_display: '测试来源',
        source: 'toutiao',
        url: `https://example.com/${id}`,
        report_type: null,
        status: 'selected',
        manual_status: null,
        version: 1,
        bonus_keywords: [],
        is_beijing_related: true,
        llm_source_raw: null,
        ...extra,
    };
}

function reviewCardIds(page) {
    return [...page.document.querySelectorAll('#review-list .article-card')]
        .map((card) => card.dataset.id);
}

async function bootAdminReviewPage(reviewItems) {
    const page = await bootPage('admin', { reviewItems });
    await waitForCondition(
        () => reviewCardIds(page).length === reviewItems.length,
        '审阅列表未渲染'
    );
    return page;
}

// 值班端 /duty 默认停在「筛选」，要切到「审阅」；数据走 workspace.js 转译层。
async function bootDutyReviewPage(reviewItems) {
    // articleCount 给 1 是为了让 bootPage 等 init 跑完（值班页默认停在筛选页），
    // 否则切页签可能落在 setupTabs() 之前，点击无人响应
    const page = await bootPage('duty', { articleCount: 1, reviewItems });
    page.document.querySelector('.tab-btn[data-tab="review"]').click();
    await waitForCondition(
        () => reviewCardIds(page).length === reviewItems.length,
        '值班端审阅列表未渲染'
    );
    return page;
}

const bootByMode = {
    duty: bootDutyReviewPage,
    admin: bootAdminReviewPage,
};

function metaText(card) {
    return card.querySelector('.meta-row').textContent;
}

for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}审阅卡片：京内命中加分词标浅红，Bonus 关键词照常显示`, async () => {
        const page = await bootByMode[mode]([
            reviewItem('r1', { is_beijing_related: true, bonus_keywords: ['中考改革', '双减'] }),
        ]);
        try {
            const card = page.document.querySelector('#review-list .article-card');
            assert.ok(card.classList.contains('has-bonus'), '京内卡片命中加分词应有 has-bonus');
            assert.equal(metaText(card).includes('中考改革'), true, 'Bonus 关键词列表不受京内外限制照常显示');
            assert.deepEqual(unhandledRejections, []);
        } finally { page.close(); }
    });

    test(`${MODE_LABELS[mode]}审阅卡片：京外命中加分词不标红`, async () => {
        const page = await bootByMode[mode]([
            reviewItem('r1', { is_beijing_related: false, bonus_keywords: ['中考改革'] }),
        ]);
        try {
            const card = page.document.querySelector('#review-list .article-card');
            assert.equal(card.classList.contains('has-bonus'), false, '京外卡片命中加分词也应保持白底');
            assert.equal(metaText(card).includes('中考改革'), true, 'Bonus 关键词列表照常显示');
            assert.deepEqual(unhandledRejections, []);
        } finally { page.close(); }
    });
}

test('审阅卡片：未归类命中加分词不标红', async () => {
    const page = await bootAdminReviewPage([
        reviewItem('r1', { is_beijing_related: null, bonus_keywords: ['中考改革'] }),
    ]);
    try {
        const card = page.document.querySelector('#review-list .article-card');
        assert.equal(card.classList.contains('has-bonus'), false, '未归类不属于京内，不应标红');
    } finally { page.close(); }
});

test('审阅卡片：京内未命中加分词不标红', async () => {
    const page = await bootAdminReviewPage([
        reviewItem('r1', { is_beijing_related: true, bonus_keywords: [] }),
    ]);
    try {
        const card = page.document.querySelector('#review-list .article-card');
        assert.equal(card.classList.contains('has-bonus'), false, '京内但未命中加分词，不应标红');
        assert.equal(metaText(card).includes('Bonus'), false, '无命中时不渲染 Bonus 行');
    } finally { page.close(); }
});
