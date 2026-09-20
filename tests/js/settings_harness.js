// 系统设置页（/admin/settings）的 jsdom 行为测试夹具。
//
// 页面 HTML 由 pytest 包装层（tests/test_settings_js_behavior.py）经真实路由渲染后
// 写入临时文件，通过环境变量传入；脚本按模板中的 <script> 顺序内联执行。
// 后端由 FakeSettingsServer 模拟：可注入 409/422 响应，并可按请求类型「扣住」
// 请求、由测试显式放行，从而确定地构造「保存进行中」等时序。
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');

const STATIC_DIR = process.env.CONSOLE_STATIC_DIR;
const PAGE_HTML = process.env.SETTINGS_PAGE_HTML;

const unhandledRejections = [];
process.on('unhandledRejection', (reason) => {
    unhandledRejections.push(String((reason && reason.stack) || reason));
});

function nextTick() {
    return new Promise((resolve) => setImmediate(resolve));
}

// 等待辅助函数分成两种语义，调用处一眼可辨：
// - waitFor(predicate)：等待条件成立，超时即抛错（测试失败），失败信息带上条件源码；
// - assertNever(predicate, windowMs)：确认条件在整个时间窗内始终不成立，
//   窗口内成立即抛错。「某事不应发生」的否定断言必须用这个，禁止用 waitFor 的返回值代替。
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
        const file = path.join(STATIC_DIR, cleanSrc.slice('/static/'.length));
        if (!fs.existsSync(file)) throw new Error(`模板引用的脚本不存在：${cleanSrc}`);
        const code = fs.readFileSync(file, 'utf8').replace(/<\/script>/gi, '<\\/script>');
        return `<script>${code}</script>`;
    });
}

const STEP_KEYS = [
    'summary',
    'source',
    'sentiment',
    'scoring',
    'external_filter',
    'beijing_gate',
    'duplicate_review',
];
const STEP_LABELS = {
    summary: '摘要生成',
    source: '来源识别',
    sentiment: '情感判断',
    scoring: '相关性评分',
    external_filter: '重要性过滤',
    beijing_gate: '京内判定',
    duplicate_review: '查重复核',
};

function defaultSteps() {
    return STEP_KEYS.map((key) => ({ key, display_name: STEP_LABELS[key] }));
}

function defaultSources() {
    return [
        { key: 'toutiao', display_name: '今日头条', daily_only: false, requires_accounts: true },
        { key: 'tencent', display_name: '腾讯新闻', daily_only: false, requires_accounts: true },
        { key: 'chinanews', display_name: '中新网', daily_only: false, requires_accounts: false },
        { key: 'btime', display_name: '北京时间', daily_only: false, requires_accounts: true },
        { key: 'beijinghao', display_name: '北京号', daily_only: false, requires_accounts: true },
        { key: 'bjrb', display_name: '北京日报', daily_only: true, requires_accounts: false },
        { key: 'ldwb', display_name: '劳动午报', daily_only: true, requires_accounts: false },
    ];
}

function defaultModelsValue() {
    return {
        default: 'deepseek/default-model',
        steps: {
            summary: { model: null, reasoning: false, endpoint: null },
            source: { model: null, reasoning: true, endpoint: null },
            sentiment: { model: null, reasoning: true, endpoint: null },
            // 旧数据形态：指定了模型但 endpoint 为 null（前端归一为默认接入点）
            scoring: { model: 'vendor/scoring-model', reasoning: true, endpoint: null },
            external_filter: { model: null, reasoning: false, endpoint: null },
            beijing_gate: { model: null, reasoning: false, endpoint: null },
            duplicate_review: { model: null, reasoning: false, endpoint: null },
        },
    };
}

