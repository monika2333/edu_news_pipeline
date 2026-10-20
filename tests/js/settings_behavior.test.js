// 系统设置页（/admin/settings）的 jsdom 行为测试。
// 覆盖验收场景 S1-S33 与芯片布局场景 N1-N23；其中 S7、S17 随排序模式删除，
// S9、S16、S29、S30、N5、N16 随旧数据源页形态删除
// （批量粘贴、展开抽屉、筛选框、面板会话这些被测形态不复存在）。
// E1-E17 覆盖接入点管理（endpoints.js）与每步骤接入点选择（models_tab.js），
// E18-E19 覆盖环境变量重载按钮。
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const {
    bootPage,
    waitFor,
    assertNever,
    defaultSections,
    defaultAccounts,
    makeAccount,
    sectionsWithBareEndpointStep,
    unhandledRejections,
} = require('./settings_harness.js');

function inputValue(page, el, value) {
    el.value = value;
    el.dispatchEvent(new page.window.Event('input', { bubbles: true }));
}

function sourceListOrder(page) {
    return [...page.document.querySelectorAll('#sources-list li[data-source]')]
        .map((item) => item.dataset.source);
}

function sourceListIndex(page, key) {
    return sourceListOrder(page).indexOf(key);
}

function sourceRow(page, key) {
    return page.document.querySelector(`li[data-source="${key}"]`);
}

// 指定来源的账号面板：面板随来源行始终平铺渲染，按 data-accounts-for 作用域查询
function accountsPanel(page, key) {
    return page.document
        .querySelector(`.source-accounts-panel[data-accounts-for="${key}"]`);
}

// 账号面板不再展开/收起；保留 helper 以维持既有调用点，等待面板就绪后返回
async function expandSource(page, key) {
    await waitFor(() => accountsPanel(page, key));
    return accountsPanel(page, key);
}

// 芯片布局下的常用取数：芯片（账号 id 全局唯一，不按面板区分）、
// 面板内的共享错误行、页面级待提交条
function accountChip(page, id) {
    return page.document.querySelector(`.account-chip[data-account-id="${id}"]`);
}

async function waitForChip(page, id) {
    await waitFor(() => accountChip(page, id));
    return accountChip(page, id);
}

function accountsPanelError(page, key) {
    return accountsPanel(page, key).querySelector('.accounts-panel-error');
}

// 待提交条在展开面板内，每个来源一条；计数只统计本来源的标记
function deleteBar(page, key) {
    const panel = accountsPanel(page, key);
    return panel ? panel.querySelector('.accounts-delete-bar') : null;
}

function deleteBarCount(page, key) {
    const bar = deleteBar(page, key);
    return bar ? bar.querySelector('.accounts-delete-count').textContent : null;
}

// 没有页面级管理模式：操作按钮随面板始终可用，展开即可操作。
// 保留一个等待辅助，确保账号异步加载后芯片上的删除按钮已经可用
async function waitForAccountActions(page) {
    await waitFor(() => {
        const btn = page.document.querySelector('.account-chip-delete');
        return btn && !btn.disabled;
    });
}

test('S1：配置分区缺失时对应页签显示导入提示且不渲染编辑控件', async () => {
    const page = await bootPage({ sections: {} });
    try {
        const modelsNotice = page.panel('models').querySelector('.settings-import-notice');
        assert.ok(modelsNotice);
        assert.match(modelsNotice.textContent, /尚未导入/);
        assert.match(modelsNotice.textContent, /import-settings/);
        assert.equal(page.document.getElementById('btn-models-save'), null);
        assert.equal(page.document.getElementById('models-default-input'), null);

        const sourcesNotice = page.panel('sources').querySelector('.settings-import-notice');
        assert.ok(sourcesNotice);
        assert.equal(page.document.getElementById('sources-list'), null);
        // 账号管理并入数据源页签，分区缺失时没有来源行
        assert.equal(page.panel('sources').querySelector('li[data-source]'), null);
    } finally {
        page.close();
    }
});

test('S2：修改默认模型联动跟随默认提示，保存请求包含全部七个步骤与版本号', async () => {
    const page = await bootPage();
    try {
        const defaultInput = page.document.getElementById('models-default-input');
        assert.equal(defaultInput.value, 'deepseek/default-model');
        const summaryHint = page.modelsRow('summary').querySelector('.step-follow-hint');
        assert.match(
            summaryHint.textContent,
            /跟随默认（接入点：OpenRouter · 模型：deepseek\/default-model）/,
        );

        inputValue(page, defaultInput, 'new/default-x');
        assert.match(
            summaryHint.textContent,
            /跟随默认（接入点：OpenRouter · 模型：new\/default-x）/,
        );
        // 指定模型的步骤不显示跟随提示
        const scoringHint = page.modelsRow('scoring').querySelector('.step-follow-hint');
        assert.ok(scoringHint.hidden);

        page.document.getElementById('btn-models-save').click();
        await waitFor(() => page.server.requests('save-models').length === 1);
        const body = page.server.requests('save-models')[0].body;
        assert.equal(body.expected_version, 7);
        assert.equal(body.value.default, 'new/default-x');
        assert.deepEqual(Object.keys(body.value.steps).sort(), [
            'beijing_gate',
            'duplicate_review',
            'external_filter',
            'scoring',
            'sentiment',
            'source',
            'summary',
        ]);
        // 跟随默认的步骤必须保存为 null，而不是默认模型名
        assert.equal(body.value.steps.summary.model, null);
        assert.equal(body.value.steps.source.model, null);
        // 指定模型的步骤保留自己的模型名
        assert.equal(body.value.steps.scoring.model, 'vendor/scoring-model');
        assert.equal(body.value.steps.scoring.reasoning, true);

        await waitFor(() => page.server.requests('save-models')[0].done);
        await waitFor(() => page.document.getElementById('models-default-input') === null
            || page.document.getElementById('models-default-input').value === 'new/default-x');
    } finally {
        page.close();
    }
});

test('S3：测试按钮发送页面当前值，修改步骤后旧结果被清除', async () => {
    const page = await bootPage();
    try {
        const row = page.modelsRow('summary');
        const defaultInput = page.document.getElementById('models-default-input');
        inputValue(page, defaultInput, 'page/current-model');
        const reasoning = row.querySelector('.step-reasoning');
        reasoning.checked = true;
        reasoning.dispatchEvent(new page.window.Event('change', { bubbles: true }));

        row.querySelector('.step-test-btn').click();
        await waitFor(() => page.server.requests('model-test').length === 1);
        const body = page.server.requests('model-test')[0].body;
        // 必须是页面当前值：跟随默认取默认输入框当前内容，reasoning 取当前开关
        assert.equal(body.step, 'summary');
        assert.equal(body.model, 'page/current-model');
        assert.equal(body.reasoning, true);

        const result = row.querySelector('.step-test-result');
        await waitFor(() => result.textContent.includes('成功'));
        assert.match(result.textContent, /耗时/);

        reasoning.checked = false;
        reasoning.dispatchEvent(new page.window.Event('change', { bubbles: true }));
        assert.equal(result.textContent, '');
    } finally {
        page.close();
    }
});

test('S4：保存进行中再次点击保存不会发出第二个请求', async () => {
    const page = await bootPage();
    try {
        page.server.hold('save-models');
        const saveBtn = page.document.getElementById('btn-models-save');
        saveBtn.click();
        saveBtn.click();
        await waitFor(() => page.server.requests('save-models').length >= 1);
        // 保存进行中再次点击不应发出第二个请求
        await assertNever(() => page.server.requests('save-models').length > 1, 200);
        assert.ok(saveBtn.disabled);
        assert.match(
            page.document.getElementById('models-save-status').textContent,
            /保存中/,
        );
        page.server.release('save-models');
        await waitFor(() => page.server.requests('save-models')[0].done);
        assert.equal(page.server.requests('save-models').length, 1);
    } finally {
        page.close();
    }
});

test('S5：保存返回 422 时显示服务端原因并保留全部修改', async () => {
    const page = await bootPage();
    try {
        page.server.saveBehavior.llm_models = {
            status: 422,
            payload: { detail: '摘要生成模型测试失败：连接超时' },
        };
        const defaultInput = page.document.getElementById('models-default-input');
        inputValue(page, defaultInput, 'broken/model');

        page.document.getElementById('btn-models-save').click();
        await waitFor(() => page.server.requests('save-models').length === 1
            && page.server.requests('save-models')[0].done);
        const status = page.document.getElementById('models-save-status');
        await waitFor(() => status.textContent.includes('摘要生成模型测试失败：连接超时'));
        // 本地修改保留，保存按钮恢复可用
        assert.equal(defaultInput.value, 'broken/model');
        assert.equal(page.document.getElementById('btn-models-save').disabled, false);
        assert.ok(page.document.getElementById('btn-models-reload').hidden);
    } finally {
        page.close();
    }
});

test('S6：保存返回 409 时保留修改，仅点击「载入最新配置」后才覆盖本地值', async () => {
    const sections = defaultSections();
    const page = await bootPage({ sections });
    try {
        // 模拟另一处已经把默认模型改掉；detail 刻意不含「冲突」字样，
        // 保证下面的断言锁的是界面自己的冲突前缀，而不是 fixture 文本回显
        page.server.saveBehavior.llm_models = {
            status: 409,
            payload: { detail: '配置版本已变化：当前版本为 8' },
        };
        page.server.sections.llm_models = {
            ...page.server.sections.llm_models,
            value: { ...page.server.sections.llm_models.value, default: 'server/new-default' },
            version: 8,
        };
        const defaultInput = page.document.getElementById('models-default-input');
        inputValue(page, defaultInput, 'local/edit');

        page.document.getElementById('btn-models-save').click();
        await waitFor(() => page.server.requests('save-models').length === 1
            && page.server.requests('save-models')[0].done);
        const status = page.document.getElementById('models-save-status');
        await waitFor(() => status.textContent.startsWith('保存冲突：'));
        assert.match(status.textContent, /配置版本已变化：当前版本为 8/);
        // 本地修改未被覆盖，也没有自动重新拉取
        assert.equal(defaultInput.value, 'local/edit');
        assert.equal(page.server.requests('get-settings').length, 0);
        const reloadBtn = page.document.getElementById('btn-models-reload');
        assert.equal(reloadBtn.hidden, false);

        reloadBtn.click();
        await waitFor(
            () => page.server.requests('get-settings').length === 1,
        );
        await waitFor(
            () => page.document.getElementById('models-default-input').value === 'server/new-default',
        );
        assert.equal(
            page.document.getElementById('btn-models-reload').hidden,
            true,
        );
    } finally {
        page.close();
    }
});

test('S8：需要账号的来源启用数为 0 时徽标显示跳过提醒，徽标是纯展示不可点击', async () => {
    const sections = defaultSections();
    sections.crawl_sources.value = ['toutiao', 'tencent'];
    const page = await bootPage({ sections });
    try {
        page.clickTab('sources');
        const tencentItem = page.document
            .querySelector('#sources-list li[data-source="tencent"]');
        const badge = tencentItem.querySelector('.source-account-badge');
        assert.ok(badge.classList.contains('is-warning'));
        assert.match(badge.textContent, /启用 0 \/ 共 0/);
        assert.match(badge.textContent, /本轮会跳过该来源/);
        // 徽标不再可点：不是按钮，不带展开入口（账号面板始终平铺，无处可展开）
        assert.equal(badge.tagName, 'SPAN');
        assert.equal(badge.dataset.expandAccounts, undefined);

        const toutiaoBadge = page.document
            .querySelector('#sources-list li[data-source="toutiao"] .source-account-badge');
        assert.ok(!toutiaoBadge.classList.contains('is-warning'));
        assert.match(toutiaoBadge.textContent, /启用 1 \/ 共 1/);
        assert.equal(toutiaoBadge.tagName, 'SPAN');

        // 账号面板始终平铺，不依赖任何点击
        assert.ok(accountsPanel(page, 'tencent'));
    } finally {
        page.close();
    }
});

test('S10：启用开关 PATCH 失败时芯片开关恢复原状态并在共享错误行显示错误', async () => {
    const accounts = defaultAccounts();
    accounts.tencent = [makeAccount({
        id: 'acc-tencent-1',
        source: 'tencent',
        normalized_identifier: 'tencent-one',
        enabled: true,
    })];
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'tencent');
        const chip = await waitForChip(page, 'acc-tencent-1');
        const toggle = chip.querySelector('.account-enabled-toggle');
        assert.equal(toggle.checked, true);

        page.server.failNext['patch-account'] = 1;
        toggle.click();
        assert.equal(toggle.checked, false);
        await waitFor(() => page.server.requests('patch-account').length === 1
            && page.server.requests('patch-account')[0].done);
        await waitFor(() => toggle.checked === true);
        assert.match(accountsPanelError(page, 'tencent').textContent, /状态切换失败/);
    } finally {
        page.close();
    }
});

test('S10b：启用开关请求抛网络异常时芯片开关恢复原状态并在共享错误行显示错误', async () => {
    const accounts = defaultAccounts();
    accounts.tencent = [makeAccount({
        id: 'acc-tencent-1',
        source: 'tencent',
        normalized_identifier: 'tencent-one',
        enabled: true,
    })];
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'tencent');
        const chip = await waitForChip(page, 'acc-tencent-1');
        const toggle = chip.querySelector('.account-enabled-toggle');
        assert.equal(toggle.checked, true);

        page.server.throwNext['patch-account'] = 1;
        toggle.click();
        assert.equal(toggle.checked, false);
        await waitFor(() => page.server.requests('patch-account').length === 1
            && page.server.requests('patch-account')[0].done);
        await waitFor(() => toggle.checked === true);
        assert.match(accountsPanelError(page, 'tencent').textContent, /状态切换失败/);
    } finally {
        page.close();
    }
});

test('S11：后端返回的 display_name 含 HTML 时在芯片名称里按纯文本渲染，不生成元素', async () => {
    const payload = '<img src=x onerror=window.__xssHit=1>';
    const accounts = defaultAccounts();
    accounts.toutiao = [makeAccount({
        id: 'acc-xss',
        source: 'toutiao',
        normalized_identifier: 'xss-id',
        profile_url: 'https://example.com/xss-profile',
        display_name: payload,
    })];
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        const chip = await waitForChip(page, 'acc-xss');
        const panel = page.document
            .querySelector('.source-accounts-panel[data-accounts-for="toutiao"]');
        assert.equal(chip.querySelectorAll('img').length, 0);
        assert.equal(panel.querySelectorAll('img').length, 0);
        assert.equal(page.window.__xssHit, undefined);
        // display_name 整体作为文本写进芯片名称
        const nameEl = chip.querySelector('.account-chip-name');
        assert.equal(nameEl.textContent, payload);
        // 主页链接始终可用
        const link = chip.querySelector('a.account-chip-open');
        assert.equal(link.getAttribute('aria-hidden'), null);
        assert.equal(link.getAttribute('tabindex'), null);
    } finally {
        page.close();
    }
});

test('S12：模型页签的未保存修改在页签切换后保留，离开页面时触发离开确认', async () => {
    const page = await bootPage();
    try {
        // 无修改时不拦截离开
        const cleanEvent = new page.window.Event('beforeunload', { cancelable: true });
        page.window.dispatchEvent(cleanEvent);
        assert.equal(cleanEvent.defaultPrevented, false);

        // 在模型页签制造未保存修改
        inputValue(
            page,
            page.document.getElementById('models-default-input'),
            'local/unsaved-model',
        );

        // 切到数据源页签再切回来，修改仍在（页签切换只隐藏面板，不重渲染）
        page.clickTab('sources');
        assert.ok(page.panel('models').hidden);
        page.clickTab('models');
        assert.equal(
            page.document.getElementById('models-default-input').value,
            'local/unsaved-model',
        );

        // 有未保存修改时拦截离开
        const dirtyEvent = new page.window.Event('beforeunload', { cancelable: true });
        page.window.dispatchEvent(dirtyEvent);
        assert.equal(dirtyEvent.defaultPrevented, true);

        // 放弃修改后回到服务器值，离开不再拦截
        page.document.getElementById('btn-models-discard').click();
        assert.equal(
            page.document.getElementById('models-default-input').value,
            'deepseek/default-model',
        );
        const discardedEvent = new page.window.Event('beforeunload', { cancelable: true });
        page.window.dispatchEvent(discardedEvent);
        assert.equal(discardedEvent.defaultPrevented, false);
    } finally {
        page.close();
    }
});

test('S13：来源启停即时保存，请求体按目录序构造并带当前版本号；启停不改变行位置', async () => {
    const page = await bootPage();
    try {
        // 单一列表按目录序渲染全部非每日来源
        assert.deepEqual(
            sourceListOrder(page),
            ['toutiao', 'tencent', 'chinanews', 'btime', 'beijinghao'],
        );
        const chinanewsIndex = sourceListIndex(page, 'chinanews');

        // 停用 chinanews：['toutiao', 'chinanews'] → ['toutiao']
        const chinanewsRow = sourceRow(page, 'chinanews');
        chinanewsRow.querySelector('.source-enabled-toggle').click();
        await waitFor(() => page.server.requests('save-sources').length === 1);
        let body = page.server.requests('save-sources')[0].body;
        assert.deepEqual(body.value, ['toutiao']);
        assert.equal(body.expected_version, 4);
        await waitFor(() => page.server.requests('save-sources')[0].done);
        // 等面板重渲染完成（行节点被重建）
        await waitFor(() => sourceRow(page, 'chinanews') !== chinanewsRow);
        // 判别性断言：chinanews 仍在 #sources-list 原位置，只是开关变为 unchecked
        assert.equal(sourceListIndex(page, 'chinanews'), chinanewsIndex);
        assert.equal(
            sourceRow(page, 'chinanews').querySelector('.source-enabled-toggle').checked,
            false,
        );

        // 启用 tencent：请求体按目录序构造，版本号跟随上一次保存
        sourceRow(page, 'tencent').querySelector('.source-enabled-toggle').click();
        await waitFor(() => page.server.requests('save-sources').length === 2);
        body = page.server.requests('save-sources')[1].body;
        assert.deepEqual(body.value, ['toutiao', 'tencent']);
        assert.equal(body.expected_version, 5);
        await waitFor(() => page.server.requests('save-sources')[1].done);

        // 目录序判别：toutiao 停用再启用，请求体必须回到目录序而不是追加在末尾
        // （toutiao 在目录中排最前，[...current, key] 追加实现在这里会给出 ['tencent', 'toutiao']）
        const toutiaoRow = sourceRow(page, 'toutiao');
        toutiaoRow.querySelector('.source-enabled-toggle').click();
        await waitFor(() => page.server.requests('save-sources').length === 3
            && page.server.requests('save-sources')[2].done);
        assert.deepEqual(page.server.requests('save-sources')[2].body.value, ['tencent']);
        await waitFor(() => sourceRow(page, 'toutiao') !== toutiaoRow);
        sourceRow(page, 'toutiao').querySelector('.source-enabled-toggle').click();
        await waitFor(() => page.server.requests('save-sources').length === 4);
        assert.deepEqual(
            page.server.requests('save-sources')[3].body.value,
            ['toutiao', 'tencent'],
        );
        await waitFor(() => page.server.requests('save-sources')[3].done);
    } finally {
        page.close();
    }
});

