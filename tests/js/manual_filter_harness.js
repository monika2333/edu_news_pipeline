// 人工筛选页（管理员 /manual_filter 与值班 /duty）的 jsdom 行为测试夹具。
//
// 页面 HTML 由 pytest 包装层（tests/test_manual_filter_js_behavior.py）经真实路由渲染后
// 写入临时文件，通过环境变量传入；脚本按模板中的 <script> 顺序内联执行，
// 走真实的 DOMContentLoaded 启动流程。后端由 FakeWorkspaceServer 模拟：
// 校验乐观锁版本（不符返回 409），并可按请求类型「扣住」下一次请求，
// 由测试显式放行，从而确定地构造并发时序，不依赖固定等待。
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');

const STATIC_DIR = process.env.CONSOLE_STATIC_DIR;
const PAGE_HTML = {
    duty: process.env.FILTER_PAGE_DUTY_HTML,
    admin: process.env.FILTER_PAGE_ADMIN_HTML,
};

const unhandledRejections = [];
process.on('unhandledRejection', (reason) => {
    unhandledRejections.push(String((reason && reason.stack) || reason));
});

function nextTick() {
    return new Promise((resolve) => setImmediate(resolve));
}

// 轮询等待条件成立；只用于「最终会成立」的正向条件，超时即失败。
async function waitFor(predicate, timeoutMs = 3000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        try {
            if (predicate()) return true;
        } catch (error) {
            // 条件依赖的 DOM 可能尚未渲染，继续等待
        }
        await new Promise((resolve) => setTimeout(resolve, 5));
    }
    return false;
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

class FakeWorkspaceServer {
    constructor({ articleCount = 0, clusters = null } = {}) {
        this.articles = new Map();
        for (let index = 0; index < articleCount; index += 1) {
            const id = `a${String(index).padStart(2, '0')}`;
            this.articles.set(id, {
                article_id: id,
                title: `标题${id}`,
                llm_summary: `LLM摘要${id}`,
                llm_source_display: `来源${id}`,
                manual_summary: null,
                manual_source: null,
                version: 1,
                decision: 'pending',
            });
        }
        // clusters：二维数组，每组是一个聚类的文章 id；未列出的文章各自成为单条聚类
        this.clusterGroups = clusters || [];
        this.log = [];
        this.inflight = 0;
        this.holds = {};
        this.held = [];
        this.failNext = {};
    }

    pendingArticles() {
        return [...this.articles.values()].filter((article) => article.decision === 'pending');
    }

    serializeItem(article) {
        return {
            article_id: article.article_id,
            title: article.title,
            summary: article.manual_summary ?? article.llm_summary,
            llm_source_display: article.manual_source ?? article.llm_source_display,
            version: article.version,
            status: 'pending',
            is_beijing_related: true,
            sentiment_label: 'positive',
        };
    }

    clusterPage(params) {
        const limit = Number(params.get('limit') || 10);
        const offset = Number(params.get('offset') || 0);
        const pendingIds = new Set(this.pendingArticles().map((article) => article.article_id));
        const grouped = new Set(this.clusterGroups.flat());
        const clusters = [];
        this.clusterGroups.forEach((group, index) => {
            const members = group.filter((id) => pendingIds.has(id));
            if (members.length) clusters.push({ cluster_id: `c${index}`, members });
        });
        this.pendingArticles()
            .filter((article) => !grouped.has(article.article_id))
            .forEach((article) => {
                clusters.push({ cluster_id: `single-${article.article_id}`, members: [article.article_id] });
            });
        const page = clusters.slice(offset, offset + limit).map((cluster) => ({
            cluster_id: cluster.cluster_id,
            bucket_key: 'internal_positive',
            item_ids: cluster.members,
            items: cluster.members.map((id) => this.serializeItem(this.articles.get(id))),
            size: cluster.members.length,
        }));
        return {
            clusters: page,
            items: [],
            total: clusters.length,
            item_total: pendingIds.size,
            limit,
            offset,
            view_mode: 'browse',
        };
    }

    classify(url) {
        const pathname = url.pathname;
        if (pathname.endsWith('/clusters')) return 'list';
        if (pathname.endsWith('/candidates')) {
            // 管理员列表走 candidates?cluster=true；侧栏计数走 cluster=false，单独归类，避免占用列表扣押
            return url.searchParams.get('cluster') === 'true' ? 'list' : 'counts';
        }
        if (pathname.endsWith('/edit')) return 'edit';
        if (pathname.endsWith('/decide')) return 'decide';
        return 'other';
    }

