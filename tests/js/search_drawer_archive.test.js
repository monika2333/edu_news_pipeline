// 检索抽屉「报送存档检索」的浏览器端行为测试。
// 由 tests/test_search_drawer_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
// 复用 manual_filter_harness 启动 /manual_filter 页：检索抽屉在该页无条件渲染，
// 且与 content_drawer.js 同页共存（appendArchiveHighlight 必须与
// appendHighlightedText 保持不同名的场景即在此页出现）。
//
// 覆盖的行为约定（改动这些行为时必须同步更新本文件）：
// 1. 高亮词列表直接取响应 data.terms，前端不自行切词；响应缺 terms 时只展示原文；
// 2. 多个词可分别命中标题、正文、来源，均以 <mark> 标出，全文不丢字；
// 3. 大小写不敏感命中，<mark> 内保留原文写法；
// 4. 词相互包含时（「课后」与「课后服务」）整段只包一个 <mark>，不嵌套、不重复；
// 5. 条目文本里的 HTML 字符串按纯文本呈现，不产生元素。
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { bootPage, waitFor, unhandledRejections } = require('./manual_filter_harness');

const PLACEHOLDER_TEXT = '输入检索词，多个词用空格分隔';

async function withPage(archiveResponses, body) {
    const page = await bootPage('admin', {});
    try {
        // 挂上存档检索响应：按调用顺序出队，超出后重复最后一组。
        const respond = page.server.respond;
        const requests = [];
        page.server.respond = function (url, request) {
            if (url.pathname === '/api/submission-archive/search') {
                requests.push(url);
                const response = archiveResponses[
                    Math.min(requests.length - 1, archiveResponses.length - 1)
                ];
                return [200, response];
            }
            return respond.call(this, url, request);
        };
        await body(page, requests);
    } finally {
        page.close();
    }
}

function archiveResponse(items, terms) {
    return { items, terms, query: '', total: items.length };
}

async function runArchiveSearch(page, query) {
    const input = page.document.getElementById('archive-search-q');
    input.value = query;
    input.dispatchEvent(new page.window.Event('input', { bubbles: true }));
    page.document.getElementById('btn-archive-search').click();
    const settled = await waitFor(() => {
        const results = page.document.getElementById('archive-search-results');
        return results.querySelector('.archive-item, .empty, .error');
    });
    assert.ok(settled, '存档检索结果未渲染');
}

function markTexts(container) {
    return [...container.querySelectorAll('mark')].map(mark => mark.textContent);
}

test('两个检索框提示可用空格分隔多个词', async () => {
    await withPage([archiveResponse([], [])], async (page) => {
        for (const id of ['archive-search-q', 'search-q']) {
            const input = page.document.getElementById(id);
            assert.ok(input, `缺少检索框 #${id}`);
            assert.equal(input.placeholder, PLACEHOLDER_TEXT);
        }
    });
});

test('terms 两词分别命中标题与正文：两处各有 mark，全文拼回与原文一致', async () => {
    const item = {
        id: 'a1',
        report_type: 'zongbao',
        report_date: '2026-09-15',
        title: '双减政策落地',
        body: '本周推进课后服务。',
        source: '北京日报',
    };
    await withPage([archiveResponse([item], ['双减', '课后'])], async (page, requests) => {
        await runArchiveSearch(page, '双减 课后');
        // 原始输入整串交给后端，切词以后端为准
        assert.equal(requests[0].searchParams.get('q'), '双减 课后');

        const itemEl = page.document.querySelector('#archive-search-results .archive-item');
        assert.deepEqual(markTexts(itemEl.querySelector('.archive-item-title')), ['双减']);
        const bodyEl = itemEl.querySelector('.archive-item-body');
        assert.ok(markTexts(bodyEl).includes('课后'), `正文未高亮「课后」：${bodyEl.textContent}`);
        assert.equal(
            itemEl.textContent,
            '综报2026-09-15双减政策落地本周推进课后服务。（北京日报）',
            '高亮不应丢失或改动任何文字'
        );
    });
});