test('S14：启停保存失败时开关回滚、行内显示错误，行的位置与开关状态都不变', async () => {
    const page = await bootPage();
    try {
        page.server.saveBehavior.crawl_sources = {
            status: 422,
            payload: { detail: '来源列表无效' },
        };
        const orderBefore = sourceListOrder(page);
        const row = page.document
            .querySelector('#sources-list li[data-source="chinanews"]');
        const toggle = row.querySelector('.source-enabled-toggle');
        assert.equal(toggle.checked, true);

        toggle.click();
        assert.equal(toggle.checked, false);
        await waitFor(() => page.server.requests('save-sources').length === 1
            && page.server.requests('save-sources')[0].done);
        // 开关回滚、行内错误，失败不重渲染：行节点、位置与开关状态都保持
        await waitFor(() => toggle.checked === true);
        assert.match(row.querySelector('.source-row-error').textContent, /停用失败/);
        assert.match(row.querySelector('.source-row-error').textContent, /来源列表无效/);
        assert.deepEqual(sourceListOrder(page), orderBefore);
        assert.equal(sourceRow(page, 'chinanews'), row);
        assert.equal(toggle.checked, true);
    } finally {
        page.close();
    }
});

test('S15：停用最后一个启用来源被前端拦截，不发出请求', async () => {
    const sections = defaultSections();
    sections.crawl_sources.value = ['toutiao'];
    const page = await bootPage({ sections });
    try {
        // 列表仍按目录序渲染全部来源，只有 toutiao 处于启用状态
        assert.deepEqual(
            sourceListOrder(page),
            ['toutiao', 'tencent', 'chinanews', 'btime', 'beijinghao'],
        );
        const row = page.document
            .querySelector('#sources-list li[data-source="toutiao"]');
        const toggle = row.querySelector('.source-enabled-toggle');
        assert.equal(toggle.checked, true);

        toggle.click();
        // 前端拦截：不应发出任何保存请求（这是本测试的判别性断言，必须最先检查）
        await assertNever(() => page.server.requests('save-sources').length > 0, 300);
        // 开关恢复、行内提示
        assert.equal(toggle.checked, true);
        assert.match(
            row.querySelector('.source-row-error').textContent,
            /每小时来源不能为空/,
        );
    } finally {
        page.close();
    }
});

test('S18：旧 hash #accounts:toutiao 被改写为页签级的 #sources', async () => {
    const page = await bootPage({ hash: '#accounts:toutiao' });
    try {
        await waitFor(() => page.window.location.hash === '#sources');
        assert.ok(!page.panel('sources').hidden);
        // 账号面板始终平铺，hash 子锚点没有落点也不再做任何展开
        assert.ok(accountsPanel(page, 'toutiao'));
    } finally {
        page.close();
    }
});


test('S19：启停请求进行中锁定全部来源开关，放行后第二次启停基于最新列表', async () => {
    const page = await bootPage();
    try {
        page.clickTab('sources');
        page.server.hold('save-sources');

        // 停用 chinanews，请求被扣住
        sourceRow(page, 'chinanews').querySelector('.source-enabled-toggle').click();
        await waitFor(() => page.server.requests('save-sources').length === 1);

        // 请求进行中：点击其余来源的开关发不出第二个请求（判别性断言，最先检查）
        const tencentToggle = sourceRow(page, 'tencent')
            .querySelector('.source-enabled-toggle');
        tencentToggle.click();
        await assertNever(() => page.server.requests('save-sources').length > 1, 300);
        assert.equal(tencentToggle.disabled, true);
        assert.equal(tencentToggle.checked, false);

        // 放行第一个请求：停用成功后面板重渲染，开关恢复可点，chinanews 位置不变
        page.server.release('save-sources');
        await waitFor(() => {
            const row = sourceRow(page, 'tencent');
            const toggle = row && row.querySelector('.source-enabled-toggle');
            return toggle && !toggle.disabled;
        });
        assert.equal(
            sourceRow(page, 'chinanews').querySelector('.source-enabled-toggle').checked,
            false,
        );
        assert.equal(sourceListIndex(page, 'chinanews'), 2);

        // 第二次启停基于更新后的列表与版本号
        sourceRow(page, 'tencent').querySelector('.source-enabled-toggle').click();
        await waitFor(() => page.server.requests('save-sources').length === 2);
        const second = page.server.requests('save-sources')[1];
        assert.deepEqual(second.body.value, ['toutiao', 'tencent']);
        assert.equal(second.body.expected_version, 5);
        // 等第二次保存收尾（重渲染完成）再关页面，避免异步续跑泄漏到测试结束后
        await waitFor(() => second.done
            && sourceRow(page, 'tencent').querySelector('.source-enabled-toggle').checked);
    } finally {
        page.server.release('save-sources');
        page.close();
    }
});

test('S20：启停其他来源触发重渲染后，待删标记与进行中的名称刷新状态保留', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(2);
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-2');
        await waitForAccountActions(page);
        // 制造待删标记与进行中的名称刷新
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');
        page.server.hold('refresh-names');
        sourceRow(page, 'toutiao').querySelector('.accounts-refresh-btn').click();
        await waitFor(() => page.server.requests('refresh-names').length === 1);

        // 停用另一个来源并等待保存成功，面板整体重渲染
        const chinanewsRow = sourceRow(page, 'chinanews');
        chinanewsRow.querySelector('.source-enabled-toggle').click();
        await waitFor(() => page.server.requests('save-sources').length === 1
            && page.server.requests('save-sources')[0].done);
        // 等重渲染完成（行节点被重建）
        await waitFor(() => sourceRow(page, 'chinanews') !== chinanewsRow);
        await waitForChip(page, 'tt-1');

        // 芯片启停开关始终可用，标记与确认条保留
        assert.equal(
            accountChip(page, 'tt-1').querySelector('.account-enabled-toggle').disabled,
            false,
        );
        // 待删标记保留：芯片外观与待提交条计数不变
        assert.ok(accountChip(page, 'tt-1').classList.contains('is-marked'));
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');
        // 进行中的名称刷新状态保留：重建后的刷新按钮仍禁用
        assert.equal(
            sourceRow(page, 'toutiao').querySelector('.accounts-refresh-btn').disabled,
            true,
        );

        // 放行刷新：收尾落在重建后的按钮上，标记不受影响
        page.server.release('refresh-names');
        await waitFor(() => page.server.requests('refresh-names')[0].done);
        await waitFor(() => page.document.getElementById('toast').textContent
            .includes('已更新 2 个名称'));
        await waitFor(() => sourceRow(page, 'toutiao')
            .querySelector('.accounts-refresh-btn').disabled === false);
        assert.ok(accountChip(page, 'tt-1').classList.contains('is-marked'));
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');
    } finally {
        page.close();
    }
});

function makeToutiaoAccounts(count) {
    return Array.from({ length: count }, (_, i) => makeAccount({
        id: `tt-${i + 1}`,
        source: 'toutiao',
        normalized_identifier: `tt-token-${i + 1}`,
        original_input: `https://www.toutiao.com/c/user/tt-token-${i + 1}/`,
        profile_url: `https://www.toutiao.com/c/user/tt-token-${i + 1}/`,
    }));
}

test('S21：芯片名称三种状态——已解析、待获取、获取失败', async () => {
    const longToken = 'MS4wLjABAAAA-very-long-token-that-must-not-stretch-the-row-0123456789';
    const accounts = defaultAccounts();
    accounts.toutiao = [
        makeAccount({
            id: 'acc-resolved',
            source: 'toutiao',
            normalized_identifier: 'tok-resolved',
            profile_url: 'https://example.com/resolved',
            display_name: '头条账号甲',
        }),
        makeAccount({
            id: 'acc-pending',
            source: 'toutiao',
            normalized_identifier: longToken,
            profile_url: 'https://example.com/pending',
        }),
    ];
    accounts.tencent = [makeAccount({
        id: 'acc-failed',
        source: 'tencent',
        normalized_identifier: 'author-failed',
        profile_url: 'https://example.com/failed',
        display_name_error: '解析超时',
    })];
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'acc-pending');

        // 已解析：芯片显示名称，无待获取标记，整体不带未解析底色
        const resolvedChip = accountChip(page, 'acc-resolved');
        const resolvedName = resolvedChip.querySelector('.account-chip-name');
        assert.equal(resolvedName.textContent, '头条账号甲');
        assert.equal(resolvedName.title, '头条账号甲');
        assert.ok(!resolvedChip.classList.contains('is-unresolved'));
        assert.equal(resolvedChip.querySelector('.account-name-badge'), null);

        // 未解析：芯片整体带 is-unresolved 底色，显示截断标识（CSS 省略号），
        // 完整值放 title，加弱化标记「名称待获取」
        const pendingChip = accountChip(page, 'acc-pending');
        assert.ok(pendingChip.classList.contains('is-unresolved'));
        const pendingName = pendingChip.querySelector('.account-chip-name');
        assert.equal(pendingName.textContent, longToken);
        assert.equal(pendingName.title, longToken);
        assert.match(
            pendingChip.querySelector('.account-name-badge').textContent,
            /名称待获取/,
        );

        // 其他来源解析失败：同样截断标识，标记为「名称获取失败」，原因放标记的 title
        await expandSource(page, 'tencent');
        const failedChip = await waitForChip(page, 'acc-failed');
        assert.ok(failedChip.classList.contains('is-unresolved'));
        assert.equal(failedChip.querySelector('.account-chip-name').textContent, 'author-failed');
        const failedBadge = failedChip.querySelector('.account-name-badge');
        assert.match(failedBadge.textContent, /名称获取失败/);
        assert.ok(failedBadge.classList.contains('is-error'));
        assert.equal(failedBadge.title, '解析超时');
    } finally {
        page.close();
    }
});

test('S21b：头条账号解析失败时与其他来源一致，显示「名称获取失败」警示', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = [makeAccount({
        id: 'acc-toutiao-waiting',
        source: 'toutiao',
        normalized_identifier: 'tok-waiting',
        profile_url: 'https://example.com/toutiao-waiting',
        display_name_error: '头条主页 feed 里没有可用的账号名',
    })];
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        const chip = await waitForChip(page, 'acc-toutiao-waiting');

        const badge = chip.querySelector('.account-name-badge');
        assert.match(badge.textContent, /名称获取失败/);
        assert.ok(badge.classList.contains('is-error'));
        assert.equal(badge.title, '头条主页 feed 里没有可用的账号名');
    } finally {
        page.close();
    }
});

test('S22：芯片内结构为 checkbox + 名称，aria-label 带账号名；空状态渲染提示节点', async () => {
    const page = await bootPage();
    try {
        await expandSource(page, 'toutiao');
        const chip = await waitForChip(page, 'acc-toutiao-1');
        const label = chip.querySelector('label.account-chip-label');
        assert.ok(label, '芯片主体应是 label（整个芯片即点击区）');
        const toggle = label.querySelector('input[type="checkbox"].account-enabled-toggle');
        assert.ok(toggle, '芯片内应是启用开关 checkbox');
        assert.match(toggle.getAttribute('aria-label'), /头条一号/);

        // 启停状态由芯片整体配色表达，名称前不再有装饰圆点
        assert.equal(label.querySelector('.account-chip-dot'), null,
            '芯片内不应再有圆点节点');
        assert.ok(label.querySelector('.account-chip-name'), '芯片内应有名称节点');
        // 芯片上的删除按钮与主页链接始终可用；展开面板就是编辑面
        assert.equal(chip.querySelector('.account-chip-delete').disabled, false);
        const openLink = chip.querySelector('.account-chip-open');
        assert.equal(openLink.getAttribute('tabindex'), null);
        assert.equal(openLink.getAttribute('aria-hidden'), null);

        // 空状态：芯片网格内渲染提示节点（不再是表格 colspan 行）
        await expandSource(page, 'tencent');
        await waitFor(() => page.document
            .querySelector('.source-accounts-panel[data-accounts-for="tencent"]'
                + ' .accounts-chip-grid .settings-source-empty'));
        const empty = page.document
            .querySelector('.source-accounts-panel[data-accounts-for="tencent"]'
                + ' .accounts-chip-grid .settings-source-empty');
        assert.match(empty.textContent, /该来源还没有抓取账号/);
        assert.equal(empty.tagName, 'P');
    } finally {
        page.close();
    }
});

test('S23：账号启停请求进行中开关处于 disabled，放行后恢复可用', async () => {
    const accounts = defaultAccounts();
    accounts.tencent = [makeAccount({
        id: 'acc-tencent-1',
        source: 'tencent',
        normalized_identifier: 'tencent-one',
        enabled: true,
    })];
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'tencent');
        const chip = await waitForChip(page, 'acc-tencent-1');
        const toggle = chip.querySelector('.account-enabled-toggle');

        page.server.hold('patch-account');
        toggle.click();
        await waitFor(() => page.server.requests('patch-account').length === 1);
        assert.equal(toggle.disabled, true);

        page.server.release('patch-account');
        await waitFor(() => page.server.requests('patch-account')[0].done);
        await waitFor(() => toggle.disabled === false);
        assert.equal(toggle.checked, false);
    } finally {
        page.close();
    }
});

test('S24：来源行内顺序为开关 → 名称 → 账号数徽标 → 刷新按钮；停用行带弱化样式类；每日任务行名称直接对齐标题', async () => {
    const page = await bootPage();
    try {
        // 启用行：开关打头，名称、账号数徽标依次在后；徽标是纯展示 span。
        // 「刷新账号名称」常驻 DOM，但只在账号面板展开时可见
        const toutiaoRow = page.document
            .querySelector('#sources-list li[data-source="toutiao"]');
        assert.ok(toutiaoRow.children[0].matches('input[type="checkbox"].source-enabled-toggle'));
        assert.ok(toutiaoRow.children[1].classList.contains('settings-source-name'));
        assert.ok(toutiaoRow.children[2].classList.contains('source-account-badge'));
        assert.equal(toutiaoRow.children[2].tagName, 'SPAN');
        assert.ok(toutiaoRow.children[3].classList.contains('accounts-refresh-btn'));
        assert.equal(toutiaoRow.children[3].hidden, true);
        assert.ok(!toutiaoRow.classList.contains('is-disabled'));

        // 停用行：开关仍在最前，行带弱化样式类
        const tencentRow = page.document
            .querySelector('#sources-list li[data-source="tencent"]');
        assert.ok(tencentRow.classList.contains('is-disabled'));
        assert.ok(tencentRow.children[0].matches('input[type="checkbox"].source-enabled-toggle'));
        assert.ok(tencentRow.children[1].classList.contains('settings-source-name'));

        // 每日任务来源只出现在只读分组：不在每小时来源列表、也没有启用开关
        const daily = page.document.getElementById('sources-daily-list');
        assert.match(daily.textContent, /北京日报/);
        assert.match(daily.textContent, /劳动午报/);
        assert.match(daily.textContent, /每日单独任务，由服务器计划任务调度/);
        assert.equal(daily.querySelector('.source-enabled-toggle'), null);
        assert.equal(
            page.document.querySelector('#sources-list li[data-source="bjrb"]'),
            null,
        );

        // 每日任务只读行：无开关，名称直接从列表左缘开始，与「每日任务」标题对齐
        const dailyRow = page.document
            .querySelector('#sources-daily-list li[data-source="bjrb"]');
        assert.ok(dailyRow.children[0].classList.contains('settings-source-name'));
        assert.equal(
            dailyRow.querySelector('.settings-source-toggle-placeholder'),
            null,
        );

        // 「刷新账号名称」只在账号面板展开时可见，折叠的来源列表保持干净
        assert.equal(toutiaoRow.children[3].hidden, true);
        toutiaoRow.querySelector('.source-accounts-toggle').click();
        assert.equal(toutiaoRow.children[3].hidden, false);
        assert.equal(
            toutiaoRow.querySelector('.source-accounts-toggle').getAttribute('aria-expanded'),
            'true',
        );
    } finally {
        page.close();
    }
});

test('S24b：账号面板默认折叠，展开一个来源会收起其他来源', async () => {
    const page = await bootPage();
    try {
        assert.equal(accountsPanel(page, 'toutiao').hidden, true);
        assert.equal(accountsPanel(page, 'tencent').hidden, true);

        sourceRow(page, 'toutiao').querySelector('.source-accounts-toggle').click();
        assert.equal(accountsPanel(page, 'toutiao').hidden, false);
        assert.equal(accountsPanel(page, 'tencent').hidden, true);
        const toutiaoToggle = sourceRow(page, 'toutiao').querySelector('.source-accounts-toggle');
        assert.equal(toutiaoToggle.getAttribute('aria-expanded'), 'true');
        assert.equal(
            toutiaoToggle.querySelector('.source-accounts-toggle-label').textContent,
            '收起',
        );

        sourceRow(page, 'tencent').querySelector('.source-accounts-toggle').click();
        assert.equal(accountsPanel(page, 'toutiao').hidden, true);
        assert.equal(accountsPanel(page, 'tencent').hidden, false);

        sourceRow(page, 'tencent').querySelector('.source-accounts-toggle').click();
        assert.equal(accountsPanel(page, 'tencent').hidden, true);
    } finally { page.close(); }
});

test('S24c：启停来源触发的整块重渲染保留已展开的账号面板', async () => {
    const page = await bootPage();
    try {
        sourceRow(page, 'toutiao').querySelector('.source-accounts-toggle').click();
        const oldPanel = accountsPanel(page, 'toutiao');
        assert.equal(oldPanel.hidden, false);

        sourceRow(page, 'chinanews').querySelector('.source-enabled-toggle').click();
        await waitFor(() => page.server.inflight === 0
            && accountsPanel(page, 'toutiao') !== oldPanel);

        assert.equal(page.window.eval('state.expandedSource'), 'toutiao');
        assert.equal(accountsPanel(page, 'toutiao').hidden, false);
        assert.equal(
            sourceRow(page, 'toutiao').querySelector('.source-accounts-toggle')
                .getAttribute('aria-expanded'),
            'true',
        );
    } finally { page.close(); }
});

test('S25：刷新账号名称按每批最多 20 个切片串行请求，按钮显示进度', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(25);
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-25');
        await waitForAccountActions(page);
        const refreshBtn = sourceRow(page, 'toutiao').querySelector('.accounts-refresh-btn');
        assert.equal(refreshBtn.textContent, '刷新账号名称');

        page.server.hold('refresh-names');
        refreshBtn.click();
        await waitFor(() => page.server.requests('refresh-names').length === 1);
        // 刷新进行中：按钮禁用并显示进度
        assert.equal(refreshBtn.disabled, true);
        assert.match(refreshBtn.textContent, /刷新中… 0\/25/);
        // 串行：第一批未返回前不发第二批
        await assertNever(() => page.server.requests('refresh-names').length > 1, 200);
        page.server.release('refresh-names');

        await waitFor(() => page.server.requests('refresh-names').length === 2
            && page.server.requests('refresh-names').every((entry) => entry.done));
        const [first, second] = page.server.requests('refresh-names');
        assert.equal(first.body.account_ids.length, 20);
        assert.equal(second.body.account_ids.length, 5);

        await waitFor(() => page.document.getElementById('toast').textContent
            .includes('已更新 25 个名称，0 个获取失败'));
        assert.equal(refreshBtn.disabled, false);
        assert.equal(refreshBtn.textContent, '刷新账号名称');
        // 逐芯片更新到位：第一个芯片显示解析出的名称
        const firstName = accountChip(page, 'tt-1').querySelector('.account-chip-name');
        assert.equal(firstName.textContent, '自动名称-tt-token-1');
    } finally {
        page.close();
    }
});