// 已保存的接入点分区值（与后端 app_settings.llm_endpoints 的 value 同形，
// 不含 api_key_env_configured——那是顶层 endpoints 负载里的展示字段）
function defaultEndpointsValue() {
    return {
        default: 'openrouter',
        items: [
            {
                key: 'openrouter',
                label: 'OpenRouter',
                base_url: 'https://openrouter.ai/api/v1',
                api_key_env: 'LLM_API_KEY',
                api_style: 'openrouter',
                temperature_override: null,
            },
            {
                key: 'deepseek',
                label: 'DeepSeek',
                base_url: 'https://api.deepseek.com',
                api_key_env: 'DEEPSEEK_API_KEY',
                api_style: 'thinking',
                temperature_override: null,
            },
        ],
    };
}

// GET /api/admin/settings 顶层的 endpoints 负载（与 settings_service._endpoints_payload 同形）
function defaultEndpointsPayload() {
    return {
        default: 'openrouter',
        allowed_hosts: ['openrouter.ai', 'api.deepseek.com', 'open.bigmodel.cn'],
        items: [
            {
                key: 'openrouter',
                label: 'OpenRouter',
                base_url: 'https://openrouter.ai/api/v1',
                api_key_env: 'LLM_API_KEY',
                api_style: 'openrouter',
                temperature_override: null,
                api_key_env_configured: true,
            },
            {
                key: 'deepseek',
                label: 'DeepSeek',
                base_url: 'https://api.deepseek.com',
                api_key_env: 'DEEPSEEK_API_KEY',
                api_style: 'thinking',
                temperature_override: null,
                api_key_env_configured: false,
            },
        ],
    };
}

function defaultSections() {
    return {
        llm_endpoints: {
            value: defaultEndpointsValue(),
            version: 3,
            updated_at: '2026-01-10T08:29:00Z',
            updated_by: { user_id: 'u1', display_name: 'Wimp' },
        },
        llm_models: {
            value: defaultModelsValue(),
            version: 7,
            updated_at: '2026-01-10T08:30:00Z',
            updated_by: { user_id: 'u1', display_name: 'Wimp' },
        },
        score_keyword_bonuses: {
            value: [{ keyword: '专题Z', bonus: 10 }, { keyword: '专题A', bonus: 20 }],
            version: 11, updated_at: '2026-09-18T08:00:00Z', updated_by: { display_name: '编辑甲' },
        },
        education_keywords: {
            value: ['教育', '学校'], version: 12,
            updated_at: '2026-09-18T08:00:00Z', updated_by: { display_name: '编辑乙' },
        },
        beijing_keywords: {
            value: ['北京', '海淀'], version: 13,
            updated_at: '2026-09-18T08:00:00Z', updated_by: { display_name: '编辑丙' },
        },
        source_aliases: {
            value: { suffixes: ['客户端', '网'], aliases: { '北青': '北京青年报' } }, version: 14,
            updated_at: '2026-09-18T08:00:00Z', updated_by: { display_name: '编辑丁' },
        },
        crawl_sources: {
            value: ['toutiao', 'chinanews'],
            version: 4,
            updated_at: '2026-01-10T08:31:00Z',
            updated_by: { user_id: 'u1', display_name: 'Wimp' },
        },
    };
}

// 「有 endpoint、无 model」的步骤形态：正常界面产不出这种状态，
// 但库里可能存在手改出来的行（页面加载后不碰该行直接保存会踩空模型拦截）
function sectionsWithBareEndpointStep() {
    const sections = defaultSections();
    sections.llm_models.value.steps.scoring = {
        model: null,
        reasoning: true,
        endpoint: 'deepseek',
    };
    return sections;
}

function makeAccount(overrides = {}) {
    return {
        id: overrides.id || `acc-${Math.random().toString(36).slice(2, 10)}`,
        source: 'toutiao',
        normalized_identifier: 'account-1',
        original_input: 'account-1',
        profile_url: 'https://example.com/account-1',
        display_name: null,
        display_name_error: null,
        enabled: true,
        created_at: '2026-01-09T02:00:00Z',
        created_by_display_name: 'Wimp',
        ...overrides,
    };
}

