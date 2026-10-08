// 值班汇总页（/admin/duty-summary）的轻量 jsdom 夹具。
//
// 该页路由渲染时会写班次库，不适合像人工筛选页那样「真实路由渲染 → 注入
// 整页 HTML」；而本次被测的搜索过滤（state.searchQuery → getVisibleItems）
// 只依赖 core.js 的全局 state 与 render.js 的纯过滤函数，三个脚本在加载期
// 均不解引用任何 DOM 元素。因此用空 body 的 JSDOM 按真实加载顺序内联脚本，
// 测试直接读写 state 并调用 getVisibleItems()。
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');

const STATIC_DIR = process.env.CONSOLE_STATIC_DIR;
const SCRIPT_FILES = [
    'js/duty_summary/core.js',
    'js/duty_summary/utils.js',
    'js/duty_summary/render.js',
];

function bootDutySummaryCore() {
    if (!STATIC_DIR) throw new Error('CONSOLE_STATIC_DIR 未设置');
    const scripts = SCRIPT_FILES.map((rel) => {
        const file = path.join(STATIC_DIR, rel);
        if (!fs.existsSync(file)) throw new Error(`脚本不存在：${rel}`);
        const code = fs.readFileSync(file, 'utf8').replace(/<\/script>/gi, '<\\/script>');
        return `<script>${code}</script>`;
    }).join('\n');
    const scriptErrors = [];
    const virtualConsole = new VirtualConsole();
    virtualConsole.on('jsdomError', (error) => scriptErrors.push(error.message));
    const dom = new JSDOM(`<!doctype html><html><body>${scripts}</body></html>`, {
        runScripts: 'dangerously',
        virtualConsole,
    });
    return {
        window: dom.window,
        scriptErrors,
        setQuery(query) {
            dom.window.eval(`state.searchQuery = ${JSON.stringify(query)};`);
        },
        setItems(items) {
            dom.window.eval(`state.items = ${JSON.stringify(items)};`);
        },
        visibleIds() {
            return JSON.parse(
                dom.window.eval(
                    'JSON.stringify(getVisibleItems().map((item) => item.article_id))'
                )
            );
        },
    };
}

module.exports = { bootDutySummaryCore };