    respond(url, body) {
        const pathname = url.pathname;
        if (pathname === '/api/duty/shifts') {
            return [200, {
                items: [{
                    id: 's1',
                    status: 'active',
                    starts_at: '2026-09-11T00:00:00Z',
                    ends_at: '2026-09-12T00:00:00Z',
                }],
            }];
        }
        if (pathname.endsWith('/stats')) {
            return [200, { pending: this.pendingArticles().length, selected: 0, backup: 0, discarded: 0 }];
        }
        if (pathname.endsWith('/clusters')) return [200, this.clusterPage(url.searchParams)];
        if (pathname.endsWith('/candidates')) {
            if (url.searchParams.get('cluster') === 'true') return [200, this.clusterPage(url.searchParams)];
            const items = this.pendingArticles().map((article) => this.serializeItem(article));
            return [200, { items, total: items.length, limit: 200, offset: 0 }];
        }
        if (pathname.endsWith('/edit')) {
            const edits = Object.entries(body.edits || {});
            for (const [id] of edits) {
                if ((body.versions || {})[id] !== this.articles.get(id).version) {
                    return [409, { detail: 'Review version is stale' }];
                }
            }
            const versions = {};
            for (const [id, edit] of edits) {
                const article = this.articles.get(id);
                article.manual_summary = edit.summary;
                article.manual_source = edit.llm_source;
                article.version += 1;
                versions[id] = article.version;
            }
            return [200, { updated: edits.length, versions }];
        }
        if (pathname.endsWith('/decide')) {
            const groups = {
                selected: body.selected_ids || [],
                backup: body.backup_ids || [],
                discarded: body.discarded_ids || [],
                pending: body.pending_ids || [],
            };
            for (const ids of Object.values(groups)) {
                for (const id of ids) {
                    if ((body.versions || {})[id] !== this.articles.get(id).version) {
                        return [409, { detail: 'Review version is stale' }];
                    }
                }
            }
            const versions = {};
            for (const [decision, ids] of Object.entries(groups)) {
                for (const id of ids) {
                    const article = this.articles.get(id);
                    article.decision = decision;
                    article.version += 1;
                    versions[id] = article.version;
                }
            }
            return [200, { versions }];
        }
        return [200, {}];
    }

    async fetch(input, options = {}) {
        const url = new URL(String(input), 'http://localhost/');
        const kind = this.classify(url);
        const body = options.body ? JSON.parse(options.body) : null;
        const entry = { kind, path: url.pathname, body, status: null, done: false };
        this.log.push(entry);
        this.inflight += 1;
        const willFail = (this.failNext[kind] || 0) > 0;
        if (willFail) this.failNext[kind] -= 1;
        // 列表在请求发出时取快照，模拟「先发出的请求带旧数据、后返回」
        const snapshot = kind === 'list' ? this.respond(url, body) : null;
        try {
            if ((this.holds[kind] || 0) > 0) {
                this.holds[kind] -= 1;
                await new Promise((release) => this.held.push({ kind, release }));
            } else {
                await nextTick();
            }
            const [status, payload] = willFail
                ? [500, { detail: 'injected failure' }]
                : (snapshot || this.respond(url, body));
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

    // 扣住接下来 count 个指定类型的请求，直到 release(kind)
    hold(kind, count = 1) {
        this.holds[kind] = (this.holds[kind] || 0) + count;
    }

    heldCount(kind) {
        return this.held.filter((entry) => entry.kind === kind).length;
    }

    release(kind) {
        const releasing = this.held.filter((entry) => entry.kind === kind);
        this.held = this.held.filter((entry) => entry.kind !== kind);
        releasing.forEach((entry) => entry.release());
    }

    requests(kind) {
        return this.log.filter((entry) => entry.kind === kind);
    }
}

async function bootPage(mode, serverOptions = {}) {
    const htmlFile = PAGE_HTML[mode];
    if (!htmlFile) throw new Error(`缺少 ${mode} 页面 HTML（应由 pytest 包装层提供）`);
    const server = new FakeWorkspaceServer(serverOptions);
    const scriptErrors = [];
    const virtualConsole = new VirtualConsole();
    virtualConsole.on('jsdomError', (error) => scriptErrors.push(error.message));
    const dom = new JSDOM(inlineScripts(fs.readFileSync(htmlFile, 'utf8')), {
        url: mode === 'duty' ? 'http://localhost/duty' : 'http://localhost/manual_filter',
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
        cardIds() {
            return [...window.document.querySelectorAll('#filter-list .article-card')]
                .map((card) => card.dataset.id);
        },
        toastText() {
            return window.document.getElementById('toast').textContent;
        },
        listHtml() {
            return window.document.getElementById('filter-list').innerHTML;
        },
        card(id) {
            return window.document.querySelector(`#filter-list .article-card[data-id="${id}"]`);
        },
        setEditValue(id, selector, value) {
            const box = page.card(id).querySelector(selector);
            box.value = value;
            return box;
        },
        fireChange(element) {
            element.dispatchEvent(new window.Event('change', { bubbles: true }));
        },
        chooseRadio(radio) {
            radio.checked = true;
            radio.dispatchEvent(new window.Event('change', { bubbles: true }));
        },
        close() {
            window.close();
        },
    };
    const expectedCards = Math.min(serverOptions.articleCount || 0, 10) > 0;
    const booted = await waitFor(
        () => (!expectedCards || page.cardIds().length > 0) && server.inflight === 0
    );
    if (!booted || scriptErrors.length) {
        throw new Error(`页面启动失败：${scriptErrors.join(' | ') || page.listHtml().slice(0, 200)}`);
    }
    // 启动期请求不计入各场景的断言
    server.log.length = 0;
    return page;
}

module.exports = { bootPage, waitFor, unhandledRejections };
