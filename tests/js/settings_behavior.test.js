// 系统设置页（/admin/settings）的 jsdom 行为测试。
// 覆盖验收场景 S1-S31；各场景语义见 tests/test_settings_js_behavior.py 与交付说明。
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
    unhandledRejections,
} = require('./settings_harness.js');

function inputValue(page, el, value) {
    el.value = value;
    el.dispatchEvent(new page.window.Event('input', { bubbles: true }));
}

function hourlyOrder(page) {
    return [...page.document.querySelectorAll('#sources-hourly-list li[data-source]')]
        .map((item) => item.dataset.source);
}

function sortOrder(page) {
    return [...page.document.querySelectorAll('#sources-sort-list li[data-source]')]
        .map((item) => item.dataset.source);
}

function buttonByText(container, text) {
    return [...container.querySelectorAll('button')]
        .find((btn) => btn.textContent.trim() === text);
}

function sourceRow(page, key) {
    return page.document.querySelector(`li[data-source="${key}"]`);
}

// 点击来源行的展开箭头并等待展开区出现
async function expandSource(page, key) {
    const arrow = sourceRow(page, key).querySelector('.source-expand-toggle');
    assert.ok(arrow, `来源 ${key} 应有展开箭头`);
    arrow.click();
    await waitFor(() => page.document
        .querySelector(`.source-accounts-panel[data-accounts-for="${key}"]`));
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
        assert.equal(page.document.getElementById('btn-sources-save'), null);
        // 账号管理并入数据源页签，分区缺失时没有来源行可供展开
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
        assert.match(summaryHint.textContent, /跟随默认（当前：deepseek\/default-model）/);

        inputValue(page, defaultInput, 'new/default-x');
        assert.match(summaryHint.textContent, /跟随默认（当前：new\/default-x）/);
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

test('S7：排序模式下上移下移后保存，排序模式不渲染启用开关与展开箭头', async () => {
    const sections = defaultSections();
    // 初始顺序与字母序不同，便于识别「保存时按字母排序」的变异
    sections.crawl_sources.value = ['chinanews', 'toutiao'];
    const page = await bootPage({ sections });
    try {
        // 默认视图：已启用分组按 crawl_sources 顺序排列
        assert.deepEqual(hourlyOrder(page), ['chinanews', 'toutiao']);

        // 每日任务来源只出现在只读分组，不在已启用/未启用分组、也没有启用开关
        const daily = page.document.getElementById('sources-daily-list');
        assert.match(daily.textContent, /北京日报/);
        assert.match(daily.textContent, /劳动午报/);
        assert.match(daily.textContent, /每日单独任务，由服务器计划任务调度/);
        assert.equal(daily.querySelector('.source-enabled-toggle'), null);
        assert.equal(
            page.document.querySelector('#sources-hourly-list li[data-source="bjrb"]'),
            null,
        );
        assert.equal(
            page.document.querySelector('#sources-available-list li[data-source="bjrb"]'),
            null,
        );

        // 进入排序模式
        page.document.getElementById('btn-sources-sort-mode').click();
        await waitFor(() => page.document.getElementById('sources-sort-list'));
        assert.deepEqual(sortOrder(page), ['chinanews', 'toutiao']);
        // 排序模式只显示已启用来源
        assert.equal(page.document.getElementById('sources-available-list'), null);
        assert.equal(page.document.getElementById('sources-daily-list'), null);
        // 排序模式不渲染启用开关与展开箭头（不是 disabled，而是不存在）
        await assertNever(
            () => page.document.querySelector('#sources-sort-list .source-enabled-toggle'),
            200,
        );
        await assertNever(
            () => page.document.querySelector('#sources-sort-list .source-expand-toggle'),
            200,
        );

        const toutiaoItem = page.document
            .querySelector('#sources-sort-list li[data-source="toutiao"]');
        buttonByText(toutiaoItem, '上移').click();
        assert.deepEqual(sortOrder(page), ['toutiao', 'chinanews']);

        page.document.getElementById('btn-sources-save').click();
        await waitFor(() => page.server.requests('save-sources').length === 1);
        const body = page.server.requests('save-sources')[0].body;
        assert.equal(body.expected_version, 4);
        assert.deepEqual(body.value, ['toutiao', 'chinanews']);
        await waitFor(() => page.server.requests('save-sources')[0].done);

        // 保存成功后回到默认视图，顺序已重排
        await waitFor(() => page.document.getElementById('btn-sources-sort-mode'));
        assert.deepEqual(hourlyOrder(page), ['toutiao', 'chinanews']);
    } finally {
        page.close();
    }
});

test('S8：需要账号的来源启用账号数为 0 时显示跳过提醒，点击徽标就地展开该来源', async () => {
    const sections = defaultSections();
    sections.crawl_sources.value = ['toutiao', 'tencent'];
    const page = await bootPage({ sections });
    try {
        page.clickTab('sources');
        const tencentItem = page.document
            .querySelector('#sources-hourly-list li[data-source="tencent"]');
        const badge = tencentItem.querySelector('.source-account-badge');
        assert.ok(badge.classList.contains('is-warning'));
        assert.match(badge.textContent, /启用账号 0/);
        assert.match(badge.textContent, /本轮会跳过该来源/);

        const toutiaoBadge = page.document
            .querySelector('#sources-hourly-list li[data-source="toutiao"] .source-account-badge');
        assert.ok(!toutiaoBadge.classList.contains('is-warning'));
        assert.match(toutiaoBadge.textContent, /启用账号 1/);

        badge.click();
        // 就地展开：停留在数据源页签，出现该来源的账号展开区
        await waitFor(() => page.document
            .querySelector('.source-accounts-panel[data-accounts-for="tencent"]'));
        assert.ok(!page.panel('sources').hidden);
        assert.equal(page.window.location.hash, '#sources:tencent');
    } finally {
        page.close();
    }
});

test('S9：批量预览渲染四种状态、确认按钮受可新增数量与预览时效约束', async () => {
    const page = await bootPage();
    try {
        await expandSource(page, 'toutiao');
        // 「新增账号」与「批量粘贴」收进默认折叠的「添加账号」<details>
        const details = page.document.querySelector('.account-add-details');
        assert.ok(details);
        assert.equal(details.open, false);
        details.querySelector('summary').click();
        assert.equal(details.open, true);

        const textarea = page.document.getElementById('account-bulk-text');
        const confirmBtn = page.document.getElementById('btn-account-bulk-confirm');
        assert.ok(confirmBtn.disabled);

        // 全部为已存在/重复：没有可新增项时确认按钮不可用
        inputValue(page, textarea, 'toutiao-one');
        page.document.getElementById('btn-account-bulk-preview').click();
        await waitFor(() => page.server.requests('preview-accounts').length === 1
            && page.server.requests('preview-accounts')[0].done);
        await waitFor(() => page.document
            .getElementById('account-bulk-summary').textContent.includes('已存在 1'));
        assert.ok(confirmBtn.disabled);

        // 四种状态同时出现
        inputValue(page, textarea, 'new-id-1\ntoutiao-one\nnew-id-1\ninvalid-line');
        page.document.getElementById('btn-account-bulk-preview').click();
        await waitFor(() => page.server.requests('preview-accounts').length === 2
            && page.server.requests('preview-accounts')[1].done);
        const summary = page.document.getElementById('account-bulk-summary');
        await waitFor(() => summary.textContent.includes('可新增 1'));
        assert.match(summary.textContent, /已存在 1/);
        assert.match(summary.textContent, /本批内重复 1/);
        assert.match(summary.textContent, /无法解析 1/);
        assert.ok(!confirmBtn.disabled);
        assert.equal(confirmBtn.textContent, '确认添加 1 个');
        const invalidRow = page.document
            .querySelector('.account-bulk-row.is-invalid');
        // 「无法解析」是界面按状态生成的标签（fixture 的错误文案刻意不含这四个字），
        // 后面的原因则来自服务端原文
        assert.match(invalidRow.textContent, /无法解析：不认识的输入格式/);

        // 预览后修改文本：预览作废，确认按钮失效
        inputValue(page, textarea, 'new-id-1\ntoutiao-one\nnew-id-1\ninvalid-line\nnew-id-2');
        assert.ok(confirmBtn.disabled);
        await waitFor(() => summary.textContent.includes('重新预览'));

        // 重新预览后确认，列表刷新、结果显示「已添加」
        page.document.getElementById('btn-account-bulk-preview').click();
        await waitFor(() => page.server.requests('preview-accounts').length === 3
            && page.server.requests('preview-accounts')[2].done);
        await waitFor(() => !confirmBtn.disabled);
        assert.equal(confirmBtn.textContent, '确认添加 2 个');
        confirmBtn.click();
        await waitFor(() => page.server.requests('bulk-accounts').length === 1
            && page.server.requests('bulk-accounts')[0].done);
        await waitFor(() => page.document
            .querySelector('#account-bulk-results').textContent.includes('已添加'));
        await waitFor(() => page.document
            .getElementById('accounts-body').textContent.includes('new-id-1'));
        assert.ok(confirmBtn.disabled);
    } finally {
        page.close();
    }
});

test('S10：启用开关 PATCH 失败时开关恢复原状态并显示错误', async () => {
    const accounts = defaultAccounts();
    accounts.tencent = [makeAccount({
        id: 'acc-tencent-1',
        source: 'tencent',
        normalized_identifier: 'tencent-one',
        enabled: true,
    })];
    const page = await bootPage({ accounts });
    try {
        // tencent 默认未启用，在未启用分组中展开
        await expandSource(page, 'tencent');
        await waitFor(() => page.document
            .querySelector('#accounts-body tr[data-account-id="acc-tencent-1"]'));
        const row = page.document
            .querySelector('#accounts-body tr[data-account-id="acc-tencent-1"]');
        const toggle = row.querySelector('.account-enabled-toggle');
        assert.equal(toggle.checked, true);

        page.server.failNext['patch-account'] = 1;
        toggle.click();
        assert.equal(toggle.checked, false);
        await waitFor(() => page.server.requests('patch-account').length === 1
            && page.server.requests('patch-account')[0].done);
        await waitFor(() => toggle.checked === true);
        assert.match(row.querySelector('.account-row-error').textContent, /状态切换失败/);
    } finally {
        page.close();
    }
});

test('S10b：启用开关请求抛网络异常时开关恢复原状态并显示错误', async () => {
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
        await waitFor(() => page.document
            .querySelector('#accounts-body tr[data-account-id="acc-tencent-1"]'));
        const row = page.document
            .querySelector('#accounts-body tr[data-account-id="acc-tencent-1"]');
        const toggle = row.querySelector('.account-enabled-toggle');
        assert.equal(toggle.checked, true);

        page.server.throwNext['patch-account'] = 1;
        toggle.click();
        assert.equal(toggle.checked, false);
        await waitFor(() => page.server.requests('patch-account').length === 1
            && page.server.requests('patch-account')[0].done);
        await waitFor(() => toggle.checked === true);
        assert.match(row.querySelector('.account-row-error').textContent, /状态切换失败/);
    } finally {
        page.close();
    }
});

