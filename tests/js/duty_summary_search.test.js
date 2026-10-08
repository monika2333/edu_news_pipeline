// 值班汇总页搜索框（summary-search-input → state.searchQuery → getVisibleItems）
// 的多词 AND 过滤行为测试。夹具见 duty_summary_harness.js。
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { bootDutySummaryCore } = require('./duty_summary_harness.js');

const BASE_ITEM = {
    title: '',
    edited_summary: '',
    summary: '',
    llm_summary: '',
    source: '',
    llm_source: '',
    decision: '',
    admin_discarded_by_display_name: '',
};

function item(id, overrides = {}) {
    return { article_id: id, ...BASE_ITEM, ...overrides };
}

function pageWith(items, query) {
    const page = bootDutySummaryCore();
    page.setItems(items);
    page.setQuery(query);
    return page;
}

test('S1：单词检索仍按子串命中（回归）', () => {
    const page = pageWith([
        item('a', { title: '市教委发布寒假安排' }),
        item('b', { title: '大学科研成果转化落地' }),
    ], '寒假');
    try {
        assert.deepEqual(page.visibleIds(), ['a']);
    } finally { page.window.close(); }
});

test('S2：两词 AND——分别命中不同字段才可见', () => {
    const page = pageWith([
        item('a', { title: '市教委发布寒假安排', llm_summary: '中小学课后服务扩面' }),
        item('b', { title: '市教委发布寒假安排', llm_summary: '大学科研转化落地' }),
        item('c', { title: '一条无关消息', llm_summary: '中小学课后服务扩面' }),
    ], '市教委 课后');
    try {
        assert.deepEqual(page.visibleIds(), ['a']);
    } finally { page.window.close(); }
});

test('S3：全角空格与连续空白都作为分隔符', () => {
    const items = [
        item('a', { title: '市教委发布寒假安排', llm_summary: '中小学课后服务扩面' }),
        item('b', { title: '市教委发布寒假安排', llm_summary: '大学科研转化落地' }),
    ];
    for (const query of ['市教委　课后', '市教委   课后', ' 市教委　课后 ']) {
        const page = pageWith(items, query);
        try {
            assert.deepEqual(page.visibleIds(), ['a'], `query=${JSON.stringify(query)}`);
        } finally { page.window.close(); }
    }
});

test('S4：大小写不敏感（zh-CN locale 归一）', () => {
    const page = pageWith([
        item('a', { title: 'K12课后服务', llm_summary: 'AI助手上线' }),
        item('b', { title: 'K12课后服务', llm_summary: '人工客服值班' }),
    ], 'k12 ai');
    try {
        assert.deepEqual(page.visibleIds(), ['a']);
    } finally { page.window.close(); }
});

test('S5：空查询与纯空白返回全部', () => {
    const items = [item('a', { title: '甲' }), item('b', { title: '乙' })];
    for (const query of ['', '   ', '　']) {
        const page = pageWith(items, query);
        try {
            assert.deepEqual(page.visibleIds(), ['a', 'b'], `query=${JSON.stringify(query)}`);
        } finally { page.window.close(); }
    }
});

test('S6：检索词不允许跨字段拼接命中', () => {
    // title 末字与 summary 首字拼起来才构成第三词，AND 语义下不得命中
    const page = pageWith([
        item('a', { title: '市教委', summary: '发布安排' }),
    ], '市教委 发布 安排 委发');
    try {
        assert.deepEqual(page.visibleIds(), []);
    } finally { page.window.close(); }
});