function defaultAccounts() {
    return {
        toutiao: [
            makeAccount({
                id: 'acc-toutiao-1',
                source: 'toutiao',
                normalized_identifier: 'toutiao-one',
                original_input: 'https://www.toutiao.com/c/user/toutiao-one/',
                profile_url: 'https://www.toutiao.com/c/user/toutiao-one/',
                display_name: '头条一号',
            }),
        ],
        tencent: [],
        btime: [],
        beijinghao: [],
    };
}

class FakeSettingsServer {
    constructor(options = {}) {
        this.sections = options.sections === undefined ? defaultSections() : options.sections;
        this.sources = options.sources || defaultSources();
        this.steps = options.steps || defaultSteps();
        this.endpoints = options.endpoints === undefined
            ? defaultEndpointsPayload()
            : options.endpoints;
        this.environment = options.environment || {
            llm_api_key_configured: true,
            embedding_model: 'BAAI/bge-m3',
        };
        this.accounts = options.accounts || defaultAccounts();
        // 环境变量重载的定制行为：reloadEnvBehavior() 在返回前执行，测试用它
        // 模拟「服务器端 .env 变化后的重载结果」（如某接入点 Key 由未配置变为已配置）；
        // envVarCount 是响应里的 loaded_env_vars 回显
        this.reloadEnvBehavior = options.reloadEnvBehavior || null;
        this.envVarCount = options.envVarCount === undefined ? 24 : options.envVarCount;
        // 分区保存的定制响应：{ [section]: { status, payload } }，命中一次后失效
        this.saveBehavior = {};
        this.testBehavior = null;
        this.addBehavior = null;
        this.log = [];
        this.inflight = 0;
        this.holds = {};
        this.held = [];
        // 支持经 serverOptions.failNext / throwNext 在 boot 前注入失败
        // （例如让初始化时的账号概览加载失败，构造「账号数未知」徽标态）
        this.failNext = options.failNext || {};
        // 与 failNext 对齐的异常注入：按请求 kind 计数、消费一次，让 fetch 直接抛错，
        // 覆盖「网络异常」而非「HTTP 错误状态码」的失败路径
        this.throwNext = {};
        this.accountSeq = 0;
        // refresh-names 的可定制行为：refreshBehavior(id, attempt) 返回
        // 'skipped' / { status: 'failed', error } / { name } / null（走默认解析）。
        // attempt 是该 id 第几次被尝试，便于构造「首次 skipped、重试成功」的时序。
        this.refreshBehavior = null;
        this.refreshAttempts = {};
        // DELETE 的可定制行为：deleteBehavior(id) 返回 null（走默认删除）或
        // { status, detail }（按该响应回复，不动数据），便于构造「第 2 个 id 删除
        // 失败、第 3 个 404、其余成功」这类按 id 定制的时序——failNext 按 kind
        // 计数，无法表达这种序列
        this.deleteBehavior = null;
    }

    classify(url, method) {
        const pathname = url.pathname;
        if (pathname === '/api/admin/settings') return 'get-settings';
        if (pathname === '/api/admin/settings/environment/reload') return 'reload-env';
        if (pathname === '/api/admin/settings/llm_models/test') return 'model-test';
        if (pathname === '/api/admin/settings/llm_models') return 'save-models';
        if (pathname === '/api/admin/settings/llm_endpoints') return 'save-endpoints';
        if (pathname === '/api/admin/settings/crawl_sources') return 'save-sources';
        const dictionaryMatch = pathname.match(/^\/api\/admin\/settings\/(score_keyword_bonuses|education_keywords|beijing_keywords|source_aliases)$/);
        if (dictionaryMatch) return `save-${dictionaryMatch[1]}`;
        if (pathname === '/api/admin/crawl-accounts') {
            return method === 'POST' ? 'add-account' : 'list-accounts';
        }
        if (pathname === '/api/admin/crawl-accounts/preview') return 'preview-accounts';
        if (pathname === '/api/admin/crawl-accounts/bulk') return 'bulk-accounts';
        if (pathname === '/api/admin/crawl-accounts/refresh-names') return 'refresh-names';
        if (pathname.startsWith('/api/admin/crawl-accounts/')) {
            return method === 'DELETE' ? 'delete-account' : 'patch-account';
        }
        return 'other';
    }

