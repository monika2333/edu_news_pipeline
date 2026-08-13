// Manual Filter JS - Utils

function formatScore(value) {
    if (value === null || value === undefined || value === '') return '-';
    const score = Number(value);
    return Number.isFinite(score) ? String(Math.round(score)) : '-';
}

// 控制台共用的本地时间格式化（内容抽屉、检索抽屉归因共用）。
// 后端返回带 Z 的 UTC 时间，必须经 new Date 按本地时区取值；
// 直接截字符串等于把 UTC 当本地时间显示，凌晨入库的文章日期会差一天。
function formatLocalDateTime(iso) {
    if (!iso) return '-';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return String(iso);
    const pad = value => String(value).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
        + `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function getManualReviewVersion(articleId) {
    const card = Array.from(document.querySelectorAll('.article-card[data-id]'))
        .find(candidate => candidate.dataset.id === articleId);
    const domVersion = Number(card?.dataset.version);
    if (Number.isInteger(domVersion) && domVersion > 0) return domVersion;
    const reviewItems = [
        ...(state.reviewData?.selected || []),
        ...(state.reviewData?.backup || [])
    ];
    const item = reviewItems.find(candidate => candidate?.article_id === articleId);
    const stateVersion = Number(item?.version);
    return Number.isInteger(stateVersion) && stateVersion > 0 ? stateVersion : null;
}

function collectManualReviewVersions(articleIds) {
    const versions = {};
    (articleIds || []).forEach(articleId => {
        const version = getManualReviewVersion(articleId);
        if (version !== null) versions[articleId] = version;
    });
    return versions;
}

function applyManualReviewVersions(versions) {
    Object.entries(versions || {}).forEach(([articleId, rawVersion]) => {
        const version = Number(rawVersion);
        if (!Number.isInteger(version) || version <= 0) return;
        document.querySelectorAll('.article-card[data-id]').forEach(card => {
            if (card.dataset.id === articleId) card.dataset.version = String(version);
        });
        ['selected', 'backup'].forEach(status => {
            const item = (state.reviewData?.[status] || [])
                .find(candidate => candidate?.article_id === articleId);
            if (item) item.version = version;
        });
    });
}

async function requireManualMutationSuccess(response, fallbackMessage) {
    const payload = await response.json().catch(() => ({}));
    if (response.status === 409) {
        throw new Error('该记录已被其他操作更新，请刷新后重试');
    }
    if (!response.ok) {
        throw new Error(formatApiError(payload, fallbackMessage));
    }
    applyManualReviewVersions(payload.versions);
    return payload;
}

function renderArticleCard(item, { showStatus = true, collapsed = false } = {}) {
    const safe = item || {};
    const currentStatus = safe.manual_status || safe.status || 'pending';
    const sourcePlaceholder = safe.llm_source_raw ? `(LLM: ${safe.llm_source_raw})` : '留空则回退抓取来源';
    const bonusClass = safe.bonus_keywords && safe.bonus_keywords.length ? ' has-bonus' : '';
    const duplicate = safe.submission_duplicate;
    const duplicateState = duplicate?.has_confirmed ? 'confirmed'
        : duplicate?.has_suspected ? 'suspected'
            : '';
    const duplicateLabel = duplicateState === 'confirmed' ? '已报送' : '疑似已报送';
    const duplicateTitle = (duplicate?.matches || []).map(match => {
        const reportLabel = {
            zongbao: '综报',
            wanbao: '晚报',
            feedback: '反馈'
        }[match.report_type] || match.report_type || '存档';
        const score = Number(match.similarity);
        const scoreText = Number.isFinite(score) ? `（相似度 ${score.toFixed(2)}）` : '';
        const extraCount = Number(match.extra_count) || 0;
        const extraText = extraCount > 0 ? `，另有 ${extraCount} 条记录` : '';
        return `${match.report_date || ''} ${reportLabel}：${match.title || ''}${scoreText}${extraText}`;
    }).join('\n');
    const duplicateBadge = duplicateState ? `
        <span class="submission-duplicate-wrap">
            <button type="button" class="submission-duplicate-badge ${duplicateState}"
                data-article-id="${safeHtml(safe.article_id || '')}"
                data-duplicate-state="${duplicateState}"
                title="${safeHtml(duplicateTitle)}">${duplicateLabel}</button>
            ${duplicateState === 'suspected' ? `
            <button type="button" class="submission-duplicate-dismiss"
                data-article-id="${safeHtml(safe.article_id || '')}">不是重复</button>
            ` : ''}
        </span>
    ` : '';
    const statusGroup = showStatus ? `
        <div class="radio-group" role="radiogroup">
            <div class="radio-option">
                <input type="radio" name="status-${safe.article_id}" value="selected" id="sel-${safe.article_id}" ${currentStatus === 'selected' ? 'checked' : ''}>
                <label for="sel-${safe.article_id}" class="radio-label">采纳</label>
            </div>
            <div class="radio-option">
                <input type="radio" name="status-${safe.article_id}" value="backup" id="bak-${safe.article_id}" ${currentStatus === 'backup' ? 'checked' : ''}>
                <label for="bak-${safe.article_id}" class="radio-label">备选</label>
            </div>
            <div class="radio-option">
                <input type="radio" name="status-${safe.article_id}" value="discarded" id="dis-${safe.article_id}" ${currentStatus === 'discarded' ? 'checked' : ''}>
                <label for="dis-${safe.article_id}" class="radio-label">放弃</label>
            </div>
        </div>
    ` : '';

    return `
        <div class="article-card${bonusClass}${collapsed ? ' collapsed' : ''}" data-id="${safe.article_id || ''}" data-status="${currentStatus}" data-version="${safe.version || 0}" ${collapsed ? 'style="display:none;"' : ''}>
            <div class="card-header">
                <h3 class="article-title">
                    ${safeHtml(safe.title || '(No Title)')}
                    <button type="button" class="content-drawer-trigger"
                        data-article-id="${escapeAttr(safe.article_id || '')}"
                        data-bonus-keywords="${escapeAttr((safe.bonus_keywords || []).join('\n'))}"
                        title="查看原文">原文</button>
                    ${duplicateBadge}
                </h3>
                ${statusGroup}
            </div>

            <div class="meta-row">
                <div class="meta-item">来源: ${safe.source || '-'}</div>
                ${renderScoreFeedbackControl(safe)}
                <div class="meta-item">
                    <span class="badge ${getSentimentClass(safe.sentiment_label)}">${safe.sentiment_label || '-'}</span>
                </div>
                <div class="meta-item">京内: ${safe.is_beijing_related ? '是' : '否'
        }</div>
    ${safe.bonus_keywords && safe.bonus_keywords.length ?
            `<div class="meta-item">Bonus: ${safe.bonus_keywords.join(', ')}</div>` : ''
        }
            </div>

    <textarea class="summary-box" id="summary-${safe.article_id}">${safe.summary || ''}</textarea>
    <input class="source-box" id="source-${safe.article_id}" value="${safe.llm_source_display || ''}" placeholder="${sourcePlaceholder}">
        </div>
    `;
}

function setupTabs() {
    const syncWorkspaceTabActions = currentTab => {
        document.querySelectorAll('[data-workspace-action-tab]').forEach(action => {
            action.hidden = action.dataset.workspaceActionTab !== currentTab;
        });
    };

    syncWorkspaceTabActions(state.currentTab);
    elements.tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            elements.tabs.forEach(t => t.classList.remove('active'));
            elements.contents.forEach(c => c.classList.remove('active'));

            tab.classList.add('active');
            document.getElementById(`${tab.dataset.tab}-tab`).classList.add('active');
            state.currentTab = tab.dataset.tab;
            syncWorkspaceTabActions(state.currentTab);

            // Reset tab state to defaults
            if (state.currentTab === 'filter') {
                state.filterCategory = 'internal_positive';
                state.filterPage = 1;
                if (elements.filterTabButtons) {
                    elements.filterTabButtons.forEach(btn => {
                        btn.classList.toggle('active', btn.dataset.category === 'internal_positive');
                    });
                }
            } else if (state.currentTab === 'review') {
                state.reviewView = 'selected';
            }

            reloadCurrentTab();
        });
    });
}

function reloadCurrentTab(options = {}) {
    if (state.currentTab === 'filter') loadFilterData(options);
    else if (state.currentTab === 'review') {
        loadReviewData();
        if (IS_DUTY_WORKSPACE) loadDutyFinalizationStatus();
    }
    else if (state.currentTab === 'discard') loadDiscardData();
}

function updateReviewRailCounts() {
    if (!elements.reviewRailButtons || !elements.reviewRailButtons.length) return;
    elements.reviewRailButtons.forEach(btn => {
        const baseLabel = btn.dataset.label || btn.textContent.trim();
        btn.dataset.label = baseLabel;
        const rt = btn.dataset.reportType === 'wanbao' ? 'wanbao' : 'zongbao';
        const view = btn.dataset.view === 'backup' ? 'backup' : 'selected';
        const count = (state.reviewCounts[rt] && state.reviewCounts[rt][view]) || 0;
        btn.textContent = `${baseLabel} (${count})`;
    });
    if (typeof updateDuplicateReviewJobUI === 'function') {
        updateDuplicateReviewJobUI();
    }
}

function updateFilterCountsUI() {
    if (!elements.filterTabButtons || !elements.filterTabButtons.length) return;
    elements.filterTabButtons.forEach(btn => {
        const baseLabel = btn.dataset.label || btn.textContent.trim();
        btn.dataset.label = baseLabel;
        const key = btn.dataset.category || '';
        const count = state.filterCounts[key] || 0;
        btn.textContent = `${baseLabel} (${count})`;
    });
}

function setReviewReportType(value) {
    const normalized = value === 'wanbao' ? 'wanbao' : 'zongbao';
    if (state.reviewReportType === normalized) return;

    state.reviewReportType = normalized;
    if (elements.reportTypeTabText) {
        elements.reportTypeTabText.textContent = normalized === 'wanbao' ? '晚报' : '综报';
    }
    if (elements.reportTypeButtons && elements.reportTypeButtons.length) {
        elements.reportTypeButtons.forEach(btn => {
            const isActive = btn.dataset.type === normalized;
            btn.classList.toggle('active', isActive);
            btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
        });
    }
    // Update rail buttons active state based on new report type
    if (elements.reviewRailButtons && elements.reviewRailButtons.length) {
        elements.reviewRailButtons.forEach(btn => {
            btn.classList.toggle(
                'active',
                btn.dataset.reportType === normalized && btn.dataset.view === state.reviewView
            );
        });
    }
    updateReviewRailCounts();
    loadStats();
    loadReviewData();
    if (IS_DUTY_WORKSPACE) loadDutyFinalizationStatus();
}

function setReviewView(view) {
    const normalized = view === 'backup' ? 'backup' : 'selected';
    if (state.reviewView === normalized && state.reviewData[normalized]?.length) {
        // Optional: if view is same and we have data, we could just render (or do nothing if already rendered?)
        // But let's allow re-render in case of sort mode toggles etc,
        // Just don't reload data.
    }
    state.reviewView = normalized;
    if (elements.reviewRailButtons && elements.reviewRailButtons.length) {
        elements.reviewRailButtons.forEach(btn => {
            btn.classList.toggle(
                'active',
                btn.dataset.reportType === state.reviewReportType && btn.dataset.view === normalized
            );
        });
    }
    updateReviewRailCounts();
    renderReviewView();
}

async function loadStats() {
    try {
        const [zbRes, wbRes] = await Promise.all([
            workspaceFetch(`${API_BASE}/stats?report_type=zongbao`),
            workspaceFetch(`${API_BASE}/stats?report_type=wanbao`)
        ]);
        const zbData = await zbRes.json();
        const wbData = await wbRes.json();
        state.reviewCounts = {
            zongbao: { selected: zbData.selected || 0, backup: zbData.backup || 0 },
            wanbao: { selected: wbData.selected || 0, backup: wbData.backup || 0 }
        };
        // pending / discarded 不分报别，两个响应里的数值一致；采纳 / 备选徽标按当前报别取值
        const currentData = state.reviewReportType === 'wanbao' ? wbData : zbData;
        const badgeValues = {
            pending: zbData.pending,
            discarded: zbData.discarded,
            selected: currentData.selected,
            backup: currentData.backup
        };
        Object.entries(badgeValues).forEach(([key, value]) => {
            if (elements.stats[key] && value !== undefined && value !== null) {
                elements.stats[key].textContent = value;
            }
        });
        updateReviewRailCounts();
    } catch (e) {
        showToast('加载统计信息失败', 'error');
    }
}

function getSentimentClass(label) {
    if (!label) return 'neutral';
    label = label.toLowerCase();
    if (label === 'positive') return 'positive';
    if (label === 'negative') return 'negative';
    return 'neutral';
}

// Global timeout variable to clear previous timeouts
let toastTimeout;

const UNDO_ACTION_ICON = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
  <path d="M9 10h7a4 4 0 0 1 0 8h-1" />
  <path d="M12 7l-3 3 3 3" />
</svg>`;

function buildUndoToastAction(callback, title = '撤销操作') {
    return {
        icon: UNDO_ACTION_ICON,
        title,
        callback
    };
}

function showToastAt(toastElement, msg, type = 'success', action = null) {
    if (toastTimeout) {
        clearTimeout(toastTimeout);
        toastTimeout = null;
    }

    // Reset content first
    toastElement.innerHTML = '';
    toastElement.textContent = '';

    const span = document.createElement('span');
    span.textContent = msg;
    toastElement.appendChild(span);

    if (action && (action.text || action.icon) && action.callback) {
        const btn = document.createElement('button');
        if (action.icon) {
            btn.innerHTML = action.icon;
            btn.title = action.title || '撤销'; // Tooltip
        } else {
            btn.textContent = action.text;
        }

        btn.className = 'btn-action';
        // marginLeft removed, handled by gap in .toast flex container
        btn.style.color = '#60a5fa'; // Light blue
        btn.style.background = 'transparent';
        btn.style.border = 'none';
        btn.style.cursor = 'pointer';
        btn.style.padding = '4px';
        btn.style.display = 'inline-flex';
        btn.style.alignItems = 'center';

        if (action.text) {
            btn.style.textDecoration = 'underline';
            btn.style.fontSize = 'inherit';
        }

        btn.onclick = (e) => {
            e.stopPropagation();
            action.callback();
            toastElement.classList.remove('show');
        };
        toastElement.appendChild(btn);
    }

    toastElement.className = `toast show ${type}`;

    // Increase timeout if there is an action to give user more time
    const duration = action ? 5000 : 3000;

    toastTimeout = setTimeout(() => {
        toastElement.classList.remove('show');
    }, duration);
}

function updatePagination(tab, total, currentPage, pageSize) {
    // pageSize comes from the list response's limit field; fall back to 10.
    const size = Math.max(1, Math.floor(Number(pageSize)) || 10);
    const totalPages = Math.ceil(total / size);
    const container = document.getElementById(`${tab}-pagination`);
    if (!container) return;

    container.innerHTML = `
        <button class="btn btn-secondary" ${currentPage <= 1 ? 'disabled' : ''} onclick="changePage('${tab}', ${currentPage - 1})">上一页</button>
        <span>第 ${currentPage} 页 / 共 ${totalPages} 页</span>
        <button class="btn btn-secondary" ${currentPage >= totalPages ? 'disabled' : ''} onclick="changePage('${tab}', ${currentPage + 1})">下一页</button>
    `;
}

function setupPagination() {
    // Pagination buttons are rendered with inline changePage handlers.
}

window.changePage = function (tab, page) {
    if (tab === 'filter') state.filterPage = page;
    else if (tab === 'discard') state.discardPage = page;
    reloadCurrentTab();
};


const safeHtml = (str) => {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
};

const escapeAttr = (str) => String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

// --- DOM Helpers ---
function createEl(tag, className, textOrChildren = '', attributes = {}) {
    const el = document.createElement(tag);
    if (className) el.className = className;

    // Check if textOrChildren is a string (text/HTML) or an array/Node (children)
    if (typeof textOrChildren === 'string') {
        el.textContent = textOrChildren;
    } else if (Array.isArray(textOrChildren)) {
        textOrChildren.forEach(child => {
            if (child) el.appendChild(child);
        });
    } else if (textOrChildren instanceof Node) {
        el.appendChild(textOrChildren);
    }

    Object.entries(attributes).forEach(([key, value]) => {
        if (key === 'dataset') {
            Object.entries(value).forEach(([dKey, dVal]) => el.dataset[dKey] = dVal);
        } else if (key === 'style' && typeof value === 'object') {
            Object.assign(el.style, value);
        } else if (key === 'onclick' && typeof value === 'function') {
            el.addEventListener('click', value);
        } else {
            el.setAttribute(key, value);
        }
    });

    return el;
}

function clearEl(el) {
    if (typeof el === 'string') el = document.getElementById(el);
    if (el) el.innerHTML = '';
}

function renderSkeleton(count = 3) {
    return Array(count).fill(0).map(() => `
        <div class="skeleton-card">
            <div class="skeleton-header">
                <div class="skeleton-line title"></div>
                <div class="skeleton-line short"></div>
            </div>
            <div class="skeleton-line full"></div>
            <div class="skeleton-line full"></div>
            <div class="skeleton-line short"></div>
        </div>
    `).join('');
}
