// 系统设置页 · 提示词页签的 jsdom 行为测试。
// 覆盖：列表与编辑器渲染、草稿切换保留、保存追加版本、409 保留草稿、
// 历史版本就地展开/手风琴、自动优化详情与案例分组懒渲染、恢复、文件来源
// 首次保存补 baseline、加载失败提示。
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

function historyItems(page) {
    return [...page.document.querySelectorAll('.prompt-history-item')];
}

function historyItemByVersion(page, version) {
    return historyItems(page).find(
        (item) => item.querySelector('.prompt-version').textContent === version,
    );
}

function versionToggle(item) {
    return item.querySelector('.prompt-history-toggle');
}

function versionLabel(item) {
    return versionToggle(item)
        .querySelector('.prompt-history-toggle-label').textContent;
}

function historyPanel(item) {
    return item.querySelector('.prompt-history-panel');
}

function revisionPreview(item) {
    return item.querySelector('.prompt-history-preview');
}

function optimizationToggle(item) {
    return item.querySelector('.prompt-optimization-toggle');
}

function optimizationDetail(item) {
    return item.querySelector('.prompt-optimization-detail');
}

function caseGroup(detail, label) {
    return [...detail.querySelectorAll('.prompt-optimization-case-group')]
        .find((group) => group
            .querySelector('.prompt-optimization-case-group-head')
            .textContent.includes(label));
}

function caseRows(root) {
    return [...root.querySelectorAll('.prompt-optimization-case')];
}

async function waitHistory(page) {
    await waitFor(() => page.document.querySelector('.prompt-history-item'));
}

async function expandVersion(item) {
    versionToggle(item).click();
    await waitFor(() => {
        const panel = historyPanel(item);
        return panel && !panel.hidden && revisionPreview(item);
    });
}

async function expandOptimization(item) {
    optimizationToggle(item).click();
    await waitFor(() => {
        const detail = optimizationDetail(item);
        return detail && !detail.hidden;
    });
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

test('P5 历史版本可就地展开正文并可恢复为草稿', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await waitHistory(page);
        const v2Item = historyItemByVersion(page, 'v2');
        await expandVersion(v2Item);
        assert.equal(revisionPreview(v2Item).textContent, 'EXTERNAL POSITIVE PROMPT v2');

        const v1Item = historyItemByVersion(page, 'v1');
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
        await waitHistory(page);
        const items = historyItems(page);
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

test('P10 自动版本详情展示改动、检查结论与案例（历史记录缺字段容错）', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await waitHistory(page);
        const v2 = historyItemByVersion(page, 'v2');
        await expandVersion(v2);
        // 「本次改动」（替换前/替换后）跟着第一次展开直接显示。
        const panel = historyPanel(v2);
        assert.match(panel.textContent, /本次改动/);
        assert.match(panel.textContent, /替换前/);
        assert.match(panel.textContent, /替换后/);
        assert.match(panel.textContent, /若涉及高校/);
        assert.match(panel.textContent, /且出现舆情风险/);
        const edits = panel.querySelector('.prompt-optimization-edits');
        assert.ok(edits);
        // 检查结论与案例默认收起，必须再点二级开关。
        assert.equal(optimizationDetail(v2).hidden, true);
        assert.equal(optimizationDetail(v2).contains(edits), false);
        await expandOptimization(v2);
        const detail = optimizationDetail(v2);
        assert.match(detail.textContent, /通过/);
        // 这份夹具是没有确认回放字段的历史运行记录：结论行不带计权/波动、
        // 分数栏保持单次形式，且不能出现 undefined。
        assert.match(detail.textContent, /T 修好 2\/3 条 · R 改坏 1\/4 条/);
        assert.doesNotMatch(detail.textContent, /计权/);
        assert.doesNotMatch(detail.textContent, /波动/);
        assert.doesNotMatch(panel.textContent, /undefined/);
        // 默认只渲染第一组（已修好）的案例行。
        const rows = caseRows(detail);
        assert.equal(rows.length, 1);
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
        await waitHistory(page);
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
        const v2 = historyItemByVersion(page, 'v2');
        await expandVersion(v2);
        await expandOptimization(v2);
        const detail = optimizationDetail(v2);
        assert.match(
            detail.textContent,
            /T 修好 1\/2 条（计权 2 分） · R 改坏 1\/4 条 · 波动 2 条/,
        );
        // 默认展开「已修好」组；「波动」组要等点开组头后才创建行。
        const fixedGroup = caseGroup(detail, '已修好');
        assert.ok(fixedGroup);
        const fixedRows = caseRows(fixedGroup);
        assert.equal(fixedRows.length, 1);
        // 只有候选有第二次时只合并候选一段。
        assert.match(fixedRows[0].textContent, /45 → 40 → 62\/62/);
        assert.match(fixedRows[0].textContent, /达标/);
        assert.equal(caseRows(detail).length, 1);

        const fluctuationHead = caseGroup(detail, '波动')
            .querySelector('.prompt-optimization-case-group-head');
        fluctuationHead.click();
        await waitFor(() => caseRows(detail).length === 2);
        const fluctuationRow = caseRows(detail)
            .find((row) => /波动案例/.test(row.textContent));
        // 有确认分数时显示成两次的形式；状态栏显示波动。
        assert.match(fluctuationRow.textContent, /58 → 58\/62 → 66\/64/);
        assert.match(fluctuationRow.textContent, /波动/);
        assert.doesNotMatch(fluctuationRow.textContent, /undefined/);
        assert.doesNotMatch(detail.textContent, /undefined/);
    } finally {
        page.close();
    }
});