test('S26：返回 skipped 的账号被重新排入下一批继续刷新', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(2);
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-2');
        // tt-1 第一次尝试被 skipped（预算耗尽），重试时成功
        page.server.refreshBehavior = (id, attempt) => (
            id === 'tt-1' && attempt === 1 ? 'skipped' : null
        );

        await waitForAccountActions(page);
        sourceRow(page, 'toutiao').querySelector('.accounts-refresh-btn').click();
        await waitFor(() => page.server.requests('refresh-names').length === 2
            && page.server.requests('refresh-names').every((entry) => entry.done));
        const second = page.server.requests('refresh-names')[1];
        assert.deepEqual(second.body.account_ids, ['tt-1']);

        await waitFor(() => page.document.getElementById('toast').textContent
            .includes('已更新 2 个名称，0 个获取失败'));
        const nameEl = accountChip(page, 'tt-1').querySelector('.account-chip-name');
        assert.equal(nameEl.textContent, '自动名称-tt-token-1');
    } finally {
        page.close();
    }
});

test('S27：某一批完全没有推进时循环停止并提示，不会无限重发', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(25);
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-25');
        // 后 5 个账号始终 skipped：第一批推进 20 个，第二批 5 个全部 skipped、零推进
        page.server.refreshBehavior = (id) => (
            Number(id.split('-')[1]) > 20 ? 'skipped' : null
        );

        await waitForAccountActions(page);
        const refreshBtn = sourceRow(page, 'toutiao').querySelector('.accounts-refresh-btn');
        refreshBtn.click();
        await waitFor(() => page.server.requests('refresh-names').length === 2
            && page.server.requests('refresh-names').every((entry) => entry.done));
        // 兜底生效：不再发出第三次请求
        await assertNever(() => page.server.requests('refresh-names').length >= 3, 300);
        await waitFor(() => accountsPanelError(page, 'toutiao').textContent
            .includes('部分账号未能刷新，请稍后重试'));
        assert.equal(refreshBtn.disabled, false);
        assert.equal(refreshBtn.textContent, '刷新账号名称');
    } finally {
        page.close();
    }
});

test('S28：刷新逐芯片就地更新，不替换芯片节点，共存的待删标记外观保持', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(2);
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-2');
        // 刷新与待删标记可以共存
        await waitForAccountActions(page);
        accountChip(page, 'tt-2').querySelector('.account-chip-delete').click();
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');
        const chipBefore = accountChip(page, 'tt-1');

        page.server.hold('refresh-names');
        sourceRow(page, 'toutiao').querySelector('.accounts-refresh-btn').click();
        await waitFor(() => page.server.requests('refresh-names').length === 1);
        page.server.release('refresh-names');
        await waitFor(() => page.server.requests('refresh-names')[0].done);
        await waitFor(() => accountChip(page, 'tt-1')
            .querySelector('.account-chip-name').textContent === '自动名称-tt-token-1');

        // 就地更新：芯片节点没有被替换
        assert.equal(accountChip(page, 'tt-1'), chipBefore);
        // 共存标记的芯片同样被就地更新，且标记外观保持
        const markedChip = accountChip(page, 'tt-2');
        assert.ok(markedChip.classList.contains('is-marked'));
        assert.equal(
            markedChip.querySelector('.account-chip-name').textContent,
            '自动名称-tt-token-2',
        );
        assert.equal(markedChip.querySelector('.account-chip-delete').textContent, '↩');
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');
    } finally {
        page.close();
    }
});

test('S31：添加即时成功并显示「名称解析中」，随后自动补一次解析就地落名称', async () => {
    const page = await bootPage();
    try {
        await expandSource(page, 'toutiao');
        await waitForAccountActions(page);
        // 挂起 refresh-names 的响应，锁住「解析中」这一中间态
        page.server.hold('refresh-names');
        // 新的就地输入流程：点网格末尾的虚线芯片，输入后点「添加」
        const grid = accountsPanel(page, 'toutiao').querySelector('.accounts-chip-grid');
        grid.querySelector('.account-add-chip').click();
        const addInput = grid.querySelector('.account-add-input');
        addInput.value = 'brand-new-id';
        grid.querySelector('.btn-account-add').click();
        await waitFor(() => page.server.requests('add-account').length === 1
            && page.server.requests('add-account')[0].done);
        // 请求体不再携带 display_name
        assert.equal(page.server.requests('add-account')[0].body.display_name, undefined);

        const created = page.server.accounts.toutiao
            .find((item) => item.normalized_identifier === 'brand-new-id');
        // 芯片在添加请求返回后立即出现，不等名称解析
        const chip = await waitForChip(page, created.id);
        assert.equal(chip.querySelector('.account-chip-name').textContent, 'brand-new-id');
        const resolvingBadge = chip.querySelector('.account-name-badge');
        assert.match(resolvingBadge.textContent, /名称解析中/);
        assert.ok(!resolvingBadge.classList.contains('is-error'));
        // 前端自动对这一条补发 refresh-names（响应仍挂着）
        await waitFor(() => page.server.requests('refresh-names').length === 1);
        assert.deepEqual(
            page.server.requests('refresh-names')[0].body.account_ids,
            [created.id],
        );

        // 放行解析响应：名称就地补上，解析中徽章消失
        page.server.release('refresh-names');
        await waitFor(() => accountChip(page, created.id)
            .querySelector('.account-chip-name').textContent === '自动名称-brand-new-id');
        const doneChip = accountChip(page, created.id);
        assert.ok(!doneChip.classList.contains('is-unresolved'));
        assert.equal(doneChip.querySelector('.account-name-badge'), null);
    } finally {
        page.close();
    }
});

test('S31b：新增后的名称解析失败时，徽章变「名称获取失败」并写面板错误行', async () => {
    const page = await bootPage();
    try {
        page.server.refreshBehavior = () => ({ status: 'failed', error: '头条主页请求超时' });
        await expandSource(page, 'toutiao');
        await waitForAccountActions(page);
        const grid = accountsPanel(page, 'toutiao').querySelector('.accounts-chip-grid');
        grid.querySelector('.account-add-chip').click();
        const addInput = grid.querySelector('.account-add-input');
        addInput.value = 'doomed-id';
        grid.querySelector('.btn-account-add').click();
        await waitFor(() => page.server.requests('add-account').length === 1
            && page.server.requests('add-account')[0].done);
        const created = page.server.accounts.toutiao
            .find((item) => item.normalized_identifier === 'doomed-id');
        await waitForChip(page, created.id);

        await waitFor(() => {
            const badge = accountChip(page, created.id).querySelector('.account-name-badge');
            return badge !== null && /名称获取失败/.test(badge.textContent);
        });
        const failedChip = accountChip(page, created.id);
        const failedBadge = failedChip.querySelector('.account-name-badge');
        assert.ok(failedBadge.classList.contains('is-error'));
        assert.equal(failedBadge.title, '头条主页请求超时');
        assert.match(accountsPanelError(page, 'toutiao').textContent, /名称解析失败/);
    } finally {
        page.close();
    }
});

test('S32：反复启停同一个来源，它在列表中的位置索引始终不变', async () => {
    const page = await bootPage();
    try {
        const initialOrder = sourceListOrder(page);
        const chinanewsIndex = sourceListIndex(page, 'chinanews');
        // 停用 → 启用 → 再停用：每次成功后整表重渲染，位置索引与整体顺序都不得变化
        // （护栏：防止将来有人又按启用状态重新分组）
        const rounds = [[1, false], [2, true], [3, false]];
        for (const [round, checked] of rounds) {
            const rowBefore = sourceRow(page, 'chinanews');
            rowBefore.querySelector('.source-enabled-toggle').click();
            await waitFor(() => page.server.requests('save-sources').length === round
                && page.server.requests('save-sources')[round - 1].done);
            // 等面板重渲染完成（行节点被重建）再断言，避免读到旧 DOM
            await waitFor(() => sourceRow(page, 'chinanews') !== rowBefore);
            assert.equal(
                sourceListIndex(page, 'chinanews'),
                chinanewsIndex,
                `第 ${round} 次启停后 chinanews 的位置索引不变`,
            );
            assert.equal(
                sourceRow(page, 'chinanews').querySelector('.source-enabled-toggle').checked,
                checked,
            );
            assert.deepEqual(sourceListOrder(page), initialOrder);
        }
    } finally {
        page.close();
    }
});

test('S33：排序模式已删除——页面上不存在「调整抓取顺序」按钮与排序列表', async () => {
    const page = await bootPage();
    try {
        page.clickTab('sources');
        // 护栏：防止排序模式死代码残留
        assert.equal(page.document.getElementById('btn-sources-sort-mode'), null);
        assert.equal(page.document.getElementById('sources-sort-list'), null);
        assert.ok(!page.panel('sources').textContent.includes('调整抓取顺序'));
        assert.ok(!page.panel('sources').textContent.includes('各来源按顺序抓取'));
        await assertNever(() => page.document.getElementById('btn-sources-sort-mode'), 200);
        await assertNever(() => page.document.getElementById('sources-sort-list'), 200);
    } finally {
        page.close();
    }
});

// ---------- 芯片布局场景（N1-N23） ----------

test('N1：默认态点击芯片发出 PATCH，成功后芯片状态与来源行徽标同步更新', async () => {
    const page = await bootPage();
    try {
        await expandSource(page, 'toutiao');
        const chip = await waitForChip(page, 'acc-toutiao-1');
        const toggle = chip.querySelector('.account-enabled-toggle');
        assert.equal(toggle.checked, true);
        const badge = sourceRow(page, 'toutiao').querySelector('.source-account-badge');
        assert.match(badge.textContent, /启用 1 \/ 共 1/);

        // 点击芯片主体（label）即切换启用状态
        chip.querySelector('.account-chip-label').click();
        await waitFor(() => page.server.requests('patch-account').length === 1);
        assert.equal(page.server.requests('patch-account')[0].accountId, 'acc-toutiao-1');
        assert.deepEqual(page.server.requests('patch-account')[0].body, { enabled: false });
        await waitFor(() => page.server.requests('patch-account')[0].done);
        await waitFor(() => toggle.checked === false);
        // 来源行徽标同步为「启用 0 / 共 1」并进入警告态
        await waitFor(() => badge.textContent.includes('启用 0 / 共 1'));
        assert.ok(badge.classList.contains('is-warning'));
    } finally {
        page.close();
    }
});

test('N2：展开即可编辑，芯片上的删除按钮与主页链接始终可用，未标记时没有待提交条', async () => {
    const page = await bootPage();
    try {
        await expandSource(page, 'toutiao');
        const chip = await waitForChip(page, 'acc-toutiao-1');
        const deleteBtn = chip.querySelector('.account-chip-delete');
        assert.ok(deleteBtn);
        assert.equal(deleteBtn.disabled, false);
        const openLink = chip.querySelector('.account-chip-open');
        assert.ok(openLink);
        assert.equal(openLink.getAttribute('tabindex'), null);
        assert.equal(openLink.getAttribute('aria-hidden'), null);
        assert.equal(deleteBar(page, 'toutiao'), null);
    } finally {
        page.close();
    }
});

test('N3：展开面板即编辑面，芯片可启停，↗ 指向主页，添加芯片与刷新按钮常驻', async () => {
    const page = await bootPage();
    try {
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'acc-toutiao-1');
        sourceRow(page, 'toutiao').querySelector('.source-accounts-toggle').click();

        const chip = accountChip(page, 'acc-toutiao-1');
        const toggle = chip.querySelector('.account-enabled-toggle');
        assert.equal(toggle.disabled, false, '芯片启停开关应始终可用');

        const openLink = chip.querySelector('a.account-chip-open');
        assert.ok(openLink);
        assert.equal(
            openLink.getAttribute('href'),
            'https://www.toutiao.com/c/user/toutiao-one/',
        );
        assert.equal(openLink.target, '_blank');
        assert.equal(openLink.rel, 'noopener noreferrer');
        assert.equal(openLink.getAttribute('aria-label'), '打开 头条一号 的主页');
        const deleteBtn = chip.querySelector('button.account-chip-delete');
        assert.ok(deleteBtn);
        assert.equal(deleteBtn.disabled, false);
        assert.equal(deleteBtn.getAttribute('aria-label'), '删除 头条一号');

        const grid = accountsPanel(page, 'toutiao').querySelector('.accounts-chip-grid');
        const addChip = grid.querySelector('.account-add-chip');
        assert.ok(addChip);
        assert.equal(grid.lastElementChild, addChip);
        const refreshBtn = sourceRow(page, 'toutiao').querySelector('.accounts-refresh-btn');
        assert.ok(refreshBtn);
        assert.equal(refreshBtn.hidden, false);
        assert.equal(refreshBtn.disabled, false);
    } finally {
        page.close();
    }
});

test('N4：标记与撤回只改前端外观，待提交条计数随标记增减，全部撤回后待提交条消失', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(2);
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-2');
        await waitForAccountActions(page);
        assert.equal(deleteBar(page, 'toutiao'), null, '没有标记时不显示待提交条');

        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        assert.ok(accountChip(page, 'tt-1').classList.contains('is-marked'));
        assert.equal(
            accountChip(page, 'tt-1').querySelector('.account-chip-delete').textContent,
            '↩',
        );
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');

        accountChip(page, 'tt-2').querySelector('.account-chip-delete').click();
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 2 个账号');

        // 撤回一个 → 计数回落、外观还原
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        assert.ok(!accountChip(page, 'tt-1').classList.contains('is-marked'));
        assert.equal(
            accountChip(page, 'tt-1').querySelector('.account-chip-delete').textContent,
            '×',
        );
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');

        // 全部撤回 → 待提交条消失；全程没有发出任何 DELETE
        accountChip(page, 'tt-2').querySelector('.account-chip-delete').click();
        assert.equal(deleteBar(page, 'toutiao'), null, '标记全部撤回后待提交条应消失');
        await assertNever(() => page.server.requests('delete-account').length > 0, 200);
    } finally {
        page.close();
    }
});

test('N6：确认删除按标记顺序串行发出 DELETE，且只包含已标记的 id', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(3);
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-3');
        await waitForAccountActions(page);
        // 刻意按 tt-3 → tt-1 的顺序标记，验证提交顺序跟随标记顺序
        accountChip(page, 'tt-3').querySelector('.account-chip-delete').click();
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();

        page.server.hold('delete-account', 1);
        page.document.querySelector('.btn-account-delete-confirm').click();
        await waitFor(() => page.server.requests('delete-account').length === 1);
        assert.equal(page.server.requests('delete-account')[0].accountId, 'tt-3');
        // 串行：第一个请求未返回前不发第二个
        await assertNever(() => page.server.requests('delete-account').length > 1, 200);
        page.server.release('delete-account');

        await waitFor(() => page.server.requests('delete-account').length === 2
            && page.server.requests('delete-account').every((entry) => entry.done));
        // 请求数等于标记数，顺序等于标记顺序，不包含未标记的 tt-2
        assert.deepEqual(
            page.server.requests('delete-account').map((entry) => entry.accountId),
            ['tt-3', 'tt-1'],
        );
        await waitFor(() => page.document.getElementById('toast').textContent
            .includes('已删除 2 个账号'));
    } finally {
        page.close();
    }
});

test('N7：删除返回 404 时计入成功，不产生错误提示', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(2);
    const page = await bootPage({ accounts });
    try {
        // tt-2 在库里已不存在（另一处已删除）：后端返回 404，列表也不再返回它
        page.server.deleteBehavior = (id) => (
            id === 'tt-2' ? { status: 404, detail: '配置对象不存在' } : null
        );
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-2');
        await waitForAccountActions(page);
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        accountChip(page, 'tt-2').querySelector('.account-chip-delete').click();
        page.document.querySelector('.btn-account-delete-confirm').click();

        await waitFor(() => page.server.requests('delete-account').length === 2
            && page.server.requests('delete-account').every((entry) => entry.done));
        assert.equal(page.server.requests('delete-account')[1].status, 404);
        // 404 计入成功：按全部成功收尾，无错误提示，标记清空
        await waitFor(() => page.document.getElementById('toast').textContent
            .includes('已删除 2 个账号'));
        assert.equal(accountsPanelError(page, 'toutiao').textContent, '');
        assert.equal(deleteBar(page, 'toutiao'), null);
        await waitFor(() => accountChip(page, 'tt-1') === null
            && accountChip(page, 'tt-2') === null);
    } finally {
        page.close();
    }
});

test('N8：部分失败时已成功的删除不回滚，失败的保留标记并留在确认条里', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(3);
    const page = await bootPage({ accounts });
    try {
        page.server.deleteBehavior = (id) => (
            id === 'tt-2' ? { status: 500, detail: '数据库繁忙' } : null
        );
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-3');
        await waitForAccountActions(page);
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        accountChip(page, 'tt-2').querySelector('.account-chip-delete').click();
        page.document.querySelector('.btn-account-delete-confirm').click();

        await waitFor(() => page.server.requests('delete-account').length === 2
            && page.server.requests('delete-account').every((entry) => entry.done));
        // 提示含成功数与失败数及失败原因
        await waitFor(() => page.document.getElementById('toast').textContent
            .includes('已删除 1 个，1 个失败'));
        assert.match(page.document.getElementById('toast').textContent, /数据库繁忙/);
        assert.match(accountsPanelError(page, 'toutiao').textContent, /已删除 1 个，1 个失败/);

        // 成功的已从列表消失；失败的仍带标记；未标记的不受影响
        await waitFor(() => accountChip(page, 'tt-1') === null);
        const failedChip = accountChip(page, 'tt-2');
        assert.ok(failedChip.classList.contains('is-marked'));
        assert.ok(!accountChip(page, 'tt-3').classList.contains('is-marked'));
        // 待提交条按剩余标记计数，可以继续重试
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');
    } finally {
        page.close();
    }
});

test('N9：删除提交进行中确认/取消禁用，重复点击不发出第二轮请求', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(2);
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-2');
        await waitForAccountActions(page);
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        accountChip(page, 'tt-2').querySelector('.account-chip-delete').click();

        page.server.hold('delete-account', 1);
        const confirmBtn = page.document.querySelector('.btn-account-delete-confirm');
        confirmBtn.click();
        // 重复点击不应发出第二轮请求
        confirmBtn.click();
        await waitFor(() => page.server.requests('delete-account').length === 1);
        assert.equal(confirmBtn.disabled, true);
        assert.match(confirmBtn.textContent, /删除中… 1\/2/);
        assert.equal(
            page.document.querySelector('.btn-account-delete-cancel').disabled,
            true,
        );
        // 芯片区不接受任何操作
        assert.equal(
            accountChip(page, 'tt-1').querySelector('.account-chip-delete').disabled,
            true,
        );
        await assertNever(() => page.server.requests('delete-account').length > 1, 200);

        page.server.release('delete-account');
        await waitFor(() => page.server.requests('delete-account').length === 2
            && page.server.requests('delete-account').every((entry) => entry.done));
        await waitFor(() => page.document.getElementById('toast').textContent
            .includes('已删除 2 个账号'));
        assert.equal(page.server.requests('delete-account').length, 2);
    } finally {
        page.close();
    }
});

