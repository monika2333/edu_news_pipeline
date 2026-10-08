// 复核页签「搜索本页」（review-search-input → filterReviewItems）的
// 多词 AND 过滤行为测试。页面由 pytest 包装层经真实路由渲染。
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { bootPage, waitFor, unhandledRejections } = require('./manual_filter_harness.js');

async function waitForCondition(predicate) {
    const ok = await waitFor(predicate);
    assert.ok(ok, '等待条件超时');
}

function reviewItem(id, title, overrides = {}) {
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
        ...overrides,
    };
}

function visibleCardIds(page) {
    return [...page.document.querySelectorAll('#review-list .article-card')]
        .filter((card) => card.style.display !== 'none')
        .map((card) => card.dataset.id);
}

async function bootReviewPage(reviewItems) {
    const page = await bootPage('admin', { reviewItems });
    await waitForCondition(() => page.document.querySelectorAll('#review-list .article-card').length === reviewItems.length);
    return page;
}

function search(page, value) {
    const input = page.document.getElementById('review-search-input');
    input.value = value;
    input.dispatchEvent(new page.window.Event('input', { bubbles: true }));
}

test('RS1：单词检索仍按子串命中（回归）', async () => {
    const page = await bootReviewPage([
        reviewItem('r1', '市教委发布寒假安排'),
        reviewItem('r2', '大学科研成果转化落地'),
    ]);
    try {
        search(page, '寒假');
        assert.deepEqual(visibleCardIds(page), ['r1']);
    } finally { page.close(); }
});

test('RS2：两词 AND——分别命中标题与 LLM 摘要才可见', async () => {
    const page = await bootReviewPage([
        reviewItem('r1', '市教委发布寒假安排', { llm_summary: '中小学课后服务扩面' }),
        reviewItem('r2', '市教委发布寒假安排', { llm_summary: '大学科研转化落地' }),
        reviewItem('r3', '一条无关消息', { llm_summary: '中小学课后服务扩面' }),
    ]);
    try {
        search(page, '市教委 课后');
        assert.deepEqual(visibleCardIds(page), ['r1']);
    } finally { page.close(); }
});

test('RS3：全角空格与连续空白都作为分隔符', async () => {
    const page = await bootReviewPage([
        reviewItem('r1', '市教委发布寒假安排', { llm_summary: '中小学课后服务扩面' }),
        reviewItem('r2', '市教委发布寒假安排', { llm_summary: '大学科研转化落地' }),
    ]);
    try {
        for (const query of ['市教委　课后', '市教委   课后']) {
            search(page, query);
            assert.deepEqual(visibleCardIds(page), ['r1'], `query=${JSON.stringify(query)}`);
        }
    } finally { page.close(); }
});

test('RS4：大小写不敏感', async () => {
    const page = await bootReviewPage([
        reviewItem('r1', '本市K12课后服务扩面'),
        reviewItem('r2', '大学科研转化落地'),
    ]);
    try {
        search(page, 'k12');
        assert.deepEqual(visibleCardIds(page), ['r1']);
    } finally { page.close(); }
});

test('RS5：清空按钮恢复全部卡片', async () => {
    const page = await bootReviewPage([
        reviewItem('r1', '市教委发布寒假安排'),
        reviewItem('r2', '大学科研成果转化落地'),
    ]);
    try {
        search(page, '寒假');
        assert.deepEqual(visibleCardIds(page), ['r1']);
        page.document.getElementById('review-search-clear').click();
        assert.deepEqual(visibleCardIds(page), ['r1', 'r2']);
    } finally { page.close(); }
});
