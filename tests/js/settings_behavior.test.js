// 系统设置页（/admin/settings）的 jsdom 行为测试。
// 覆盖验收场景 S1-S12；各场景语义见 tests/test_settings_js_behavior.py 与交付说明。
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const {
    bootPage,
    waitFor,
    defaultSections,
    defaultAccounts,
    makeAccount,
} = require('./settings_harness.js');

function inputValue(page, el, value) {
    el.value = value;
    el.dispatchEvent(new page.window.Event('input', { bubbles: true }));
}

function changeValue(page, el, value) {
    el.value = value;
    el.dispatchEvent(new page.window.Event('change', { bubbles: true }));
}

function hourlyOrder(page) {
    return [...page.document.querySelectorAll('#sources-hourly-list li[data-source]')]
        .map((item) => item.dataset.source);
}

function buttonByText(container, text) {
    return [...container.querySelectorAll('button')]
        .find((btn) => btn.textContent.trim() === text);
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

        // 抓取账号页签不依赖配置分区，仍可加载账号列表
        const accounts = await waitFor(
            () => page.document.querySelectorAll('#accounts-body tr[data-account-id]').length === 1,
        );
        assert.ok(accounts);
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
        const sent = await waitFor(() => page.server.requests('save-models').length === 1);
        assert.ok(sent);
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
        const sent = await waitFor(() => page.server.requests('model-test').length === 1);
        assert.ok(sent);
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
        // 给第二次点击的请求留出发出窗口
        await new Promise((resolve) => setTimeout(resolve, 50));
        assert.equal(page.server.requests('save-models').length, 1);
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
        // 模拟另一处已经把默认模型改掉
        page.server.saveBehavior.llm_models = {
            status: 409,
            payload: { detail: '配置版本冲突：当前版本为 8' } ,
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
        await waitFor(() => status.textContent.includes('冲突'));
        // 本地修改未被覆盖，也没有自动重新拉取
        assert.equal(defaultInput.value, 'local/edit');
        assert.equal(page.server.requests('get-settings').length, 0);
        const reloadBtn = page.document.getElementById('btn-models-reload');
        assert.equal(reloadBtn.hidden, false);

        reloadBtn.click();
        const reloaded = await waitFor(
            () => page.server.requests('get-settings').length === 1,
        );
        assert.ok(reloaded);
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

test('S7：数据源上移下移后保存顺序与页面一致，每日任务来源不参与每小时列表，空列表不能保存', async () => {
    const sections = defaultSections();
    // 初始顺序与字母序不同，便于识别「保存时按字母排序」的变异
    sections.crawl_sources.value = ['chinanews', 'toutiao'];
    const page = await bootPage({ sections });
    try {
        assert.deepEqual(hourlyOrder(page), ['chinanews', 'toutiao']);

        // 每日任务来源只出现在只读分组，不在每小时列表、也没有启用按钮
        const daily = page.document.getElementById('sources-daily-list');
        assert.match(daily.textContent, /北京日报/);
        assert.match(daily.textContent, /劳动午报/);
        assert.match(daily.textContent, /每日单独任务，由服务器计划任务调度/);
        assert.equal(daily.querySelector('.source-enable-btn'), null);
        assert.equal(
            page.document.querySelector('#sources-hourly-list li[data-source="bjrb"]'),
            null,
        );
        assert.equal(
            page.document.querySelector('#sources-available-list li[data-source="bjrb"]'),
            null,
        );

        const toutiaoItem = page.document
            .querySelector('#sources-hourly-list li[data-source="toutiao"]');
        buttonByText(toutiaoItem, '上移').click();
        assert.deepEqual(hourlyOrder(page), ['toutiao', 'chinanews']);

        page.document.getElementById('btn-sources-save').click();
        const sent = await waitFor(() => page.server.requests('save-sources').length === 1);
        assert.ok(sent);
        const body = page.server.requests('save-sources')[0].body;
        assert.equal(body.expected_version, 4);
        assert.deepEqual(body.value, ['toutiao', 'chinanews']);
        await waitFor(() => page.server.requests('save-sources')[0].done);
    } finally {
        page.close();
    }
});

test('S7b：每小时列表为空时前端拦截保存，不发送请求', async () => {
    const page = await bootPage();
    try {
        assert.deepEqual(hourlyOrder(page), ['toutiao', 'chinanews']);
        for (const key of ['toutiao', 'chinanews']) {
            const item = page.document
                .querySelector(`#sources-hourly-list li[data-source="${key}"]`);
            buttonByText(item, '停用').click();
        }
        assert.deepEqual(hourlyOrder(page), []);

        page.document.getElementById('btn-sources-save').click();
        await new Promise((resolve) => setTimeout(resolve, 50));
        assert.equal(page.server.requests('save-sources').length, 0);
        assert.match(
            page.document.getElementById('sources-save-status').textContent,
            /不能为空/,
        );
    } finally {
        page.close();
    }
});

test('S8：需要账号的来源启用账号数为 0 时显示跳过提醒，点击跳到对应账号页签', async () => {
    const sections = defaultSections();
    sections.crawl_sources.value = ['toutiao', 'tencent'];
    const page = await bootPage({ sections });
    try {
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
        assert.ok(!page.panel('accounts').hidden);
        assert.ok(page.panel('sources').hidden);
        const activeSourceBtn = page.document
            .querySelector('.accounts-source-btn.is-active');
        assert.equal(activeSourceBtn.dataset.accountSource, 'tencent');
        assert.equal(page.window.location.hash, '#accounts:tencent');
        // 让 renderAccountsTab 尾部的账号列表加载链落定，避免关页后触碰已销毁的 document
        await new Promise((resolve) => setImmediate(resolve));
    } finally {
        page.close();
    }
});

test('S9：批量预览渲染四种状态、确认按钮受可新增数量与预览时效约束', async () => {
    const page = await bootPage();
    try {
        page.clickTab('accounts');
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
        assert.match(invalidRow.textContent, /无法解析/);

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
        page.clickTab('accounts');
        page.document.querySelector('[data-account-source="tencent"]').click();
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

test('S11：备注名与原始输入中的 HTML 按纯文本渲染，不生成元素', async () => {
    const payload = '<img src=x onerror=window.__xssHit=1>';
    const accounts = defaultAccounts();
    accounts.toutiao = [makeAccount({
        id: 'acc-xss',
        source: 'toutiao',
        normalized_identifier: 'xss-id',
        original_input: payload,
        display_name: payload,
    })];
    const page = await bootPage({ accounts });
    try {
        page.clickTab('accounts');
        await waitFor(() => page.document
            .querySelector('#accounts-body tr[data-account-id="acc-xss"]'));
        const row = page.document
            .querySelector('#accounts-body tr[data-account-id="acc-xss"]');
        assert.equal(row.querySelectorAll('img').length, 0);
        assert.equal(page.panel('accounts').querySelectorAll('img').length, 0);
        assert.equal(page.window.__xssHit, undefined);
        assert.equal(
            row.querySelector('.account-name-input').value,
            payload,
        );
        assert.match(row.querySelector('.account-original').textContent, /<img src=x/);
    } finally {
        page.close();
    }
});

test('S12：未保存修改在页签切换后保留，离开页面时触发离开确认', async () => {
    const page = await bootPage();
    try {
        // 无修改时不拦截离开
        const cleanEvent = new page.window.Event('beforeunload', { cancelable: true });
        page.window.dispatchEvent(cleanEvent);
        assert.equal(cleanEvent.defaultPrevented, false);

        const defaultInput = page.document.getElementById('models-default-input');
        inputValue(page, defaultInput, 'edited/model');

        page.clickTab('sources');
        assert.ok(page.panel('models').hidden);
        page.clickTab('models');
        assert.equal(
            page.document.getElementById('models-default-input').value,
            'edited/model',
        );

        const dirtyEvent = new page.window.Event('beforeunload', { cancelable: true });
        page.window.dispatchEvent(dirtyEvent);
        assert.equal(dirtyEvent.defaultPrevented, true);

        // 放弃修改后不再拦截
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
