// 检索抽屉「报送存档检索」的浏览器端行为测试。
// 由 tests/test_search_drawer_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
// 复用 manual_filter_harness 启动 /manual_filter 与 /duty 页：检索抽屉在两页都无条件
// 渲染（shared 模块改动默认双跑两端），且与 manual_filter/content_drawer.js 同页共存
// （appendArchiveHighlight 必须与 appendHighlightedText 保持不同名的场景即在此页出现）。
//
// 覆盖的行为约定（改动这些行为时必须同步更新本文件）：
// 1. 高亮词列表直接取响应 data.terms，前端不自行切词；响应缺 terms 时只展示原文；
// 2. 多个词可分别命中标题、正文、来源，均以 <mark> 标出，全文不丢字；
// 3. 大小写不敏感命中，<mark> 内保留原文写法；
// 4. 词相互包含时（「课后」与「课后服务」）整段只包一个 <mark>，不嵌套、不重复；
// 5. 条目文本里的 HTML 字符串按纯文本呈现，不产生元素；
// 6. 条目 urls 在正文/来源之后渲染为可点击外链（新标签页，只认 HTTP(S)），
//    无 urls 或地址非 HTTP(S) 时不渲染链接容器。
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { bootPage, waitFor, unhandledRejections } = require('./manual_filter_harness');

const PLACEHOLDER_TEXT = '输入检索词，多个词用空格分隔';
const MODES = ['duty', 'admin'];
const MODE_LABELS = { duty: '值班', admin: '管理员' };

async function withPage(archiveResponses, body, mode = 'admin') {
    const page = await bootPage(mode, {});
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
        // 值班页 DOMContentLoaded 链（班次加载 → 工作区重载 → 统计）在 bootPage 的
        // inflight 归零检查之后还有续发请求；关窗口前多等一拍，否则回包落在已销毁的
        // 文档上会抛 createElement 错误，被「结束后无未处理的脚本错误」用例捕获。
        await new Promise((resolve) => setTimeout(resolve, 10));
        await waitFor(() => page.server.inflight === 0);
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

// 共享模块改动默认双跑两端：值班页（/duty，workspaceFetch 转译层）与管理员的
// 检索抽屉必须渲染同样的链接。
for (const mode of MODES) {
    test(`${MODE_LABELS[mode]}页：条目带 urls 时在正文与来源之后渲染可点击外链`, async () => {
        const item = {
            id: 'a8',
            report_type: 'zongbao',
            report_date: '2026-11-19',
            title: '网民反映郊区公立小学英语教师课堂教学敷衍引关注',
            body: '11月17日，小红书用户发帖称……',
            source: '小红书',
            urls: ['https://xhslink.cn/o/GGCwbSlHt9'],
        };
        await withPage([archiveResponse([item], ['小红书'])], async (page) => {
            await runArchiveSearch(page, '小红书');
            const itemEl = page.document.querySelector('#archive-search-results .archive-item');
            const links = itemEl.querySelectorAll('.archive-item-links a');
            assert.equal(links.length, 1);
            const link = links[0];
            assert.equal(link.getAttribute('href'), 'https://xhslink.cn/o/GGCwbSlHt9');
            assert.equal(link.textContent, 'https://xhslink.cn/o/GGCwbSlHt9');
            assert.equal(link.target, '_blank');
            assert.equal(link.getAttribute('rel'), 'noopener noreferrer');
            // 链接容器独立于正文块，正文/来源的高亮与拼字不受影响
            assert.equal(itemEl.querySelector('.archive-item-body .archive-item-links'), null);
            assert.ok(
                itemEl.textContent.indexOf('（小红书）')
                    < itemEl.textContent.indexOf('https://xhslink.cn/o/GGCwbSlHt9'),
                '链接应渲染在正文与来源之后'
            );
        }, mode);
    });
}

test('条目带多个 urls：按原顺序逐条渲染链接', async () => {
    const item = {
        id: 'a9',
        report_type: 'zongbao',
        report_date: '2026-11-19',
        title: '两条原帖',
        body: '正文。',
        source: '微博',
        urls: ['https://weibo.com/a', 'https://weibo.com/b'],
    };
    await withPage([archiveResponse([item], ['微博'])], async (page) => {
        await runArchiveSearch(page, '微博');
        const links = [
            ...page.document.querySelectorAll('#archive-search-results .archive-item-links a')
        ];
        assert.deepEqual(
            links.map(link => link.getAttribute('href')),
            ['https://weibo.com/a', 'https://weibo.com/b']
        );
    });
});

test('无 urls 或地址非 HTTP(S)：不渲染链接容器', async () => {
    const item = {
        id: 'a10',
        report_type: 'wanbao',
        report_date: '2026-09-15',
        title: '无链接条目',
        body: '正文。',
        source: '北京日报',
        urls: [],
    };
    const unsafeItem = { ...item, id: 'a11', urls: ['javascript:alert(1)'] };
    await withPage([archiveResponse([item, unsafeItem], [])], async (page) => {
        await runArchiveSearch(page, '正文');
        const cards = page.document.querySelectorAll('#archive-search-results .archive-item');
        assert.equal(cards.length, 2);
        cards.forEach(card => {
            assert.equal(card.querySelector('.archive-item-links'), null);
            assert.equal(card.querySelector('a'), null);
        });
    });
});

test('结束后无未处理的脚本错误', async () => {
    await withPage([archiveResponse([], [])], async () => {
        assert.deepEqual(unhandledRejections, []);
    });
});
