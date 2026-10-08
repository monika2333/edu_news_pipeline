// 汇总审阅页（管理员 /admin/duty-summary）的 jsdom 行为测试夹具。
//
// 页面 HTML 由 pytest 包装层（tests/test_duty_summary_js_behavior.py）经真实路由
// 渲染后写入临时文件，通过环境变量传入；脚本按模板中的 <script> 顺序内联执行，
// 走真实的启动流程。后端仅模拟班次列表与条目列表两个 GET 接口。
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');

const STATIC_DIR = process.env.CONSOLE_STATIC_DIR;
const PAGE_HTML = process.env.DUTY_SUMMARY_PAGE_HTML;

const unhandledRejections = [];
process.on('unhandledRejection', (reason) => {
    unhandledRejections.push(String((reason && reason.stack) || reason));
});

function nextTick() {
    return new Promise((resolve) => setImmediate(resolve));
}

// 等待条件成立；超时即抛错使测试失败（约定见 src/console/AGENTS.md 测试节），
// 不允许依赖返回值做否定断言；否定断言用 assertNever。
async function waitFor(predicate, timeoutMs = 3000, description = '') {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        try {
            if (predicate()) return true;
        } catch (error) {
            // 条件依赖的 DOM 可能尚未渲染，继续等待
        }
        await new Promise((resolve) => setTimeout(resolve, 5));
    }
    const what = description || predicate.toString();
    throw new Error(`waitFor 超时（${timeoutMs}ms），条件未成立：${what}`);
}

// 「某事在一段时间内不应发生」的否定断言：等满整个窗口，条件一旦成立即抛错。
async function assertNever(predicate, windowMs = 300, description = '') {
    const deadline = Date.now() + windowMs;
    while (Date.now() < deadline) {
        let fired = false;
        try {
            fired = !!predicate();
        } catch (error) {
            // 条件依赖的 DOM 尚未渲染视为未发生，继续观察
        }
        if (fired) {
            const what = description || predicate.toString();
            throw new Error(`assertNever 失败：条件在 ${windowMs}ms 窗口内成立：${what}`);
        }
        await new Promise((resolve) => setTimeout(resolve, 5));
    }
    return true;
}

function inlineScripts(html) {
    if (!STATIC_DIR) throw new Error('CONSOLE_STATIC_DIR 未设置');
    return html.replace(/<script\s+src="([^"]+)"[^>]*>\s*<\/script>/g, (match, src) => {
        const cleanSrc = src.split('?')[0];
        if (!cleanSrc.startsWith('/static/')) return '';
        if (cleanSrc.includes('/vendor/')) {
            return '<script>window.Sortable = function () { return { destroy() {} }; };'
                + 'window.Sortable.create = () => ({ destroy() {} });</script>';
        }
        const file = path.join(STATIC_DIR, cleanSrc.slice('/static/'.length));
        if (!fs.existsSync(file)) throw new Error(`模板引用的脚本不存在：${cleanSrc}`);
        const code = fs.readFileSync(file, 'utf8').replace(/<\/script>/gi, '<\\/script>');
        return `<script>${code}</script>`;
    });
}

// 班次窗口与后端 generate_shifts 同口径：ends_at 是「 coverage 日」的
// 班次边界时刻（这里取 5 点，真实值由环境配置，只影响时刻不影响日期键），
// starts_at 回溯一天。endDayOffset=0 即 ends_at 落在上海时区的今天——
// 也是前端 chooseInitialShift 会选中的「最近结束」的班次。
const SHIFT_BOUNDARY_HOUR = 5;

function shiftWindow(endDayOffset) {
    const key = new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
    }).format(new Date(Date.now() + endDayOffset * 86400000));
    const ends_at = `${key}T${String(SHIFT_BOUNDARY_HOUR).padStart(2, '0')}:00:00+08:00`;
    const starts_at = new Date(
        new Date(ends_at).getTime() - 86400000
    ).toISOString();
    return { starts_at, ends_at };
}

