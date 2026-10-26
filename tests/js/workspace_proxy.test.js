// 值班页请求转译层（workspace.js 的 workspaceFetch）的分流规则测试。
//
// 规则（2026-10-26 定稿）：
// 1. 需要翻译或失效缓存的路径（/review、/discarded、/edit、/decide、/order…）显式登记；
// 2. 其余 GET 直接透传到当前班次前缀——只读、幂等、后端按角色鉴权；
// 3. 未登记的写操作仍返回合成 403，并打 console.warn（漏登记必须可见）。
//
// 第 2 条是补课：/preview-header 曾因未登记而静默 403，表现为「管理员有、值班没有」。
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { bootPage, unhandledRejections } = require('./manual_filter_harness.js');

const READS = [
    '/unregistered-read?x=1',
    '/preview-header?report_type=wanbao',
];

test('WP1：未登记的只读接口直接透传到班次前缀', async () => {
    const page = await bootPage('duty', { articleCount: 1 });
    try {
        for (const url of READS) {
            const result = await page.window.eval(`
                workspaceFetch('/api/duty/shifts/s1${url}').then(r => r.status)
            `);
            assert.equal(result, 200, url);

            const hit = page.server.log.find((entry) => entry.path.endsWith(url.split('?')[0]));
            assert.ok(hit, `请求没有到达后端：${url}`);
            assert.ok(hit.path.startsWith('/api/duty/shifts/s1/'), hit.path);
        }
    } finally { page.close(); }
});

test('WP2：未登记的写操作返回 403、告警，且不发请求', async () => {
    const page = await bootPage('duty', { articleCount: 1 });
    try {
        const warnings = [];
        page.window.console.warn = (...args) => { warnings.push(args.join(' ')); };
        const result = await page.window.eval(`
            workspaceFetch('/api/duty/shifts/s1/unregistered-write', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: '{}'
            }).then(async (r) => ({ status: r.status, detail: (await r.json()).detail }))
        `);

        assert.equal(result.status, 403);
        assert.match(result.detail, /值班账号不能执行此操作/);
        assert.ok(
            warnings.some((line) => line.includes('unregistered-write')),
            `未产生告警：${warnings.join(' | ')}`
        );
        assert.equal(
            page.server.log.filter((entry) => entry.path.endsWith('/unregistered-write')).length,
            0,
            '写操作不应真的发出去'
        );
    } finally { page.close(); }
});

test('WP3：已登记的写操作照旧走转译（/decide 会失效列表缓存）', async () => {
    const page = await bootPage('duty', { articleCount: 1 });
    try {
        const result = await page.window.eval(`
            workspaceFetch('/api/duty/shifts/s1/decide', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ selected_ids: [], backup_ids: [], discarded_ids: [], pending_ids: [], versions: {} })
            }).then((r) => r.status)
        `);
        assert.equal(result, 200);
        assert.ok(
            page.server.log.some((entry) => entry.path.endsWith('/decide') && entry.kind === 'decide'),
            JSON.stringify(page.server.log.map((entry) => `${entry.kind} ${entry.path}`))
        );
    } finally { page.close(); }
});

test.after(() => {
    assert.deepEqual(unhandledRejections, []);
});