test('S11：后端返回的 display_name 含 HTML 时在名称链接里按纯文本渲染，不生成元素', async () => {
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
        await waitFor(() => page.document
            .querySelector('#accounts-body tr[data-account-id="acc-xss"]'));
        const row = page.document
            .querySelector('#accounts-body tr[data-account-id="acc-xss"]');
        const panel = page.document
            .querySelector('.source-accounts-panel[data-accounts-for="toutiao"]');
        assert.equal(row.querySelectorAll('img').length, 0);
        assert.equal(panel.querySelectorAll('img').length, 0);
        assert.equal(page.window.__xssHit, undefined);
        // display_name 整体作为文本写进名称链接，href 取自 profile_url
        const link = row.querySelector('.account-name-link');
        assert.equal(link.textContent, payload);
        assert.equal(link.getAttribute('href'), 'https://example.com/xss-profile');
        assert.equal(link.target, '_blank');
        assert.equal(link.rel, 'noopener noreferrer');
    } finally {
        page.close();
    }
});

test('S12：排序模式下的未保存修改在页签切换后保留，离开页面时触发离开确认', async () => {
    const page = await bootPage();
    try {
        // 无修改时不拦截离开
        const cleanEvent = new page.window.Event('beforeunload', { cancelable: true });
        page.window.dispatchEvent(cleanEvent);
        assert.equal(cleanEvent.defaultPrevented, false);

        // 进入排序模式并调整顺序
        page.document.getElementById('btn-sources-sort-mode').click();
        await waitFor(() => page.document.getElementById('sources-sort-list'));
        assert.deepEqual(sortOrder(page), ['toutiao', 'chinanews']);
        const chinanewsItem = page.document
            .querySelector('#sources-sort-list li[data-source="chinanews"]');
        buttonByText(chinanewsItem, '上移').click();
        assert.deepEqual(sortOrder(page), ['chinanews', 'toutiao']);

        // 切到模型页签再切回来，排序草稿仍在
        page.clickTab('models');
        assert.ok(page.panel('sources').hidden);
        page.clickTab('sources');
        assert.ok(page.document.getElementById('sources-sort-list'));
        assert.deepEqual(sortOrder(page), ['chinanews', 'toutiao']);

        // 有未保存修改时拦截离开
        const dirtyEvent = new page.window.Event('beforeunload', { cancelable: true });
        page.window.dispatchEvent(dirtyEvent);
        assert.equal(dirtyEvent.defaultPrevented, true);

        // 取消后回到默认视图，不再拦截
        page.document.getElementById('btn-sources-cancel').click();
        assert.ok(page.document.getElementById('btn-sources-sort-mode'));
        assert.deepEqual(hourlyOrder(page), ['toutiao', 'chinanews']);
        const discardedEvent = new page.window.Event('beforeunload', { cancelable: true });
        page.window.dispatchEvent(discardedEvent);
        assert.equal(discardedEvent.defaultPrevented, false);
    } finally {
        page.close();
    }
});

