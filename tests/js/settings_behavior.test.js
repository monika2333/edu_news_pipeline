// 系统设置页（/admin/settings）的 jsdom 行为测试。
// 覆盖验收场景 S1-S33（S7、S17 已随排序模式一起删除）与芯片布局新增场景 N1-N15；
// 各场景语义见 tests/test_settings_js_behavior.py 与交付说明。
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

// 指定来源的账号展开面板（可同时打开多个，面板间状态隔离）
function accountsPanel(page, key) {
    return page.document
        .querySelector(`.source-accounts-panel[data-accounts-for="${key}"]`);
}

// 点击来源行的展开箭头并等待展开区出现，返回该来源的面板
async function expandSource(page, key) {
    const arrow = sourceRow(page, key).querySelector('.source-expand-toggle');
    assert.ok(arrow, `来源 ${key} 应有展开箭头`);
    arrow.click();
    await waitFor(() => accountsPanel(page, key));
    return accountsPanel(page, key);
}

// 芯片布局下的常用取数：芯片（账号 id 全局唯一，不按面板区分）、
// 面板内的共享错误行、待提交条
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

function deleteBar(page, key) {
    return accountsPanel(page, key).querySelector('.accounts-delete-bar');
}

function deleteBarCount(page, key) {
    const bar = deleteBar(page, key);
    return bar ? bar.querySelector('.accounts-delete-count').textContent : null;
}

function manageButton(page, key) {
    return accountsPanel(page, key).querySelector('.accounts-manage-btn');
}