test('A1 版本内容在对应版本行内部就地展开', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await waitHistory(page);
        const v2 = historyItemByVersion(page, 'v2');
        // 点击后先看到面板内的「加载中…」，内容到达后替换。
        versionToggle(v2).click();
        assert.match(historyPanel(v2).textContent, /加载中/);
        await waitFor(() => revisionPreview(v2) && !historyPanel(v2).hidden);
        const preview = revisionPreview(v2);
        assert.equal(preview.textContent, 'EXTERNAL POSITIVE PROMPT v2');
        assert.equal(preview.closest('.prompt-history-item'), v2);
        const v1 = historyItemByVersion(page, 'v1');
        // 展开区在 DOM 顺序上位于 v1 行之前，不再挂到列表末尾。
        assert.ok(preview.compareDocumentPosition(v1) & 4);
        assert.equal(v1.contains(preview), false);
    } finally {
        page.close();
    }
});

test('A2 手风琴互斥：展开新版本自动收起旧版本并复位按钮', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await waitHistory(page);
        const v2 = historyItemByVersion(page, 'v2');
        const v1 = historyItemByVersion(page, 'v1');
        await expandVersion(v2);
        await expandVersion(v1);
        assert.equal(historyPanel(v2).hidden, true);
        assert.equal(versionLabel(v2), '展开');
        assert.equal(versionToggle(v2).getAttribute('aria-expanded'), 'false');
        assert.equal(versionLabel(v1), '收起');
        assert.equal(versionToggle(v1).getAttribute('aria-expanded'), 'true');
        assert.equal(historyPanel(v1).hidden, false);
    } finally {
        page.close();
    }
});

test('A3 展开按钮带小三角，展开时按钮带 is-open', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await waitHistory(page);
        const v2 = historyItemByVersion(page, 'v2');
        const toggle = versionToggle(v2);
        const caret = toggle.querySelector('.prompt-history-caret');
        assert.ok(caret);
        assert.equal(caret.getAttribute('aria-hidden'), 'true');
        assert.equal(toggle.classList.contains('is-open'), false);
        await expandVersion(v2);
        assert.equal(toggle.classList.contains('is-open'), true);
        assert.equal(versionLabel(v2), '收起');
        versionToggle(v2).click();
        assert.equal(toggle.classList.contains('is-open'), false);
        assert.equal(versionLabel(v2), '展开');
    } finally {
        page.close();
    }
});

test('A4 检查结论与案例默认收起，二级开关显示结论摘要', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await waitHistory(page);
        const v2 = historyItemByVersion(page, 'v2');
        await expandVersion(v2);
        const detail = optimizationDetail(v2);
        assert.ok(detail);
        assert.equal(detail.hidden, true);
        const toggle = optimizationToggle(v2);
        assert.ok(toggle);
        assert.match(toggle.textContent, /检查结论与案例/);
        assert.match(toggle.textContent, /通过/);
        assert.match(toggle.textContent, /修好 2\/3 条/);
        assert.match(toggle.textContent, /改坏 1\/4 条/);
        assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    } finally {
        page.close();
    }
});