test('N10：点「取消」丢弃本面板全部待删标记', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(2);
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-2');
        await waitForAccountActions(page);
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        accountChip(page, 'tt-2').querySelector('.account-chip-delete').click();
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 2 个账号');

        page.document.querySelector('.btn-account-delete-cancel').click();
        assert.equal(deleteBar(page, 'toutiao'), null);
        assert.ok(!accountChip(page, 'tt-1').classList.contains('is-marked'));
        assert.ok(!accountChip(page, 'tt-2').classList.contains('is-marked'));
        // 取消必须零成本：不发 DELETE，重新标记后仍可正常提交
        await assertNever(() => page.server.requests('delete-account').length > 0, 200);
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');
    } finally {
        page.close();
    }
});

test('N11：启停另一个来源触发整块重渲染后，各来源的待删标记保留', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(2);
    accounts.tencent = [makeAccount({
        id: 'acc-tencent-1',
        source: 'tencent',
        normalized_identifier: 'tencent-one',
        enabled: true,
    })];
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-2');
        await waitForChip(page, 'acc-tencent-1');
        await waitForAccountActions(page);
        // 跨来源标记：toutiao 与 tencent 各一个，各自面板的确认条只统计本来源
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        accountChip(page, 'acc-tencent-1').querySelector('.account-chip-delete').click();
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');
        assert.equal(deleteBarCount(page, 'tencent'), '将删除 1 个账号');

        // 停用另一个来源并等待保存成功，面板整体重渲染
        const chinanewsRow = sourceRow(page, 'chinanews');
        chinanewsRow.querySelector('.source-enabled-toggle').click();
        await waitFor(() => page.server.requests('save-sources').length === 1
            && page.server.requests('save-sources')[0].done);
        // 等重渲染完成（行节点被重建）
        await waitFor(() => sourceRow(page, 'chinanews') !== chinanewsRow);
        await waitForChip(page, 'tt-1');

        // 各来源的标记都保留，芯片启停开关保持可用
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');
        assert.equal(deleteBarCount(page, 'tencent'), '将删除 1 个账号');
        assert.ok(accountChip(page, 'tt-1').classList.contains('is-marked'));
        assert.ok(accountChip(page, 'acc-tencent-1').classList.contains('is-marked'));
        assert.equal(
            accountChip(page, 'acc-tencent-1').querySelector('.account-enabled-toggle')
                .disabled,
            false,
        );
    } finally {
        page.close();
    }
});

test('N12：徽标文案为「启用 X / 共 Y」；启用数为 0 保留跳过警告；加载失败显示未知态', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = [
        makeAccount({
            id: 'tt-a',
            source: 'toutiao',
            normalized_identifier: 'tt-a',
            display_name: '甲',
            enabled: true,
        }),
        makeAccount({
            id: 'tt-b',
            source: 'toutiao',
            normalized_identifier: 'tt-b',
            display_name: '乙',
            enabled: false,
        }),
    ];
    const page = await bootPage({ accounts });
    try {
        page.clickTab('sources');
        // 正常态：启用数与总数分别取自启用数和账号全量（1 启用 / 2 总数可区分两者）
        const toutiaoBadge = page.document
            .querySelector('#sources-list li[data-source="toutiao"] .source-account-badge');
        assert.equal(toutiaoBadge.textContent, '启用 1 / 共 2');
        assert.ok(!toutiaoBadge.classList.contains('is-warning'));

        // 启用数为 0：保留跳过警告
        const tencentBadge = page.document
            .querySelector('#sources-list li[data-source="tencent"] .source-account-badge');
        assert.equal(tencentBadge.textContent, '启用 0 / 共 0 · 本轮会跳过该来源');
        assert.ok(tencentBadge.classList.contains('is-warning'));
    } finally {
        page.close();
    }

    // 账号概览加载失败：徽标显示未知态，不进入警告态。
    // 面板平铺后每个来源在概览之外还会为芯片网格各发一次加载，8 次全部注入失败
    const failing = await bootPage({ failNext: { 'list-accounts': 8 } });
    try {
        failing.clickTab('sources');
        const badge = failing.document
            .querySelector('#sources-list li[data-source="toutiao"] .source-account-badge');
        assert.equal(badge.textContent, '账号数未知');
        assert.ok(!badge.classList.contains('is-warning'));
    } finally {
        failing.close();
    }
});

test('N13：display_name 含 HTML 时在芯片中按纯文本渲染，不生成元素', async () => {
    const payload = '<img src=x onerror=window.__xssHit=1>';
    const accounts = defaultAccounts();
    accounts.toutiao = [makeAccount({
        id: 'acc-xss-chip',
        source: 'toutiao',
        normalized_identifier: 'xss-chip-id',
        profile_url: 'https://example.com/xss-chip',
        display_name: payload,
    })];
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        const chip = await waitForChip(page, 'acc-xss-chip');
        // 名称整体作为纯文本写入，不生成任何元素、不执行注入
        assert.equal(chip.querySelector('.account-chip-name').textContent, payload);
        assert.equal(chip.querySelectorAll('img').length, 0);
        assert.equal(page.document
            .querySelector('.source-accounts-panel').querySelectorAll('img').length, 0);
        assert.equal(page.window.__xssHit, undefined);
    } finally {
        page.close();
    }
});

test('N14：启停失败回滚芯片视觉并写入共享错误行，下一次操作成功后错误清空', async () => {
    const page = await bootPage();
    try {
        await expandSource(page, 'toutiao');
        const chip = await waitForChip(page, 'acc-toutiao-1');
        const toggle = chip.querySelector('.account-enabled-toggle');
        assert.equal(toggle.checked, true);

        page.server.failNext['patch-account'] = 1;
        toggle.click();
        await waitFor(() => page.server.requests('patch-account').length === 1
            && page.server.requests('patch-account')[0].done);
        // 芯片视觉回滚，错误显示在芯片区下方的共享错误行（芯片上没有行内错误节点）
        await waitFor(() => toggle.checked === true);
        assert.match(accountsPanelError(page, 'toutiao').textContent, /状态切换失败/);
        assert.equal(chip.querySelector('.account-row-error'), null);

        // 下一次操作成功时清空错误行，徽标照常同步
        toggle.click();
        await waitFor(() => page.server.requests('patch-account').length === 2
            && page.server.requests('patch-account')[1].done);
        await waitFor(() => toggle.checked === false);
        assert.equal(accountsPanelError(page, 'toutiao').textContent, '');
        const badge = sourceRow(page, 'toutiao').querySelector('.source-account-badge');
        assert.match(badge.textContent, /启用 0 \/ 共 1/);
    } finally {
        page.close();
    }
});

test('N15：每个来源的待删标记进入自己的确认条', async () => {
    const accounts = defaultAccounts();
    accounts.tencent = [makeAccount({
        id: 'acc-tencent-1',
        source: 'tencent',
        normalized_identifier: 'tencent-one',
        enabled: true,
    })];
    const page = await bootPage({ accounts });
    try {
        await waitForChip(page, 'acc-toutiao-1');
        await waitForChip(page, 'acc-tencent-1');
        assert.equal(
            accountChip(page, 'acc-toutiao-1').querySelector('.account-enabled-toggle')
                .disabled,
            false,
        );
        assert.equal(
            accountChip(page, 'acc-tencent-1').querySelector('.account-enabled-toggle')
                .disabled,
            false,
        );

        // 各标记一个，各自面板内一条确认条，互不合并
        accountChip(page, 'acc-toutiao-1').querySelector('.account-chip-delete').click();
        accountChip(page, 'acc-tencent-1').querySelector('.account-chip-delete').click();
        assert.equal(page.document.querySelectorAll('.accounts-delete-bar').length, 2);
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');
        assert.equal(deleteBarCount(page, 'tencent'), '将删除 1 个账号');
    } finally {
        page.close();
    }
});

test('N17：芯片操作始终可用——删除按钮可点，主页链接可聚焦、点击不被拦截', async () => {
    const page = await bootPage();
    try {
        await expandSource(page, 'toutiao');
        const chip = await waitForChip(page, 'acc-toutiao-1');
        const deleteBtn = chip.querySelector('.account-chip-delete');
        assert.ok(deleteBtn);
        assert.equal(deleteBtn.disabled, false);
        const openLink = chip.querySelector('.account-chip-open');
        assert.ok(openLink);
        assert.equal(openLink.getAttribute('tabindex'), null);
        assert.equal(openLink.getAttribute('aria-hidden'), null);
        const clickEvent = new page.window.MouseEvent('click', {
            bubbles: true,
            cancelable: true,
        });
        openLink.dispatchEvent(clickEvent);
        assert.equal(clickEvent.defaultPrevented, false);
    } finally {
        page.close();
    }
});

test('N18：芯片结构稳定——label + 主页链接 + 删除按钮', async () => {
    const page = await bootPage();
    try {
        await expandSource(page, 'toutiao');
        const chip = await waitForChip(page, 'acc-toutiao-1');
        assert.equal(chip.childElementCount, 3);
        assert.ok(chip.querySelector('.account-chip-label'));
        assert.ok(chip.querySelector('.account-chip-open'));
        assert.ok(chip.querySelector('.account-chip-delete'));
    } finally {
        page.close();
    }
});

test('N19：虚线芯片就地变输入框，添加成功后输入框保持打开并清空，Esc 退回虚线形态', async () => {
    const page = await bootPage();
    try {
        await expandSource(page, 'toutiao');
        await waitForAccountActions(page);
        const grid = accountsPanel(page, 'toutiao').querySelector('.accounts-chip-grid');

        // 虚线芯片点击后就地变成输入框
        const dashed = grid.querySelector('.account-add-chip');
        assert.ok(dashed);
        dashed.click();
        const form = grid.querySelector('.account-add-inline');
        assert.ok(form, '点击后应就地变成输入框');
        assert.equal(grid.querySelector('.account-add-chip'), null);
        const input = form.querySelector('.account-add-input');
        assert.equal(input.placeholder, '主页链接或 ID');
        assert.equal(page.document.activeElement, input, '打开后焦点应进入输入框');

        // 添加成功：输入框仍在（同一节点）、值被清空、焦点保留
        input.value = 'new-acc-x';
        form.querySelector('.btn-account-add').click();
        await waitFor(() => page.server.requests('add-account').length === 1
            && page.server.requests('add-account')[0].done);
        const created = page.server.accounts.toutiao
            .find((item) => item.normalized_identifier === 'new-acc-x');
        await waitForChip(page, created.id);
        assert.equal(grid.querySelector('.account-add-input'), input, '输入框应保持打开');
        assert.equal(input.value, '', '添加成功后输入框应清空');
        assert.equal(page.document.activeElement, input, '焦点应保留在输入框');

        // Esc 退回虚线形态
        input.dispatchEvent(new page.window.KeyboardEvent('keydown', {
            key: 'Escape',
            bubbles: true,
        }));
        assert.equal(grid.querySelector('.account-add-inline'), null);
        assert.ok(grid.querySelector('.account-add-chip'));
    } finally {
        page.close();
    }
});

test('N20：确认删除只处理当前面板的标记，DELETE 按标记顺序串行发出', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(2);
    accounts.tencent = [makeAccount({
        id: 'acc-tencent-1',
        source: 'tencent',
        normalized_identifier: 'tencent-one',
        enabled: true,
    })];
    const page = await bootPage({ accounts });
    try {
        await waitForChip(page, 'tt-1');
        await waitForChip(page, 'tt-2');
        await waitForChip(page, 'acc-tencent-1');
        await waitForAccountActions(page);
        // 两个来源各标各的；toutiao 按 tt-2 → tt-1 顺序标记
        accountChip(page, 'tt-2').querySelector('.account-chip-delete').click();
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        accountChip(page, 'acc-tencent-1').querySelector('.account-chip-delete').click();
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 2 个账号');
        assert.equal(deleteBarCount(page, 'tencent'), '将删除 1 个账号');

        page.server.hold('delete-account', 1);
        accountsPanel(page, 'toutiao').querySelector('.btn-account-delete-confirm').click();
        await waitFor(() => page.server.requests('delete-account').length === 1);
        assert.equal(page.server.requests('delete-account')[0].accountId, 'tt-2');
        // 串行：第一个请求未返回前不发第二个
        await assertNever(() => page.server.requests('delete-account').length > 1, 200);
        page.server.release('delete-account');

        await waitFor(() => page.server.requests('delete-account').length === 2
            && page.server.requests('delete-account').every((entry) => entry.done));
        assert.deepEqual(
            page.server.requests('delete-account').map((entry) => entry.accountId),
            ['tt-2', 'tt-1'],
        );
        await waitFor(() => page.document.getElementById('toast').textContent
            .includes('已删除 2 个账号'));
        // 另一个来源的标记不受影响，仍留在自己的确认条里
        assert.ok(accountChip(page, 'acc-tencent-1').classList.contains('is-marked'));
        assert.equal(deleteBarCount(page, 'tencent'), '将删除 1 个账号');
    } finally {
        page.close();
    }
});

test('N21：删除提交进行中，全部来源的刷新按钮均被禁用', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(2);
    accounts.tencent = [makeAccount({
        id: 'acc-tencent-1',
        source: 'tencent',
        normalized_identifier: 'tencent-one',
        enabled: true,
    })];
    const page = await bootPage({ accounts });
    try {
        await waitForChip(page, 'tt-2');
        await waitForAccountActions(page);
        // 每个需要账号的来源行都有刷新按钮（折叠时隐藏，但节点常驻）
        const refreshButtons = () => [
            ...page.document.querySelectorAll('li[data-source] .accounts-refresh-btn'),
        ];
        assert.equal(refreshButtons().length, 4);
        refreshButtons().forEach((btn) => assert.equal(btn.disabled, false));

        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        page.server.hold('delete-account', 1);
        page.document.querySelector('.btn-account-delete-confirm').click();
        await waitFor(() => page.server.requests('delete-account').length === 1);
        // 提交进行中：全部来源的刷新按钮置灰
        refreshButtons().forEach((btn) => assert.equal(btn.disabled, true));

        page.server.release('delete-account');
        await waitFor(() => page.server.requests('delete-account')[0].done);
        await waitFor(() => page.document.getElementById('toast').textContent
            .includes('已删除 1 个账号'));
    } finally {
        page.close();
    }
});

test('N22：单列来源列表 DOM 顺序与目录序一致，账号面板紧随来源行', async () => {
    const page = await bootPage();
    try {
        // DOM 顺序与目录序一致（账号面板是紧随来源行的兄弟节点，不带 data-source）
        assert.deepEqual(
            sourceListOrder(page),
            ['toutiao', 'tencent', 'chinanews', 'btime', 'beijinghao'],
        );
        // 需要账号的来源行带 has-accounts，折叠的面板紧随其后
        for (const key of ['toutiao', 'tencent', 'btime', 'beijinghao']) {
            const row = sourceRow(page, key);
            assert.ok(row.classList.contains('has-accounts'),
                `${key} 行应带 has-accounts`);
            const panel = row.nextElementSibling;
            assert.ok(panel && panel.classList.contains('source-accounts-panel'),
                `${key} 的账号面板应紧随其后`);
            assert.equal(panel.dataset.accountsFor, key);
        }
        // 无子账号的来源不带 has-accounts，后面也不跟账号面板
        const plain = sourceRow(page, 'chinanews');
        assert.ok(!plain.classList.contains('has-accounts'));
        assert.ok(!plain.nextElementSibling.classList.contains('source-accounts-panel'));
    } finally {
        page.close();
    }
});

test('N23：刷新进行中启停其他来源触发整块重渲染，刷新收尾不写入已脱离文档的按钮', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(2);
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-2');
        await waitForAccountActions(page);
        page.server.hold('refresh-names');
        const oldBtn = sourceRow(page, 'toutiao').querySelector('.accounts-refresh-btn');
        oldBtn.click();
        await waitFor(() => page.server.requests('refresh-names').length === 1);
        assert.match(oldBtn.textContent, /刷新中… 0\/2/);

        // 刷新进行中停用另一个来源：整块重渲染，旧刷新按钮脱离文档
        sourceRow(page, 'chinanews').querySelector('.source-enabled-toggle').click();
        await waitFor(() => page.server.requests('save-sources').length === 1
            && page.server.requests('save-sources')[0].done);
        await waitFor(() => {
            const btn = sourceRow(page, 'toutiao').querySelector('.accounts-refresh-btn');
            return btn && btn !== oldBtn;
        });
        assert.equal(oldBtn.isConnected, false);
        // 重建后的按钮保持禁用（refreshInflight 状态保留）
        assert.equal(
            sourceRow(page, 'toutiao').querySelector('.accounts-refresh-btn').disabled,
            true,
        );

        page.server.release('refresh-names');
        await waitFor(() => page.server.requests('refresh-names')[0].done);
        await waitFor(() => page.document.getElementById('toast').textContent
            .includes('已更新 2 个名称'));
        // 收尾落在重建后的按钮上：恢复可用且文案还原
        await waitFor(() => {
            const btn = sourceRow(page, 'toutiao').querySelector('.accounts-refresh-btn');
            return btn && !btn.disabled && btn.textContent === '刷新账号名称';
        });
        // 收尾不写入已脱离文档的旧按钮（isConnected 守卫）
        assert.equal(oldBtn.textContent, '刷新中… 0/2');
        assert.deepEqual(unhandledRejections, []);
    } finally {
        page.close();
    }
});

// ---------- 接入点场景（E1-E12） ----------

function endpointsBlock(page) {
    return page.document.querySelector('.endpoints-block');
}

async function enterEndpointsManage(page) {
    page.document.querySelector('.endpoints-manage-btn').click();
    await waitFor(() => page.document.querySelector('.endpoint-edit-row'));
}

// 已保存接入点的编辑行：clientId 与 key 相同；草稿新增行是 new-N，用 data-client-id 查
function endpointEditRow(page, clientId) {
    return page.document
        .querySelector(`.endpoint-edit-row[data-client-id="${clientId}"]`);
}

function endpointOptions(page, step) {
    return [...page.modelsRow(step).querySelector('.step-endpoint-select').options]
        .map((option) => option.value);
}

function switchStepMode(page, row, mode) {
    const modeSelect = row.querySelector('.step-model-mode');
    modeSelect.value = mode;
    modeSelect.dispatchEvent(new page.window.Event('change', { bubbles: true }));
}

test('E1：步骤切到「指定」出现接入点下拉与空的模型输入框，切换接入点清空模型与该行测试结果', async () => {
    const page = await bootPage();
    try {
        const row = page.modelsRow('summary');
        // 跟随默认提示同时给出默认接入点与默认模型
        const hint = row.querySelector('.step-follow-hint');
        assert.match(
            hint.textContent,
            /跟随默认（接入点：OpenRouter · 模型：deepseek\/default-model）/,
        );

        // 先在跟随默认下跑一次测试，让该行有测试结果可供「被清除」断言
        row.querySelector('.step-test-btn').click();
        await waitFor(() => row.querySelector('.step-test-result').textContent.includes('成功'));

        switchStepMode(page, row, 'custom');
        const endpointSelect = row.querySelector('.step-endpoint-select');
        await waitFor(() => !endpointSelect.hidden);
        const modelInput = row.querySelector('.step-model-input');
        assert.ok(!modelInput.hidden);
        // 切到「指定」不预填：跨服务商预填必然是错的，留空比留错值安全
        assert.equal(modelInput.value, '');
        assert.equal(endpointSelect.value, 'openrouter', '首次切到指定应选中当前默认接入点');
        assert.equal(row.querySelector('.step-test-result').textContent, '');

        // 填入模型并再次产生测试结果后换接入点：模型输入与测试结果都被清空
        inputValue(page, modelInput, 'deepseek/reasoner-v4');
        row.querySelector('.step-test-btn').click();
        await waitFor(() => row.querySelector('.step-test-result').textContent.includes('成功'));
        endpointSelect.value = 'deepseek';
        endpointSelect.dispatchEvent(new page.window.Event('change', { bubbles: true }));
        assert.equal(modelInput.value, '');
        assert.equal(row.querySelector('.step-test-result').textContent, '');
    } finally {
        page.close();
    }
});

