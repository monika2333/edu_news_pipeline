// 管理页账号搜索（#admin-user-search）的空格分词行为测试。
// 由 tests/test_admin_user_search_js_behavior.py 经 pytest 触发，不要直接用 node 运行
// （页面 HTML 需要 pytest 包装层经真实路由渲染后传入）。
//
// 行为约定：按空白切词、大小写不敏感、逐词 AND；每个词可落在姓名或用户名任一字段；
// 空检索串展示全部账号；无命中展示空态行。
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM, VirtualConsole } = require('jsdom');

const STATIC_DIR = process.env.CONSOLE_STATIC_DIR;
const PAGE_HTML = process.env.ADMIN_PAGE_HTML;

const unhandledRejections = [];
process.on('unhandledRejection', (reason) => {
    unhandledRejections.push(String((reason && reason.stack) || reason));
});

function waitFor(predicate, timeoutMs = 3000) {
    return new Promise((resolve, reject) => {
        const deadline = Date.now() + timeoutMs;
        const poll = () => {
            let ok = false;
            try {
                ok = Boolean(predicate());
            } catch (error) {
                // DOM 尚未渲染，继续等待
            }
            if (ok) return resolve(true);
            if (Date.now() > deadline) {
                return reject(new Error(`waitFor 超时，条件未成立：${predicate}`));
            }
            setTimeout(poll, 5);
        };
        poll();
    });
}

function inlineScripts(html) {
    if (!STATIC_DIR) throw new Error('CONSOLE_STATIC_DIR 未设置');
    return html.replace(/<script\s+src="([^"]+)"[^>]*>\s*<\/script>/g, (match, src) => {
        const cleanSrc = src.split('?')[0];
        if (!cleanSrc.startsWith('/static/')) return '';
        const file = path.join(STATIC_DIR, cleanSrc.slice('/static/'.length));
        if (!fs.existsSync(file)) throw new Error(`模板引用的脚本不存在：${cleanSrc}`);
        const code = fs.readFileSync(file, 'utf8').replace(/<\/script>/gi, '<\\/script>');
        return `<script>${code}</script>`;
    });
}

const USERS = [
    {
        id: 'u1', display_name: '张伟', username: 'zhangwei',
        role: 'admin', preferred_weekday: 0, is_active: true, last_login_at: null,
    },
    {
        id: 'u2', display_name: '张娜', username: 'zhangna',
        role: 'editor', preferred_weekday: null, is_active: true, last_login_at: null,
    },
    {
        id: 'u3', display_name: '李伟', username: 'liwei',
        role: 'editor', preferred_weekday: null, is_active: false, last_login_at: null,
    },
];

class FakeAdminServer {
    respond(url) {
        const pathname = url.pathname;
        if (pathname === '/api/admin/users') return [200, { items: USERS }];
        if (pathname === '/api/admin/duty-editors') return [200, { items: [] }];
        if (pathname === '/api/admin/schedules') return [200, { items: [] }];
        if (pathname === '/api/admin/shifts') return [200, { items: [], coverage: {} }];
        return [200, {}];
    }

    async fetch(input) {
        const url = new URL(String(input), 'http://localhost/');
        await new Promise((resolve) => setImmediate(resolve));
        const [status, payload] = this.respond(url);
        return new Response(JSON.stringify(payload), {
            status,
            headers: { 'Content-Type': 'application/json' },
        });
    }
}

async function bootAdminPage() {
    if (!PAGE_HTML) throw new Error('缺少页面 HTML（应由 pytest 包装层提供）');
    const server = new FakeAdminServer();
    const scriptErrors = [];
    const virtualConsole = new VirtualConsole();
    virtualConsole.on('jsdomError', (error) => scriptErrors.push(error.message));
    const dom = new JSDOM(inlineScripts(fs.readFileSync(PAGE_HTML, 'utf8')), {
        url: 'http://localhost/admin',
        runScripts: 'dangerously',
        pretendToBeVisual: true,
        virtualConsole,
        beforeParse(window) {
            window.fetch = (input) => server.fetch(input);
            window.Response = Response;
            window.Request = Request;
            window.Headers = Headers;
            window.scrollTo = () => {};
            window.matchMedia = () => ({
                matches: false,
                addListener() {},
                removeListener() {},
                addEventListener() {},
                removeEventListener() {},
            });
        },
    });
    const window = dom.window;
    const page = {
        window,
        document: window.document,
        search(value) {
            const input = window.document.getElementById('admin-user-search');
            input.value = value;
            input.dispatchEvent(new window.Event('input', { bubbles: true }));
        },
        visibleIds() {
            return [...window.document.querySelectorAll('#users-body tr[data-user-id]')]
                .map((row) => row.dataset.userId);
        },
        emptyText() {
            return window.document.getElementById('users-body').textContent.trim();
        },
        close() {
            window.close();
        },
    };
    await waitFor(() => page.visibleIds().length > 0);
    if (scriptErrors.length) throw new Error(`页面启动失败：${scriptErrors.join(' | ')}`);
    return page;
}

test('A1 空格分隔多词：逐词 AND，词可跨姓名/用户名字段', async () => {
    const page = await bootAdminPage();
    try {
        page.search('张 伟');
        assert.deepEqual(page.visibleIds(), ['u1']);
        page.search('张 zhang');
        assert.deepEqual(page.visibleIds(), ['u1', 'u2']);
        assert.deepEqual(unhandledRejections, []);
    } finally {
        page.close();
    }
});

test('A2 大小写不敏感；空检索串展示全部', async () => {
    const page = await bootAdminPage();
    try {
        page.search('ZHANG 伟');
        assert.deepEqual(page.visibleIds(), ['u1']);
        page.search('   ');
        assert.deepEqual(page.visibleIds(), ['u1', 'u2', 'u3']);
    } finally {
        page.close();
    }
});

test('A3 无命中时展示空态行', async () => {
    const page = await bootAdminPage();
    try {
        page.search('张 李');
        assert.deepEqual(page.visibleIds(), []);
        assert.ok(page.emptyText().includes('没有找到匹配的用户'));
    } finally {
        page.close();
    }
});