test('词命中来源：来源片段中有 mark', async () => {
    const item = {
        id: 'a2',
        report_type: 'wanbao',
        report_date: '2026-09-15',
        title: '校园开放日',
        body: '多所小学本周开放。',
        source: '新京报',
    };
    await withPage([archiveResponse([item], ['新京报'])], async (page) => {
        await runArchiveSearch(page, '新京报');
        const sourceEl = page.document.querySelector(
            '#archive-search-results .archive-item .archive-item-source'
        );
        assert.ok(sourceEl, '来源片段未渲染');
        assert.deepEqual(markTexts(sourceEl), ['新京报']);
        assert.equal(sourceEl.textContent, '（新京报）');
    });
});

test('大小写不敏感：terms 为 AI 时正文里的小写 ai 也高亮，且保留原文写法', async () => {
    const item = {
        id: 'a3',
        report_type: 'zongbao',
        report_date: '2026-09-15',
        title: 'ai 助教进课堂',
        body: '',
        source: '',
    };
    await withPage([archiveResponse([item], ['AI'])], async (page) => {
        await runArchiveSearch(page, 'AI');
        const titleEl = page.document.querySelector(
            '#archive-search-results .archive-item .archive-item-title'
        );
        assert.deepEqual(markTexts(titleEl), ['ai']);
    });
});

test('terms 同时包含「课后」与「课后服务」：整段只包一个 mark，无嵌套', async () => {
    const item = {
        id: 'a4',
        report_type: 'zongbao',
        report_date: '2026-09-15',
        title: '放学后去哪儿',
        body: '课后服务覆盖全校。',
        source: '',
    };
    await withPage([archiveResponse([item], ['课后', '课后服务'])], async (page) => {
        await runArchiveSearch(page, '课后 课后服务');
        const bodyEl = page.document.querySelector(
            '#archive-search-results .archive-item .archive-item-body'
        );
        assert.deepEqual(markTexts(bodyEl), ['课后服务'], '应优先高亮更长的词');
        assert.equal(bodyEl.querySelectorAll('mark mark').length, 0, '不允许嵌套 mark');
        assert.equal(bodyEl.textContent, '课后服务覆盖全校。', '高亮不应丢失或重复文字');
    });
});

test('高亮词以响应 terms 为准：与输入框内容不同时不按输入框高亮', async () => {
    const item = {
        id: 'a5',
        report_type: 'zongbao',
        report_date: '2026-09-15',
        title: '存档报道里的课后服务专题',
        body: '',
        source: '',
    };
    await withPage([archiveResponse([item], ['课后服务'])], async (page) => {
        await runArchiveSearch(page, '存档 报道');
        const titleEl = page.document.querySelector(
            '#archive-search-results .archive-item .archive-item-title'
        );
        assert.deepEqual(
            markTexts(titleEl),
            ['课后服务'],
            '输入框里的词不在响应 terms 中，不得出现在高亮里'
        );
    });
});

test('条目文本含 <script> 字符串：按纯文本呈现，不产生元素', async () => {
    const item = {
        id: 'a6',
        report_type: 'zongbao',
        report_date: '2026-09-15',
        title: '课后服务<script>alert(1)</script>通知',
        body: '',
        source: '',
    };
    await withPage([archiveResponse([item], ['课后'])], async (page) => {
        await runArchiveSearch(page, '课后');
        const results = page.document.getElementById('archive-search-results');
        assert.equal(results.querySelector('script'), null, '不得产生 script 元素');
        const titleEl = results.querySelector('.archive-item .archive-item-title');
        assert.equal(
            titleEl.textContent,
            '课后服务<script>alert(1)</script>通知',
            'HTML 字符串应原样作为文本呈现'
        );
    });
});

test('响应缺 terms：只展示原文，不报错', async () => {
    const item = {
        id: 'a7',
        report_type: 'zongbao',
        report_date: '2026-09-15',
        title: '课后服务安排',
        body: '',
        source: '',
    };
    await withPage([{ items: [item], query: '课后', total: 1 }], async (page) => {
        await runArchiveSearch(page, '课后');
        const titleEl = page.document.querySelector(
            '#archive-search-results .archive-item .archive-item-title'
        );
        assert.deepEqual(markTexts(titleEl), []);
        assert.equal(titleEl.textContent, '课后服务安排');
    });
});

test('结束后无未处理的脚本错误', async () => {
    await withPage([archiveResponse([], [])], async () => {
        assert.deepEqual(unhandledRejections, []);
    });
});
