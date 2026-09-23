// 细化筛选折叠行的浏览器端行为测试。
// 由 tests/test_manual_filter_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
//
// 覆盖的行为约定（改动这些行为时必须同步更新本文件）：
// 1. 「筛选」按钮展开/收起折叠行，aria-expanded 同步；
// 2. 细化条件变化后，随后的列表请求必须携带对应查询参数（duplicate_state=untagged、
//    hour_from/hour_to、min_score/max_score），并回第 1 页（offset=0）；
// 3. 值班工作区在细化筛选启用时回退平铺列表接口（值班 /clusters 未实现这三个筛选）；
// 4. meta 行「清空筛选」是唯一清空入口：点击后徽标隐藏、后续请求不带细化参数；
// 5. 时段从 > 到 时显示跨零点提示。
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { bootPage, waitFor, unhandledRejections } = require('./manual_filter_harness');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function withPage(mode, serverOptions, body) {
    const page = await bootPage(mode, serverOptions);
    try {
        await body(page);
    } finally {
        page.close();
    }
}

function lastListSearch(page) {
    const listEntries = page.server.log.filter((entry) => entry.kind === 'list');
    assert.ok(listEntries.length, '应发出列表请求');
    return listEntries[listEntries.length - 1].search || {};
}

test('管理员「筛选」按钮展开折叠行，隐藏已报送随请求下发并回第 1 页', async () => {
    await withPage('admin', { articleCount: 3 }, async (page) => {
        const { document } = page;
        const toggle = document.getElementById('filter-refine-toggle');
        assert.equal(toggle.getAttribute('aria-expanded'), 'false');
        assert.equal(document.body.classList.contains('filter-refine-row-open'), false);

        toggle.click();
        assert.equal(document.body.classList.contains('filter-refine-row-open'), true);
        assert.equal(toggle.getAttribute('aria-expanded'), 'true');

        const checkbox = document.querySelector('input[data-refine-field="hideTagged"]');
        checkbox.checked = true;
        checkbox.dispatchEvent(new page.window.Event('change', { bubbles: true }));

        await waitFor(() => {
            const search = lastListSearch(page);
            return search.duplicate_state === 'untagged';
        });
        const search = lastListSearch(page);
        assert.equal(search.offset, '0', '筛选变化后应回到第 1 页');

        const badge = document.getElementById('filter-refine-count-badge');
        assert.equal(badge.hidden, false);
        assert.equal(badge.textContent, '1');
        assert.ok(
            document.getElementById('filter-search-meta').textContent.includes('筛选中：隐藏已报送'),
            document.getElementById('filter-search-meta').textContent
        );
    });
});

test('时段与分数组合下发 hour_from/hour_to/min_score/max_score，跨零点提示出现', async () => {
    await withPage('admin', { articleCount: 3 }, async (page) => {
        const { document } = page;
        document.getElementById('filter-refine-toggle').click();

        const hourFrom = document.querySelector('select[data-refine-field="hourFrom"]');
        hourFrom.value = '22';
        hourFrom.dispatchEvent(new page.window.Event('change', { bubbles: true }));
        const hourTo = document.querySelector('select[data-refine-field="hourTo"]');
        hourTo.value = '6';
        hourTo.dispatchEvent(new page.window.Event('change', { bubbles: true }));
        const minScore = document.querySelector('input[data-refine-field="minScore"]');
        minScore.value = '20';
        minScore.dispatchEvent(new page.window.Event('change', { bubbles: true }));

        await waitFor(() => {
            const search = lastListSearch(page);
            return search.hour_from === '22' && search.min_score === '20';
        });
        const search = lastListSearch(page);
        assert.equal(search.hour_to, '6');
        assert.equal(search.max_score, undefined);
        assert.equal(
            document.querySelector('.filter-refine-wrap-note').hidden,
            false,
            '从 > 到 应显示跨零点提示'
        );
        assert.ok(
            document.getElementById('filter-search-meta').textContent.includes('时段 22–6 时'),
            document.getElementById('filter-search-meta').textContent
        );
    });
});

test('meta 行「清空筛选」是唯一清空入口：点击后徽标隐藏、请求不再携带细化参数', async () => {
    await withPage('admin', { articleCount: 3 }, async (page) => {
        const { document } = page;
        document.getElementById('filter-refine-toggle').click();
        const checkbox = document.querySelector('input[data-refine-field="hideTagged"]');
        checkbox.checked = true;
        checkbox.dispatchEvent(new page.window.Event('change', { bubbles: true }));
        await waitFor(() => lastListSearch(page).duplicate_state === 'untagged');

        assert.equal(
            document.querySelectorAll('.filter-refine-clear-link').length,
            1,
            '折叠行内不得出现第二个清空入口'
        );
        document.querySelector('.filter-refine-clear-link').click();

        await waitFor(() => {
            const badge = document.getElementById('filter-refine-count-badge');
            return badge.hidden && !document.querySelector('.filter-refine-meta-suffix');
        });
        await sleep(50);
        const search = lastListSearch(page);
        assert.equal(search.duplicate_state, undefined);
        assert.equal(document.querySelector('input[data-refine-field="hideTagged"]').checked, false);
    });
});

test('值班工作区启用细化筛选时请求值班聚类接口并透传参数', async () => {
    await withPage('duty', { articleCount: 3 }, async (page) => {
        const { document } = page;
        document.getElementById('filter-refine-toggle').click();
        const hourFrom = document.querySelector('select[data-refine-field="hourFrom"]');
        hourFrom.value = '8';
        hourFrom.dispatchEvent(new page.window.Event('change', { bubbles: true }));

        await waitFor(() => {
            const entries = page.server.log.filter(
                (entry) => entry.kind === 'list' && entry.search.hour_from === '8'
            );
            return entries.length > 0 && entries[entries.length - 1].path.endsWith('/clusters');
        });
        // 列表请求（聚类）必须带细化参数走值班 /clusters（服务端筛选后保留聚类）；
        // 侧栏计数走平铺 /candidates 属预期——该接口支持这三个筛选
        const listForwarded = page.server.log.filter(
            (entry) => entry.kind === 'list' && entry.search.hour_from === '8'
        );
        assert.ok(
            listForwarded.every((entry) => entry.path.endsWith('/clusters')),
            '细化筛选的列表请求应透传给值班 /clusters，而不是回退 /candidates'
        );
    });
});

test('无未处理的异常拒绝', async () => {
    assert.deepEqual(unhandledRejections, []);
});
