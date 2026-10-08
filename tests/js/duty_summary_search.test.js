// 汇总审阅页搜索框（summary-search-input → state.searchQuery → getVisibleItems）
// 的多词 AND 过滤行为测试。复用 duty_summary_harness 启动 /admin/duty-summary 页，
// 由 pytest 包装层（tests/test_duty_summary_search_js_behavior.py）经真实路由渲染传入。
// 过滤走真实链路：输入框 input 事件 → renderItems → getVisibleItems，
// 不可见条目不渲染进 DOM，断言渲染出的卡片集合。
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { bootPage, waitFor, unhandledRejections, shiftRow } = require('./duty_summary_harness');

const BASE_ITEM = {
    title: '',
    edited_summary: '',
    summary: '',
    llm_summary: '',
    source: '',
    llm_source: '',
    decision: null,
    admin_status: 'unprocessed',
    admin_discarded_at: null,
    finalized_at: null,
    admin_discarded_by_display_name: null,
};

function reviewItem(id, overrides = {}) {
    return { article_id: id, ...BASE_ITEM, ...overrides };
}

async function withPage(reviewItems, body) {
    // 至少给一个「今天」的班次：chooseInitialShift 选中它后启动流程才会拉条目列表
    const page = await bootPage({
        shifts: [shiftRow({ id: 's-today', displayName: '测试值班', endDayOffset: 0 })],
        reviewItems,
    });
    try {
        await waitFor(
            () => page.document.querySelectorAll('#summary-items .article-card').length === reviewItems.length,
            3000,
            '条目卡片未渲染'
        );
        await body(page);
    } finally {
        page.close();
    }
}

function search(page, value) {
    const input = page.document.getElementById('summary-search-input');
    input.value = value;
    input.dispatchEvent(new page.window.Event('input', { bubbles: true }));
}

function renderedIds(page) {
    return [...page.document.querySelectorAll('#summary-items .article-card input[data-article-id]')]
        .map((input) => input.dataset.articleId);
}

test('S1：单词检索仍按子串命中（回归）', async () => {
    await withPage([
        reviewItem('a', { title: '市教委发布寒假安排' }),
        reviewItem('b', { title: '大学科研成果转化落地' }),
    ], async (page) => {
        search(page, '寒假');
        assert.deepEqual(renderedIds(page), ['a']);
    });
});

test('S2：两词 AND——分别命中不同字段才可见', async () => {
    await withPage([
        reviewItem('a', { title: '市教委发布寒假安排', llm_summary: '中小学课后服务扩面' }),
        reviewItem('b', { title: '市教委发布寒假安排', llm_summary: '大学科研转化落地' }),
        reviewItem('c', { title: '一条无关消息', llm_summary: '中小学课后服务扩面' }),
    ], async (page) => {
        search(page, '市教委 课后');
        assert.deepEqual(renderedIds(page), ['a']);
    });
});

test('S3：全角空格与连续空白都作为分隔符', async () => {
    await withPage([
        reviewItem('a', { title: '市教委发布寒假安排', llm_summary: '中小学课后服务扩面' }),
        reviewItem('b', { title: '市教委发布寒假安排', llm_summary: '大学科研转化落地' }),
    ], async (page) => {
        for (const query of ['市教委　课后', '市教委   课后', ' 市教委　课后 ']) {
            search(page, query);
            assert.deepEqual(renderedIds(page), ['a'], `query=${JSON.stringify(query)}`);
        }
    });
});

test('S4：大小写不敏感（zh-CN locale 归一）', async () => {
    await withPage([
        reviewItem('a', { title: 'K12课后服务', llm_summary: 'AI助手上线' }),
        reviewItem('b', { title: 'K12课后服务', llm_summary: '人工客服值班' }),
    ], async (page) => {
        search(page, 'k12 ai');
        assert.deepEqual(renderedIds(page), ['a']);
    });
});

test('S5：空查询与纯空白返回全部', async () => {
    await withPage([
        reviewItem('a', { title: '甲' }),
        reviewItem('b', { title: '乙' }),
    ], async (page) => {
        for (const query of ['', '   ', '　']) {
            search(page, query);
            assert.deepEqual(renderedIds(page), ['a', 'b'], `query=${JSON.stringify(query)}`);
        }
    });
});

test('S6：检索词不允许跨字段拼接命中', async () => {
    // title 末字与 summary 首字拼起来才构成第三词，AND 语义下不得命中
    await withPage([
        reviewItem('a', { title: '市教委', summary: '发布安排' }),
    ], async (page) => {
        search(page, '市教委 发布 安排 委发');
        assert.deepEqual(renderedIds(page), []);
    });
});
