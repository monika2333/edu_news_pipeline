// 控制台主题（亮色 / 暗色 / 跟随系统）的 jsdom 行为测试夹具。
// 页面由 pytest 包装层经真实路由渲染到临时文件，路径经环境变量传入；
// theme.js 从 CONSOLE_STATIC_DIR 读取后内联执行，其余外链脚本一律移除。
'use strict';

const fs = require('fs');
const path = require('path');

const { JSDOM } = require('jsdom');

const STATIC_DIR = process.env.CONSOLE_STATIC_DIR
    || path.join(__dirname, '..', '..', 'src', 'console', 'web_static');

function readThemeScript() {
    return fs.readFileSync(path.join(STATIC_DIR, 'js', 'theme.js'), 'utf8');
}

function readPage(envName) {
    const pagePath = process.env[envName];
    if (!pagePath) {
        throw new Error(`缺少环境变量 ${envName}`);
    }
    return fs.readFileSync(pagePath, 'utf8');
}

function createMatchMedia(initialDark) {
    const listeners = [];
    const mql = {
        matches: Boolean(initialDark),
        media: '(prefers-color-scheme: dark)',
        onchange: null,
        addEventListener(type, listener) {
            if (type === 'change') {
                listeners.push(listener);
            }
        },
        removeEventListener(type, listener) {
            if (type !== 'change') {
                return;
            }
            const index = listeners.indexOf(listener);
            if (index >= 0) {
                listeners.splice(index, 1);
            }
        },
        addListener(listener) {
            listeners.push(listener);
        },
        removeListener(listener) {
            const index = listeners.indexOf(listener);
            if (index >= 0) {
                listeners.splice(index, 1);
            }
        },
        setMatches(value) {
            mql.matches = Boolean(value);
            listeners.forEach((listener) => {
                listener({ matches: mql.matches, media: mql.media });
            });
        },
        dispatchEvent() {
            return true;
        },
        listenerCount() {
            return listeners.length;
        },
    };
    return mql;
}

const THEME_SCRIPT_MARKER = '<!--THEME_JS_INLINE_PLACEHOLDER-->';

function stripScripts(html) {
    return html
        .replace(
            /\s*<script\s+defer\s+src="\/static\/js\/theme\.js\?v=[^"]*"><\/script>/g,
            THEME_SCRIPT_MARKER
        )
        .replace(/\s*<script\s+src="[^"]*"><\/script>/g, '');
}

function waitForLoad(window) {
    if (window.document.readyState === 'complete') {
        return Promise.resolve();
    }
    return new Promise((resolve) => {
        window.addEventListener('load', () => resolve(), { once: true });
    });
}

/**
 * 加载一个页面快照。
 * options.storedPreference：解析前写入 localStorage.console_theme（undefined 表示不写）。
 * options.systemDark：matchMedia('(prefers-color-scheme: dark)').matches 初值。
 * options.inlineThemeJs：false 时只保留首帧内联引导脚本，用于验证「第一帧」。
 */
async function loadPage(envName, options = {}) {
    const { storedPreference, systemDark = false, inlineThemeJs = true } = options;
    const themeScript = readThemeScript();
    let html = stripScripts(readPage(envName));
    if (inlineThemeJs) {
        html = html.replace(THEME_SCRIPT_MARKER, `<script>${themeScript}</script>`);
    } else {
        html = html.replace(THEME_SCRIPT_MARKER, '');
    }
    const matchMedia = createMatchMedia(systemDark);
    const dom = new JSDOM(html, {
        runScripts: 'dangerously',
        url: 'http://localhost/manual_filter',
        pretendToBeVisual: true,
        beforeParse(window) {
            window.matchMedia = () => matchMedia;
            if (storedPreference !== undefined) {
                window.localStorage.setItem('console_theme', storedPreference);
            }
        },
    });
    await waitForLoad(dom.window);
    return {
        dom,
        window: dom.window,
        document: dom.window.document,
        matchMedia,
        close() {
            dom.window.close();
        },
    };
}

function themeOf(document) {
    return document.documentElement.getAttribute('data-theme');
}

function optionButtons(document) {
    return Array.from(document.querySelectorAll('[data-theme-option]'));
}

function pressedStates(document) {
    const states = {};
    optionButtons(document).forEach((button) => {
        states[button.getAttribute('data-theme-option')] = button.getAttribute('aria-pressed');
    });
    return states;
}

function activeOptions(document) {
    return optionButtons(document)
        .filter((button) => button.classList.contains('is-active'))
        .map((button) => button.getAttribute('data-theme-option'));
}

function dispatchStorage(window, key, newValue) {
    const event = new window.StorageEvent('storage', {
        key,
        newValue,
        storageArea: window.localStorage,
        url: window.location.href,
    });
    window.dispatchEvent(event);
}

module.exports = {
    STATIC_DIR,
    loadPage,
    readPage,
    themeOf,
    optionButtons,
    pressedStates,
    activeOptions,
    dispatchStorage,
};
