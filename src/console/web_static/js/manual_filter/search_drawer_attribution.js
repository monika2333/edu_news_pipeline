// Manual Filter JS - Search Drawer · 归因（A 块）
// 只做后端 attribution 枚举到中文文案的映射与呈现，判据全部在后端，前端不重算。
// 由 _search_drawer.html 引入，运行期依赖 utils.js 的
// createEl/clearEl/formatLocalDateTime/formatScore。

// 链上节点：brief_items 只是自动导出的初稿，不代表筛选结果，不再作为链上节点。
const ATTRIBUTION_CHAIN_STEPS = [
    { key: 'keyword', label: '初筛' },
    { key: 'relevance', label: '相关性' },
    { key: 'importance', label: '重要性' },
    { key: 'review', label: '人工复核' }
];

// level → 中文文案 + 在链上停下的节点下标。
// not_reviewed 与 discarded 都落在「人工复核」节点：一个未报出，一个被放弃。
const ATTRIBUTION_LEVELS = {
    keyword_missed: { label: '初筛未命中', stepIndex: 0 },
    relevance_below: { label: '相关性未达标', stepIndex: 1 },
    importance_below: { label: '重要性未达标', stepIndex: 2 },
    not_reviewed: { label: '尚未报出', stepIndex: 3 },
    discarded: { label: '人工放弃', stepIndex: 3 }
};

const MANUAL_WORKSPACE_LABELS = {
    admin: '管理员工作区',
    duty: '值班工作区'
};

// datetime（ingested_at / decided_at / window_start）一律走 utils.js 的
// formatLocalDateTime（new Date + 本地取值）：后端返回带 Z 的 UTC 时间，
// 截字符串等于把 UTC 当本地时间，凌晨入库的文章日期会差一天，与内容抽屉也对不上。
// formatSearchDate 只用于不带时区的 date 字段（存档 report_date），保持截取。
function formatSearchDate(value) {
    if (!value) return '';
    return String(value).substring(0, 10);
}

// datetime 的本地日期部分（YYYY-MM-DD），用于 data-* 钩子。
function formatSearchLocalDate(value) {
    const formatted = formatLocalDateTime(value);
    return formatted.includes(' ') ? formatted.split(' ')[0] : formatted;
}

// window_start 是 UTC datetime，起始日期必须按本地时区取，否则早班时段会算早一天。
function formatSearchWindowStart(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value).substring(0, 10);
    const year = date.getFullYear();
    const month = date.getMonth() + 1;
    const day = date.getDate();
    if (year === new Date().getFullYear()) return `${month} 月 ${day} 日`;
    return `${year} 年 ${month} 月 ${day} 日`;
}

// 有结果时也要可见的检索窗口信息（不必显眼）。
function renderSearchWindowInfo(data) {
    const days = data.lookback_days || '';
    const startText = formatSearchWindowStart(data.window_start);
    const text = startText
        ? `检索范围：最近 ${days} 天（自 ${startText}起）`
        : `检索范围：最近 ${days} 天`;
    return createEl('span', 'search-window-info', text, {
        dataset: {
            lookbackDays: String(days),
            windowStart: formatSearchLocalDate(data.window_start)
        }
    });
}

// 空结果就是结论：写明实际生效的检索窗口，并提供扩大范围重搜的入口。
function renderSearchEmptyState(data) {
    const days = data.lookback_days || SEARCH_DEFAULT_LOOKBACK_DAYS;
    const startText = formatSearchWindowStart(data.window_start);
    const rangeText = startText ? `最近 ${days} 天（自 ${startText}起）` : `最近 ${days} 天`;
    const box = createEl('div', 'search-empty', '', {
        dataset: {
            lookbackDays: String(days),
            windowStart: formatSearchLocalDate(data.window_start)
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

// 状态徽章：跟在标题链接后面，标明文章目前停在哪一级。
function renderAttributionStatusBadge(attribution) {
    const level = attribution && attribution.level ? attribution.level : '';
    const config = ATTRIBUTION_LEVELS[level] || null;
    return createEl(
        'span',
        `search-status-badge${level ? ` level-${level}` : ''}`,
        config ? config.label : '级别未知',
        { dataset: { level } }
    );
}

// 人工复核节点的小字与悬浮内容：全部人工决定，一行一条。
function formatDecisionTooltip(decisions) {
    return decisions.map(decision => {
        const workspace = MANUAL_WORKSPACE_LABELS[decision.workspace] || decision.workspace || '未知工作区';
        const actor = decision.actor || '未知操作人';
        const time = formatLocalDateTime(decision.decided_at);
        return `${workspace} · ${actor} · ${time} · ${decision.decision || '-'}`;
    }).join('\n');
}

// 节点小字：相关性/重要性标分数（无分数则不标），
// 人工复核标决定条数（悬浮看明细），轮到它却还没有决定时标「无」。
function renderStepExtra(stepKey, stepState, attribution) {
    if (!attribution) return null;
    if (stepKey === 'relevance' || stepKey === 'importance') {
        const score = stepKey === 'relevance'
            ? attribution.relevance_score
            : attribution.importance_score;
        // null（无此值）与 0（分数确实是零）必须区分，不能用假值判断。
        if (score === null || score === undefined) return null;
        const label = stepKey === 'relevance' ? '相关性分' : '重要性分';
        return createEl('span', 'attribution-step-extra', formatScore(score), {
            title: `${label}：${formatScore(score)}`
        });
    }
    if (stepKey === 'review') {
        const decisions = Array.isArray(attribution.manual_decisions)
            ? attribution.manual_decisions
            : [];
        if (decisions.length) {
            return createEl('span', 'attribution-step-extra', `${decisions.length} 条决定`, {
                title: formatDecisionTooltip(decisions)
            });
        }
        if (stepState === 'current') {
            return createEl('span', 'attribution-step-extra', '无');
        }
    }
    return null;
}

// 链上位置：整条链四个节点，高亮停下的那一级，之前的节点视为已通过。
// 状态文案已挪到标题后的徽章（renderAttributionStatusBadge），链条只保留节点。
function renderAttributionChain(attribution) {
    const level = attribution && attribution.level ? attribution.level : '';
    const config = ATTRIBUTION_LEVELS[level] || null;
    const chain = createEl('div', `attribution-chain${level ? ` level-${level}` : ''}`, '', {
        dataset: { attributionLevel: level }
    });
    const currentIndex = config ? config.stepIndex : -1;
    const steps = createEl('div', 'attribution-chain-steps');
    ATTRIBUTION_CHAIN_STEPS.forEach((step, index) => {
        const stepState = index < currentIndex ? 'passed' : (index === currentIndex ? 'current' : 'pending');
        const stepEl = createEl('div', `attribution-step is-${stepState}`, [
            createEl('span', 'attribution-step-dot'),
            createEl('span', 'attribution-step-label', step.label)
        ], {
            dataset: { step: step.key, stepState }
        });
        const extra = renderStepExtra(step.key, stepState, attribution);
        if (extra) stepEl.appendChild(extra);
        steps.appendChild(stepEl);
    });
    chain.appendChild(steps);
    return chain;
}