test('E2：保存请求体的七个步骤都带 endpoint，跟随默认为 null，指定的为所选 key', async () => {
    const page = await bootPage();
    try {
        const row = page.modelsRow('summary');
        switchStepMode(page, row, 'custom');
        // 先换接入点再填模型（换接入点会清空模型输入）
        const endpointSelect = row.querySelector('.step-endpoint-select');
        endpointSelect.value = 'deepseek';
        endpointSelect.dispatchEvent(new page.window.Event('change', { bubbles: true }));
        inputValue(page, row.querySelector('.step-model-input'), 'deepseek/chat-v4');

        page.document.getElementById('btn-models-save').click();
        await waitFor(() => page.server.requests('save-models').length === 1);
        const steps = page.server.requests('save-models')[0].body.value.steps;
        const expectedKeys = [
            'beijing_gate',
            'duplicate_review',
            'external_filter',
            'scoring',
            'sentiment',
            'source',
            'summary',
        ];
        assert.deepEqual(Object.keys(steps).sort(), expectedKeys);
        expectedKeys.forEach((key) => {
            assert.ok('endpoint' in steps[key], `${key} 必须带 endpoint 字段`);
        });
        assert.equal(steps.summary.endpoint, 'deepseek');
        assert.equal(steps.summary.model, 'deepseek/chat-v4');
        for (const key of ['source', 'sentiment', 'external_filter', 'beijing_gate', 'duplicate_review']) {
            assert.equal(steps[key].endpoint, null, `${key} 跟随默认时 endpoint 应为 null`);
            assert.equal(steps[key].model, null, `${key} 跟随默认时 model 应为 null`);
        }
        // 旧数据（指定模型但无 endpoint）归一为默认接入点：与后端 resolve 语义一致
        assert.equal(steps.scoring.endpoint, 'openrouter');
        assert.equal(steps.scoring.model, 'vendor/scoring-model');
    } finally {
        page.close();
    }
});

test('E3：指定接入点但模型为空时保存被前端拦截，不发请求并行内报错', async () => {
    const page = await bootPage();
    try {
        const row = page.modelsRow('summary');
        switchStepMode(page, row, 'custom');
        // 模型输入留空

        page.document.getElementById('btn-models-save').click();
        // 判别性断言最先检查：拦截意味着没有保存请求
        await assertNever(() => page.server.requests('save-models').length > 0, 300);
        const status = page.document.getElementById('models-save-status');
        assert.match(status.textContent, /摘要生成/);
        assert.match(status.textContent, /模型名为空/);
        assert.ok(status.classList.contains('is-error'));
    } finally {
        page.close();
    }
});

test('E4：步骤接入点下拉只列已保存接入点；接入点分区为脏时步骤表上方出现提示', async () => {
    const page = await bootPage();
    try {
        await enterEndpointsManage(page);
        // 制造脏标记：改已保存接入点的标签
        inputValue(page, endpointEditRow(page, 'openrouter').querySelector('.endpoint-label-input'),
            'OpenRouter 改');
        const note = page.document.querySelector('.endpoints-dirty-note');
        assert.ok(note, '步骤表上方应有脏提示节点');
        assert.ok(!note.hidden);
        assert.match(note.textContent, /接入点有未保存修改/);

        // 草稿里新增的接入点不出现在步骤下拉：后端校验引用查的是库里的接入点
        page.document.querySelector('.endpoint-add-btn').click();
        await waitFor(() => page.document.querySelectorAll('.endpoint-edit-row').length === 3);
        inputValue(page, endpointEditRow(page, 'new-1').querySelector('.endpoint-base-url-input'),
            'https://open.bigmodel.cn/api/paas/v4');
        assert.deepEqual(endpointOptions(page, 'summary'), ['openrouter', 'deepseek']);
        assert.equal(page.window.eval('state.dirty.llm_endpoints'), true);

        // 模型页签整体重渲染（放弃修改会重建全部 DOM）后，下拉仍只来自已保存值：
        // 重渲染发生在接入点草稿为脏期间，绝不能把草稿里的新接入点带进选项
        page.document.getElementById('btn-models-discard').click();
        await waitFor(() => page.modelsRow('summary')
            && !page.document.querySelector('.endpoints-dirty-note').hidden);
        assert.deepEqual(endpointOptions(page, 'summary'), ['openrouter', 'deepseek']);

        // 放弃接入点修改：提示消失、脏标记复位、下拉不变
        page.document.querySelector('.btn-endpoints-discard').click();
        await waitFor(() => page.document.querySelector('.endpoints-dirty-note').hidden);
        assert.equal(page.window.eval('state.dirty.llm_endpoints'), false);
        assert.deepEqual(endpointOptions(page, 'summary'), ['openrouter', 'deepseek']);
        assert.equal(page.window.eval('state.dirty.llm_models'), false);
    } finally {
        page.close();
    }
});

test('E5：删除被步骤引用的接入点被拦截，提示包含引用步骤的中文名，不发请求', async () => {
    const sections = defaultSections();
    // 已保存的模型配置里，相关性评分指定了 deepseek 接入点
    sections.llm_models.value.steps.scoring.endpoint = 'deepseek';
    const page = await bootPage({ sections });
    try {
        await enterEndpointsManage(page);
        const row = endpointEditRow(page, 'deepseek');
        row.querySelector('.endpoint-delete-btn').click();
        // 拦截 = 不发出保存请求，行也不从草稿中移除
        await assertNever(() => page.server.requests('save-endpoints').length > 0, 300);
        const error = row.querySelector('.endpoint-row-error');
        assert.match(error.textContent, /DeepSeek/);
        assert.match(error.textContent, /相关性评分/);
        assert.ok(endpointEditRow(page, 'deepseek'), '被引用的接入点不应从草稿移除');
    } finally {
        page.close();
    }
});

test('E6：删除默认接入点被拦截；仅剩一个接入点时删除同样被拦截', async () => {
    const page = await bootPage();
    try {
        await enterEndpointsManage(page);
        const row = endpointEditRow(page, 'openrouter');
        row.querySelector('.endpoint-delete-btn').click();
        await assertNever(() => page.server.requests('save-endpoints').length > 0, 300);
        assert.match(row.querySelector('.endpoint-row-error').textContent, /默认接入点/);
        assert.match(row.querySelector('.endpoint-row-error').textContent, /OpenRouter/);
        assert.ok(endpointEditRow(page, 'openrouter'));
    } finally {
        page.close();
    }

    // 只剩一个接入点（必然是默认）时：删除被「至少保留一个」拦截
    const sections = defaultSections();
    sections.llm_endpoints.value.items = sections.llm_endpoints.value.items.slice(0, 1);
    const single = await bootPage({ sections });
    try {
        await enterEndpointsManage(single);
        const row = endpointEditRow(single, 'openrouter');
        row.querySelector('.endpoint-delete-btn').click();
        await assertNever(() => single.server.requests('save-endpoints').length > 0, 300);
        assert.match(row.querySelector('.endpoint-row-error').textContent, /至少保留一个接入点/);
        assert.ok(endpointEditRow(single, 'openrouter'));
    } finally {
        single.close();
    }
});

test('E7：地址主机不在白名单时行内报错且不发请求；完整请求地址提示随输入实时更新', async () => {
    const page = await bootPage();
    try {
        await enterEndpointsManage(page);
        const row = endpointEditRow(page, 'deepseek');
        const urlInput = row.querySelector('.endpoint-base-url-input');
        const preview = row.querySelector('.endpoint-url-preview');
        // 初始预览 = 已保存 base_url + /chat/completions；DeepSeek 没有 /v1 层
        assert.equal(preview.textContent, 'https://api.deepseek.com/chat/completions');
        inputValue(page, urlInput, 'https://api.deepseek.com/v1/');
        assert.equal(preview.textContent, 'https://api.deepseek.com/v1/chat/completions');

        // 白名单外主机：保存被行内报错拦下，不发请求
        inputValue(page, urlInput, 'https://evil.example.com/v1');
        assert.equal(preview.textContent, 'https://evil.example.com/v1/chat/completions');
        page.document.querySelector('.btn-endpoints-save').click();
        await assertNever(() => page.server.requests('save-endpoints').length > 0, 300);
        const rowError = row.querySelector('.endpoint-row-error');
        assert.match(rowError.textContent, /evil\.example\.com/);
        assert.match(rowError.textContent, /LLM_ALLOWED_HOSTS/);

        // 非 https 同样被拦
        inputValue(page, urlInput, 'http://api.deepseek.com');
        page.document.querySelector('.btn-endpoints-save').click();
        await assertNever(() => page.server.requests('save-endpoints').length > 0, 300);
        assert.match(row.querySelector('.endpoint-row-error').textContent, /https/);
    } finally {
        page.close();
    }
});

test('E8：接入点保存成功后下拉立即包含新接入点，步骤表格未保存的模型修改不丢失', async () => {
    const page = await bootPage();
    try {
        // 先在模型页制造未保存修改：摘要生成切「指定」并填模型
        const row = page.modelsRow('summary');
        switchStepMode(page, row, 'custom');
        inputValue(page, row.querySelector('.step-model-input'), 'local/unsaved-model');
        assert.equal(page.window.eval('state.dirty.llm_models'), true);

        // 接入点编辑态新增 glm 并保存：key 无需起名，按地址自动生成
        await enterEndpointsManage(page);
        page.document.querySelector('.endpoint-add-btn').click();
        await waitFor(() => endpointEditRow(page, 'new-1'));
        const newRow = endpointEditRow(page, 'new-1');
        inputValue(page, newRow.querySelector('.endpoint-label-input'), '智谱 GLM');
        inputValue(page, newRow.querySelector('.endpoint-base-url-input'),
            'https://open.bigmodel.cn/api/paas/v4');
        inputValue(page, newRow.querySelector('.endpoint-key-env-input'), 'ZHIPU_API_KEY');
        page.document.querySelector('.btn-endpoints-save').click();
        await waitFor(() => page.server.requests('save-endpoints').length === 1
            && page.server.requests('save-endpoints')[0].done);

        // 保存成功后编辑器回到只读态，下拉立即包含新接入点（key 从主机名派生）
        await waitFor(() => !page.document.querySelector('.endpoint-edit-row'));
        assert.deepEqual(endpointOptions(page, 'summary'),
            ['openrouter', 'deepseek', 'open-bigmodel-cn']);

        // 步骤表格里未保存的模型修改仍在（只重建 DOM，不重建模型草稿）
        const rowAfter = page.modelsRow('summary');
        assert.equal(rowAfter.querySelector('.step-model-mode').value, 'custom');
        assert.equal(rowAfter.querySelector('.step-model-input').value, 'local/unsaved-model');
        assert.equal(page.window.eval('state.dirty.llm_models'), true);
        assert.equal(page.window.eval('state.dirty.llm_endpoints'), false);
    } finally {
        page.close();
    }
});

test('E9：模型分区与接入点分区的脏状态互相独立，任一为脏都触发离开确认', async () => {
    const page = await bootPage();
    try {
        const guardFires = () => {
            const event = new page.window.Event('beforeunload', { cancelable: true });
            page.window.dispatchEvent(event);
            return event.defaultPrevented;
        };
        assert.equal(guardFires(), false, '初始无修改不应拦截离开');

        // 仅模型分区脏
        inputValue(page, page.document.getElementById('models-default-input'), 'x/model-a');
        assert.equal(page.window.eval('state.dirty.llm_models'), true);
        assert.equal(page.window.eval('state.dirty.llm_endpoints'), false);
        assert.equal(guardFires(), true);
        page.document.getElementById('btn-models-discard').click();
        assert.equal(page.window.eval('state.dirty.llm_models'), false);
        assert.equal(guardFires(), false);

        // 仅接入点分区脏
        await enterEndpointsManage(page);
        inputValue(page, endpointEditRow(page, 'openrouter').querySelector('.endpoint-label-input'),
            'OpenRouter X');
        assert.equal(page.window.eval('state.dirty.llm_endpoints'), true);
        assert.equal(page.window.eval('state.dirty.llm_models'), false);
        assert.equal(guardFires(), true);
        page.document.querySelector('.btn-endpoints-discard').click();
        await waitFor(() => page.document.querySelector('.endpoints-dirty-note').hidden);
        assert.equal(page.window.eval('state.dirty.llm_endpoints'), false);
        assert.equal(guardFires(), false);
    } finally {
        page.close();
    }
});

test('E10：测试按钮对「指定」步骤发送 endpoint，对「跟随默认」步骤不发送该字段', async () => {
    const page = await bootPage();
    try {
        const row = page.modelsRow('summary');
        switchStepMode(page, row, 'custom');
        inputValue(page, row.querySelector('.step-model-input'), 'm/custom');

        row.querySelector('.step-test-btn').click();
        await waitFor(() => page.server.requests('model-test').length === 1);
        const customBody = page.server.requests('model-test')[0].body;
        assert.equal(customBody.endpoint, 'openrouter', '指定步骤应带上所选接入点');
        assert.equal(customBody.model, 'm/custom');
        await waitFor(() => page.server.requests('model-test')[0].done);

        // 切回跟随默认：不带 endpoint 字段，模型取默认模型输入框当前值
        switchStepMode(page, row, 'follow');
        row.querySelector('.step-test-btn').click();
        await waitFor(() => page.server.requests('model-test').length === 2);
        const followBody = page.server.requests('model-test')[1].body;
        assert.equal('endpoint' in followBody, false, '跟随默认不应发送 endpoint 字段');
        assert.equal(followBody.model, 'deepseek/default-model');
    } finally {
        page.close();
    }
});

test('E11：接入点标签含 HTML 时按纯文本渲染，不生成元素', async () => {
    const payload = '<img src=x onerror=window.__xssHit=1>';
    const sections = defaultSections();
    sections.llm_endpoints.value.items[0].label = payload;
    const page = await bootPage({ sections });
    try {
        const block = endpointsBlock(page);
        assert.equal(block.querySelectorAll('img').length, 0);
        assert.equal(block.querySelector('.endpoint-item-label').textContent, payload);
        assert.equal(page.window.__xssHit, undefined);

        // 编辑态同样按纯文本进入输入框 value
        await enterEndpointsManage(page);
        assert.equal(endpointsBlock(page).querySelectorAll('img').length, 0);
        assert.equal(
            endpointEditRow(page, 'openrouter').querySelector('.endpoint-label-input').value,
            payload,
        );
        assert.equal(page.window.__xssHit, undefined);
    } finally {
        page.close();
    }
});

test('E12：环境块不再显示 API 地址与 API Key，改为显示允许的接入点主机', async () => {
    const page = await bootPage();
    try {
        const envBlock = page.document.querySelector('.settings-env-block');
        const text = envBlock.textContent;
        assert.ok(!text.includes('API 地址'), '环境块不应再有 API 地址（字段已从接口移除）');
        assert.ok(!text.includes('API Key'), '环境块不应再有 API Key（Key 归各接入点）');
        assert.match(text, /向量模型/);
        assert.match(text, /允许的接入点主机/);
        assert.match(text, /openrouter\.ai, api\.deepseek\.com, open\.bigmodel\.cn/);
    } finally {
        page.close();
    }
});

// ---------- 接入点补丁场景（E13-E16） ----------

test('E13：接入点 409 后「载入最新配置」同步刷新步骤下拉，模型草稿保留', async () => {
    const page = await bootPage();
    try {
        // 模型页先制造未保存修改：摘要生成切「指定」+ 模型名
        const row = page.modelsRow('summary');
        switchStepMode(page, row, 'custom');
        inputValue(page, row.querySelector('.step-model-input'), 'local/unsaved-model');

        // 接入点保存撞 409（另一处已改），保存栏出现「载入最新配置」
        await enterEndpointsManage(page);
        inputValue(page, endpointEditRow(page, 'openrouter').querySelector('.endpoint-label-input'),
            'OpenRouter 409');
        page.server.saveBehavior.llm_endpoints = {
            status: 409,
            payload: { detail: '配置版本已变化：当前版本为 4' },
        };
        page.document.querySelector('.btn-endpoints-save').click();
        await waitFor(() => page.server.requests('save-endpoints').length === 1
            && page.server.requests('save-endpoints')[0].done);
        const reloadBtn = page.document.querySelector('.btn-endpoints-reload');
        await waitFor(() => !reloadBtn.hidden);

        // 另一处已删掉 deepseek：重拉后的已保存清单里只剩 openrouter
        const sections = page.server.sections;
        sections.llm_endpoints = {
            ...sections.llm_endpoints,
            value: {
                default: 'openrouter',
                items: [sections.llm_endpoints.value.items[0]],
            },
            version: 4,
        };

        reloadBtn.click();
        // 步骤下拉与重拉后的接入点清单一致（deepseek 消失）
        await waitFor(() => endpointOptions(page, 'summary').length === 1);
        assert.deepEqual(endpointOptions(page, 'summary'), ['openrouter']);
        // 步骤表格里未保存的模型修改仍在（只重建 DOM，不重建模型草稿）
        const rowAfter = page.modelsRow('summary');
        assert.equal(rowAfter.querySelector('.step-model-mode').value, 'custom');
        assert.equal(rowAfter.querySelector('.step-model-input').value, 'local/unsaved-model');
        assert.equal(page.window.eval('state.dirty.llm_models'), true);
        assert.equal(page.window.eval('state.dirty.llm_endpoints'), false);
    } finally {
        page.close();
    }
});

test('E14：「载入最新配置」失败时不重渲染，行内错误仍可见', async () => {
    const page = await bootPage();
    try {
        await enterEndpointsManage(page);
        // 先让保存撞 409，让「载入最新配置」按钮出现
        inputValue(page, endpointEditRow(page, 'openrouter').querySelector('.endpoint-label-input'),
            'OpenRouter 409');
        page.server.saveBehavior.llm_endpoints = {
            status: 409,
            payload: { detail: '配置版本已变化：当前版本为 4' },
        };
        page.document.querySelector('.btn-endpoints-save').click();
        await waitFor(() => page.server.requests('save-endpoints').length === 1
            && page.server.requests('save-endpoints')[0].done);
        const reloadBtn = page.document.querySelector('.btn-endpoints-reload');
        await waitFor(() => !reloadBtn.hidden);

        // 再制造一行行内校验错误（白名单外地址，前端拦截不发请求）
        const deepseekRow = endpointEditRow(page, 'deepseek');
        const rowError = deepseekRow.querySelector('.endpoint-row-error');
        inputValue(page, deepseekRow.querySelector('.endpoint-base-url-input'),
            'https://evil.example.com/v1');
        page.document.querySelector('.btn-endpoints-save').click();
        await waitFor(() => rowError.textContent.includes('evil.example.com'));

        // 载入失败（GET settings 注入 500）：不重渲染，行内错误与节点保持原样
        const rowBefore = endpointEditRow(page, 'deepseek');
        const modelsRowBefore = page.modelsRow('summary');
        page.server.failNext['get-settings'] = 1;
        reloadBtn.click();
        await waitFor(() => page.server.requests('get-settings').length === 1
            && page.server.requests('get-settings')[0].done);
        await waitFor(() => page.document.querySelector('.endpoints-save-status').textContent
            .includes('载入失败'));
        assert.equal(page.document.querySelectorAll('.endpoint-edit-row').length, 2);
        assert.equal(endpointEditRow(page, 'deepseek'), rowBefore, '失败分支不得重建编辑行');
        assert.equal(rowError.textContent.includes('evil.example.com'), true);
        assert.ok(rowError.isConnected);
        assert.equal(page.modelsRow('summary'), modelsRowBefore, '失败分支不得重建步骤表格');
        assert.equal(page.window.eval('state.dirty.llm_endpoints'), true);
    } finally {
        page.close();
    }
});

