// 系统设置页 · 提示词试跑（P2）的 jsdom 行为测试。
// 覆盖：搜索抽屉选择模式、选文章、评分试跑、草稿标记、京内判定、错误回显、
// 以及选择模式不污染筛选页/值班页共享的 localStorage。
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { bootPage, waitFor, unhandledRejections } = require('./settings_harness.js');

function promptsPanel(page) {
    return page.panel('prompts');
}

function editor(page) {
    return page.document.getElementById('prompt-editor');
}

function setEditor(page, value) {
    const el = editor(page);
    el.value = value;
    el.dispatchEvent(new page.window.Event('input', { bubbles: true }));
}

async function openPrompts(page) {
    page.clickTab('prompts');
    await waitFor(() => promptsPanel(page).querySelector('.prompt-nav-item'));
}

async function pickArticle(page) {
    promptsPanel(page).querySelector('.prompt-test-pick').click();
    await waitFor(() => page.document.getElementById('search-drawer').classList.contains('active'));
    const input = page.document.getElementById('search-q');
    input.value = '教育';
    input.dispatchEvent(new page.window.Event('input', { bubbles: true }));
    page.document.getElementById('btn-drawer-search').click();
    await waitFor(() => page.document.querySelector('.search-item'));
    page.document.querySelector('.search-pick-btn').click();
    await waitFor(() => promptsPanel(page).querySelector('.prompt-test-article'));
}

function queryText(page, selector) {
    const el = page.document.querySelector(selector);
    return el ? el.textContent : '';
}

test('P8 选择文章后试跑评分并显示当前生产分数', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        assert.equal(page.window.localStorage.getItem('search_drawer_open'), null);
        assert.equal(page.window.localStorage.getItem('search_drawer_tab'), null);
        assert.equal(page.window.localStorage.getItem('search_filters'), null);

        promptsPanel(page).querySelector('.prompt-test-pick').click();
        const drawer = page.document.getElementById('search-drawer');
        await waitFor(() => drawer.classList.contains('active'));
        assert.equal(
            drawer.querySelector('.drawer-header h3').textContent,
            '选择一篇文章',
        );
        assert.equal(drawer.querySelector('.search-tabs').hidden, true);
        assert.equal(page.window.localStorage.getItem('search_drawer_open'), null);

        const input = page.document.getElementById('search-q');
        input.value = '教育';
        input.dispatchEvent(new page.window.Event('input', { bubbles: true }));
        page.document.getElementById('btn-drawer-search').click();
        await waitFor(() => page.document.querySelector('.search-item'));
        page.document.querySelector('.search-pick-btn').click();

        await waitFor(() => promptsPanel(page).querySelector('.prompt-test-article'));
        assert.equal(drawer.classList.contains('active'), false);
        assert.match(
            promptsPanel(page).querySelector('.prompt-test-article').textContent,
            /北京某中学开展课后服务/,
        );
        assert.match(
            promptsPanel(page).querySelector('.prompt-test-article').textContent,
            /当前生产分 72/,
        );
        assert.equal(page.window.localStorage.getItem('search_drawer_open'), null);
        assert.equal(page.window.localStorage.getItem('search_drawer_tab'), null);
        assert.equal(page.window.localStorage.getItem('search_filters'), null);

        promptsPanel(page).querySelector('.prompt-test-run').click();
        await waitFor(() => promptsPanel(page).querySelector('.prompt-test-result'));
        assert.match(queryText(page, '.prompt-test-score'), /78/);
        assert.match(queryText(page, '.prompt-test-verdict'), /通过/);
        assert.match(queryText(page, '.prompt-test-current'), /72/);
        assert.match(queryText(page, '.prompt-test-meta'), /v2/);
        const request = page.server.requests('prompt-test')[0];
        assert.equal(request.body.article_id, 'article-1');
        assert.equal(request.body.content, 'EXTERNAL POSITIVE PROMPT v2');
    } finally {
        page.close();
    }
});

test('P9 未保存的编辑器内容作为草稿试跑', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await pickArticle(page);
        setEditor(page, 'DRAFT PROMPT');
        promptsPanel(page).querySelector('.prompt-test-run').click();
        await waitFor(() => promptsPanel(page).querySelector('.prompt-test-result'));
        assert.match(queryText(page, '.prompt-test-meta'), /草稿（未保存）/);
        assert.equal(
            page.server.requests('prompt-test')[0].body.content,
            'DRAFT PROMPT',
        );
    } finally {
        page.close();
    }
});

test('P10 京内判定提示词展示结论与理由', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await pickArticle(page);
        page.document
            .querySelector('.prompt-nav-item[data-prompt-key="beijing_gate"]')
            .click();
        await waitFor(() => editor(page).value === 'BEIJING GATE PROMPT v1');
        promptsPanel(page).querySelector('.prompt-test-run').click();
        await waitFor(() => promptsPanel(page).querySelector('.prompt-test-result'));
        assert.match(queryText(page, '.prompt-test-verdict'), /判为京内/);
        assert.match(queryText(page, '.prompt-test-reason'), /北京市教委主导/);
        assert.match(queryText(page, '.prompt-test-current'), /京内/);
    } finally {
        page.close();
    }
});

test('P11 试跑失败显示服务端原因', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await pickArticle(page);
        page.server.promptTestBehavior = {
            status: 422,
            payload: { detail: '该文章尚无正文，无法试跑' },
        };
        promptsPanel(page).querySelector('.prompt-test-run').click();
        await waitFor(() => promptsPanel(page).querySelector('.prompt-test-error'));
        assert.match(queryText(page, '.prompt-test-error'), /尚无正文/);
    } finally {
        page.close();
    }
});

test.after(() => {
    assert.deepEqual(unhandledRejections, []);
});