// 进入管理态并等芯片重建出管理态操作按钮
async function enterManageMode(page, key) {
    manageButton(page, key).click();
    await waitFor(() => accountsPanel(page, key).querySelector('.account-chip-delete'));
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

test('S8：需要账号的来源启用数为 0 时徽标显示跳过提醒，点击徽标就地展开该来源', async () => {
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

        const toutiaoBadge = page.document
            .querySelector('#sources-list li[data-source="toutiao"] .source-account-badge');
        assert.ok(!toutiaoBadge.classList.contains('is-warning'));
        assert.match(toutiaoBadge.textContent, /启用 1 \/ 共 1/);

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
        const panel = await expandSource(page, 'toutiao');
        // 「新增账号」与「批量粘贴」收进默认折叠的「添加账号」<details>
        const details = panel.querySelector('.account-add-details');
        assert.ok(details);
        assert.equal(details.open, false);
        details.querySelector('summary').click();
        assert.equal(details.open, true);

        const textarea = panel.querySelector('.account-bulk-text');
        const previewBtn = panel.querySelector('.btn-account-bulk-preview');
        const confirmBtn = panel.querySelector('.btn-account-bulk-confirm');
        assert.ok(confirmBtn.disabled);

        // 全部为已存在/重复：没有可新增项时确认按钮不可用
        inputValue(page, textarea, 'toutiao-one');
        previewBtn.click();
        await waitFor(() => page.server.requests('preview-accounts').length === 1
            && page.server.requests('preview-accounts')[0].done);
        const summary = panel.querySelector('.account-bulk-summary');
        await waitFor(() => summary.textContent.includes('已存在 1'));
        assert.ok(confirmBtn.disabled);

        // 四种状态同时出现
        inputValue(page, textarea, 'new-id-1\ntoutiao-one\nnew-id-1\ninvalid-line');
        previewBtn.click();
        await waitFor(() => page.server.requests('preview-accounts').length === 2
            && page.server.requests('preview-accounts')[1].done);
        await waitFor(() => summary.textContent.includes('可新增 1'));
        assert.match(summary.textContent, /已存在 1/);
        assert.match(summary.textContent, /本批内重复 1/);
        assert.match(summary.textContent, /无法解析 1/);
        assert.ok(!confirmBtn.disabled);
        assert.equal(confirmBtn.textContent, '确认添加 1 个');
        const invalidRow = panel.querySelector('.account-bulk-row.is-invalid');
        // 「无法解析」是界面按状态生成的标签（fixture 的错误文案刻意不含这四个字），
        // 后面的原因则来自服务端原文
        assert.match(invalidRow.textContent, /无法解析：不认识的输入格式/);

        // 预览后修改文本：预览作废，确认按钮失效
        inputValue(page, textarea, 'new-id-1\ntoutiao-one\nnew-id-1\ninvalid-line\nnew-id-2');
        assert.ok(confirmBtn.disabled);
        await waitFor(() => summary.textContent.includes('重新预览'));

        // 重新预览后确认，列表刷新、结果显示「已添加」
        previewBtn.click();
        await waitFor(() => page.server.requests('preview-accounts').length === 3
            && page.server.requests('preview-accounts')[2].done);
        await waitFor(() => !confirmBtn.disabled);
        assert.equal(confirmBtn.textContent, '确认添加 2 个');
        confirmBtn.click();
        await waitFor(() => page.server.requests('bulk-accounts').length === 1
            && page.server.requests('bulk-accounts')[0].done);
        await waitFor(() => panel.querySelector('.account-bulk-results').textContent
            .includes('已添加'));
        await waitFor(() => panel.querySelector('.accounts-chip-grid').textContent
            .includes('new-id-1'));
        assert.ok(confirmBtn.disabled);
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
        // display_name 整体作为文本写进芯片名称，默认态芯片上没有主页链接
        const nameEl = chip.querySelector('.account-chip-name');
        assert.equal(nameEl.textContent, payload);
        assert.equal(chip.querySelector('a'), null);
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

test('S16：多开——同时展开多个来源互不收起，收起其中一个不影响另一个，hash 记最近展开的来源', async () => {
    const page = await bootPage();
    try {
        page.clickTab('sources');
        await expandSource(page, 'toutiao');
        assert.equal(page.document.querySelectorAll('.source-accounts-panel').length, 1);

        // tencent 处于停用状态，同样可展开管理账号；展开后两个面板共存
        await expandSource(page, 'tencent');
        assert.equal(page.document.querySelectorAll('.source-accounts-panel').length, 2);
        // 两个箭头都处于展开态，hash 指向最近展开的 tencent
        const toutiaoArrow = page.document.querySelector(
            '#sources-list li[data-source="toutiao"] .source-expand-toggle',
        );
        const tencentArrow = page.document.querySelector(
            '#sources-list li[data-source="tencent"] .source-expand-toggle',
        );
        assert.equal(toutiaoArrow.getAttribute('aria-expanded'), 'true');
        assert.equal(tencentArrow.getAttribute('aria-expanded'), 'true');
        assert.equal(page.window.location.hash, '#sources:tencent');

        // 收起 tencent：toutiao 面板与箭头不受影响，hash 回写到仍打开的 toutiao
        tencentArrow.click();
        await waitFor(() => accountsPanel(page, 'tencent') === null);
        assert.ok(accountsPanel(page, 'toutiao'));
        assert.equal(toutiaoArrow.getAttribute('aria-expanded'), 'true');
        assert.equal(tencentArrow.getAttribute('aria-expanded'), 'false');
        assert.equal(page.window.location.hash, '#sources:toutiao');
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

test('S20：启停其他来源触发重渲染后，展开区、筛选词与「添加账号」开合状态保留', async () => {
    const page = await bootPage();
    try {
        page.clickTab('sources');
        const panel = await expandSource(page, 'toutiao');
        const filter = panel.querySelector('.accounts-filter');
        inputValue(page, filter, '头条');
        const details = panel.querySelector('.account-add-details');
        assert.equal(details.open, false, '「添加账号」默认折叠');
        details.open = true;

        // 停用另一个来源并等待保存成功，面板整体重渲染
        sourceRow(page, 'chinanews').querySelector('.source-enabled-toggle').click();
        await waitFor(() => page.server.requests('save-sources').length === 1
            && page.server.requests('save-sources')[0].done);
        // 等重渲染完成：展开区被销毁并重建（筛选框是新的 DOM 节点）
        await waitFor(() => {
            const rebuilt = accountsPanel(page, 'toutiao');
            return rebuilt && rebuilt.querySelector('.accounts-filter') !== filter;
        });

        const panels = page.document.querySelectorAll('.source-accounts-panel');
        assert.equal(panels.length, 1);
        assert.equal(panels[0].dataset.accountsFor, 'toutiao');
        const rebuiltPanel = accountsPanel(page, 'toutiao');
        assert.equal(rebuiltPanel.querySelector('.accounts-filter').value, '头条');
        assert.equal(rebuiltPanel.querySelector('.account-add-details').open, true);
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

test('S21b：头条账号带名称错误时仍显示中性待获取状态', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = [makeAccount({
        id: 'acc-toutiao-waiting',
        source: 'toutiao',
        normalized_identifier: 'tok-waiting',
        profile_url: 'https://example.com/toutiao-waiting',
        display_name_error: '头条账号名将在下一轮抓取后自动获取',
    })];
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        const chip = await waitForChip(page, 'acc-toutiao-waiting');

        const badge = chip.querySelector('.account-name-badge');
        assert.equal(badge.textContent, '名称待获取');
        assert.ok(!badge.classList.contains('is-error'));
        assert.equal(badge.title, '头条账号名将在下一轮抓取后自动获取');
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
        // 默认态芯片上没有删除按钮与主页链接
        assert.equal(chip.querySelector('.account-chip-delete'), null);
        assert.equal(chip.querySelector('.account-chip-open'), null);

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

test('S24：来源行内顺序为开关 → 名称 → 账号数徽标 → 展开箭头；停用行带弱化样式类；每日任务只读行有等宽占位', async () => {
    const page = await bootPage();
    try {
        // 启用行：开关打头，名称、账号数徽标、展开箭头依次在后（没有序号）
        const toutiaoRow = page.document
            .querySelector('#sources-list li[data-source="toutiao"]');
        assert.ok(toutiaoRow.children[0].matches('input[type="checkbox"].source-enabled-toggle'));
        assert.ok(toutiaoRow.children[1].classList.contains('settings-source-name'));
        assert.ok(toutiaoRow.children[2].classList.contains('source-account-badge'));
        assert.ok(toutiaoRow.children[3].classList.contains('source-expand-toggle'));
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

        // 每日任务只读行：无开关，首个子元素是与开关等宽的占位，名称紧随其后
        const dailyRow = page.document
            .querySelector('#sources-daily-list li[data-source="bjrb"]');
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
        const panel = await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-25');
        const refreshBtn = panel.querySelector('.accounts-refresh-btn');
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
        const panel = await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-2');
        // tt-1 第一次尝试被 skipped（预算耗尽），重试时成功
        page.server.refreshBehavior = (id, attempt) => (
            id === 'tt-1' && attempt === 1 ? 'skipped' : null
        );

        panel.querySelector('.accounts-refresh-btn').click();
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
        const panel = await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-25');
        // 后 5 个账号始终 skipped：第一批推进 20 个，第二批 5 个全部 skipped、零推进
        page.server.refreshBehavior = (id) => (
            Number(id.split('-')[1]) > 20 ? 'skipped' : null
        );

        panel.querySelector('.accounts-refresh-btn').click();
        await waitFor(() => page.server.requests('refresh-names').length === 2
            && page.server.requests('refresh-names').every((entry) => entry.done));
        // 兜底生效：不再发出第三次请求
        await assertNever(() => page.server.requests('refresh-names').length >= 3, 300);
        await waitFor(() => panel.querySelector('.accounts-refresh-status').textContent
            .includes('部分账号未能刷新，请稍后重试'));
        const refreshBtn = panel.querySelector('.accounts-refresh-btn');
        assert.equal(refreshBtn.disabled, false);
        assert.equal(refreshBtn.textContent, '刷新账号名称');
    } finally {
        page.close();
    }
});

test('S28：刷新逐芯片就地更新，进行中另一芯片的启用开关仍可点击且不被重建打断', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(2);
    const page = await bootPage({ accounts });
    try {
        const panel = await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-2');
        const chipBefore = accountChip(page, 'tt-1');
        const toggle = accountChip(page, 'tt-2').querySelector('.account-enabled-toggle');
        assert.equal(toggle.checked, true);

        page.server.hold('refresh-names');
        panel.querySelector('.accounts-refresh-btn').click();
        await waitFor(() => page.server.requests('refresh-names').length === 1);

        // 刷新请求被扣住期间，另一芯片的启停照常可用
        toggle.click();
        await waitFor(() => page.server.requests('patch-account').length === 1
            && page.server.requests('patch-account')[0].done);
        await waitFor(() => toggle.checked === false);

        page.server.release('refresh-names');
        await waitFor(() => page.server.requests('refresh-names')[0].done);
        await waitFor(() => accountChip(page, 'tt-1')
            .querySelector('.account-chip-name').textContent === '自动名称-tt-token-1');
        // 就地更新：芯片节点没有被替换，启停结果保留
        const chipAfter = accountChip(page, 'tt-1');
        assert.equal(chipAfter, chipBefore);
        assert.equal(toggle.checked, false);
        assert.equal(toggle.disabled, false);
    } finally {
        page.close();
    }
});

test('S29：批量添加成功后自动只对新建账号触发名称刷新', async () => {
    const page = await bootPage();
    try {
        const panel = await expandSource(page, 'toutiao');
        const details = panel.querySelector('.account-add-details');
        details.open = true;
        const textarea = panel.querySelector('.account-bulk-text');
        inputValue(page, textarea, 'new-x\nnew-y');
        panel.querySelector('.btn-account-bulk-preview').click();
        await waitFor(() => page.server.requests('preview-accounts').length === 1
            && page.server.requests('preview-accounts')[0].done);
        const confirmBtn = panel.querySelector('.btn-account-bulk-confirm');
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

test('S30：刷新进行中收起该来源面板，后到的响应不写入 DOM 也不再发请求', async () => {
    const accounts = defaultAccounts();
    // 25 个账号：收起面板后若守卫缺失，串行循环会继续发第二批，可被确定地捕获
    accounts.toutiao = makeToutiaoAccounts(25);
    const page = await bootPage({ accounts });
    try {
        const panel = await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-25');

        page.server.hold('refresh-names');
        panel.querySelector('.accounts-refresh-btn').click();
        await waitFor(() => page.server.requests('refresh-names').length === 1);

        // 请求被扣住时收起 toutiao 面板：多开语义下展开别的来源不再销毁本面板，
        // 只有收起本面板才触发守卫
        sourceRow(page, 'toutiao').querySelector('.source-expand-toggle').click();
        await waitFor(() => accountsPanel(page, 'toutiao') === null);

        page.server.release('refresh-names');
        await waitFor(() => page.server.requests('refresh-names')[0].done);
        // 面板归属守卫：循环终止不再发下一批，解析结果不写 DOM、不弹汇总 toast
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
        const panel = await expandSource(page, 'toutiao');
        const details = panel.querySelector('.account-add-details');
        details.open = true;
        const addInput = panel.querySelector('.account-add-input');
        addInput.value = 'brand-new-id';
        panel.querySelector('.btn-account-add').click();
        await waitFor(() => page.server.requests('add-account').length === 1
            && page.server.requests('add-account')[0].done);
        // 请求体不再携带 display_name
        assert.equal(page.server.requests('add-account')[0].body.display_name, undefined);

        const created = page.server.accounts.toutiao
            .find((item) => item.normalized_identifier === 'brand-new-id');
        const chip = await waitForChip(page, created.id);
        // 直接用响应里的行渲染：名称已解析，不显示「待获取」
        assert.equal(
            chip.querySelector('.account-chip-name').textContent,
            '名称-brand-new-id',
        );
        assert.equal(chip.querySelector('.account-name-badge'), null);
        // 没有发出任何 refresh-names 请求
        await assertNever(() => page.server.requests('refresh-names').length > 0, 300);
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

// ---------- 芯片布局新增场景（N1-N14） ----------

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

test('N2：默认态芯片上不存在删除按钮与主页链接，也没有待提交条', async () => {
    const page = await bootPage();
    try {
        await expandSource(page, 'toutiao');
        const chip = await waitForChip(page, 'acc-toutiao-1');
        assert.equal(chip.querySelector('.account-chip-delete'), null);
        assert.equal(chip.querySelector('.account-chip-open'), null);
        assert.equal(chip.querySelector('a'), null);
        assert.equal(chip.querySelector('button'), null);
        assert.equal(deleteBar(page, 'toutiao'), null);
    } finally {
        page.close();
    }
});

test('N3：管理态下芯片不可启停，↗ 是指向主页的新标签页链接，添加账号与名称刷新被锁定', async () => {
    const page = await bootPage();
    try {
        const panel = await expandSource(page, 'toutiao');
        await waitForChip(page, 'acc-toutiao-1');
        const details = panel.querySelector('.account-add-details');
        details.open = true;
        const refreshBtn = panel.querySelector('.accounts-refresh-btn');
        assert.equal(refreshBtn.disabled, false);

        await enterManageMode(page, 'toutiao');
        const chip = accountChip(page, 'acc-toutiao-1');
        const toggle = chip.querySelector('.account-enabled-toggle');
        assert.equal(toggle.disabled, true, '管理态应禁用芯片 checkbox');
        // 点击芯片主体不发出 PATCH、不改变启用状态
        chip.querySelector('.account-chip-label').click();
        await assertNever(() => page.server.requests('patch-account').length > 0, 300);
        assert.equal(toggle.checked, true);

        const openLink = chip.querySelector('a.account-chip-open');
        assert.ok(openLink, '管理态芯片应有打开主页入口');
        assert.equal(
            openLink.getAttribute('href'),
            'https://www.toutiao.com/c/user/toutiao-one/',
        );
        assert.equal(openLink.target, '_blank');
        assert.equal(openLink.rel, 'noopener noreferrer');
        assert.equal(openLink.getAttribute('aria-label'), '打开 头条一号 的主页');
        const deleteBtn = chip.querySelector('button.account-chip-delete');
        assert.ok(deleteBtn, '管理态芯片应有标记删除按钮');
        assert.equal(deleteBtn.getAttribute('aria-label'), '删除 头条一号');

        // 管理态锁定：添加账号折叠并锁定、名称刷新禁用
        assert.equal(details.open, false);
        assert.ok(details.classList.contains('is-locked'));
        assert.equal(refreshBtn.disabled, true);

        // 退出管理态后恢复进入前的开合状态与可用状态
        manageButton(page, 'toutiao').click();
        await waitFor(() => {
            const current = accountChip(page, 'acc-toutiao-1');
            return current && !current.querySelector('.account-enabled-toggle').disabled;
        });
        assert.equal(details.open, true);
        assert.ok(!details.classList.contains('is-locked'));
        assert.equal(panel.querySelector('.accounts-refresh-btn').disabled, false);
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
        await enterManageMode(page, 'toutiao');
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

test('N5：被筛选隐藏的已标记芯片仍计入待提交条数量', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(2);
    const page = await bootPage({ accounts });
    try {
        const panel = await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-2');
        await enterManageMode(page, 'toutiao');
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');

        // 输入筛选词把已标记芯片隐藏，待提交条数量不变
        inputValue(page, panel.querySelector('.accounts-filter'), '不存在的名字');
        await waitFor(() => accountChip(page, 'tt-1') === null);
        assert.match(
            panel.querySelector('.accounts-chip-grid').textContent,
            /没有匹配的账号/,
        );
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');
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
        await enterManageMode(page, 'toutiao');
        // 刻意按 tt-3 → tt-1 的顺序标记，验证提交顺序跟随标记顺序
        accountChip(page, 'tt-3').querySelector('.account-chip-delete').click();
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();

        page.server.hold('delete-account', 1);
        accountsPanel(page, 'toutiao').querySelector('.btn-account-delete-confirm').click();
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
        await enterManageMode(page, 'toutiao');
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        accountChip(page, 'tt-2').querySelector('.account-chip-delete').click();
        accountsPanel(page, 'toutiao').querySelector('.btn-account-delete-confirm').click();

        await waitFor(() => page.server.requests('delete-account').length === 2
            && page.server.requests('delete-account').every((entry) => entry.done));
        assert.equal(page.server.requests('delete-account')[1].status, 404);
        // 404 计入成功：按全部成功收尾，无错误提示
        await waitFor(() => page.document.getElementById('toast').textContent
            .includes('已删除 2 个账号'));
        assert.equal(accountsPanelError(page, 'toutiao').textContent, '');
        assert.equal(manageButton(page, 'toutiao').getAttribute('aria-pressed'), 'false');
        assert.equal(deleteBar(page, 'toutiao'), null);
        await waitFor(() => accountChip(page, 'tt-1') === null
            && accountChip(page, 'tt-2') === null);
    } finally {
        page.close();
    }
});

test('N8：部分失败时已成功的删除不回滚，失败的保留标记并停留在管理态', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(3);
    const page = await bootPage({ accounts });
    try {
        page.server.deleteBehavior = (id) => (
            id === 'tt-2' ? { status: 500, detail: '数据库繁忙' } : null
        );
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-3');
        await enterManageMode(page, 'toutiao');
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        accountChip(page, 'tt-2').querySelector('.account-chip-delete').click();
        accountsPanel(page, 'toutiao').querySelector('.btn-account-delete-confirm').click();

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
        // 停留在管理态，待提交条按剩余标记计数
        assert.equal(manageButton(page, 'toutiao').getAttribute('aria-pressed'), 'true');
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');
    } finally {
        page.close();
    }
});

test('N9：删除提交进行中确认/取消/管理均禁用，重复点击不发出第二轮请求', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(2);
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-2');
        await enterManageMode(page, 'toutiao');
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        accountChip(page, 'tt-2').querySelector('.account-chip-delete').click();

        page.server.hold('delete-account', 1);
        const confirmBtn = accountsPanel(page, 'toutiao')
            .querySelector('.btn-account-delete-confirm');
        confirmBtn.click();
        // 重复点击不应发出第二轮请求
        confirmBtn.click();
        await waitFor(() => page.server.requests('delete-account').length === 1);
        assert.equal(confirmBtn.disabled, true);
        assert.match(confirmBtn.textContent, /删除中… 1\/2/);
        assert.equal(
            accountsPanel(page, 'toutiao').querySelector('.btn-account-delete-cancel').disabled,
            true,
        );
        assert.equal(manageButton(page, 'toutiao').disabled, true);
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

test('N10：退出管理态、点「取消」、收起再展开同一来源都会丢弃待删标记', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(2);
    const page = await bootPage({ accounts });
    try {
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-2');

        // 路径一：再点「管理」退出
        await enterManageMode(page, 'toutiao');
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');
        manageButton(page, 'toutiao').click();
        assert.equal(deleteBar(page, 'toutiao'), null);
        await enterManageMode(page, 'toutiao');
        assert.equal(deleteBar(page, 'toutiao'), null, '重新进入管理态时标记应已清空');
        assert.ok(!accountChip(page, 'tt-1').classList.contains('is-marked'));
        manageButton(page, 'toutiao').click();

        // 路径二：点待提交条「取消」退出
        await enterManageMode(page, 'toutiao');
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        accountsPanel(page, 'toutiao').querySelector('.btn-account-delete-cancel').click();
        assert.equal(deleteBar(page, 'toutiao'), null);
        await enterManageMode(page, 'toutiao');
        assert.equal(deleteBar(page, 'toutiao'), null, '取消后重新进入管理态时标记应已清空');
        assert.ok(!accountChip(page, 'tt-1').classList.contains('is-marked'));

        // 路径三：收起再展开同一来源（收起即重置该来源的面板状态）
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');
        sourceRow(page, 'toutiao').querySelector('.source-expand-toggle').click();
        await waitFor(() => accountsPanel(page, 'toutiao') === null);
        await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-1');
        await enterManageMode(page, 'toutiao');
        assert.equal(deleteBar(page, 'toutiao'), null, '收起再展开后重新进入管理态时标记应已清空');
        assert.ok(!accountChip(page, 'tt-1').classList.contains('is-marked'));
        await assertNever(() => page.server.requests('delete-account').length > 0, 200);
    } finally {
        page.close();
    }
});

test('N11：启停另一个来源触发整块重渲染后，管理态、待删标记与筛选词全部保留', async () => {
    const accounts = defaultAccounts();
    accounts.toutiao = makeToutiaoAccounts(2);
    const page = await bootPage({ accounts });
    try {
        const panel = await expandSource(page, 'toutiao');
        await waitForChip(page, 'tt-2');
        await enterManageMode(page, 'toutiao');
        accountChip(page, 'tt-1').querySelector('.account-chip-delete').click();
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');
        const filter = panel.querySelector('.accounts-filter');
        inputValue(page, filter, 'tt');

        // 停用另一个来源并等待保存成功，面板整体重渲染
        sourceRow(page, 'chinanews').querySelector('.source-enabled-toggle').click();
        await waitFor(() => page.server.requests('save-sources').length === 1
            && page.server.requests('save-sources')[0].done);
        // 等重渲染完成：展开区被销毁并重建（筛选框是新的 DOM 节点）
        await waitFor(() => {
            const rebuilt = accountsPanel(page, 'toutiao');
            return rebuilt && rebuilt.querySelector('.accounts-filter') !== filter;
        });
        await waitForChip(page, 'tt-1');

        const rebuiltPanel = accountsPanel(page, 'toutiao');
        assert.equal(rebuiltPanel.querySelector('.accounts-filter').value, 'tt');
        assert.equal(manageButton(page, 'toutiao').getAttribute('aria-pressed'), 'true');
        assert.equal(deleteBarCount(page, 'toutiao'), '将删除 1 个账号');
        assert.ok(accountChip(page, 'tt-1').classList.contains('is-marked'));
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

        // 启用数为 0：保留跳过警告与点击展开行为
        const tencentBadge = page.document
            .querySelector('#sources-list li[data-source="tencent"] .source-account-badge');
        assert.equal(tencentBadge.textContent, '启用 0 / 共 0 · 本轮会跳过该来源');
        assert.ok(tencentBadge.classList.contains('is-warning'));
    } finally {
        page.close();
    }

    // 账号概览加载失败：徽标显示未知态，不进入警告态
    const failing = await bootPage({ failNext: { 'list-accounts': 4 } });
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
        // 管理态下名称同样按纯文本渲染
        await enterManageMode(page, 'toutiao');
        const managedChip = accountChip(page, 'acc-xss-chip');
        assert.equal(managedChip.querySelector('.account-chip-name').textContent, payload);
        assert.equal(managedChip.querySelectorAll('img').length, 0);
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

test('N15：两个面板同时打开时，筛选词与管理态按来源隔离、互不影响', async () => {
    const accounts = defaultAccounts();
    accounts.tencent = [makeAccount({
        id: 'acc-tencent-1',
        source: 'tencent',
        normalized_identifier: 'tencent-one',
        enabled: true,
    })];
    const page = await bootPage({ accounts });
    try {
        const toutiaoPanel = await expandSource(page, 'toutiao');
        const tencentPanel = await expandSource(page, 'tencent');
        await waitForChip(page, 'acc-toutiao-1');
        await waitForChip(page, 'acc-tencent-1');

        // 在 tencent 面板进入管理态：toutiao 面板的管理按钮与芯片不受影响
        await enterManageMode(page, 'tencent');
        assert.equal(
            manageButton(page, 'toutiao').getAttribute('aria-pressed'),
            'false',
            'toutiao 面板不应随 tencent 进入管理态',
        );
        const toutiaoChip = accountChip(page, 'acc-toutiao-1');
        assert.equal(toutiaoChip.querySelector('.account-chip-delete'), null);
        assert.equal(toutiaoChip.querySelector('.account-enabled-toggle').disabled, false);
        // 退出 tencent 管理态，避免干扰后面的筛选手段
        manageButton(page, 'tencent').click();
        await waitFor(() => tencentPanel.querySelector('.account-chip-delete') === null);

        // 在 tencent 面板输入筛选词：tencent 列表被过滤，toutiao 列表原样保留
        inputValue(page, tencentPanel.querySelector('.accounts-filter'), '不存在的名字');
        await waitFor(() => accountChip(page, 'acc-tencent-1') === null);
        assert.match(
            tencentPanel.querySelector('.accounts-chip-grid').textContent,
            /没有匹配的账号/,
        );
        assert.ok(
            accountChip(page, 'acc-toutiao-1'),
            'toutiao 面板列表不应被 tencent 的筛选词影响',
        );
        assert.equal(toutiaoPanel.querySelector('.accounts-filter').value, '');
    } finally {
        page.close();
    }
});