test('E15：步骤草稿指向的接入点删除同样被拦截，提示含步骤中文名与保存指引', async () => {
    const page = await bootPage();
    try {
        // 已保存值无人引用 deepseek；只在草稿里把摘要生成改指向它
        const row = page.modelsRow('summary');
        switchStepMode(page, row, 'custom');
        const endpointSelect = row.querySelector('.step-endpoint-select');
        endpointSelect.value = 'deepseek';
        endpointSelect.dispatchEvent(new page.window.Event('change', { bubbles: true }));
        inputValue(page, row.querySelector('.step-model-input'), 'deepseek/chat-v4');
        assert.equal(page.window.eval('state.dirty.llm_models'), true);

        await enterEndpointsManage(page);
        const deepseekRow = endpointEditRow(page, 'deepseek');
        deepseekRow.querySelector('.endpoint-delete-btn').click();
        // 拦截 = 不发保存请求、行不移除；提示同时给出切步骤与保存模型配置的指引
        await assertNever(() => page.server.requests('save-endpoints').length > 0, 300);
        const error = deepseekRow.querySelector('.endpoint-row-error');
        assert.match(error.textContent, /DeepSeek/);
        assert.match(error.textContent, /摘要生成/);
        assert.match(error.textContent, /保存模型配置/);
        assert.ok(endpointEditRow(page, 'deepseek'), '草稿引用的接入点不应从草稿移除');
    } finally {
        page.close();
    }
});

test('E16：「有 endpoint、无 model」的行点保存走空模型拦截，不抛异常不发请求', async () => {
    const page = await bootPage({ sections: sectionsWithBareEndpointStep() });
    try {
        // 相关性评分在库里是 endpoint=deepseek、model=null：渲染为指定 + 空输入框
        const row = page.modelsRow('scoring');
        assert.equal(row.querySelector('.step-model-mode').value, 'custom');
        assert.equal(row.querySelector('.step-model-input').value, '');

        page.document.getElementById('btn-models-save').click();
        // 判别性断言最先检查：走拦截意味着没有保存请求
        await assertNever(() => page.server.requests('save-models').length > 0, 300);
        const status = page.document.getElementById('models-save-status');
        assert.match(status.textContent, /相关性评分/);
        assert.match(status.textContent, /模型名为空/);
        assert.ok(status.classList.contains('is-error'));
        // 手改行不得在保存路径里抛未处理异常
        assert.deepEqual(unhandledRejections, []);
    } finally {
        page.close();
    }
});

// ---------- 接入点 key 自动生成场景（E17） ----------

test('E17：新增接入点 key 按地址自动生成，头部实时预览，重名自动加后缀', async () => {
    const page = await bootPage();
    try {
        await enterEndpointsManage(page);
        // 新增卡不再有 key 输入框，头部是预览芯片：初始无地址时给占位
        page.document.querySelector('.endpoint-add-btn').click();
        await waitFor(() => endpointEditRow(page, 'new-1'));
        const newRow = endpointEditRow(page, 'new-1');
        assert.equal(newRow.querySelector('.endpoint-key-input'), null,
            '新增卡不应再有 key 输入框');
        const preview = newRow.querySelector('.endpoint-key-static');
        assert.ok(preview, '头部应有 key 预览芯片');
        assert.equal(preview.textContent, 'endpoint', '无地址时显示占位标识');

        // 输入地址后 key 实时派生：api.deepseek.com → api-deepseek-com
        inputValue(page, newRow.querySelector('.endpoint-base-url-input'),
            'https://api.deepseek.com');
        await waitFor(() => preview.textContent === 'api-deepseek-com');

        // 同主机再加一个：保存时 key 自动加 -2 后缀去重
        page.document.querySelector('.endpoint-add-btn').click();
        await waitFor(() => endpointEditRow(page, 'new-2'));
        const row2 = endpointEditRow(page, 'new-2');
        inputValue(page, row2.querySelector('.endpoint-base-url-input'),
            'https://api.deepseek.com/v1');
        inputValue(page, newRow.querySelector('.endpoint-label-input'), 'DeepSeek 主');
        inputValue(page, row2.querySelector('.endpoint-label-input'), 'DeepSeek 备用');
        inputValue(page, newRow.querySelector('.endpoint-key-env-input'), 'DEEPSEEK_API_KEY');
        inputValue(page, row2.querySelector('.endpoint-key-env-input'), 'DEEPSEEK_API_KEY_2');
        page.document.querySelector('.btn-endpoints-save').click();
        await waitFor(() => page.server.requests('save-endpoints').length === 1
            && page.server.requests('save-endpoints')[0].done);

        const keys = page.server.sections.llm_endpoints.value.items.map((item) => item.key);
        assert.deepEqual(keys.sort(),
            ['api-deepseek-com', 'api-deepseek-com-2', 'deepseek', 'openrouter']);
        // 步骤下拉立即能看到两个新接入点（下拉值是派生 key）
        await waitFor(() => !page.document.querySelector('.endpoint-edit-row'));
        assert.deepEqual(endpointOptions(page, 'summary'),
            ['openrouter', 'deepseek', 'api-deepseek-com', 'api-deepseek-com-2']);
    } finally {
        page.close();
    }
});

// ---------- 环境变量重载场景（E18-E19） ----------

test('E18：「重新加载环境变量」发出 POST 并重拉设置，Key 配置状态翻转为已配置，模型草稿保留', async () => {
    const page = await bootPage();
    try {
        // 只读接入点列表初始为「Key 未配置」（fixture 里 deepseek 未配置）
        const deepseekBefore = page.document
            .querySelector('.endpoint-item[data-endpoint-key="deepseek"]');
        assert.match(deepseekBefore.textContent, /Key 未配置/);

        // 制造未保存的模型修改，验证重载后的页签重建不丢草稿
        inputValue(page, page.document.getElementById('models-default-input'),
            'local/draft-model');

        // 重载时模拟服务器端效果：.env 里补上 Key 后，endpoints 负载翻为已配置
        page.server.reloadEnvBehavior = () => {
            page.server.endpoints.items[1].api_key_env_configured = true;
        };

        page.document.querySelector('.env-reload-btn').click();
        await waitFor(() => page.server.requests('reload-env').length === 1
            && page.server.requests('reload-env')[0].done);
        // 重载成功后必须重拉设置，让 api_key_env_configured 重新计算
        await waitFor(() => page.server.requests('get-settings').length === 1
            && page.server.requests('get-settings')[0].done);
        await waitFor(() => page.document.getElementById('toast').textContent
            .includes('环境变量已重新加载'));

        // 重建后的只读列表状态翻为「Key 已配置」
        await waitFor(() => {
            const item = page.document
                .querySelector('.endpoint-item[data-endpoint-key="deepseek"]');
            return item && item.textContent.includes('Key 已配置');
        });
        // 模型草稿保留（只重建 DOM，不重建模型草稿）
        assert.equal(page.window.eval('state.dirty.llm_models'), true);
        assert.equal(
            page.document.getElementById('models-default-input').value,
            'local/draft-model',
        );
    } finally {
        page.close();
    }
});

test('E19：重载失败时不重拉设置、不重渲染，行内错误可见且按钮恢复可用', async () => {
    const page = await bootPage();
    try {
        page.server.failNext['reload-env'] = 1;
        const btn = page.document.querySelector('.env-reload-btn');
        btn.click();
        await waitFor(() => page.server.requests('reload-env').length === 1
            && page.server.requests('reload-env')[0].done);
        await waitFor(() => page.document
            .querySelector('.env-reload-status').textContent.includes('重载失败'));

        // 判别性断言：失败分支不重拉设置，也不重建环境块
        assert.equal(page.server.requests('get-settings').length, 0);
        await assertNever(() => page.server.requests('get-settings').length > 0, 200);
        assert.equal(page.document.querySelector('.env-reload-btn'), btn);
        assert.equal(btn.disabled, false);
    } finally {
        page.close();
    }
});


// 二期设置：F1-F12 用真实 DOM 交互和请求日志锁定行为。
function dictionaryBlock(page, section) {
    return section === 'score_keyword_bonuses' ? page.panel('bonuses')
        : page.document.querySelector(`[data-section="${section}"]`);
}
function dictionaryPuts(page) { return page.server.log.filter((entry) => entry.method === 'PUT'); }
async function saveDictionary(page, section) {
    dictionaryBlock(page, section).querySelector('.section-save').click();
    await waitFor(() => page.server.inflight === 0);
}

// 加分词典是即时保存：改完值后焦点离开整行（change + focusout）即触发整段 PUT；
// focusout 同时负责把行挪到对应的正负分组
function commitBonusValue(page, el, value) {
    el.value = value;
    el.dispatchEvent(new page.window.Event('input', { bubbles: true }));
    el.dispatchEvent(new page.window.Event('change', { bubbles: true }));
    el.dispatchEvent(new page.window.FocusEvent('focusout', {
        bubbles: true,
        relatedTarget: page.panel('bonuses').querySelector('.bonus-title'),
    }));
}
function leaveBonusRow(page, row) {
    row.querySelector('.bonus-value').dispatchEvent(new page.window.FocusEvent('focusout', {
        bubbles: true,
        relatedTarget: page.panel('bonuses').querySelector('.bonus-title'),
    }));
}
function unloadBlocked(page) {
    const event = new page.window.Event('beforeunload', { cancelable: true });
    page.window.dispatchEvent(event);
    return event.defaultPrevented;
}

test('F1：改分值增行删行后按词典顺序（先加分后减分）自动保存', async () => {
    const page = await bootPage({ hash: '#bonuses' });
    try {
        const panel = page.panel('bonuses');
        const firstRow = bonusRows(page)[0];
        commitBonusValue(page, firstRow.querySelector('.bonus-value'), '-100');
        await waitFor(() => dictionaryPuts(page).length === 1 && page.server.inflight === 0);
        leaveBonusRow(page, firstRow);
        await waitFor(() => bonusGroupRows(page, 'negative').length === 1);

        panel.querySelector('.bonus-add').click();
        const positiveRows = bonusGroupRows(page, 'positive');
        const newRow = positiveRows[positiveRows.length - 1];
        inputValue(page, newRow.querySelector('.bonus-keyword'), '  专题B  ');
        commitBonusValue(page, newRow.querySelector('.bonus-value'), '100');
        await waitFor(() => dictionaryPuts(page).length === 2 && page.server.inflight === 0);

        bonusGroupRows(page, 'positive')[0].querySelector('.bonus-delete').click();
        await waitFor(() => dictionaryPuts(page).length === 3 && page.server.inflight === 0);

        assert.deepEqual(dictionaryPuts(page).map(({ path, body }) => ({ path, body })), [
            {
                path: '/api/admin/settings/score_keyword_bonuses',
                body: {
                    expected_version: 11,
                    value: [{ keyword: '专题A', bonus: 20 }, { keyword: '专题Z', bonus: -100 }],
                },
            },
            {
                path: '/api/admin/settings/score_keyword_bonuses',
                body: {
                    expected_version: 12,
                    value: [
                        { keyword: '专题A', bonus: 20 },
                        { keyword: '专题B', bonus: 100 },
                        { keyword: '专题Z', bonus: -100 },
                    ],
                },
            },
            {
                path: '/api/admin/settings/score_keyword_bonuses',
                body: {
                    expected_version: 13,
                    value: [{ keyword: '专题B', bonus: 100 }, { keyword: '专题Z', bonus: -100 }],
                },
            },
        ]);
        // 焦点离开后行已归组：加分词在上、减分词在下
        assert.deepEqual([...panel.querySelectorAll('.bonus-keyword')].map((el) => el.value), ['专题B', '专题Z']);
        assert.match(panel.querySelector('.bonus-save-status').textContent, /已自动保存/);
        assert.equal(unloadBlocked(page), false);
    } finally { page.close(); }
});

function bonusGroupKeys(page) {
    return [...page.panel('bonuses').querySelectorAll('.bonus-group')]
        .map((group) => group.dataset.bonusGroup);
}

function bonusGroupRows(page, key) {
    return [...page.panel('bonuses')
        .querySelectorAll(`.bonus-group[data-bonus-group="${key}"] .bonus-row:not(.bonus-heading)`)];
}

test('加分词典按正负分组：加分词在上、减分词在下，写入也是先加分后减分', async () => {
    const page = await bootPage({ hash: '#bonuses' });
    try {
        const panel = page.panel('bonuses');
        assert.deepEqual(bonusGroupKeys(page), ['positive', 'negative']);
        assert.equal(bonusGroupRows(page, 'negative').length, 0);
        assert.equal(
            panel.querySelector('.bonus-group[data-bonus-group="negative"] .bonus-group-empty').hidden,
            false,
        );

        // 改完分值即自动提交：仍按加分在前、减分在后回传
        const rows = bonusRows(page);
        commitBonusValue(page, rows[1].querySelector('.bonus-value'), '-20');
        await waitFor(() => dictionaryPuts(page).length === 1 && page.server.inflight === 0);
        assert.deepEqual(dictionaryPuts(page)[0].body.value, [
            { keyword: '专题Z', bonus: 10 }, { keyword: '专题A', bonus: -20 },
        ]);

        // 焦点离开后按新顺序归组：A 进减分组、Z 留加分组
        leaveBonusRow(page, rows[1]);
        await waitFor(() => bonusGroupRows(page, 'negative').length === 1);
        assert.deepEqual(bonusGroupRows(page, 'negative')
            .map((row) => row.querySelector('.bonus-keyword').value), ['专题A']);
        assert.deepEqual(bonusGroupRows(page, 'positive')
            .map((row) => row.querySelector('.bonus-keyword').value), ['专题Z']);
        assert.equal(
            panel.querySelector('.bonus-group[data-bonus-group="negative"] .bonus-group-count').textContent,
            '1 条',
        );
    } finally { page.close(); }
});

test('加分词典：分值改成负数并离开该行后自动归入减分组', async () => {
    const page = await bootPage({ hash: '#bonuses' });
    try {
        const panel = page.panel('bonuses');
        const rows = bonusRows(page);
        commitBonusValue(page, rows[1].querySelector('.bonus-value'), '-30');
        leaveBonusRow(page, rows[1]);
        await waitFor(() => bonusGroupRows(page, 'negative').length === 1);
        await waitFor(() => dictionaryPuts(page).length === 1 && page.server.inflight === 0);
        assert.deepEqual(bonusGroupRows(page, 'negative')
            .map((row) => row.querySelector('.bonus-keyword').value), ['专题A']);
        assert.deepEqual(bonusGroupRows(page, 'positive')
            .map((row) => row.querySelector('.bonus-keyword').value), ['专题Z']);
        assert.deepEqual(dictionaryPuts(page)[0].body.value, [
            { keyword: '专题Z', bonus: 10 }, { keyword: '专题A', bonus: -30 },
        ]);
    } finally { page.close(); }
});

test('加分词典：减分组末尾的添加入口直接在该组生成新行', async () => {
    const page = await bootPage({ hash: '#bonuses' });
    try {
        const panel = page.panel('bonuses');
        panel.querySelector('.bonus-group[data-bonus-group="negative"] .bonus-add').click();
        await waitFor(() => bonusGroupRows(page, 'negative').length === 1);
        const row = bonusGroupRows(page, 'negative')[0];
        assert.equal(row.querySelector('.bonus-keyword'), page.document.activeElement);
        inputValue(page, row.querySelector('.bonus-keyword'), '减分词X');
        commitBonusValue(page, row.querySelector('.bonus-value'), '-25');
        await waitFor(() => dictionaryPuts(page).length === 1 && page.server.inflight === 0);
        assert.deepEqual(dictionaryPuts(page)[0].body.value, [
            { keyword: '专题Z', bonus: 10 },
            { keyword: '专题A', bonus: 20 },
            { keyword: '减分词X', bonus: -25 },
        ]);
    } finally { page.close(); }
});

for (const [name, selector, value] of [
    ['F2 重复词', '.bonus-keyword', ' 专题A '],
    ['空关键词', '.bonus-keyword', '  '],
    ['F3 小数', '.bonus-value', '1.5'],
    ['F3 超上限', '.bonus-value', '101'],
    ['F3 超下限', '.bonus-value', '-101'],
    ['F3 空分值', '.bonus-value', ''],
]) {
    test(`${name}：词典逐行标红且零请求`, async () => {
        const page = await bootPage();
        try {
            const panel = page.panel('bonuses');
            const field = panel.querySelector(selector);
            commitBonusValue(page, field, value);
            await assertNever(() => dictionaryPuts(page).length > 0);
            assert.ok(panel.querySelector('.bonus-row.is-error'));
            assert.equal(field.getAttribute('aria-invalid'), 'true');
            assert.match(panel.querySelector('.bonus-save-status').textContent, /有未保存的修改/);
        } finally { page.close(); }
    });
}

for (const [section, name, consequence] of [
    ['education_keywords', 'F4', '所有文章直接放行'],
    ['beijing_keywords', 'F5', '所有稿件判为京外'],
]) {
    test(`${name}：空词表拦截并说明后果`, async () => {
        const page = await bootPage();
        try {
            const block = dictionaryBlock(page, section);
            inputValue(page, block.querySelector('textarea'), ' \n\t\n');
            block.querySelector('.section-save').click();
            await assertNever(() => dictionaryPuts(page).length > 0);
            assert.ok(block.querySelector('.settings-save-status').textContent.includes(consequence));
            assert.match(block.querySelector('.settings-word-count').textContent, /0 个词/);
        } finally { page.close(); }
    });
}

test('F6 F12：全角等号按首个拆分且后缀顺序不变', async () => {
    const page = await bootPage();
    try {
        const block = dictionaryBlock(page, 'source_aliases');
        inputValue(page, block.querySelector('.alias-suffixes'), ' 网 \n 客户端\n报');
        inputValue(page, block.querySelector('.alias-mappings'), ' 北青 ＝ 北京青年报=新版\n __proto__=标准来源\nA=B＝C');
        await saveDictionary(page, 'source_aliases');
        assert.deepEqual(dictionaryPuts(page)[0].body, {
            expected_version: 14,
            value: { suffixes: ['网', '客户端', '报'], aliases: JSON.parse('{"北青":"北京青年报=新版","__proto__":"标准来源","A":"B＝C"}') },
        });
        assert.equal(block.querySelector('.alias-mappings').value, '北青=北京青年报=新版\n__proto__=标准来源\nA=B＝C');
    } finally { page.close(); }
});