test('A12 第一次展开就能看到本次改动，不需要再点二级开关', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await waitHistory(page);
        const v2 = historyItemByVersion(page, 'v2');
        await expandVersion(v2);
        // 只点了一次版本展开：二级详情仍然收起，但替换前/替换后已经可见。
        assert.equal(optimizationDetail(v2).hidden, true);
        const panel = historyPanel(v2);
        const edits = panel.querySelector('.prompt-optimization-edits');
        assert.ok(edits);
        assert.equal(edits.closest('.prompt-optimization-detail'), null);
        assert.match(edits.textContent, /替换前/);
        assert.match(edits.textContent, /替换后/);
        assert.match(edits.textContent, /若涉及高校/);
        assert.match(edits.textContent, /且出现舆情风险/);
        // 运行头部（结论/时间/模型）也在常显区，不在收起的详情里。
        assert.ok(panel.querySelector('.prompt-optimization-run-head'));
    } finally {
        page.close();
    }
});

test('A5 展开后依次是本次改动、检查结论、案例', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await waitHistory(page);
        const v2 = historyItemByVersion(page, 'v2');
        await expandVersion(v2);
        await expandOptimization(v2);
        // 「本次改动」在常显区，检查结论/案例在二级详情里，三者顺序仍固定。
        const subtitles = [...v2.querySelectorAll('.prompt-optimization-subtitle')]
            .map((el) => el.textContent);
        assert.deepEqual(subtitles, ['本次改动', '检查结论', '案例']);
    } finally {
        page.close();
    }
});

test('A6 案例按结论分组，默认只渲染第一组，点组头才创建其余行', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await waitHistory(page);
        const v2 = historyItemByVersion(page, 'v2');
        await expandVersion(v2);
        await expandOptimization(v2);
        const detail = optimizationDetail(v2);
        const groupHeads = [...detail.querySelectorAll('.prompt-optimization-case-group-head')]
            .map((head) => head.textContent);
        assert.equal(groupHeads.length, 2);
        assert.match(groupHeads[0], /已修好 1 条/);
        assert.match(groupHeads[1], /未变化 1 条/);
        // 默认只把第一个非空组（已修好）的行建进 DOM。
        let rows = caseRows(detail);
        assert.equal(rows.length, 1);
        assert.match(rows[0].textContent, /某高校舆情事件/);
        // 其余组只有组头，行在点开组头时才出现。
        caseGroup(detail, '未变化')
            .querySelector('.prompt-optimization-case-group-head').click();
        await waitFor(() => caseRows(detail).length === 2);
        rows = caseRows(detail);
        assert.match(rows[1].textContent, /参照案例/);
        // R 案例本来就达标、只是没被改坏：状态不能写成「未达标」。
        assert.match(rows[1].textContent, /达标/);
        assert.doesNotMatch(rows[1].textContent, /未达标/);
    } finally {
        page.close();
    }
});

test('A7 历史记录缺 fix_state 时按 bucket 与候选判定回退分组', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        // P10 夹具的案例是没有 fix_state / 确认回放字段的历史运行记录。
        const optimization = page.server.promptHistory.external_positive[1].optimization;
        assert.equal(optimization.cases[0].fix_state, undefined);
        optimization.cases.push({
            article_id: 'a3',
            title: 'T 未达标案例',
            origin_label: '评分标注·偏低',
            direction_label: '调高',
            original_score: 30,
            current_score: 35,
            candidate_score: 36,
            candidate_satisfied: false,
            bucket: 'T',
            reason: '仍未达标',
        });
        await waitHistory(page);
        const v2 = historyItemByVersion(page, 'v2');
        await expandVersion(v2);
        await expandOptimization(v2);
        const detail = optimizationDetail(v2);
        // T 且 candidate_satisfied: true → 已修好；R 且 candidate_satisfied: true
        // → 未变化；T 且 candidate_satisfied: false → 未变化（不能被误判成已修好）。
        const fixedGroup = caseGroup(detail, '已修好');
        assert.equal(caseRows(fixedGroup).length, 1);
        assert.match(caseRows(fixedGroup)[0].textContent, /某高校舆情事件/);
        assert.doesNotMatch(fixedGroup.textContent, /T 未达标案例/);

        const unchangedGroup = caseGroup(detail, '未变化');
        assert.match(
            unchangedGroup.querySelector('.prompt-optimization-case-group-head').textContent,
            /未变化 2 条/,
        );
        unchangedGroup.querySelector('.prompt-optimization-case-group-head').click();
        await waitFor(() => caseRows(unchangedGroup).length === 2);
        const unchangedTexts = caseRows(unchangedGroup).map((row) => row.textContent);
        const referenceRow = unchangedTexts.find((text) => /参照案例/.test(text));
        const notFixedRow = unchangedTexts.find((text) => /T 未达标案例/.test(text));
        assert.ok(referenceRow);
        assert.ok(notFixedRow);
        // R 本来就达标、只是没被改坏：显示「达标」而不是「未达标」。
        assert.match(referenceRow, /达标/);
        assert.doesNotMatch(referenceRow, /未达标/);
        // T 没修好仍是「未达标」。
        assert.match(notFixedRow, /未达标/);
    } finally {
        page.close();
    }
});