test('S13：来源启停即时保存，请求体是切换后的完整有序列表并带当前版本号', async () => {
    const page = await bootPage();
    try {
        // 停用 chinanews：['toutiao', 'chinanews'] → ['toutiao']
        const chinanewsToggle = page.document.querySelector(
            '#sources-hourly-list li[data-source="chinanews"] .source-enabled-toggle',
        );
        chinanewsToggle.click();
        await waitFor(() => page.server.requests('save-sources').length === 1);
        let body = page.server.requests('save-sources')[0].body;
        assert.deepEqual(body.value, ['toutiao']);
        assert.equal(body.expected_version, 4);
        await waitFor(() => page.server.requests('save-sources')[0].done);
        // 成功后重排：chinanews 移到未启用分组
        await waitFor(() => page.document
            .querySelector('#sources-available-list li[data-source="chinanews"]'));
        assert.deepEqual(hourlyOrder(page), ['toutiao']);

        // 启用 tencent：追加到列表末尾，版本号跟随上一次保存
        const tencentToggle = page.document.querySelector(
            '#sources-available-list li[data-source="tencent"] .source-enabled-toggle',
        );
        tencentToggle.click();
        await waitFor(() => page.server.requests('save-sources').length === 2);
        body = page.server.requests('save-sources')[1].body;
        assert.deepEqual(body.value, ['toutiao', 'tencent']);
        assert.equal(body.expected_version, 5);
        await waitFor(() => page.server.requests('save-sources')[1].done);
        await waitFor(() => hourlyOrder(page).join(',') === 'toutiao,tencent');
    } finally {
        page.close();
    }
});

