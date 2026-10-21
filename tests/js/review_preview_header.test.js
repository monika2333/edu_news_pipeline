// 汇总审阅页「预览」的期号题头行为测试：默认包含题头、可关闭并记住选择、
// 备选桶不出现题头、没有基准/接口失败时正文照常可用。
// 页面由 pytest 包装层经 /admin/review 真实路由渲染，后端由
// manual_filter_harness 模拟。
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { bootPage, waitFor, unhandledRejections } = require('./manual_filter_harness.js');

async function waitForCondition(predicate, message = '等待条件超时') {
    const ok = await waitFor(predicate);
    assert.ok(ok, message);
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

const HEADER = [
    '首都教育舆情',
    '总第4848期',
    '2026年10月21日',
];

function previewHeaderPayload() {
    return {
        available: true,
        lines: HEADER,
        title_line: HEADER[0],
        issue_line: HEADER[1],
        date_line: HEADER[2],
        issue_date: '2026-10-21',
        report_date: '2026-10-21',
        day_offset: 1,
        baseline: {
            report_date: '2026-10-21',
            compiled_date: '2026-10-20',
            issue_no: '总第4847期',
        },
        notices: [],
    };
}

async function bootReviewPage(options = {}) {
    const page = await bootPage('admin', {
        reviewItems: [reviewItem('r1', '市教委发布寒假安排')],
        previewHeader: previewHeaderPayload(),
        ...options,
    });
    await waitForCondition(
        () => page.document.querySelectorAll('#review-list .article-card').length > 0,
        '复核列表未渲染'
    );
    return page;
}

async function openPreview(page) {
    page.document.getElementById('btn-preview-copy').click();
    await waitForCondition(
        () => page.document.getElementById('preview-modal').classList.contains('active'),
        '预览弹窗未打开'
    );
}

test('PH1：题头默认拼在预览文本开头，与正文同框', async () => {
    const page = await bootReviewPage();
    try {
        await openPreview(page);

        assert.equal(page.document.getElementById('preview-header-toggle').hidden, false);
        assert.equal(page.document.getElementById('preview-doc-notices').hidden, true);
        // 题头不再是独立区块，只存在于 textarea 的前三行
        assert.equal(page.document.getElementById('preview-doc-header'), null);

        // 请求带当前桶的报别
        const requests = page.server.requests('preview-header');
        assert.equal(requests.length, 1);
        assert.ok(requests[0].path.endsWith('/preview-header'), requests[0].path);
        assert.equal(requests[0].search.report_type, 'zongbao');

        const text = page.document.getElementById('preview-text').value;
        assert.ok(text.startsWith(`${HEADER.join('\n')}\n\n`), text.slice(0, 120));
        assert.equal(page.window.buildPreviewCopyText(), text);
    } finally { page.close(); }
});

test('PH2：取消勾选后预览文本不含题头，并记住选择', async () => {
    const page = await bootReviewPage();
    try {
        await openPreview(page);
        const checkbox = page.document.getElementById('preview-include-header');
        assert.equal(checkbox.checked, true);

        checkbox.checked = false;
        checkbox.dispatchEvent(new page.window.Event('change', { bubbles: true }));

        assert.equal(
            page.window.localStorage.getItem('manual_filter_preview_header'),
            'false'
        );
        const text = page.document.getElementById('preview-text').value;
        assert.ok(text.length > 0);
        assert.ok(!text.startsWith(HEADER[0]), text.slice(0, 120));
        assert.equal(page.window.buildPreviewCopyText(), text);
    } finally { page.close(); }
});

test('PH3：上次关掉题头后，新打开页面默认不带题头', async () => {
    const page = await bootReviewPage({ previewHeaderEnabled: false });
    try {
        await openPreview(page);
        assert.equal(page.document.getElementById('preview-include-header').checked, false);
        const text = page.document.getElementById('preview-text').value;
        assert.ok(!text.startsWith(HEADER[0]), text.slice(0, 120));
        assert.equal(page.window.buildPreviewCopyText(), text);
    } finally { page.close(); }
});

test('PH4：备选桶不出现题头，也没有开关', async () => {
    const page = await bootReviewPage({
        reviewItems: [
            reviewItem('r1', '采纳条目'),
            reviewItem('b1', '备选条目', { report_type: 'wanbao', status: 'backup' }),
        ],
    });
    try {
        page.document
            .querySelector('.review-category-btn[data-report-type="wanbao"][data-view="backup"]')
            .click();
        await waitForCondition(
            () => page.document.querySelector('#review-list .article-card[data-id="b1"]'),
            '备选桶未渲染'
        );
        await openPreview(page);

        assert.equal(page.document.getElementById('preview-header-toggle').hidden, true);
        const text = page.document.getElementById('preview-text').value;
        assert.ok(!text.startsWith(HEADER[0]), text.slice(0, 120));
        // 备选不成文，前端不该为此发题头请求
        assert.equal(page.server.requests('preview-header').length, 0);
    } finally { page.close(); }
});

test('PH5：没有期号基准时只给提示，正文照常预览', async () => {
    const page = await bootReviewPage({ previewHeader: null });
    try {
        await openPreview(page);

        assert.equal(page.document.getElementById('preview-header-toggle').hidden, true);
        const notices = page.document.getElementById('preview-doc-notices');
        assert.equal(notices.hidden, false);
        assert.match(notices.textContent, /无法推算期号/);
        assert.ok(page.document.getElementById('preview-text').value.length > 0);
    } finally { page.close(); }
});

test('PH6：题头接口失败不影响预览', async () => {
    const page = await bootReviewPage();
    try {
        page.server.fail('preview-header');
        await openPreview(page);

        assert.equal(page.document.getElementById('preview-doc-notices').hidden, true);
        const text = page.document.getElementById('preview-text').value;
        assert.ok(text.length > 0);
        assert.ok(!text.startsWith(HEADER[0]), text.slice(0, 120));
    } finally { page.close(); }
});

test('PH7：按 Escape 关闭预览弹窗', async () => {
    const page = await bootReviewPage();
    try {
        await openPreview(page);
        const modal = page.document.getElementById('preview-modal');
        assert.equal(modal.classList.contains('active'), true);

        // 在 body 上派发，走真实的捕获→冒泡路径
        page.document.body.dispatchEvent(new page.window.KeyboardEvent('keydown', {
            key: 'Escape',
            bubbles: true,
            cancelable: true,
        }));

        assert.equal(modal.classList.contains('active'), false);
    } finally { page.close(); }
});

test.after(() => {
    assert.deepEqual(unhandledRejections, []);
});
