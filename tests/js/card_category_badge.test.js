// 筛选页单条新闻卡片的分类标签行为测试。
// 由 tests/test_manual_filter_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
//
// 覆盖的行为约定（改动这些行为时必须同步更新本文件）：
// 1. 地域与情感合并成一个标签（京内正面 / 京内负面 / 京外正面 / 京外负面），
//    卡片 meta 行不再分别渲染 sentiment 原文徽章与「京内: 是/否」两个字段；
// 2. 样式沿用分类徽章：badge + search-category-badge + positive/negative；
// 3. 情感非 negative 一律按正面，neutral 情感走 neutral 样式类（与检索卡片、原文抽屉同口径）；
// 4. 地域或情感未就绪时不猜词，留中性「-」占位，且不得出现「京内:」这类旧字段；
// 5. 聚类内的成员卡片与单条卡片走同一渲染，标签口径一致。
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { bootPage, unhandledRejections } = require('./manual_filter_harness');

const MODES = ['duty', 'admin'];
const MODE_LABELS = { duty: '值班', admin: '管理员' };

async function withPage(serverOptions, body) {
    const page = await bootPage('admin', serverOptions);
    try {
        await body(page);
        assert.deepEqual(unhandledRejections, []);
    } finally {
        page.close();
    }
}

function metaBadges(card) {
    return [...card.querySelectorAll('.meta-row .badge.search-category-badge')];
}

function metaText(card) {
    return card.querySelector('.meta-row').textContent;
}

for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}单条卡片：地域与情感合并成「京内正面」一个徽章`, async () => {
        const page = await bootPage(mode, {
            articleCount: 1,
            articleCategories: { a00: { is_beijing_related: true, sentiment_label: 'positive' } },
        });
        try {
            const card = page.card('a00');
            const badges = metaBadges(card);
            assert.equal(badges.length, 1, 'meta 行应只有一个分类徽章');
            assert.equal(badges[0].textContent, '京内正面');
            assert.ok(badges[0].classList.contains('positive'), '正面应带 positive 样式类');
            assert.ok(badges[0].classList.contains('badge'), '沿用全局 badge 样式');
            const text = metaText(card);
            assert.equal(text.includes('京内:'), false, '旧的「京内: 是/否」字段应删除');
            assert.equal(text.includes('positive'), false, '不应再显示 sentiment 原文');
            assert.deepEqual(unhandledRejections, []);
        } finally {
            page.close();
        }
    });
}

test('京外负面：negative 样式类与「京外负面」文案', async () => {
    await withPage({
        articleCount: 1,
        articleCategories: { a00: { is_beijing_related: false, sentiment_label: 'negative' } },
    }, async (page) => {
        const badges = metaBadges(page.card('a00'));
        assert.equal(badges.length, 1);
        assert.equal(badges[0].textContent, '京外负面');
        assert.ok(badges[0].classList.contains('negative'));
    });
});

test('情感非 negative 一律按正面：neutral 情感仍作「京内正面」，样式类为 neutral', async () => {
    await withPage({
        articleCount: 1,
        articleCategories: { a00: { is_beijing_related: true, sentiment_label: 'neutral' } },
    }, async (page) => {
        const badge = metaBadges(page.card('a00'))[0];
        assert.equal(badge.textContent, '京内正面');
        assert.ok(badge.classList.contains('neutral'), '非正非负应带 neutral 样式类');
    });
});

test('情感未就绪：不猜分类，留中性「-」占位', async () => {
    await withPage({
        articleCount: 1,
        articleCategories: { a00: { is_beijing_related: true, sentiment_label: null } },
    }, async (page) => {
        const card = page.card('a00');
        const badge = metaBadges(card)[0];
        assert.equal(badge.textContent, '-');
        assert.ok(badge.classList.contains('neutral'));
        assert.equal(metaText(card).includes('京内:'), false);
    });
});

test('地域未就绪：不猜分类，留中性「-」占位', async () => {
    await withPage({
        articleCount: 1,
        articleCategories: { a00: { is_beijing_related: null, sentiment_label: 'negative' } },
    }, async (page) => {
        const badge = metaBadges(page.card('a00'))[0];
        assert.equal(badge.textContent, '-');
        assert.ok(badge.classList.contains('neutral'));
    });
});

test('聚类内成员卡片同样合并成一个分类标签', async () => {
    await withPage({
        articleCount: 2,
        clusters: [['a00', 'a01']],
        articleCategories: {
            a00: { is_beijing_related: false, sentiment_label: 'positive' },
            a01: { is_beijing_related: false, sentiment_label: 'negative' },
        },
    }, async (page) => {
        assert.equal(metaBadges(page.card('a00'))[0].textContent, '京外正面');
        assert.equal(metaBadges(page.card('a00'))[0].className.includes('positive'), true);
        assert.equal(metaBadges(page.card('a01'))[0].textContent, '京外负面');
        assert.equal(metaBadges(page.card('a01'))[0].className.includes('negative'), true);
    });
});