test('S14：启停保存失败时开关回滚、行内显示错误且本地列表不变', async () => {
    const page = await bootPage();
    try {
        page.server.saveBehavior.crawl_sources = {
            status: 422,
            payload: { detail: '来源列表无效' },
        };
        const row = page.document
            .querySelector('#sources-hourly-list li[data-source="chinanews"]');
        const toggle = row.querySelector('.source-enabled-toggle');
        assert.equal(toggle.checked, true);

        toggle.click();
        assert.equal(toggle.checked, false);
        await waitFor(() => page.server.requests('save-sources').length === 1
            && page.server.requests('save-sources')[0].done);
        // 开关回滚、行内错误、本地列表不做任何改动
        await waitFor(() => toggle.checked === true);
        assert.match(row.querySelector('.source-row-error').textContent, /停用失败/);
        assert.match(row.querySelector('.source-row-error').textContent, /来源列表无效/);
        assert.deepEqual(hourlyOrder(page), ['toutiao', 'chinanews']);
        assert.equal(
            page.document.querySelector('#sources-available-list li[data-source="chinanews"]'),
            null,
        );
    } finally {
        page.close();
    }
});

test('S15：停用最后一个启用来源被前端拦截，不发出请求', async () => {
    const sections = defaultSections();
    sections.crawl_sources.value = ['toutiao'];
    const page = await bootPage({ sections });
    try {
        assert.deepEqual(hourlyOrder(page), ['toutiao']);
        const row = page.document
            .querySelector('#sources-hourly-list li[data-source="toutiao"]');
        const toggle = row.querySelector('.source-enabled-toggle');

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

test('S16：手风琴——展开第二个来源时第一个自动收起，未启用来源同样可展开', async () => {
    const page = await bootPage();
    try {
        page.clickTab('sources');
        await expandSource(page, 'toutiao');
        assert.equal(page.document.querySelectorAll('.source-accounts-panel').length, 1);

        // tencent 在未启用分组，同样可展开管理账号
        await expandSource(page, 'tencent');
        const panels = page.document.querySelectorAll('.source-accounts-panel');
        assert.equal(panels.length, 1);
        assert.equal(panels[0].dataset.accountsFor, 'tencent');
        // 第一个来源的箭头恢复未展开状态
        const toutiaoArrow = page.document.querySelector(
            '#sources-hourly-list li[data-source="toutiao"] .source-expand-toggle',
        );
        assert.equal(toutiaoArrow.getAttribute('aria-expanded'), 'false');
        assert.equal(page.window.location.hash, '#sources:tencent');
    } finally {
        page.close();
    }
});

test('S17：排序模式「取消」丢弃草稿并回到默认视图，顺序不变也不发出请求', async () => {
    const page = await bootPage();
    try {
        page.document.getElementById('btn-sources-sort-mode').click();
        await waitFor(() => page.document.getElementById('sources-sort-list'));
        const chinanewsItem = page.document
            .querySelector('#sources-sort-list li[data-source="chinanews"]');
        buttonByText(chinanewsItem, '上移').click();
        assert.deepEqual(sortOrder(page), ['chinanews', 'toutiao']);

        page.document.getElementById('btn-sources-cancel').click();
        // 回到默认视图，顺序与进入排序模式前一致
        assert.ok(page.document.getElementById('btn-sources-sort-mode'));
        assert.deepEqual(hourlyOrder(page), ['toutiao', 'chinanews']);
        // 取消不发出保存请求
        await assertNever(() => page.server.requests('save-sources').length > 0, 200);
        // 草稿已清理，离开页面不再拦截
        const event = new page.window.Event('beforeunload', { cancelable: true });
        page.window.dispatchEvent(event);
        assert.equal(event.defaultPrevented, false);
    } finally {
        page.close();
    }
});

test('S18：旧 hash #accounts:toutiao 被改写为 #sources:toutiao 并展开对应来源', async () => {
    const page = await bootPage({ hash: '#accounts:toutiao' });
    try {
        await waitFor(() => page.window.location.hash === '#sources:toutiao');
        assert.ok(!page.panel('sources').hidden);
        await waitFor(() => page.document
            .querySelector('.source-accounts-panel[data-accounts-for="toutiao"]'));
    } finally {
        page.close();
    }
});


test('S19：启停请求进行中锁定全部来源开关，放行后第二次启停基于最新列表', async () => {
    const page = await bootPage();
    try {
        page.clickTab('sources');
        assert.deepEqual(hourlyOrder(page), ['toutiao', 'chinanews']);
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

        // 放行第一个请求：停用成功后面板重渲染，开关恢复可点
        page.server.release('save-sources');
        await waitFor(() => {
            const row = sourceRow(page, 'tencent');
            const toggle = row && row.querySelector('.source-enabled-toggle');
            return toggle && !toggle.disabled;
        });
        assert.deepEqual(hourlyOrder(page), ['toutiao']);

        // 第二次启停基于更新后的列表与版本号
        sourceRow(page, 'tencent').querySelector('.source-enabled-toggle').click();
        await waitFor(() => page.server.requests('save-sources').length === 2);
        const second = page.server.requests('save-sources')[1];
        assert.deepEqual(second.body.value, ['toutiao', 'tencent']);
        assert.equal(second.body.expected_version, 5);
        // 等第二次保存收尾（重渲染完成）再关页面，避免异步续跑泄漏到测试结束后
        await waitFor(() => second.done && hourlyOrder(page).includes('tencent'));
    } finally {
        page.server.release('save-sources');
        page.close();
    }
});

test('S20：启停其他来源触发重渲染后，展开区、筛选词与「添加账号」开合状态保留', async () => {
    const page = await bootPage();
    try {
        page.clickTab('sources');
        await expandSource(page, 'toutiao');
        const filter = page.document.getElementById('accounts-filter');
        inputValue(page, filter, '头条');
        const details = page.document.querySelector('.account-add-details');
        assert.equal(details.open, false, '「添加账号」默认折叠');
        details.open = true;

        // 停用另一个来源并等待保存成功，面板整体重渲染
        sourceRow(page, 'chinanews').querySelector('.source-enabled-toggle').click();
        await waitFor(() => page.server.requests('save-sources').length === 1
            && page.server.requests('save-sources')[0].done);
        // 等重渲染完成：展开区被销毁并重建（筛选框是新的 DOM 节点）
        await waitFor(() => page.document.getElementById('accounts-filter') !== filter);

        const panels = page.document.querySelectorAll('.source-accounts-panel');
        assert.equal(panels.length, 1);
        assert.equal(panels[0].dataset.accountsFor, 'toutiao');
        assert.equal(page.document.getElementById('accounts-filter').value, '头条');
        assert.equal(page.document.querySelector('.account-add-details').open, true);
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

test('S21：名称单元格三种状态——已解析、待获取、获取失败', async () => {
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
        makeAccount({
            id: 'acc-failed',
            source: 'toutiao',
            normalized_identifier: 'tok-failed',
            profile_url: 'https://example.com/failed',
            display_name_error: '解析超时',
        }),
    ];
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitFor(() => page.document
            .querySelector('#accounts-body tr[data-account-id="acc-failed"]'));

        // 已解析：名称文本，链接指向主页
        const resolvedRow = page.document
            .querySelector('#accounts-body tr[data-account-id="acc-resolved"]');
        const resolvedLink = resolvedRow.querySelector('.account-name-link');
        assert.equal(resolvedLink.textContent, '头条账号甲');
        assert.equal(resolvedLink.getAttribute('href'), 'https://example.com/resolved');
        assert.equal(resolvedLink.target, '_blank');
        assert.equal(resolvedLink.rel, 'noopener noreferrer');
        assert.equal(resolvedLink.title, '打开主页');
        assert.equal(resolvedRow.querySelector('.account-name-badge'), null);

        // 未解析：显示截断标识（is-unresolved 由 CSS 做单行省略号），完整值放 title
        const pendingRow = page.document
            .querySelector('#accounts-body tr[data-account-id="acc-pending"]');
        const pendingLink = pendingRow.querySelector('.account-name-link');
        assert.ok(pendingLink.classList.contains('is-unresolved'));
        assert.equal(pendingLink.textContent, longToken);
        assert.equal(pendingLink.title, longToken);
        assert.match(
            pendingRow.querySelector('.account-name-badge').textContent,
            /名称待获取/,
        );

        // 解析失败：同样截断标识，标记为「名称获取失败」，原因放标记的 title
        const failedRow = page.document
            .querySelector('#accounts-body tr[data-account-id="acc-failed"]');
        const failedLink = failedRow.querySelector('.account-name-link');
        assert.ok(failedLink.classList.contains('is-unresolved'));
        assert.equal(failedLink.textContent, 'tok-failed');
        const failedBadge = failedRow.querySelector('.account-name-badge');
        assert.match(failedBadge.textContent, /名称获取失败/);
        assert.equal(failedBadge.title, '解析超时');
    } finally {
        page.close();
    }
});

test('S22：启用开关是每行第一个单元格，仍是 checkbox 且 aria-label 带账号名；空状态 colspan=3', async () => {
    const page = await bootPage();
    try {
        await expandSource(page, 'toutiao');
        await waitFor(() => page.document
            .querySelector('#accounts-body tr[data-account-id="acc-toutiao-1"]'));
        const row = page.document
            .querySelector('#accounts-body tr[data-account-id="acc-toutiao-1"]');
        assert.equal(row.cells.length, 3);
        const toggle = row.cells[0].querySelector('input[type="checkbox"].account-enabled-toggle');
        assert.ok(toggle, '第一个单元格应是启用开关 checkbox');
        assert.match(toggle.getAttribute('aria-label'), /头条一号/);

        const headers = page.document.querySelectorAll('.accounts-table thead th');
        assert.equal(headers.length, 3);
        assert.equal(headers[0].textContent, '');
        assert.equal(headers[1].textContent, '名称');
        assert.equal(headers[2].textContent, '操作');

        // 空状态行 colspan 与三列结构对齐
        await expandSource(page, 'tencent');
        await waitFor(() => page.document
            .querySelector('.source-accounts-panel[data-accounts-for="tencent"] #accounts-body td'));
        const emptyCell = page.document
            .querySelector('.source-accounts-panel[data-accounts-for="tencent"] #accounts-body td');
        assert.equal(emptyCell.getAttribute('colspan'), '3');
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
        await waitFor(() => page.document
            .querySelector('#accounts-body tr[data-account-id="acc-tencent-1"]'));
        const toggle = page.document
            .querySelector('#accounts-body tr[data-account-id="acc-tencent-1"] .account-enabled-toggle');

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

test('S24：来源行开关打头、序号名称徽标箭头依次在后；每日任务只读行有等宽占位', async () => {
    const page = await bootPage();
    try {
        const toutiaoRow = page.document
            .querySelector('#sources-hourly-list li[data-source="toutiao"]');
        assert.ok(toutiaoRow.children[0].matches('input[type="checkbox"].source-enabled-toggle'));
        assert.ok(toutiaoRow.children[1].classList.contains('settings-source-index'));
        assert.ok(toutiaoRow.children[2].classList.contains('settings-source-name'));
        assert.ok(toutiaoRow.children[3].classList.contains('source-account-badge'));
        assert.ok(toutiaoRow.children[4].classList.contains('source-expand-toggle'));

        // 未启用分组没有序号，开关仍在最前
        const tencentRow = page.document
            .querySelector('#sources-available-list li[data-source="tencent"]');
        assert.ok(tencentRow.children[0].matches('input[type="checkbox"].source-enabled-toggle'));
        assert.ok(tencentRow.children[1].classList.contains('settings-source-name'));

        // 每日任务只读行：无开关，首个子元素是与开关等宽的占位，名称紧随其后
        const dailyRow = page.document
            .querySelector('#sources-daily-list li[data-source="bjrb"]');
        assert.equal(dailyRow.querySelector('.source-enabled-toggle'), null);
        assert.ok(dailyRow.children[0].classList.contains('settings-source-toggle-placeholder'));
        assert.ok(dailyRow.children[1].classList.contains('settings-source-name'));
    } finally {
        page.close();
    }
});

test('S25：刷新账号名称按每批最多 20 个切片串行请求，按钮显示进度', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(25);
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitFor(() => page.document
            .querySelector('#accounts-body tr[data-account-id="tt-25"]'));
        const refreshBtn = page.document.getElementById('btn-account-refresh-names');
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
        // 逐行更新到位：第一行显示解析出的名称
        const firstLink = page.document
            .querySelector('#accounts-body tr[data-account-id="tt-1"] .account-name-link');
        assert.equal(firstLink.textContent, '自动名称-tt-token-1');
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
        await waitFor(() => page.document
            .querySelector('#accounts-body tr[data-account-id="tt-2"]'));
        // tt-1 第一次尝试被 skipped（预算耗尽），重试时成功
        page.server.refreshBehavior = (id, attempt) => (
            id === 'tt-1' && attempt === 1 ? 'skipped' : null
        );

        page.document.getElementById('btn-account-refresh-names').click();
        await waitFor(() => page.server.requests('refresh-names').length === 2
            && page.server.requests('refresh-names').every((entry) => entry.done));
        const second = page.server.requests('refresh-names')[1];
        assert.deepEqual(second.body.account_ids, ['tt-1']);

        await waitFor(() => page.document.getElementById('toast').textContent
            .includes('已更新 2 个名称，0 个获取失败'));
        const link = page.document
            .querySelector('#accounts-body tr[data-account-id="tt-1"] .account-name-link');
        assert.equal(link.textContent, '自动名称-tt-token-1');
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
        await waitFor(() => page.document
            .querySelector('#accounts-body tr[data-account-id="tt-25"]'));
        // 后 5 个账号始终 skipped：第一批推进 20 个，第二批 5 个全部 skipped、零推进
        page.server.refreshBehavior = (id) => (
            Number(id.split('-')[1]) > 20 ? 'skipped' : null
        );

        page.document.getElementById('btn-account-refresh-names').click();
        await waitFor(() => page.server.requests('refresh-names').length === 2
            && page.server.requests('refresh-names').every((entry) => entry.done));
        // 兜底生效：不再发出第三次请求
        await assertNever(() => page.server.requests('refresh-names').length >= 3, 300);
        await waitFor(() => page.document
            .getElementById('accounts-refresh-status').textContent
            .includes('部分账号未能刷新，请稍后重试'));
        const refreshBtn = page.document.getElementById('btn-account-refresh-names');
        assert.equal(refreshBtn.disabled, false);
        assert.equal(refreshBtn.textContent, '刷新账号名称');
    } finally {
        page.close();
    }
});

test('S28：刷新逐行就地更新，进行中某行的启用开关仍可点击且不被重建打断', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(2);
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitFor(() => page.document
            .querySelector('#accounts-body tr[data-account-id="tt-2"]'));
        const rowBefore = page.document
            .querySelector('#accounts-body tr[data-account-id="tt-1"]');
        const toggle = page.document
            .querySelector('#accounts-body tr[data-account-id="tt-2"] .account-enabled-toggle');
        assert.equal(toggle.checked, true);

        page.server.hold('refresh-names');
        page.document.getElementById('btn-account-refresh-names').click();
        await waitFor(() => page.server.requests('refresh-names').length === 1);

        // 刷新请求被扣住期间，另一行的启停照常可用
        toggle.click();
        await waitFor(() => page.server.requests('patch-account').length === 1
            && page.server.requests('patch-account')[0].done);
        await waitFor(() => toggle.checked === false);

        page.server.release('refresh-names');
        await waitFor(() => page.server.requests('refresh-names')[0].done);
        await waitFor(() => page.document
            .querySelector('#accounts-body tr[data-account-id="tt-1"] .account-name-link')
            .textContent === '自动名称-tt-token-1');
        // 就地更新：行节点没有被替换，启停结果保留
        const rowAfter = page.document
            .querySelector('#accounts-body tr[data-account-id="tt-1"]');
        assert.equal(rowAfter, rowBefore);
        assert.equal(toggle.checked, false);
        assert.equal(toggle.disabled, false);
    } finally {
        page.close();
    }
});

test('S29：批量添加成功后自动只对新建账号触发名称刷新', async () => {
    const page = await bootPage();
    try {
        await expandSource(page, 'toutiao');
        const details = page.document.querySelector('.account-add-details');
        details.open = true;
        const textarea = page.document.getElementById('account-bulk-text');
        inputValue(page, textarea, 'new-x\nnew-y');
        page.document.getElementById('btn-account-bulk-preview').click();
        await waitFor(() => page.server.requests('preview-accounts').length === 1
            && page.server.requests('preview-accounts')[0].done);
        const confirmBtn = page.document.getElementById('btn-account-bulk-confirm');
        await waitFor(() => !confirmBtn.disabled);
        confirmBtn.click();
        await waitFor(() => page.server.requests('bulk-accounts').length === 1
            && page.server.requests('bulk-accounts')[0].done);

        await waitFor(() => page.server.requests('refresh-names').length === 1
            && page.server.requests('refresh-names')[0].done);
        const newIds = page.server.accounts.toutiao
            .filter((item) => item.id.startsWith('acc-bulk-'))
            .map((item) => item.id);
        assert.equal(newIds.length, 2);
        // 只刷新本批新建的 id，不碰存量账号
        assert.deepEqual(
            page.server.requests('refresh-names')[0].body.account_ids,
            newIds,
        );

        await waitFor(() => page.document.getElementById('toast').textContent
            .includes('已更新 2 个名称，0 个获取失败'));
    } finally {
        page.close();
    }
});

test('S30：刷新进行中切换到别的来源，后到的响应不写入 DOM 也不再发请求', async () => {
    const accounts = defaultAccounts();
    // 25 个账号：切走后若守卫缺失，串行循环会继续发第二批，可被确定地捕获
    accounts.toutiao = makeToutiaoAccounts(25);
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitFor(() => page.document
            .querySelector('#accounts-body tr[data-account-id="tt-25"]'));

        page.server.hold('refresh-names');
        page.document.getElementById('btn-account-refresh-names').click();
        await waitFor(() => page.server.requests('refresh-names').length === 1);

        // 请求被扣住时切换到 tencent：toutiao 展开区被销毁
        await expandSource(page, 'tencent');
        await waitFor(() => page.document
            .querySelector('.source-accounts-panel[data-accounts-for="tencent"]'));

        page.server.release('refresh-names');
        await waitFor(() => page.server.requests('refresh-names')[0].done);
        // 响应归属守卫：循环终止不再发下一批，解析结果不写 DOM、不弹汇总 toast
        await assertNever(() => page.server.requests('refresh-names').length > 1, 200);
        await assertNever(() => page.document.body.textContent.includes('自动名称-tt-token'), 200);
        await assertNever(() => page.document.getElementById('toast').textContent
            .includes('已更新'), 200);
        assert.deepEqual(unhandledRejections, []);
    } finally {
        page.close();
    }
});

test('S31：单个新增后端已同步解析名称，前端不再额外调刷新', async () => {
    const page = await bootPage();
    try {
        await expandSource(page, 'toutiao');
        const details = page.document.querySelector('.account-add-details');
        details.open = true;
        const addInput = page.document.getElementById('account-add-input');
        addInput.value = 'brand-new-id';
        page.document.getElementById('btn-account-add').click();
        await waitFor(() => page.server.requests('add-account').length === 1
            && page.server.requests('add-account')[0].done);
        // 请求体不再携带 display_name
        assert.equal(page.server.requests('add-account')[0].body.display_name, undefined);

        const created = page.server.accounts.toutiao
            .find((item) => item.normalized_identifier === 'brand-new-id');
        await waitFor(() => page.document
            .querySelector(`#accounts-body tr[data-account-id="${created.id}"]`));
        // 直接用响应里的行渲染：名称已解析，不显示「待获取」
        const row = page.document
            .querySelector(`#accounts-body tr[data-account-id="${created.id}"]`);
        assert.equal(row.querySelector('.account-name-link').textContent, '名称-brand-new-id');
        assert.equal(row.querySelector('.account-name-badge'), null);
        // 没有发出任何 refresh-names 请求
        await assertNever(() => page.server.requests('refresh-names').length > 0, 300);
    } finally {
        page.close();
    }
});