    parseLine(source, cleaned) {
        if (cleaned.includes('invalid')) {
            throw new Error(`不认识的输入格式：${cleaned}`);
        }
        return {
            source,
            normalized_identifier: cleaned,
            original_input: cleaned,
            profile_url: `https://example.com/${encodeURIComponent(cleaned)}`,
        };
    }

    previewItems(source, text) {
        const existing = new Set(
            (this.accounts[source] || []).map((item) => item.normalized_identifier),
        );
        const seen = new Set();
        const results = [];
        text.split('\n').forEach((rawLine, index) => {
            const cleaned = rawLine.trim();
            if (!cleaned || cleaned.startsWith('#')) return;
            const base = { line_number: index + 1, input: cleaned };
            let parsed;
            try {
                parsed = this.parseLine(source, cleaned);
            } catch (error) {
                results.push({ ...base, status: 'invalid', error: error.message });
                return;
            }
            const identifier = parsed.normalized_identifier;
            let status;
            if (existing.has(identifier)) status = 'existing_duplicate';
            else if (seen.has(identifier)) status = 'batch_duplicate';
            else {
                status = 'addable';
                seen.add(identifier);
            }
            results.push({ ...base, ...parsed, status, error: null });
        });
        return results;
    }

    findAccount(id) {
        for (const source of Object.keys(this.accounts)) {
            const account = this.accounts[source].find((item) => item.id === id);
            if (account) return account;
        }
        return null;
    }

    // 对齐后端 refresh_account_names：failed 时 account 为 null，error 带原因；
    // 解析成功/未变时返回完整账号行
    refreshResultFor(id) {
        this.refreshAttempts[id] = (this.refreshAttempts[id] || 0) + 1;
        const attempt = this.refreshAttempts[id];
        const verdict = this.refreshBehavior ? this.refreshBehavior(id, attempt) : null;
        if (verdict === 'skipped') {
            return { id, status: 'skipped', account: null, error: '名称刷新已达到 30 秒预算' };
        }
        const account = this.findAccount(id);
        if (!account) {
            return { id, status: 'failed', account: null, error: '账号不存在' };
        }
        if (verdict && verdict.status === 'failed') {
            account.display_name = null;
            account.display_name_error = verdict.error || '名称解析失败';
            return { id, status: 'failed', account: null, error: account.display_name_error };
        }
        const name = (verdict && verdict.name) || `自动名称-${account.normalized_identifier}`;
        const status = name === account.display_name ? 'unchanged' : 'resolved';
        account.display_name = name;
        account.display_name_error = null;
        return { id, status, account: { ...account }, error: null };
    }

