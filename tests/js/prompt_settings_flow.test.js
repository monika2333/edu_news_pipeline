// 系统设置页 · 提示词页签的 jsdom 行为测试。
// 覆盖：列表与编辑器渲染、草稿切换保留、保存追加版本、409 保留草稿、
// 历史查看/恢复、文件来源首次保存补 baseline、加载失败提示。
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { bootPage, waitFor, unhandledRejections } = require('./settings_harness.js');

function editor(page) {
    return page.document.getElementById('prompt-editor');
}

function setEditor(page, value) {
    const el = editor(page);
    el.value = value;
    el.dispatchEvent(new page.window.Event('input', { bubbles: true }));
}

function saveButton(page) {
    return page.panel('prompts').querySelector('.prompt-actions .btn-primary');
}

function navButtons(page) {
    return [...page.panel('prompts').querySelectorAll('.prompt-nav-item')];
}

function statusText(page) {
    const el = page.document.getElementById('prompt-save-status');
    return el ? el.textContent : '';
}

async function openPrompts(page) {
    page.clickTab('prompts');
    await waitFor(() => page.panel('prompts').querySelector('.prompt-nav-item'));
}

test('P1 提示词列表渲染五个提示词并默认选中第一个', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        const buttons = navButtons(page);
        assert.equal(buttons.length, 5);
        assert.equal(buttons[0].classList.contains('is-active'), true);
        assert.match(buttons[0].textContent, /京外正面重要性/);
        assert.equal(editor(page).value, 'EXTERNAL POSITIVE PROMPT v2');
        assert.match(
            page.panel('prompts').querySelector('.prompt-head-meta').textContent,
            /v2/,
        );
        assert.match(
            page.panel('prompts').querySelector('.prompt-head-meta').textContent,
            /数据库/,
        );
    } finally {
        page.close();
    }
});

test('P2 切换提示词保留未保存草稿并标记未保存', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        setEditor(page, 'DRAFT CONTENT');
        assert.equal(page.window.hasUnsavedChanges(), true);
        assert.equal(navButtons(page)[0].classList.contains('is-dirty'), true);

        navButtons(page)[1].click();
        await waitFor(() => editor(page).value === 'EXTERNAL NEGATIVE PROMPT v1');
        assert.equal(page.window.hasUnsavedChanges(), true);

        navButtons(page)[0].click();
        await waitFor(() => editor(page).value === 'DRAFT CONTENT');
        assert.equal(
            page.panel('prompts').querySelector('.prompt-nav-item .prompt-nav-dirty').hidden,
            false,
        );
    } finally {
        page.close();
    }
});

test('P3 保存追加版本并带上修改说明', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        setEditor(page, 'EXTERNAL POSITIVE PROMPT v3');
        page.document.getElementById('prompt-note-input').value = '收紧档位';
        saveButton(page).click();
        await waitFor(
            () => editor(page).value === 'EXTERNAL POSITIVE PROMPT v3'
                && page.server.requests('save-prompt').length === 1
                && page.server.inflight === 0,
        );
        const request = page.server.requests('save-prompt')[0];
        assert.equal(request.body.expected_revision_id, 12);
        assert.equal(request.body.note, '收紧档位');
        assert.equal(page.server.prompts[0].version, 'v3');
        assert.equal(page.server.prompts[0].source, 'database');
        assert.match(statusText(page), /已保存为 v3/);
        assert.equal(page.window.hasUnsavedChanges(), false);
        await waitFor(() => page.document.querySelector('.prompt-history-item'));
        assert.match(page.panel('prompts').querySelector('.prompt-history-list').textContent, /v3/);
    } finally {
        page.close();
    }
});

test('P4 保存冲突时保留草稿并显示载入最新入口', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        setEditor(page, 'CONFLICT DRAFT');
        // 模拟另一个管理员已经保存：服务端的 revision_id 变了，页面仍拿着旧值。
        page.server.prompts[0].revision_id = 999;
        saveButton(page).click();
        await waitFor(() => /保存冲突/.test(statusText(page)));
        assert.equal(editor(page).value, 'CONFLICT DRAFT');
        const reload = [...page.panel('prompts').querySelectorAll('.prompt-actions button')]
            .find((button) => button.textContent === '载入最新配置');
        assert.ok(reload);
        assert.equal(reload.hidden, false);
    } finally {
        page.close();
    }
});

