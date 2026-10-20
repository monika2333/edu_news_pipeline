// 原文抽屉「检索正文」的空格分词行为测试。
// 由 tests/test_content_drawer_search_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
//
// 覆盖两套抽屉实现（同一份 _content_drawer.html，但脚本实现不同）：
// - 筛选页（manual_filter/content_drawer.js）：点卡片「原文」经事件委托打开；
// - 汇总/存档页（submission_archive/content_drawer.js）：直接调 loadArchiveDrawerArticle。
//
// 行为约定（改动这些行为时必须同步更新本文件）：
// 1. 检索框按 Unicode 空白切分，输入「教育 政策」时两个词都高亮；
// 2. 大小写不敏感去重：重复输入同一个词只高亮一遍；
// 3. 命中计数与上一个/下一个序列包含所有词的命中，而不是把整串当成一个短语。
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

const SEARCH_CONTENT = '教育政策解读。';

function drawerBody(page) {
    return page.document.getElementById('content-drawer-body');
}

function searchMarks(page) {
    return [...drawerBody(page).querySelectorAll('mark.content-hl-search')]
        .map((mark) => mark.textContent);
}

function searchCount(page) {
    return page.document.getElementById('content-drawer-search-count').textContent;
}

function typeSearch(page, value) {
    const input = page.document.getElementById('content-drawer-search');
    input.value = value;
    input.dispatchEvent(new page.window.Event('input', { bubbles: true }));
}

async function waitForMarks(page, expected, { summary = false } = {}) {
    const predicate = () => searchMarks(page).join('|') === expected;
    if (summary) {
        await waitForSummary(
            predicate,
            3000,
            `检索高亮应为 ${expected}，实际 ${searchMarks(page).join('|')}`
        );
        return;
    }
    const ok = await waitFor(predicate);
    assert.ok(
        ok,
        `检索高亮应为 ${expected}，实际 ${searchMarks(page).join('|')}`
    );
}

async function openFilterDrawer(body) {
    const page = await bootPage('admin', {
        articleCount: 1,
        articleContents: {
            a00: {
                title: '抽屉检索测试文章',
                source: '测试来源',
                created_at: '2026-10-13T01:00:00Z',
                content_markdown: SEARCH_CONTENT,
            },
        },
    });
    try {
        page.card('a00').querySelector('.content-drawer-trigger').click();
        await waitFor(() => drawerBody(page).querySelectorAll('p').length > 0);
        await body(page);
    } finally {
        page.close();
    }
}

test('C1 筛选页抽屉：空格分隔的多个词都高亮并计入命中序列', async () => {
    await openFilterDrawer(async (page) => {
        typeSearch(page, '教育 政策');
        await waitForMarks(page, '教育|政策');
        assert.equal(searchCount(page), '1/2', '计数应包含两个词的全部命中');
        assert.deepEqual(unhandledRejections, []);
    });
});

test('C2 筛选页抽屉：重复词去重后只高亮一遍', async () => {
    await openFilterDrawer(async (page) => {
        typeSearch(page, '教育  教育');
        await waitForMarks(page, '教育');
        assert.equal(searchCount(page), '1/1');
    });
});

test('C3 汇总/存档页抽屉：同样支持空格分词高亮', async () => {
    const page = await bootSummaryPage({
        shifts: [shiftRow({ id: 's-today', displayName: '测试值班', endDayOffset: 0 })],
        articleContents: {
            x1: {
                title: '存档抽屉检索测试文章',
                source: '测试来源',
                created_at: '2026-10-13T01:00:00Z',
                content_markdown: SEARCH_CONTENT,
            },
        },
    });
    try {
        page.window.loadArchiveDrawerArticle('x1');
        await waitForSummary(() => drawerBody(page).querySelectorAll('p').length > 0);
        typeSearch(page, '教育 政策');
        await waitForMarks(page, '教育|政策', { summary: true });
        assert.equal(searchCount(page), '1/2');
        assert.deepEqual(summaryRejections, []);
    } finally {
        page.close();
    }
});