    respond(url, method, body) {
        const pathname = url.pathname;
        if (pathname === '/api/admin/settings') {
            return [200, {
                sections: this.sections,
                sources: this.sources,
                steps: this.steps,
                environment: this.environment,
                endpoints: this.endpoints,
            }];
        }
        if (pathname === '/api/admin/settings/environment/reload' && method === 'POST') {
            if (this.reloadEnvBehavior) this.reloadEnvBehavior();
            return [200, { reloaded: true, loaded_env_vars: this.envVarCount }];
        }
        if (pathname === '/api/admin/settings/llm_models/test') {
            if (this.testBehavior) {
                const behavior = this.testBehavior;
                this.testBehavior = null;
                return [behavior.status, behavior.payload];
            }
            // endpoint 回显：响应带上请求里的 endpoint，便于对照请求体断言
            return [200, {
                success: true,
                elapsed_ms: 120,
                error: null,
                endpoint: body && body.endpoint !== undefined ? body.endpoint : null,
            }];
        }
        const saveMatch = pathname.match(
            /^\/api\/admin\/settings\/(llm_models|llm_endpoints|crawl_sources|score_keyword_bonuses|education_keywords|beijing_keywords|source_aliases)$/,
        );
        if (saveMatch && method === 'PUT') {
            const section = saveMatch[1];
            const behavior = this.saveBehavior[section];
            if (behavior) {
                delete this.saveBehavior[section];
                return [behavior.status, behavior.payload];
            }
            const current = this.sections[section];
            if (!current) return [404, { detail: '配置对象不存在' }];
            if (body.expected_version !== current.version) {
                return [409, { detail: `配置版本冲突：当前版本为 ${current.version}` }];
            }
            let value = body.value;
            if (section === 'score_keyword_bonuses') {
                value = value.map((item) => ({ keyword: item.keyword.trim(), bonus: item.bonus }));
            } else if (['education_keywords', 'beijing_keywords'].includes(section)) {
                value = [...new Set(value.map((item) => item.trim()).filter(Boolean))];
            }
            const updated = {
                value,
                version: current.version + 1,
                updated_at: '2026-01-11T00:00:00Z',
            };
            this.sections[section] = { ...current, ...updated };
            return [200, { item: updated }];
        }
        if (pathname === '/api/admin/crawl-accounts' && method === 'GET') {
            const source = url.searchParams.get('source');
            return [200, { items: this.accounts[source] || [] }];
        }
        if (pathname === '/api/admin/crawl-accounts' && method === 'POST') {
            if (this.addBehavior) {
                const behavior = this.addBehavior;
                this.addBehavior = null;
                return [behavior.status, behavior.payload];
            }
            let parsed;
            try {
                parsed = this.parseLine(body.source, body.text.trim());
            } catch (error) {
                return [422, { detail: error.message }];
            }
            const existing = (this.accounts[body.source] || [])
                .some((item) => item.normalized_identifier === parsed.normalized_identifier);
            if (existing) return [409, { detail: '账号已存在' }];
            const account = makeAccount({
                id: `acc-new-${this.accountSeq += 1}`,
                // 后端在单个新增时同步解析名称
                display_name: `名称-${parsed.normalized_identifier}`,
                ...parsed,
            });
            this.accounts[body.source] = [...(this.accounts[body.source] || []), account];
            return [201, { item: account }];
        }
        if (pathname === '/api/admin/crawl-accounts/preview') {
            return [200, { items: this.previewItems(body.source, body.text) }];
        }
        if (pathname === '/api/admin/crawl-accounts/bulk') {
            const items = this.previewItems(body.source, body.text);
            items.forEach((item) => {
                if (item.status !== 'addable') return;
                const account = makeAccount({
                    id: `acc-bulk-${this.accountSeq += 1}`,
                    source: item.source,
                    normalized_identifier: item.normalized_identifier,
                    original_input: item.original_input,
                    profile_url: item.profile_url,
                });
                this.accounts[item.source] = [...(this.accounts[item.source] || []), account];
                item.account = account;
            });
            return [200, { items }];
        }
        if (pathname === '/api/admin/crawl-accounts/refresh-names' && method === 'POST') {
            return [200, { items: body.account_ids.map((id) => this.refreshResultFor(id)) }];
        }
        const accountMatch = pathname.match(/^\/api\/admin\/crawl-accounts\/([^/]+)$/);
        if (accountMatch) {
            const id = decodeURIComponent(accountMatch[1]);
            const source = Object.keys(this.accounts)
                .find((key) => this.accounts[key].some((item) => item.id === id));
            const account = source
                ? this.accounts[source].find((item) => item.id === id)
                : null;
            if (!account) return [404, { detail: '配置对象不存在' }];
            if (method === 'PATCH') {
                if (Object.prototype.hasOwnProperty.call(body, 'display_name')) {
                    account.display_name = body.display_name || null;
                }
                if (Object.prototype.hasOwnProperty.call(body, 'enabled')) {
                    account.enabled = body.enabled;
                }
                return [200, { item: account }];
            }
            if (method === 'DELETE') {
                const verdict = this.deleteBehavior ? this.deleteBehavior(id) : null;
                if (verdict) {
                    // 404 的语义是库里已无此账号：列表也不应再返回它
                    if (verdict.status === 404) {
                        this.accounts[source] = this.accounts[source]
                            .filter((item) => item.id !== id);
                    }
                    return [verdict.status, { detail: verdict.detail || '删除失败' }];
                }
                this.accounts[source] = this.accounts[source].filter((item) => item.id !== id);
                return [200, { item: account }];
            }
        }
        return [404, { detail: `未模拟的接口：${method} ${pathname}` }];
    }