test('P5 历史版本可查看并可恢复为草稿', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await waitFor(() => page.document.querySelector('.prompt-history-item'));
        const items = [...page.document.querySelectorAll('.prompt-history-item')];
        const v2Item = items.find(
            (item) => item.querySelector('.prompt-version').textContent === 'v2',
        );
        const view = [...v2Item.querySelectorAll('button')]
            .find((button) => button.textContent === '查看');
        view.click();
        await waitFor(() => {
            const preview = page.document.querySelector('.prompt-history-preview');
            return preview && !preview.hidden && preview.textContent.length > 0;
        });
        const preview = page.document.querySelector('.prompt-history-preview');
        assert.equal(preview.textContent, 'EXTERNAL POSITIVE PROMPT v2');

        const v1Item = items.find(
            (item) => item.querySelector('.prompt-version').textContent === 'v1',
        );
        const restore = [...v1Item.querySelectorAll('button')]
            .find((button) => button.textContent === '恢复');
        restore.click();
        await waitFor(() => editor(page).value === 'EXTERNAL POSITIVE v1');
        assert.equal(
            page.document.getElementById('prompt-note-input').value,
            '恢复自 v1',
        );
        assert.equal(page.window.hasUnsavedChanges(), true);
        assert.match(statusText(page), /已载入 v1 的内容/);
    } finally {
        page.close();
    }
});

test('P6 文件来源首次保存先补 baseline 再追加新版本', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        navButtons(page)[1].click();
        await waitFor(() => editor(page).value === 'EXTERNAL NEGATIVE PROMPT v1');
        setEditor(page, 'EXTERNAL NEGATIVE PROMPT v2');
        saveButton(page).click();
        await waitFor(
            () => page.server.prompts[1].version === 'v2'
                && page.server.inflight === 0,
        );
        assert.equal(page.server.prompts[1].source, 'database');
        const history = page.server.promptHistory.external_negative;
        assert.equal(history.length, 2);
        assert.equal(history[0].version, 'v1');
        assert.equal(history[0].note, '初始文件版本');
        assert.equal(history[1].version, 'v2');
    } finally {
        page.close();
    }
});

test('P7 提示词加载失败时显示重试入口', async () => {
    const page = await bootPage({ failNext: { 'get-prompts': 1 } });
    try {
        page.clickTab('prompts');
        await waitFor(() => /提示词加载失败/.test(page.panel('prompts').textContent));
        const retry = [...page.panel('prompts').querySelectorAll('button')]
            .find((button) => button.textContent === '重新加载');
        assert.ok(retry);
        retry.click();
        await waitFor(() => page.panel('prompts').querySelector('.prompt-nav-item'));
    } finally {
        page.close();
    }
});

test('P8 每个评分提示词显示最近一次自动优化状态', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        const statuses = [...page.panel('prompts').querySelectorAll('.prompt-auto-status')]
            .map((el) => el.textContent);
        assert.equal(statuses.length, 4);
        assert.match(statuses[0], /生成 v2/);
        assert.match(statuses[1], /未改动：无需修正/);
        assert.match(statuses[2], /未改动：模型判断不改/);
        assert.match(statuses[3], /未通过检查：修好 1 条、改坏 3 条/);

        page.window.eval(`
            state.prompts.payload.items[3].optimization_status = {
                outcome: 'error',
                outcome_label: '出错',
                finished_at: '2026-10-30T01:00:00Z',
                error: '模型调用超时',
            };
            renderPromptsTab();
        `);
        await waitFor(() => /出错：模型调用超时/
            .test(page.panel('prompts').textContent));
    } finally {
        page.close();
    }
});

test('P9 自动版本显示「自动优化」，初始文件版本仍显示「系统导入」', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await waitFor(() => page.document.querySelector('.prompt-history-item'));
        const items = [...page.document.querySelectorAll('.prompt-history-item')];
        const v2 = items.find(
            (item) => item.querySelector('.prompt-version').textContent === 'v2',
        );
        const v1 = items.find(
            (item) => item.querySelector('.prompt-version').textContent === 'v1',
        );
        assert.match(v2.querySelector('.prompt-history-user').textContent, /自动优化/);
        assert.match(v1.querySelector('.prompt-history-user').textContent, /系统导入/);
        assert.doesNotMatch(v2.textContent, /系统导入/);
    } finally {
        page.close();
    }
});