for (const [name, value, message] of [
    ['F7', ' 北青 =甲\n北青＝乙', /原名称重复/],
    ['缺等号', '北青', /缺少等号/],
    ['空原名', '=标准', /两侧不能为空/],
    ['空目标', '原名＝ ', /两侧不能为空/],
]) {
    test(`${name}：别名格式错误不发送请求`, async () => {
        const page = await bootPage();
        try {
            const block = dictionaryBlock(page, 'source_aliases');
            inputValue(page, block.querySelector('.alias-mappings'), value);
            block.querySelector('.section-save').click();
            await assertNever(() => dictionaryPuts(page).length > 0);
            assert.match(block.querySelector('.settings-save-status').textContent, message);
        } finally { page.close(); }
    });
}

const DICTIONARY_SECTIONS = ['score_keyword_bonuses', 'education_keywords', 'beijing_keywords', 'source_aliases'];
// 加分词典是即时保存，没有保存/放弃/载入按钮；其余三个词表仍是草稿式保存
const DRAFT_DICTIONARY_SECTIONS = DICTIONARY_SECTIONS.filter((key) => key !== 'score_keyword_bonuses');
for (const section of DRAFT_DICTIONARY_SECTIONS) {
    test(`F9 F10：${section} 独立版本请求与未保存守卫`, async () => {
        const page = await bootPage();
        try {
            const block = dictionaryBlock(page, section);
            const field = block.querySelector('.settings-textarea');
            const original = field.value;
            assert.equal(unloadBlocked(page), false);
            inputValue(page, field, original + '新词');
            assert.equal(unloadBlocked(page), true);
            page.clickTab('sources');
            page.clickTab('advanced');
            assert.equal(block.querySelector('.settings-textarea'), field);
            assert.equal(field.value, original + '新词');
            await saveDictionary(page, section);
            assert.equal(dictionaryPuts(page).length, 1);
            assert.equal(dictionaryPuts(page)[0].path, `/api/admin/settings/${section}`);
            assert.equal(dictionaryPuts(page)[0].body.expected_version, defaultSections()[section].version);
            assert.equal(unloadBlocked(page), false);
            inputValue(page, block.querySelector('.settings-textarea'), '草稿');
            block.querySelector('.section-discard').click();
            assert.equal(unloadBlocked(page), false);
        } finally { page.close(); }
    });
    for (const status of [409, 422]) {
        test(`分区错误：${section} ${status} 保留草稿可重试`, async () => {
            const page = await bootPage();
            try {
                const block = dictionaryBlock(page, section);
                const field = block.querySelector('.settings-textarea');
                inputValue(page, field, field.value + '草稿');
                const draft = field.value;
                page.server.saveBehavior[section] = { status, payload: { detail: '服务端说明' } };
                await saveDictionary(page, section);
                assert.equal(dictionaryPuts(page)[0].status, status);
                assert.equal(field.value, draft);
                assert.equal(unloadBlocked(page), true);
                assert.match(block.querySelector('.settings-save-status').textContent, /服务端说明/);
                assert.equal(block.querySelector('.section-reload').hidden, status !== 409);
                assert.equal(block.querySelector('.section-save').disabled, false);
            } finally { page.close(); }
        });
    }
}

// 即时保存的加分词典：改完值自动提交、冲突时保留本地修改并可载入最新配置
test('加分词典：即时保存使用当前版本，保存后清除未保存标记', async () => {
    const page = await bootPage({ hash: '#bonuses' });
    try {
        const panel = page.panel('bonuses');
        const field = panel.querySelector('.bonus-keyword');
        assert.equal(unloadBlocked(page), false);
        inputValue(page, field, field.value + '新');
        assert.equal(unloadBlocked(page), true);
        field.dispatchEvent(new page.window.FocusEvent('focusout', {
            bubbles: true,
            relatedTarget: panel.querySelector('.bonus-title'),
        }));
        await waitFor(() => dictionaryPuts(page).length === 1 && page.server.inflight === 0);
        assert.equal(dictionaryPuts(page)[0].path, '/api/admin/settings/score_keyword_bonuses');
        assert.equal(dictionaryPuts(page)[0].body.expected_version, 11);
        assert.equal(unloadBlocked(page), false);
        page.clickTab('sources');
        page.clickTab('bonuses');
        assert.equal(panel.querySelector('.bonus-keyword'), field);
        assert.equal(field.value, '专题Z新');
    } finally { page.close(); }
});

test('加分词典：409 冲突保留本地修改并可载入最新配置', async () => {
    const page = await bootPage({ hash: '#bonuses' });
    try {
        const panel = page.panel('bonuses');
        const input = panel.querySelector('.bonus-value');
        page.server.saveBehavior.score_keyword_bonuses = { status: 409, payload: { detail: '服务端说明' } };
        commitBonusValue(page, input, '-40');
        await waitFor(() => dictionaryPuts(page).length === 1 && page.server.inflight === 0);
        assert.equal(dictionaryPuts(page)[0].status, 409);
        assert.equal(input.value, '-40');
        assert.match(panel.querySelector('.bonus-save-status').textContent, /服务端说明/);
        assert.equal(panel.querySelector('.bonus-reload').hidden, false);
        assert.equal(unloadBlocked(page), true);

        panel.querySelector('.bonus-reload').click();
        await waitFor(() => panel.querySelector('.bonus-reload').hidden && page.server.inflight === 0);
        assert.equal(panel.querySelector('.bonus-value').value, '10');
        assert.equal(unloadBlocked(page), false);
    } finally { page.close(); }
});

test('加分词典：422 保留本地修改并显示服务端原因', async () => {
    const page = await bootPage({ hash: '#bonuses' });
    try {
        const panel = page.panel('bonuses');
        const input = panel.querySelector('.bonus-value');
        page.server.saveBehavior.score_keyword_bonuses = { status: 422, payload: { detail: '服务端说明' } };
        commitBonusValue(page, input, '-40');
        await waitFor(() => dictionaryPuts(page).length === 1 && page.server.inflight === 0);
        assert.equal(dictionaryPuts(page)[0].status, 422);
        assert.equal(input.value, '-40');
        assert.match(panel.querySelector('.bonus-save-status').textContent, /服务端说明/);
        assert.equal(panel.querySelector('.bonus-reload').hidden, true);
        assert.equal(unloadBlocked(page), true);
    } finally { page.close(); }
});

for (const section of DICTIONARY_SECTIONS) {
    test(`分区缺失：仅 ${section} 显示导入提示`, async () => {
        const sections = defaultSections();
        delete sections[section];
        const page = await bootPage({ sections });
        try {
            for (const key of DICTIONARY_SECTIONS) {
                const block = dictionaryBlock(page, key);
                assert.equal(!!block.querySelector('.settings-import-notice'), key === section);
                const hasSaveBar = key !== 'score_keyword_bonuses';
                assert.equal(!!block.querySelector('.section-save'), hasSaveBar && key !== section);
            }
        } finally { page.close(); }
    });
}

for (const target of DRAFT_DICTIONARY_SECTIONS) {
    test(`F8：${target} 冲突载入仅重置自身并保留其他版本`, async () => {
        const page = await bootPage();
        try {
            const drafts = new Map();
            for (const section of DRAFT_DICTIONARY_SECTIONS) {
                const field = dictionaryBlock(page, section).querySelector('.settings-textarea');
                inputValue(page, field, field.value + '草稿');
                drafts.set(section, [field, field.value]);
                page.server.sections[section].version += 20;
            }
            await saveDictionary(page, target);
            const block = dictionaryBlock(page, target);
            assert.equal(dictionaryPuts(page)[0].status, 409);
            block.querySelector('.section-reload').click();
            await waitFor(() => block.querySelector('.section-reload').hidden && page.server.inflight === 0);
            assert.equal(block.querySelector('textarea').value, target === 'source_aliases' ? '客户端\n网' : defaultSections()[target].value.join('\n'));
            for (const section of DRAFT_DICTIONARY_SECTIONS.filter((key) => key !== target)) {
                const [field, draft] = drafts.get(section);
                assert.equal(dictionaryBlock(page, section).querySelector('.settings-textarea'), field);
                assert.equal(field.value, draft);
                assert.equal(unloadBlocked(page), true);
                await saveDictionary(page, section);
                const request = dictionaryPuts(page).at(-1);
                assert.equal(request.path, `/api/admin/settings/${section}`);
                assert.equal(request.body.expected_version, defaultSections()[section].version);
                assert.equal(request.status, 409);
            }
        } finally { page.close(); }
    });
}

test('F11：删除最后一条要确认，取消保留，确认后即时提交空数组', async () => {
    const page = await bootPage();
    try {
        const panel = page.panel('bonuses');
        // 先删掉一条（加分组的第二行），立即自动保存剩下的那一条
        bonusRows(page)[1].querySelector('.bonus-delete').click();
        await waitFor(() => dictionaryPuts(page).length === 1 && page.server.inflight === 0);
        assert.deepEqual(dictionaryPuts(page)[0].body.value, [{ keyword: '专题Z', bonus: 10 }]);

        // 删最后一条要确认；取消时保留，且不追加请求
        const prompts = [];
        page.window.confirm = (message) => { prompts.push(message); return false; };
        bonusRows(page)[0].querySelector('.bonus-delete').click();
        await assertNever(() => dictionaryPuts(page).length > 1);
        assert.equal(prompts.length, 1);
        assert.match(prompts[0], /不再有关键词加分/);
        assert.equal(bonusRows(page).length, 1);
        assert.equal(unloadBlocked(page), false);

        page.window.confirm = () => true;
        bonusRows(page)[0].querySelector('.bonus-delete').click();
        await waitFor(() => dictionaryPuts(page).length === 2 && page.server.inflight === 0);
        assert.deepEqual(dictionaryPuts(page)[1].body.value, []);
        assert.equal(panel.querySelectorAll('.bonus-keyword').length, 0);
        assert.equal(unloadBlocked(page), false);
    } finally { page.close(); }
});

// F13-F17：高级页签「自动排序关键词」——三类别文本域、查重、全空确认、立即生效语义。
const SORT_CATEGORY_NAMES = ['市教委', '中小学', '高校'];

function sortCategoryInputs(page) {
    const block = dictionaryBlock(page, 'review_sort_keywords');
    const inputs = {};
    block.querySelectorAll('.advanced-field').forEach((field) => {
        const labelText = field.querySelector('.advanced-field-label').textContent;
        const category = SORT_CATEGORY_NAMES.find((name) => labelText.startsWith(name));
        inputs[category] = field.querySelector('textarea');
    });
    return inputs;
}

test('F13：自动排序关键词渲染三个类别文本域并显示词数', async () => {
    const page = await bootPage({ hash: '#advanced' });
    try {
        const inputs = sortCategoryInputs(page);
        assert.deepEqual(Object.keys(inputs), SORT_CATEGORY_NAMES);
        assert.equal(inputs['市教委'].value, '市教委\n教工委');
        assert.equal(inputs['中小学'].value, '中小学\n小学');
        assert.equal(inputs['高校'].value, '高校\n大学');
        assert.match(inputs['高校'].parentElement.querySelector('.settings-word-count').textContent, /2 个词/);
        const block = dictionaryBlock(page, 'review_sort_keywords');
        assert.match(block.querySelector('.settings-effect-note').textContent, /立即生效/);
        assert.match(block.querySelector('.settings-effect-note').textContent, /市教委 → 中小学 → 高校/);
    } finally { page.close(); }
});

test('高级关键词：芯片逐条删除并同步 textarea 与词数', async () => {
    const page = await bootPage({ hash: '#advanced' });
    try {
        const block = dictionaryBlock(page, 'education_keywords');
        const textarea = block.querySelector('.settings-textarea');
        assert.equal(block.querySelectorAll('.keyword-chip').length, 2);
        assert.match(block.querySelector('.settings-word-count').textContent, /2 个词/);

        block.querySelectorAll('.keyword-chip')[0].querySelector('.keyword-chip-remove').click();
        assert.equal(textarea.value, '学校');
        assert.equal(block.querySelectorAll('.keyword-chip').length, 1);
        assert.match(block.querySelector('.settings-word-count').textContent, /1 个词/);
        assert.equal(unloadBlocked(page), true);
    } finally { page.close(); }
});

test('高级关键词：内联输入回车添加，批量编辑展开原生 textarea', async () => {
    const page = await bootPage({ hash: '#advanced' });
    try {
        const block = dictionaryBlock(page, 'education_keywords');
        const textarea = block.querySelector('.settings-textarea');
        const addInput = block.querySelector('.keyword-add-input');
        addInput.value = '考试';
        addInput.dispatchEvent(new page.window.Event('input', { bubbles: true }));
        addInput.dispatchEvent(new page.window.KeyboardEvent('keydown', {
            key: 'Enter', bubbles: true, cancelable: true,
        }));
        assert.equal(textarea.value, '教育\n学校\n考试');
        assert.equal(block.querySelectorAll('.keyword-chip').length, 3);
        assert.equal(addInput.value, '');
        assert.match(block.querySelector('.settings-word-count').textContent, /3 个词/);

        const toggle = block.querySelector('.keyword-batch-toggle');
        assert.equal(textarea.hidden, true);
        assert.equal(toggle.getAttribute('aria-expanded'), 'false');
        toggle.click();
        assert.equal(textarea.hidden, false);
        assert.equal(toggle.getAttribute('aria-expanded'), 'true');
        assert.equal(toggle.textContent, '收起批量');
        toggle.click();
        assert.equal(textarea.hidden, true);
    } finally { page.close(); }
});

test('高级别名：添加输入按「原名称=标准名称」拆成芯片', async () => {
    const page = await bootPage({ hash: '#advanced' });
    try {
        const block = dictionaryBlock(page, 'source_aliases');
        const aliasField = [...block.querySelectorAll('.advanced-field')]
            .find((field) => field.querySelector('.alias-mappings'));
        const addInput = aliasField.querySelector('.keyword-add-input');
        addInput.value = '中考=中考改革';
        addInput.dispatchEvent(new page.window.Event('input', { bubbles: true }));
        addInput.dispatchEvent(new page.window.KeyboardEvent('keydown', {
            key: 'Enter', bubbles: true, cancelable: true,
        }));
        assert.match(aliasField.querySelector('.alias-mappings').value, /中考=中考改革/);
        const chipTexts = [...aliasField.querySelectorAll('.keyword-chip-text')]
            .map((el) => el.textContent);
        assert.ok(chipTexts.includes('中考 → 中考改革'));
    } finally { page.close(); }
});

test('F14：自动排序关键词按三类对象保存，留空类别提交空数组', async () => {
    const page = await bootPage({ hash: '#advanced' });
    try {
        const inputs = sortCategoryInputs(page);
        inputValue(page, inputs['市教委'], '市教委\n教工委\n首都教育两委');
        inputValue(page, inputs['中小学'], '');
        inputValue(page, inputs['高校'], ' 高校 \n大学\n');
        await saveDictionary(page, 'review_sort_keywords');
        assert.deepEqual(dictionaryPuts(page)[0].body, {
            expected_version: 15,
            value: {
                '市教委': ['市教委', '教工委', '首都教育两委'],
                '中小学': [],
                '高校': ['高校', '大学'],
            },
        });
        assert.match(dictionaryBlock(page, 'review_sort_keywords').querySelector('.settings-save-status').textContent, /保存成功/);
    } finally { page.close(); }
});

for (const [name, target, lines, message] of [
    ['F15 同类重复', '中小学', '中小学\n小学\n小学', /中小学第 3 行：关键词重复/],
    ['F16 跨类重复', '中小学', '中小学\n市教委', /同时出现在 市教委 和 中小学/],
]) {
    test(`${name}：零请求标红并说明优先级语义`, async () => {
        const page = await bootPage({ hash: '#advanced' });
        try {
            const inputs = sortCategoryInputs(page);
            inputValue(page, inputs[target], lines);
            dictionaryBlock(page, 'review_sort_keywords').querySelector('.section-save').click();
            await assertNever(() => dictionaryPuts(page).length > 0);
            assert.equal(inputs[target].getAttribute('aria-invalid'), 'true');
            assert.match(dictionaryBlock(page, 'review_sort_keywords').querySelector('.settings-save-status').textContent, message);
        } finally { page.close(); }
    });
}

test('F17：三类全空保存先确认，取消零请求，确认提交三个空数组', async () => {
    const page = await bootPage({ hash: '#advanced' });
    try {
        const inputs = sortCategoryInputs(page);
        Object.values(inputs).forEach((input) => inputValue(page, input, ''));
        const prompts = [];
        page.window.confirm = (message) => { prompts.push(message); return false; };
        dictionaryBlock(page, 'review_sort_keywords').querySelector('.section-save').click();
        await assertNever(() => dictionaryPuts(page).length > 0);
        assert.equal(prompts.length, 1);
        assert.match(prompts[0], /自动排序将不会调整条目顺序/);
        assert.equal(unloadBlocked(page), true);
        page.window.confirm = () => true;
        await saveDictionary(page, 'review_sort_keywords');
        assert.deepEqual(dictionaryPuts(page)[0].body.value, { '市教委': [], '中小学': [], '高校': [] });
    } finally { page.close(); }
});

for (const tab of ['bonuses', 'advanced']) {
    test(`恢复 #${tab} hash 与 aria 页签语义`, async () => {
        const page = await bootPage({ hash: `#${tab}` });
        try {
            assert.equal(page.panel(tab).hidden, false);
            for (const button of page.document.querySelectorAll('[data-settings-tab]')) {
                const active = button.dataset.settingsTab === tab;
                assert.equal(button.getAttribute('aria-selected'), String(active));
                assert.equal(page.document.getElementById(button.getAttribute('aria-controls')).hidden, !active);
            }
        } finally { page.close(); }
    });
}

test('设置页头随页签切换更新标题与说明', async () => {
    const page = await bootPage();
    const title = () => page.document.getElementById('settings-page-title').textContent;
    const desc = () => page.document.getElementById('settings-page-desc').textContent;
    try {
        assert.equal(title(), '模型与接入点');
        assert.match(desc(), /默认模型/);
        page.clickTab('sources');
        assert.equal(title(), '数据源');
        assert.match(desc(), /每小时抓取/);
        page.clickTab('advanced');
        assert.equal(title(), '高级');
        assert.match(desc(), /低频配置/);
    } finally { page.close(); }
});

test('空别名和后缀无需确认；关键词服务端去重后回填', async () => {
    const page = await bootPage();
    try {
        page.window.confirm = () => assert.fail('别名不应确认');
        const aliases = dictionaryBlock(page, 'source_aliases');
        inputValue(page, aliases.querySelector('.alias-suffixes'), '');
        inputValue(page, aliases.querySelector('.alias-mappings'), '');
        await saveDictionary(page, 'source_aliases');
        assert.deepEqual(dictionaryPuts(page)[0].body.value, { suffixes: [], aliases: {} });
        const education = dictionaryBlock(page, 'education_keywords');
        inputValue(page, education.querySelector('textarea'), ' 学校 \n学校\n\n教育');
        await saveDictionary(page, 'education_keywords');
        assert.equal(education.querySelector('textarea').value, '学校\n教育');
        assert.match(education.querySelector('.settings-word-count').textContent, /2 个词/);
    } finally { page.close(); }
});