    async fetch(input, options = {}) {
        const url = new URL(String(input), 'http://localhost/');
        const method = String(options.method || 'GET').toUpperCase();
        const kind = this.classify(url, method);
        const body = options.body ? JSON.parse(options.body) : null;
        const entry = { kind, path: url.pathname, method, body, status: null, done: false };
        // 账号级请求把 id 提到 entry.accountId，测试可按顺序读出每次 PATCH/DELETE 的目标
        if (kind === 'delete-account' || kind === 'patch-account') {
            entry.accountId = decodeURIComponent(url.pathname.split('/').pop());
        }
        this.log.push(entry);
        this.inflight += 1;
        const willFail = (this.failNext[kind] || 0) > 0;
        if (willFail) this.failNext[kind] -= 1;
        const willThrow = (this.throwNext[kind] || 0) > 0;
        if (willThrow) this.throwNext[kind] -= 1;
        try {
            if (willThrow) throw new Error('injected network failure');
            if ((this.holds[kind] || 0) > 0) {
                this.holds[kind] -= 1;
                await new Promise((release) => this.held.push({ kind, release }));
            } else {
                await nextTick();
            }
            const [status, payload] = willFail
                ? [500, { detail: 'injected failure' }]
                : this.respond(url, method, body);
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

    hold(kind, count = 1) {
        this.holds[kind] = (this.holds[kind] || 0) + count;
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

async function bootPage(serverOptions = {}) {
    if (!PAGE_HTML) throw new Error('缺少 SETTINGS_PAGE_HTML（应由 pytest 包装层提供）');
    const server = new FakeSettingsServer(serverOptions);
    const scriptErrors = [];
    const virtualConsole = new VirtualConsole();
    virtualConsole.on('jsdomError', (error) => scriptErrors.push(error.message));
    const dom = new JSDOM(inlineScripts(fs.readFileSync(PAGE_HTML, 'utf8')), {
        url: `http://localhost/admin/settings${serverOptions.hash || ''}`,
        runScripts: 'dangerously',
        pretendToBeVisual: true,
        virtualConsole,
        beforeParse(window) {
            window.fetch = (input, options) => server.fetch(input, options);
            window.Response = Response;
            window.Request = Request;
            window.Headers = Headers;
            window.scrollTo = () => {};
        },
    });
    const window = dom.window;
    const page = {
        window,
        document: window.document,
        server,
        panel(name) {
            return window.document.getElementById(`settings-panel-${name}`);
        },
        clickTab(name) {
            window.document.querySelector(`[data-settings-tab="${name}"]`).click();
        },
        modelsRow(step) {
            return window.document.querySelector(`#models-steps-body tr[data-step="${step}"]`);
        },
        close() {
            window.close();
        },
    };
    const booted = await waitFor(
        () => server.inflight === 0
            && page.panel('models').children.length > 0
            && page.panel('sources').children.length > 0,
    );
    if (!booted || scriptErrors.length) {
        throw new Error(`页面启动失败：${scriptErrors.join(' | ') || '面板未渲染'}`);
    }
    server.log.length = 0;
    return page;
}

module.exports = {
    bootPage,
    waitFor,
    assertNever,
    unhandledRejections,
    defaultSections,
    defaultAccounts,
    defaultEndpointsPayload,
    makeAccount,
    sectionsWithBareEndpointStep,
};
