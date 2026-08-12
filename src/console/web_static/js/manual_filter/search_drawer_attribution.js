// Manual Filter JS - Search Drawer · 归因（A 块）
// 只做后端 attribution 枚举到中文文案的映射与呈现，判据全部在后端，前端不重算。
// 由 _search_drawer.html 引入，运行期依赖 utils.js 的 createEl/clearEl。

const ATTRIBUTION_CHAIN_STEPS = [
    { key: 'keyword', label: '初筛' },
    { key: 'relevance', label: '相关性' },
    { key: 'importance', label: '重要性' },
    { key: 'review', label: '人工复核' },
    { key: 'export', label: '导出' }
];

// level → 中文文案 + 在链上停下的节点下标。
// not_reviewed 与 discarded 都落在「人工复核」节点：一个未报出，一个被放弃。
const ATTRIBUTION_LEVELS = {
    keyword_missed: { label: '初筛未命中', stepIndex: 0 },
    relevance_below: { label: '相关性未达标', stepIndex: 1 },
    importance_below: { label: '重要性未达标', stepIndex: 2 },
    not_reviewed: { label: '尚未报出', stepIndex: 3 },
    discarded: { label: '人工放弃', stepIndex: 3 },
    exported: { label: '已导出', stepIndex: 4 }
};

const MANUAL_WORKSPACE_LABELS = {
    admin: '管理员工作区',
    duty: '值班工作区'
};

// 后端时间字段为 ISO 字符串，直接截取展示，避免时区换算引入偏差。
function formatSearchDateTime(value) {
    if (!value) return '-';
    const text = String(value);
    return text.length >= 16 ? text.substring(0, 16).replace('T', ' ') : text.substring(0, 10);
}

function formatSearchDate(value) {
    if (!value) return '';
    return String(value).substring(0, 10);
}

function formatSearchWindowStart(value) {
    const dateText = formatSearchDate(value);
    if (!dateText) return '';
    const parts = dateText.split('-').map(Number);
    const [year, month, day] = parts;
    if (!year || !month || !day) return dateText;
    if (year === new Date().getFullYear()) return `${month} 月 ${day} 日`;
    return `${year} 年 ${month} 月 ${day} 日`;
}

// 有结果时也要可见的检索窗口信息（不必显眼）。
function renderSearchWindowInfo(data) {
    const days = data.lookback_days || '';
    const startText = formatSearchWindowStart(data.window_start);
    const text = startText
        ? `检索范围：最近 ${days} 天（自 ${startText} 起）`
        : `检索范围：最近 ${days} 天`;
    return createEl('span', 'search-window-info', text, {
        dataset: {
            lookbackDays: String(days),
            windowStart: formatSearchDate(data.window_start)
        }
    });
}

// 空结果就是结论：写明实际生效的检索窗口，并提供扩大范围重搜的入口。
function renderSearchEmptyState(data) {
    const days = data.lookback_days || SEARCH_DEFAULT_LOOKBACK_DAYS;
    const startText = formatSearchWindowStart(data.window_start);
    const rangeText = startText ? `最近 ${days} 天（自 ${startText} 起）` : `最近 ${days} 天`;
    const box = createEl('div', 'search-empty', '', {
        dataset: {
            lookbackDays: String(days),
            windowStart: formatSearchDate(data.window_start)
        }
    });
    box.appendChild(createEl(
        'p',
        'search-empty-conclusion',
        `检索范围为${rangeText}，该范围内系统未抓取到相关文章。`
    ));
    if (days < SEARCH_MAX_LOOKBACK_DAYS) {
        const next = Math.min(days * 2, SEARCH_MAX_LOOKBACK_DAYS);
        const expandBtn = createEl(
            'button',
            'btn btn-secondary btn-sm',
            `扩大时间范围重搜（最近 ${next} 天）`,
            {
                type: 'button',
                dataset: { action: 'expand-lookback', nextLookbackDays: String(next) }
            }
        );
        expandBtn.addEventListener('click', () => {
            if (typeof expandSearchWindow === 'function') expandSearchWindow();
        });
        box.appendChild(expandBtn);
    } else {
        box.appendChild(createEl('p', 'search-empty-hint', '已使用最大检索范围。'));
    }
    return box;
}