for (const section of DRAFT_DICTIONARY_SECTIONS) {
    test(`保存锁定：${section} 只锁当前块且不重复提交`, async () => {
        const page = await bootPage();
        try {
            const block = dictionaryBlock(page, section);
            const field = block.querySelector('.settings-textarea');
            inputValue(page, field, field.value + '草稿');
            page.server.hold(`save-${section}`);
            const save = block.querySelector('.section-save');
            save.click();
            await waitFor(() => page.server.held.length === 1);
            assert.equal(field.disabled, true);
            assert.equal(block.querySelector('.section-discard').disabled, true);
            save.click();
            await assertNever(() => dictionaryPuts(page).length > 1);
            const other = section === 'education_keywords' ? 'beijing_keywords' : 'education_keywords';
            const otherField = dictionaryBlock(page, other).querySelector('textarea');
            assert.equal(otherField.disabled, false);
            inputValue(page, otherField, '其他块未保存');
            page.server.release(`save-${section}`);
            await waitFor(() => !block.querySelector('.section-save').disabled);
            assert.equal(otherField.value, '其他块未保存');
            assert.equal(unloadBlocked(page), true);
        } finally { page.close(); }
    });
}

test('保存锁定：加分词典串行提交，不并发使用同一版本号', async () => {
    const page = await bootPage({ hash: '#bonuses' });
    try {
        const panel = page.panel('bonuses');
        page.server.hold('save-score_keyword_bonuses');
        commitBonusValue(page, panel.querySelectorAll('.bonus-value')[0], '-10');
        await waitFor(() => page.server.held.length === 1);
        // 第一次保存未返回时再改另一行：只排队，不并发发请求
        commitBonusValue(page, panel.querySelectorAll('.bonus-value')[1], '30');
        await assertNever(() => dictionaryPuts(page).length > 1);
        page.server.release('save-score_keyword_bonuses');
        await waitFor(() => dictionaryPuts(page).length === 2 && page.server.inflight === 0);
        assert.equal(dictionaryPuts(page)[1].body.expected_version, 12);
        assert.deepEqual(dictionaryPuts(page)[1].body.value, [
            { keyword: '专题A', bonus: 30 }, { keyword: '专题Z', bonus: -10 },
        ]);
    } finally { page.close(); }
});

test('整体 payload 重载后其余词表草稿继续使用编辑开始时的版本', async () => {
    const page = await bootPage();
    try {
        for (const section of DRAFT_DICTIONARY_SECTIONS) {
            const field = dictionaryBlock(page, section).querySelector('.settings-textarea');
            inputValue(page, field, field.value + '本地');
            page.server.sections[section].version += 10;
        }
        page.document.querySelector('.env-reload-btn').click();
        await waitFor(() => page.server.requests('get-settings').length === 1 && page.server.inflight === 0);
        for (const section of DRAFT_DICTIONARY_SECTIONS) {
            await saveDictionary(page, section);
            assert.equal(dictionaryPuts(page).at(-1).body.expected_version, defaultSections()[section].version);
            assert.equal(dictionaryPuts(page).at(-1).status, 409);
        }
    } finally { page.close(); }
});

// 其余词表仍是草稿式保存：「放弃修改」按钮（section_editor.js 的 discard 分支）
// 回到该块最后见到的快照、脏标记清除、再次保存发回原始值，且只影响当前块。
function bonusRows(page) {
    return [...page.panel('bonuses').querySelectorAll('.bonus-row:not(.bonus-heading)')];
}

for (const [section, draft] of [
    ['education_keywords', '教育\n学校\n课堂'],
    ['beijing_keywords', '北京\n海淀\n朝阳'],
]) {
    test(`放弃修改（${section}）：文本复原、脏标记清除、再保存发回原始词表`, async () => {
        const page = await bootPage();
        try {
            const block = dictionaryBlock(page, section);
            inputValue(page, block.querySelector('textarea'), draft);
            assert.equal(unloadBlocked(page), true);
            block.querySelector('.section-discard').click();
            const restored = block.querySelector('textarea');
            assert.equal(restored.value, defaultSections()[section].value.join('\n'));
            assert.equal(unloadBlocked(page), false);
            assert.equal(page.window.eval(`state.dirty.${section}`), false);

            await saveDictionary(page, section);
            assert.deepEqual(dictionaryPuts(page).map(({ path, body }) => ({ path, body })), [{
                path: `/api/admin/settings/${section}`,
                body: {
                    expected_version: defaultSections()[section].version,
                    value: defaultSections()[section].value,
                },
            }]);
        } finally { page.close(); }
    });
}

test('放弃修改（source_aliases）：两个文本框复原、脏标记清除、再保存发回原始别名表', async () => {
    const page = await bootPage();
    try {
        const block = dictionaryBlock(page, 'source_aliases');
        inputValue(page, block.querySelector('.alias-suffixes'), '客户端\n网\n日报');
        inputValue(page, block.querySelector('.alias-mappings'), '北青=北京青年报\n晚报=北京晚报');
        assert.equal(unloadBlocked(page), true);
        block.querySelector('.section-discard').click();
        assert.equal(block.querySelector('.alias-suffixes').value, '客户端\n网');
        assert.equal(block.querySelector('.alias-mappings').value, '北青=北京青年报');
        assert.equal(unloadBlocked(page), false);
        assert.equal(page.window.eval('state.dirty.source_aliases'), false);

        await saveDictionary(page, 'source_aliases');
        assert.deepEqual(dictionaryPuts(page).map(({ path, body }) => ({ path, body })), [{
            path: '/api/admin/settings/source_aliases',
            body: {
                expected_version: 14,
                value: { suffixes: ['客户端', '网'], aliases: { 北青: '北京青年报' } },
            },
        }]);
    } finally { page.close(); }
});

test('放弃修改只影响当前块：另一块的草稿与未保存标记原样保留并可独立保存', async () => {
    const page = await bootPage();
    try {
        const education = dictionaryBlock(page, 'education_keywords');
        const beijing = dictionaryBlock(page, 'beijing_keywords');
        const beijingField = beijing.querySelector('textarea');
        inputValue(page, education.querySelector('textarea'), '教育\n学校\n课堂');
        inputValue(page, beijingField, '北京\n海淀\n朝阳');
        assert.equal(unloadBlocked(page), true);

        education.querySelector('.section-discard').click();
        // 放弃只重渲染当前块：beijing 的草稿节点必须原样保留，未被重建
        assert.equal(education.querySelector('textarea').value, '教育\n学校');
        assert.equal(beijing.querySelector('textarea'), beijingField);
        assert.equal(beijingField.value, '北京\n海淀\n朝阳');
        assert.equal(page.window.eval('state.dirty.education_keywords'), false);
        assert.equal(page.window.eval('state.dirty.beijing_keywords'), true);
        assert.equal(unloadBlocked(page), true);

        await saveDictionary(page, 'beijing_keywords');
        assert.deepEqual(dictionaryPuts(page).map(({ path, body }) => ({ path, body })), [{
            path: '/api/admin/settings/beijing_keywords',
            body: { expected_version: 13, value: ['北京', '海淀', '朝阳'] },
        }]);
    } finally { page.close(); }
});

test('加分词典：只加一个空行不算未保存修改，填了一半才拦离开', async () => {
    const page = await bootPage();
    try {
        assert.equal(unloadBlocked(page), false);
        page.panel('bonuses').querySelector('.bonus-add').click();
        // 完全空着的行只是本地草稿，不会写库也不触发离开确认
        assert.equal(page.window.eval('state.dirty.score_keyword_bonuses'), false);
        assert.equal(unloadBlocked(page), false);

        const row = bonusRows(page).at(-1);
        inputValue(page, row.querySelector('.bonus-keyword'), '半条');
        assert.equal(unloadBlocked(page), true);
        row.querySelector('.bonus-keyword').dispatchEvent(new page.window.FocusEvent('focusout', {
            bubbles: true,
            relatedTarget: page.panel('bonuses').querySelector('.bonus-title'),
        }));
        await assertNever(() => dictionaryPuts(page).length > 0);
        assert.ok(row.classList.contains('is-error'));
        assert.equal(unloadBlocked(page), true);
    } finally { page.close(); }
});

test('高级载入失败保留文本和脏标记，重试可载入最新版本再保存', async () => {
    const page = await bootPage();
    try {
        const block = dictionaryBlock(page, 'education_keywords');
        inputValue(page, block.querySelector('textarea'), '本地草稿');
        page.server.sections.education_keywords.version = 88;
        page.server.sections.education_keywords.value = ['远端新词'];
        await saveDictionary(page, 'education_keywords');
        page.server.failNext['get-settings'] = 1;
        block.querySelector('.section-reload').click();
        await waitFor(() => block.querySelector('.settings-save-status').textContent.includes('载入失败'));
        assert.equal(block.querySelector('textarea').value, '本地草稿');
        assert.equal(unloadBlocked(page), true);
        block.querySelector('.section-reload').click();
        await waitFor(() => block.querySelector('textarea').value === '远端新词');
        assert.equal(unloadBlocked(page), false);
        inputValue(page, block.querySelector('textarea'), '确认后的新词');
        await saveDictionary(page, 'education_keywords');
        assert.equal(dictionaryPuts(page).at(-1).body.expected_version, 88);
        assert.equal(dictionaryPuts(page).at(-1).status, 200);
    } finally { page.close(); }
});

// ---------------------------------------------------------------------------
// 排除名单：加分词典页签表格下方的独立卡片，独立版本与即时保存
const EXCLUSION_PATH = '/api/admin/settings/score_exclusion_keywords';

function exclusionCard(page) {
    return page.panel('bonuses').querySelector('.bonus-exclusion-card');
}
function exclusionTextarea(page) {
    return exclusionCard(page).querySelector('.exclusion-keywords');
}
function exclusionStatus(page) {
    return exclusionCard(page).querySelector('.exclusion-save-status');
}
function commitExclusion(page, value) {
    const textarea = exclusionTextarea(page);
    inputValue(page, textarea, value);
    textarea.dispatchEvent(new page.window.Event('change', { bubbles: true }));
}
function exclusionPuts(page) {
    return page.server.log.filter((entry) => entry.path === EXCLUSION_PATH);
}

test('排除名单：加分表格下方渲染独立卡片，默认空数组且不新增页签', async () => {
    const page = await bootPage({ hash: '#bonuses' });
    try {
        const panel = page.panel('bonuses');
        const card = exclusionCard(page);
        assert.ok(card, '未渲染排除名单卡片');
        assert.equal(card.querySelector('.bonus-exclusion-title').textContent, '排除名单');
        assert.match(card.querySelector('.settings-effect-note').textContent, /命中即淘汰/);
        assert.match(card.querySelector('.settings-effect-note').textContent, /排除优先/);
        assert.equal(exclusionTextarea(page).value, '');
        assert.match(card.querySelector('.settings-word-count').textContent, /当前 0 个词/);
        assert.equal(exclusionStatus(page).textContent, '');
        // 卡片在加分表格之后、仍在加分词典页签内；页签集合不变
        const main = panel.querySelector('.bonus-main');
        assert.ok(main.querySelector('.bonus-table'));
        assert.ok(main.querySelector('.bonus-exclusion-card'));
        assert.equal(page.window.eval('state.activeTab'), 'bonuses');
        assert.equal(page.document.querySelectorAll('[data-settings-tab]').length, 4);
        assert.equal(
            page.document.querySelector('[data-settings-tab="score_exclusion_keywords"]'),
            null,
        );
        assert.ok(page.document.querySelector('[data-settings-tab="advanced"]'));
    } finally { page.close(); }
});

test('排除名单：输入两行后失焦即时保存，只写该分区且不影响加分词典版本', async () => {
    const page = await bootPage({ hash: '#bonuses' });
    try {
        const bonusVersionBefore = page.window.eval(
            'state.payload.sections.score_keyword_bonuses.version',
        );
        commitExclusion(page, ' 词1 \n词2\n');
        await waitFor(() => exclusionPuts(page).length === 1 && page.server.inflight === 0);

        assert.deepEqual(exclusionPuts(page)[0].body, {
            expected_version: 16,
            value: ['词1', '词2'],
        });
        assert.match(exclusionStatus(page).textContent, /已自动保存/);
        assert.equal(page.window.eval('state.payload.sections.score_keyword_bonuses.version'),
            bonusVersionBefore);
        assert.equal(page.window.eval('state.dirty.score_exclusion_keywords'), false);
        assert.equal(unloadBlocked(page), false);
    } finally { page.close(); }
});

test('排除名单：重复词带行号报错且零请求', async () => {
    const page = await bootPage({ hash: '#bonuses' });
    try {
        commitExclusion(page, '考试\n 考试 ');
        await assertNever(() => exclusionPuts(page).length > 0);
        assert.equal(exclusionTextarea(page).getAttribute('aria-invalid'), 'true');
        assert.match(exclusionStatus(page).textContent, /第 2 行重复/);
        assert.equal(page.window.eval('state.dirty.score_exclusion_keywords'), true);
        assert.equal(unloadBlocked(page), true);
    } finally { page.close(); }
});

test('排除名单：清空文本域后失焦提交空数组，不弹确认', async () => {
    const page = await bootPage({ hash: '#bonuses' });
    try {
        commitExclusion(page, '词1\n词2');
        await waitFor(() => exclusionPuts(page).length === 1 && page.server.inflight === 0);

        page.window.confirm = () => assert.fail('空名单是合法配置，不应弹确认');
        commitExclusion(page, ' \n\n');
        await waitFor(() => exclusionPuts(page).length === 2 && page.server.inflight === 0);
        assert.deepEqual(exclusionPuts(page)[1].body, {
            expected_version: 17,
            value: [],
        });
        assert.match(exclusionStatus(page).textContent, /已自动保存/);
        assert.equal(exclusionTextarea(page).getAttribute('aria-invalid'), 'false');
    } finally { page.close(); }
});

test('排除名单：409 后载入最新配置只重载本卡片，不清加分词典草稿', async () => {
    const page = await bootPage({ hash: '#bonuses' });
    try {
        const panel = page.panel('bonuses');
        const bonusInput = panel.querySelector('.bonus-keyword');
        inputValue(page, bonusInput, '未保存加分词');
        assert.equal(page.window.eval('state.dirty.score_keyword_bonuses'), true);

        page.server.saveBehavior.score_exclusion_keywords = {
            status: 409,
            payload: { detail: '服务端说明' },
        };
        commitExclusion(page, '排除词');
        await waitFor(() => exclusionPuts(page).length === 1 && page.server.inflight === 0);
        assert.equal(exclusionPuts(page)[0].status, 409);
        assert.equal(exclusionTextarea(page).value, '排除词');
        assert.match(exclusionStatus(page).textContent, /服务端说明/);
        assert.equal(exclusionCard(page).querySelector('.exclusion-reload').hidden, false);
        assert.equal(unloadBlocked(page), true);

        exclusionCard(page).querySelector('.exclusion-reload').click();
        await waitFor(() => exclusionCard(page).querySelector('.exclusion-reload').hidden
            && page.server.inflight === 0);
        // 只重载了排除分区：服务端空名单回来，加分词典的草稿节点原样保留
        assert.equal(exclusionTextarea(page).value, '');
        assert.equal(page.window.eval('state.dirty.score_exclusion_keywords'), false);
        assert.equal(panel.querySelector('.bonus-keyword'), bonusInput);
        assert.equal(bonusInput.value, '未保存加分词');
        assert.equal(page.window.eval('state.dirty.score_keyword_bonuses'), true);
    } finally { page.close(); }
});

test('排除名单：加分词典 409 载入只重绘加分表格，不清排除卡片草稿', async () => {
    const page = await bootPage({ hash: '#bonuses' });
    try {
        const panel = page.panel('bonuses');
        const textarea = exclusionTextarea(page);
        inputValue(page, textarea, '未保存排除词');
        assert.equal(page.window.eval('state.dirty.score_exclusion_keywords'), true);

        page.server.saveBehavior.score_keyword_bonuses = {
            status: 409,
            payload: { detail: '服务端说明' },
        };
        commitBonusValue(page, panel.querySelector('.bonus-value'), '-40');
        await waitFor(() => dictionaryPuts(page).length === 1 && page.server.inflight === 0);
        assert.equal(dictionaryPuts(page)[0].status, 409);

        panel.querySelector('.bonus-reload').click();
        await waitFor(() => panel.querySelector('.bonus-reload').hidden
            && page.server.inflight === 0);
        // 加分词典回到服务端值（专题Z=10），排除卡片的草稿节点与内容都没被动过
        assert.equal(panel.querySelector('.bonus-value').value, '10');
        assert.equal(exclusionTextarea(page), textarea);
        assert.equal(textarea.value, '未保存排除词');
        assert.equal(page.window.eval('state.dirty.score_exclusion_keywords'), true);
        assert.equal(unloadBlocked(page), true);
    } finally { page.close(); }
});

test('排除名单：两个分区各发各的 PUT，脏标记互相独立', async () => {
    const page = await bootPage({ hash: '#bonuses' });
    try {
        const panel = page.panel('bonuses');
        commitBonusValue(page, panel.querySelector('.bonus-value'), '-40');
        await waitFor(() => dictionaryPuts(page).length === 1 && page.server.inflight === 0);
        assert.equal(dictionaryPuts(page)[0].path, '/api/admin/settings/score_keyword_bonuses');
        assert.equal(exclusionPuts(page).length, 0);
        assert.equal(page.window.eval('state.dirty.score_keyword_bonuses'), false);

        inputValue(page, exclusionTextarea(page), '未保存排除词');
        assert.equal(page.window.eval('state.dirty.score_exclusion_keywords'), true);
        assert.equal(page.window.eval('state.dirty.score_keyword_bonuses'), false);
        assert.equal(unloadBlocked(page), true);

        commitExclusion(page, '保存后排除词');
        await waitFor(() => exclusionPuts(page).length === 1 && page.server.inflight === 0);
        assert.equal(exclusionPuts(page)[0].path, EXCLUSION_PATH);
        assert.equal(dictionaryPuts(page).length, 2);
        assert.equal(page.window.eval('state.dirty.score_exclusion_keywords'), false);
        assert.equal(unloadBlocked(page), false);
    } finally { page.close(); }
});

test('排除名单：保存失败保留输入并保持未保存标记', async () => {
    const page = await bootPage({ hash: '#bonuses' });
    try {
        page.server.saveBehavior.score_exclusion_keywords = {
            status: 422,
            payload: { detail: '服务端说明' },
        };
        commitExclusion(page, '排除词');
        await waitFor(() => exclusionPuts(page).length === 1 && page.server.inflight === 0);
        assert.equal(exclusionPuts(page)[0].status, 422);
        assert.equal(exclusionTextarea(page).value, '排除词');
        assert.match(exclusionStatus(page).textContent, /服务端说明/);
        assert.equal(page.window.eval('state.dirty.score_exclusion_keywords'), true);
        assert.equal(unloadBlocked(page), true);
    } finally { page.close(); }
});

test('排除名单：其他页签渲染与切换不受影响', async () => {
    const page = await bootPage({ hash: '#models' });
    try {
        assert.equal(page.panel('bonuses').hidden, true);
        page.clickTab('bonuses');
        assert.equal(page.panel('bonuses').hidden, false);
        assert.ok(exclusionCard(page));
        page.clickTab('advanced');
        assert.equal(page.panel('advanced').hidden, false);
        assert.ok(page.panel('advanced').querySelector('.advanced-section'));
        page.clickTab('sources');
        assert.equal(page.panel('sources').hidden, false);
    } finally { page.close(); }
});