test('P10 自动版本详情展示改动、检查结论与案例表', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await waitFor(() => page.document.querySelector('.prompt-history-item'));
        const items = [...page.document.querySelectorAll('.prompt-history-item')];
        const v2 = items.find(
            (item) => item.querySelector('.prompt-version').textContent === 'v2',
        );
        const view = [...v2.querySelectorAll('button')]
            .find((button) => button.textContent === '查看');
        view.click();
        await waitFor(() => {
            const detail = page.document.querySelector('.prompt-optimization-detail');
            return detail && !detail.hidden && detail.textContent.includes('某高校舆情事件');
        });
        const detail = page.document.querySelector('.prompt-optimization-detail');
        assert.match(detail.textContent, /若涉及高校/);
        assert.match(detail.textContent, /且出现舆情风险/);
        assert.match(detail.textContent, /通过/);
        // 这份夹具是没有确认回放字段的历史运行记录：结论行不带计权/波动、
        // 分数栏保持单次形式，且不能出现 undefined。
        assert.match(detail.textContent, /T 修好 2\/3 条 · R 改坏 1\/4 条/);
        assert.doesNotMatch(detail.textContent, /计权/);
        assert.doesNotMatch(detail.textContent, /波动/);
        assert.doesNotMatch(detail.textContent, /undefined/);
        const rows = [...detail.querySelectorAll('.prompt-optimization-cases tbody tr')];
        assert.equal(rows.length, 2);
        assert.match(rows[0].textContent, /某高校舆情事件/);
        assert.match(rows[0].textContent, /报送存档（zongbao）/);
        assert.match(rows[0].textContent, /调高/);
        assert.match(rows[0].textContent, /45 → 40 → 62/);
        assert.match(rows[0].textContent, /达标/);
        assert.match(rows[0].textContent, /已报送/);
    } finally {
        page.close();
    }
});

test('P11 确认回放分数显示两次形式，「波动」正确展示', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await waitFor(() => page.document.querySelector('.prompt-history-item'));
        // 注入带确认回放字段的运行记录（模拟新版本写出的 cases/check）。
        const optimization = page.server.promptHistory.external_positive[1].optimization;
        optimization.attempts[0].check = {
            passed: true,
            fixed_count: 1,
            broken_count: 1,
            weighted_fixed_score: 2,
            fluctuation_count: 2,
            t_total: 2,
            r_total: 4,
            unparseable_count: 0,
            failures: [],
        };
        optimization.cases = [
            {
                article_id: 'a1',
                title: '波动案例',
                origin_label: '评分标注·偏低',
                direction_label: '调高',
                original_score: 58,
                current_score: 58,
                current_confirm_score: 62,
                candidate_score: 66,
                candidate_confirm_score: 64,
                candidate_satisfied: true,
                fix_state: 'fluctuation',
                bucket: 'T',
                reason: '应该高分',
            },
            {
                article_id: 'a2',
                title: '修好案例',
                origin_label: '报送存档（zongbao）',
                direction_label: '调高',
                original_score: 45,
                current_score: 40,
                candidate_score: 62,
                candidate_confirm_score: 62,
                candidate_satisfied: true,
                fix_state: 'fixed',
                bucket: 'T',
                reason: '已报送',
            },
        ];
        const items = [...page.document.querySelectorAll('.prompt-history-item')];
        const v2 = items.find(
            (item) => item.querySelector('.prompt-version').textContent === 'v2',
        );
        const view = [...v2.querySelectorAll('button')]
            .find((button) => button.textContent === '查看');
        view.click();
        await waitFor(() => {
            const detail = page.document.querySelector('.prompt-optimization-detail');
            return detail && !detail.hidden && detail.textContent.includes('波动案例');
        });
        const detail = page.document.querySelector('.prompt-optimization-detail');
        assert.match(
            detail.textContent,
            /T 修好 1\/2 条（计权 2 分） · R 改坏 1\/4 条 · 波动 2 条/,
        );
        const rows = [...detail.querySelectorAll('.prompt-optimization-cases tbody tr')];
        assert.equal(rows.length, 2);
        // 有确认分数时显示成两次的形式；「是否达标」栏显示波动。
        assert.match(rows[0].textContent, /58 → 58\/62 → 66\/64/);
        assert.match(rows[0].textContent, /波动/);
        assert.doesNotMatch(rows[0].textContent, /undefined/);
        // 「是否达标」是第 5 列：fluctuation 案例整格恰为「波动」，fixed 案例仍为「达标」。
        assert.equal([...rows[0].querySelectorAll('td')][4].textContent, '波动');
        assert.equal([...rows[1].querySelectorAll('td')][4].textContent, '达标');
        // 修好案例仍显示达标；只有候选有第二次时只合并候选一段。
        assert.match(rows[1].textContent, /45 → 40 → 62\/62/);
        assert.match(rows[1].textContent, /达标/);
        assert.doesNotMatch(detail.textContent, /undefined/);
    } finally {
        page.close();
    }
});

test.after(() => {
    assert.deepEqual(unhandledRejections, []);
});
