/* 控制台亮色 / 暗色主题运行时。
 *
 * 偏好存 localStorage.console_theme：'system' | 'light' | 'dark'（缺省/非法一律 system）。
 * 首帧由模板内联脚本（_theme_head.html）同步应用；本文件负责：
 * - 点击 [data-theme-option] 立即切换并持久化；
 * - 偏好为 system 时跟随 matchMedia 实时变化；
 * - 监听 storage 事件，让其他标签页的改动同步到本页；
 * - 同步控件的 aria-pressed 与 .is-active。
 * 不读写其他 localStorage 键，不依赖任何现有模块。
 */
(function () {
    'use strict';

    var STORAGE_KEY = 'console_theme';
    var preference = 'system';
    var mediaQuery = null;

    try {
        mediaQuery = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
    } catch (error) {
        mediaQuery = null;
    }

    function normalize(value) {
        return value === 'light' || value === 'dark' ? value : 'system';
    }

    function readPreference() {
        try {
            return normalize(window.localStorage.getItem(STORAGE_KEY));
        } catch (error) {
            return 'system';
        }
    }

    function writePreference(value) {
        try {
            window.localStorage.setItem(STORAGE_KEY, value);
        } catch (error) {
            /* 隐私模式等场景下写入失败：仅本次会话生效 */
        }
    }

    function systemTheme() {
        return mediaQuery && mediaQuery.matches ? 'dark' : 'light';
    }

    function resolve(preferenceValue) {
        return preferenceValue === 'system' ? systemTheme() : preferenceValue;
    }

    function apply(preferenceValue) {
        var theme = resolve(preferenceValue);
        var root = document.documentElement;
        root.setAttribute('data-theme', theme);
        root.style.colorScheme = theme;
        return theme;
    }

    function syncControls(preferenceValue) {
        var selects = document.querySelectorAll('[data-theme-select]');
        for (var selectIndex = 0; selectIndex < selects.length; selectIndex += 1) {
            selects[selectIndex].value = preferenceValue;
        }
        var buttons = document.querySelectorAll('[data-theme-option]');
        for (var index = 0; index < buttons.length; index += 1) {
            var button = buttons[index];
            var isActive = normalize(button.getAttribute('data-theme-option')) === preferenceValue;
            button.setAttribute('aria-pressed', isActive ? 'true' : 'false');
            if (isActive) {
                button.classList.add('is-active');
            } else {
                button.classList.remove('is-active');
            }
        }
    }

    function setPreference(value) {
        preference = normalize(value);
        apply(preference);
        syncControls(preference);
    }

    function handleSystemChange() {
        if (preference === 'system') {
            apply(preference);
        }
    }

    function handleStorage(event) {
        if (event.key !== null && event.key !== STORAGE_KEY) {
            return;
        }
        setPreference(readPreference());
    }

    function handleOptionClick(event) {
        var target = event.target;
        while (target && target !== document) {
            if (target.nodeType === 1 && target.hasAttribute('data-theme-option')) {
                var value = normalize(target.getAttribute('data-theme-option'));
                setPreference(value);
                writePreference(value);
                return;
            }
            target = target.parentNode;
        }
    }

    function handleSelectChange(event) {
        var target = event.target;
        if (!target || !target.hasAttribute || !target.hasAttribute('data-theme-select')) {
            return;
        }
        setPreference(target.value);
        writePreference(preference);
    }

    function updateFromEnvironment() {
        preference = readPreference();
        apply(preference);
        syncControls(preference);
    }

    function init() {
        updateFromEnvironment();
        if (mediaQuery) {
            if (mediaQuery.addEventListener) {
                mediaQuery.addEventListener('change', handleSystemChange);
            } else if (mediaQuery.addListener) {
                mediaQuery.addListener(handleSystemChange);
            }
        }
        window.addEventListener('storage', handleStorage);
        document.addEventListener('click', handleOptionClick);
        document.addEventListener('change', handleSelectChange);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.consoleTheme = {
        getPreference: function () {
            return preference;
        },
        resolve: resolve,
        apply: apply,
        setPreference: function (value) {
            setPreference(value);
            writePreference(preference);
        }
    };
})();