test('A8 手工保存版本只渲染正文预览，不渲染自动优化开关', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        navButtons(page)[2].click();
        await waitFor(() => editor(page).value === 'INTERNAL POSITIVE PROMPT v2');
        await waitHistory(page);
        const v2 = historyItemByVersion(page, 'v2');
        await expandVersion(v2);
        assert.equal(optimizationToggle(v2), null);
        assert.equal(revisionPreview(v2).textContent, 'INTERNAL POSITIVE PROMPT v2');
    } finally {
        page.close();
    }
});

test('A9 恢复载入内容后重绘、不残留展开态也不报错', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await waitHistory(page);
        const v2 = historyItemByVersion(page, 'v2');
        await expandVersion(v2);
        assert.equal(versionToggle(v2).classList.contains('is-open'), true);

        const v1 = historyItemByVersion(page, 'v1');
        const restore = [...v1.querySelectorAll('button')]
            .find((button) => button.textContent === '恢复');
        restore.click();
        await waitFor(() => editor(page).value === 'EXTERNAL POSITIVE v1');
        assert.equal(page.document.getElementById('prompt-note-input').value, '恢复自 v1');
        assert.match(statusText(page), /已载入 v1 的内容/);
        assert.equal(page.document.getElementById('settings-alert').hidden, true);
        // 重绘后回到全部收起（展开态不跨重绘保持）。
        await waitHistory(page);
        assert.equal(page.document.querySelectorAll('.prompt-history-item.is-open').length, 0);
        assert.equal(
            [...page.document.querySelectorAll('.prompt-history-panel')]
                .filter((panel) => !panel.hidden).length,
            0,
        );
    } finally {
        page.close();
    }
});

test('A10 检查失败原因最多直接显示 3 条，其余就地展开', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        const optimization = page.server.promptHistory.external_positive[1].optimization;
        optimization.attempts[0].check.failures = [
            '失败一', '失败二', '失败三', '失败四', '失败五',
        ];
        await waitHistory(page);
        const v2 = historyItemByVersion(page, 'v2');
        await expandVersion(v2);
        await expandOptimization(v2);
        const detail = optimizationDetail(v2);
        assert.equal(detail.querySelectorAll('.prompt-optimization-failure').length, 3);
        const more = detail.querySelector('.prompt-optimization-more-failures');
        assert.ok(more);
        assert.match(more.textContent, /还有 2 条/);
        more.click();
        await waitFor(
            () => detail.querySelectorAll('.prompt-optimization-failure').length === 5,
        );
        const texts = [...detail.querySelectorAll('.prompt-optimization-failure')]
            .map((el) => el.textContent);
        ['失败一', '失败二', '失败三', '失败四', '失败五'].forEach((failure) => {
            assert.ok(texts.includes(failure));
        });
    } finally {
        page.close();
    }
});

test('A11 收起后再展开复用已加载面板，不闪加载态且保留二级展开', async () => {
    const page = await bootPage();
    try {
        await openPrompts(page);
        await waitHistory(page);
        const v2 = historyItemByVersion(page, 'v2');
        await expandVersion(v2);
        await expandOptimization(v2);
        // 收起版本：面板隐藏，但内容与二级展开态保留在 DOM 里。
        versionToggle(v2).click();
        assert.equal(historyPanel(v2).hidden, true);
        // 再展开：同步走已加载快路径，不应重新插入「加载中…」。
        versionToggle(v2).click();
        assert.equal(
            historyPanel(v2).querySelector('.prompt-history-panel-loading'),
            null,
        );
        assert.equal(optimizationDetail(v2).hidden, false);
        assert.equal(revisionPreview(v2).textContent, 'EXTERNAL POSITIVE PROMPT v2');
    } finally {
        page.close();
    }
});

test.after(() => {
    assert.deepEqual(unhandledRejections, []);
});