function shiftRow({
    id,
    displayName,
    endDayOffset = 0,
    username = null,
}) {
    return {
        shift_id: id,
        user_id: `user-${id}`,
        username: username === null ? `user-${id}` : username,
        display_name: displayName,
        user_is_active: true,
        ...shiftWindow(endDayOffset),
        cancelled_at: null,
        total: 0,
        pending: 0,
        selected: 0,
        backup: 0,
        discarded: 0,
        zongbao_selected: 0,
        zongbao_backup: 0,
        wanbao_selected: 0,
        wanbao_backup: 0,
        zongbao_selected_all: 0,
        zongbao_backup_all: 0,
        wanbao_selected_all: 0,
        wanbao_backup_all: 0,
    };
}

class FakeSummaryServer {
    constructor({ shifts = [], reviewItems = [] } = {}) {
        this.shifts = shifts;
        this.reviewItems = reviewItems;
        this.log = [];
        this.inflight = 0;
    }

    respond(url) {
        const pathname = url.pathname;
        if (pathname === '/api/admin/duty-summary') {
            return [200, { items: this.shifts }];
        }
        if (pathname.startsWith('/api/admin/duty-summary/') && pathname.endsWith('/reviews')) {
            return [200, { items: this.reviewItems }];
        }
        return [200, {}];
    }

    async fetch(input, options = {}) {
        const url = new URL(String(input), 'http://localhost/');
        const body = options.body ? JSON.parse(options.body) : null;
        const entry = {
            path: url.pathname,
            search: Object.fromEntries(url.searchParams.entries()),
            status: null,
        };
        this.log.push(entry);
        this.inflight += 1;
        try {
            await nextTick();
            const [status, payload] = this.respond(url, body);
            entry.status = status;
            return new Response(JSON.stringify(payload), {
                status,
                headers: { 'Content-Type': 'application/json' },
            });
        } finally {
            entry.done = true;
            this.inflight -= 1;
        }
    }
}

async function bootPage(serverOptions = {}) {
    if (!PAGE_HTML) throw new Error('缺少页面 HTML（应由 pytest 包装层提供）');
    const server = new FakeSummaryServer(serverOptions);
    const scriptErrors = [];
    const virtualConsole = new VirtualConsole();
    virtualConsole.on('jsdomError', (error) => scriptErrors.push(error.message));
    const dom = new JSDOM(inlineScripts(fs.readFileSync(PAGE_HTML, 'utf8')), {
        url: 'http://localhost/admin/duty-summary',
        runScripts: 'dangerously',
        pretendToBeVisual: true,
        virtualConsole,
        beforeParse(window) {
            window.fetch = (input, options) => server.fetch(input, options);
            window.Response = Response;
            window.Request = Request;
            window.Headers = Headers;
            window.scrollTo = () => {};
            window.HTMLElement.prototype.scrollIntoView = () => {};
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
        server,
        shiftCards() {
            return [...window.document.querySelectorAll('#summary-shift-list .summary-shift-card')]
                .map((card) => ({
                    id: card.dataset.shiftId,
                    active: card.classList.contains('active'),
                    date: card.querySelector('.summary-shift-date')?.textContent || '',
                    owner: card.querySelector('.summary-shift-owner')?.textContent || null,
                    ownerElement: card.querySelector('.summary-shift-owner'),
                    text: card.textContent,
                }));
        },
        card(id) {
            return window.document.querySelector(
                `#summary-shift-list .summary-shift-card[data-shift-id="${id}"]`
            );
        },
        contextText() {
            return window.document.getElementById('summary-context').textContent;
        },
        clickShift(id) {
            this.card(id).dispatchEvent(new window.Event('click', { bubbles: true }));
        },
        close() {
            window.close();
        },
    };
    const booted = await waitFor(
        () => page.shiftCards().length > 0 && server.inflight === 0
    );
    if (!booted || scriptErrors.length) {
        throw new Error(`页面启动失败：${scriptErrors.join(' | ') || '班次列表未渲染'}`);
    }
    return page;
}

module.exports = { bootPage, waitFor, assertNever, unhandledRejections, shiftRow };