// 链上位置：整条链五个节点，高亮停下的那一级，之前的节点视为已通过。
function renderAttributionChain(attribution) {
    const level = attribution && attribution.level ? attribution.level : '';
    const config = ATTRIBUTION_LEVELS[level] || null;
    const chain = createEl('div', `attribution-chain${level ? ` level-${level}` : ''}`, '', {
        dataset: { attributionLevel: level }
    });
    chain.appendChild(createEl(
        'div',
        'attribution-chain-status',
        config ? config.label : '级别未知',
        { dataset: { level } }
    ));
    const currentIndex = config ? config.stepIndex : -1;
    const steps = createEl('div', 'attribution-chain-steps');
    ATTRIBUTION_CHAIN_STEPS.forEach((step, index) => {
        const stepState = index < currentIndex ? 'passed' : (index === currentIndex ? 'current' : 'pending');
        steps.appendChild(createEl('div', `attribution-step is-${stepState}`, [
            createEl('span', 'attribution-step-dot'),
            createEl('span', 'attribution-step-label', step.label)
        ], {
            dataset: { step: step.key, stepState }
        }));
    });
    chain.appendChild(steps);
    return chain;
}

function buildAttributionScoreRow(label, score, field) {
    const row = createEl('div', 'search-attribution-row', '', { dataset: { detailField: field } });
    row.appendChild(createEl('span', 'search-attribution-label', label));
    // null（无此值）与 0（分数确实是零）必须区分，不能用假值判断。
    if (score === null || score === undefined) {
        row.appendChild(createEl('span', 'search-attribution-null', '无此项', {
            dataset: { field, isNull: 'true' }
        }));
    } else {
        row.appendChild(createEl('span', 'search-attribution-value', String(score), {
            dataset: { field, isNull: 'false' }
        }));
    }
    return row;
}

// 展开后的归因详情：分数、人工决定（可能多条，全列）、导出批次、fallback 平实标注。
function renderAttributionDetails(attribution) {
    const box = createEl('div', 'search-attribution', '', {
        hidden: true,
        dataset: { attributionDetails: 'true' }
    });
    if (!attribution) {
        box.appendChild(createEl('div', 'search-attribution-row', '无归因数据。'));
        return box;
    }

    box.appendChild(buildAttributionScoreRow('相关性分', attribution.relevance_score, 'relevance_score'));
    box.appendChild(buildAttributionScoreRow('重要性分', attribution.importance_score, 'importance_score'));

    const decisions = Array.isArray(attribution.manual_decisions) ? attribution.manual_decisions : [];
    const decisionRow = createEl('div', 'search-attribution-row', '', {
        dataset: { detailField: 'manual_decisions' }
    });
    decisionRow.appendChild(createEl('span', 'search-attribution-label', '人工决定'));
    if (!decisions.length) {
        decisionRow.appendChild(createEl('span', 'search-attribution-null', '无'));
    } else {
        const list = createEl('ul', 'search-attribution-decisions');
        decisions.forEach(decision => {
            const workspace = MANUAL_WORKSPACE_LABELS[decision.workspace] || decision.workspace || '未知工作区';
            const actor = decision.actor || '未知操作人';
            const time = formatSearchDateTime(decision.decided_at);
            list.appendChild(createEl(
                'li',
                'search-attribution-decision',
                `${workspace} · ${actor} · ${time} · ${decision.decision || '-'}`,
                { dataset: { workspace: decision.workspace || '' } }
            ));
        });
        decisionRow.appendChild(list);
    }
    box.appendChild(decisionRow);

    const batches = Array.isArray(attribution.export_batch_dates) ? attribution.export_batch_dates : [];
    const batchRow = createEl('div', 'search-attribution-row', '', {
        dataset: { detailField: 'export_batch_dates' }
    });
    batchRow.appendChild(createEl('span', 'search-attribution-label', '导出批次'));
    if (!batches.length) {
        batchRow.appendChild(createEl('span', 'search-attribution-null', '无'));
    } else {
        const chips = createEl('span', 'search-attribution-batches');
        batches.forEach(batchDate => {
            chips.appendChild(createEl('span', 'search-attribution-batch', formatSearchDate(batchDate), {
                dataset: { exportBatch: formatSearchDate(batchDate) }
            }));
        });
        batchRow.appendChild(chips);
    }
    box.appendChild(batchRow);

    // is_fallback 极少出现：只在详情里平实标注一行，不做醒目提示。
    if (attribution.is_fallback) {
        box.appendChild(createEl(
            'div',
            'search-attribution-fallback',
            '注：人工记录与导出记录不一致，级别按「尚未报出」展示。',
            { dataset: { isFallback: 'true' } }
        ));
    }
    return box;
}
