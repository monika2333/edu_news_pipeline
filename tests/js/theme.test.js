// 控制台主题行为测试：默认/非法/已存偏好、system 实时跟随、手动模式隔离、
// 点击持久化与控件状态、跨标签页 storage 同步、无账号菜单页面遵循偏好。
// 页面由 pytest 包装层经真实路由渲染，theme.js 内联执行。
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const {
    loadPage,
    themeOf,
    themeSelect,
    selectedTheme,
    dispatchStorage,
} = require('./theme_harness.js');

async function withPage(envName, options, callback) {
    const page = await loadPage(envName, options);
    try {
        await callback(page);
    } finally {
        page.close();
    }
}

function changeTheme(page, value) {
    const select = themeSelect(page.document);
    assert.ok(select, '缺少外观下拉框');
    select.value = value;
    select.dispatchEvent(new page.window.Event('change', { bubbles: true }));
}

test('无偏好时默认跟随系统（系统亮/暗各一）', async () => {
    await withPage('THEME_PAGE_ADMIN_HTML', { systemDark: false }, (page) => {
        assert.equal(themeOf(page.document), 'light');
        assert.equal(page.document.documentElement.style.colorScheme, 'light');
    });
    await withPage('THEME_PAGE_ADMIN_HTML', { systemDark: true }, (page) => {
        assert.equal(themeOf(page.document), 'dark');
        assert.equal(page.document.documentElement.style.colorScheme, 'dark');
        assert.equal(selectedTheme(page.document), 'system');
    });
});

test('非法值在第一帧就回退到 system，而不是固定暗色', async () => {
    await withPage(
        'THEME_PAGE_ADMIN_HTML',
        { storedPreference: 'banana', systemDark: false, inlineThemeJs: false },
        (page) => {
            assert.equal(themeOf(page.document), 'light');
            assert.equal(page.document.documentElement.style.colorScheme, 'light');
        }
    );
    await withPage(
        'THEME_PAGE_ADMIN_HTML',
        { storedPreference: 'sepia', systemDark: true, inlineThemeJs: false },
        (page) => {
            assert.equal(themeOf(page.document), 'dark');
        }
    );
});

test('已存 light/dark 覆盖系统主题', async () => {
    await withPage(
        'THEME_PAGE_ADMIN_HTML',
        { storedPreference: 'dark', systemDark: false },
        (page) => {
            assert.equal(themeOf(page.document), 'dark');
            assert.equal(selectedTheme(page.document), 'dark');
        }
    );
    await withPage(
        'THEME_PAGE_ADMIN_HTML',
        { storedPreference: 'light', systemDark: true },
        (page) => {
            assert.equal(themeOf(page.document), 'light');
            assert.equal(selectedTheme(page.document), 'light');
        }
    );
});

test('system 模式跟随系统主题实时变化，不刷新页面', async () => {
    await withPage(
        'THEME_PAGE_ADMIN_HTML',
        { storedPreference: 'system', systemDark: false },
        (page) => {
            assert.equal(themeOf(page.document), 'light');
            page.matchMedia.setMatches(true);
            assert.equal(themeOf(page.document), 'dark');
            assert.equal(page.document.documentElement.style.colorScheme, 'dark');
            page.matchMedia.setMatches(false);
            assert.equal(themeOf(page.document), 'light');
        }
    );
});

test('手动 light/dark 模式不受系统主题变化影响', async () => {
    await withPage(
        'THEME_PAGE_ADMIN_HTML',
        { storedPreference: 'light', systemDark: true },
        (page) => {
            assert.equal(themeOf(page.document), 'light');
            page.matchMedia.setMatches(false);
            assert.equal(themeOf(page.document), 'light');
            page.matchMedia.setMatches(true);
            assert.equal(themeOf(page.document), 'light');
        }
    );
    await withPage(
        'THEME_PAGE_ADMIN_HTML',
        { storedPreference: 'dark', systemDark: false },
        (page) => {
            page.matchMedia.setMatches(true);
            assert.equal(themeOf(page.document), 'dark');
            page.matchMedia.setMatches(false);
            assert.equal(themeOf(page.document), 'dark');
        }
    );
});

test('切换外观下拉框立即生效、写入 localStorage 并同步选中值', async () => {
    await withPage(
        'THEME_PAGE_ADMIN_HTML',
        { storedPreference: 'system', systemDark: false },
        (page) => {
            changeTheme(page, 'dark');
            assert.equal(themeOf(page.document), 'dark');
            assert.equal(page.window.localStorage.getItem('console_theme'), 'dark');
            assert.equal(selectedTheme(page.document), 'dark');

            changeTheme(page, 'light');
            assert.equal(themeOf(page.document), 'light');
            assert.equal(page.window.localStorage.getItem('console_theme'), 'light');
            assert.equal(selectedTheme(page.document), 'light');

            page.matchMedia.setMatches(true);
            changeTheme(page, 'system');
            assert.equal(themeOf(page.document), 'dark');
            assert.equal(page.window.localStorage.getItem('console_theme'), 'system');
            assert.equal(selectedTheme(page.document), 'system');
        }
    );
});

test('其他标签页修改 console_theme 时经 storage 事件同步主题与控件', async () => {
    await withPage(
        'THEME_PAGE_ADMIN_HTML',
        { storedPreference: 'system', systemDark: false },
        (page) => {
            assert.equal(themeOf(page.document), 'light');

            page.window.localStorage.setItem('console_theme', 'dark');
            dispatchStorage(page.window, 'console_theme', 'dark');
            assert.equal(themeOf(page.document), 'dark');
            assert.equal(selectedTheme(page.document), 'dark');

            page.window.localStorage.clear();
            dispatchStorage(page.window, null, null);
            assert.equal(themeOf(page.document), 'light');
            assert.equal(selectedTheme(page.document), 'system');
        }
    );
});

test('登录页没有外观控件，但遵循已存偏好', async () => {
    await withPage(
        'THEME_PAGE_LOGIN_HTML',
        { storedPreference: 'dark', systemDark: false },
        (page) => {
            assert.equal(themeOf(page.document), 'dark');
            assert.equal(themeSelect(page.document), null);
        }
    );
});

test('值班编辑页也有外观控件且可切换', async () => {
    await withPage(
        'THEME_PAGE_DUTY_HTML',
        { storedPreference: 'system', systemDark: false },
        (page) => {
            assert.ok(themeSelect(page.document), '缺少外观下拉框');
            changeTheme(page, 'dark');
            assert.equal(themeOf(page.document), 'dark');
            assert.equal(page.window.localStorage.getItem('console_theme'), 'dark');
        }
    );
});

test('主题切换不读写 console_theme 之外的 localStorage 键', async () => {
    await withPage(
        'THEME_PAGE_ADMIN_HTML',
        { storedPreference: 'system', systemDark: false },
        (page) => {
            page.window.localStorage.setItem('sidebar_collapsed', 'true');
            page.window.localStorage.setItem('search_filters', '{"bucket":"internal"}');
            changeTheme(page, 'dark');
            assert.equal(page.window.localStorage.getItem('sidebar_collapsed'), 'true');
            assert.equal(
                page.window.localStorage.getItem('search_filters'),
                '{"bucket":"internal"}'
            );
            assert.equal(page.window.localStorage.getItem('console_theme'), 'dark');
        }
    );
});
