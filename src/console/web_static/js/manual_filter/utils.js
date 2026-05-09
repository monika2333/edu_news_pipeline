// Manual Filter JS - Utils

function renderArticleCard(item, { showStatus = true, collapsed = false } = {}) {
    const safe = item || {};
    const currentStatus = safe.manual_status || safe.status || 'pending';
    const importanceScoreRaw = safe.external_importance_score ?? safe.score;
    const importanceScore = (importanceScoreRaw === undefined || importanceScoreRaw === null) ? '-' : importanceScoreRaw;
    const sourcePlaceholder = safe.llm_source_raw ? `(LLM: ${safe.llm_source_raw})` : '留空则回退抓取来源';
    const bonusClass = safe.bonus_keywords && safe.bonus_keywords.length ? ' has-bonus' : '';
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
        <div class="article-card${bonusClass}${collapsed ? ' collapsed' : ''}" data-id="${safe.article_id || ''}" data-status="${currentStatus}" ${collapsed ? 'style="display:none;"' : ''}>
            <div class="card-header">
                <h3 class="article-title">
                    ${safe.title || '(No Title)'}
                    ${safe.url ? `<a href="${safe.url}" target="_blank" rel="noopener noreferrer">🔗</a>` : ''}
                </h3>
                ${statusGroup}
            </div>

            <div class="meta-row">
                <div class="meta-item">来源: ${safe.source || '-'}</div>
                <div class="meta-item">分数: ${importanceScore}</div>
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
    elements.tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            elements.tabs.forEach(t => t.classList.remove('active'));
            elements.contents.forEach(c => c.classList.remove('active'));

            tab.classList.add('active');
            document.getElementById(`${tab.dataset.tab}-tab`).classList.add('active');
            state.currentTab = tab.dataset.tab;

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
                state.reviewReportType = 'zongbao';
                state.reviewView = 'selected';
                if (elements.reportTypeButtons) {
                    elements.reportTypeButtons.forEach(btn => {
                        btn.classList.toggle('active', btn.dataset.type === 'zongbao');
                    });
                }
            }

            reloadCurrentTab();
        });
    });
}

function setupActor() {
    elements.actorInput.value = state.actor;
    elements.actorInput.addEventListener('change', (e) => {
        state.actor = e.target.value.trim();
        localStorage.setItem('actor', state.actor);
    });
}

function reloadCurrentTab(options = {}) {
    if (state.currentTab === 'filter') loadFilterData(options);
    else if (state.currentTab === 'review') loadReviewData();
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
    if (elements.reportTypeButtons && elements.reportTypeButtons.length) {
        elements.reportTypeButtons.forEach(btn => {
            btn.classList.toggle('active', btn.dataset.type === normalized);
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
    loadReviewData();
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
        const [allRes, zbRes, wbRes] = await Promise.all([
            fetch(`${API_BASE}/stats`),
            fetch(`${API_BASE}/stats?report_type=zongbao`),
            fetch(`${API_BASE}/stats?report_type=wanbao`)
        ]);
        const allData = await allRes.json();
        const zbData = await zbRes.json();
        const wbData = await wbRes.json();
        Object.keys(allData).forEach(key => {
            if (elements.stats[key]) elements.stats[key].textContent = allData[key];
        });
        state.reviewCounts = {
            zongbao: { selected: zbData.selected || 0, backup: zbData.backup || 0 },
            wanbao: { selected: wbData.selected || 0, backup: wbData.backup || 0 }
        };
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

function showToast(msg, type = 'success', action = null) {
    if (toastTimeout) {
        clearTimeout(toastTimeout);
        toastTimeout = null;
    }

    // Reset content first
    elements.toast.innerHTML = '';
    elements.toast.textContent = '';

    const span = document.createElement('span');
    span.textContent = msg;
    elements.toast.appendChild(span);

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
            elements.toast.classList.remove('show');
        };
        elements.toast.appendChild(btn);
    }

    elements.toast.className = `toast show ${type}`;

    // Increase timeout if there is an action to give user more time
    const duration = action ? 5000 : 3000;

    toastTimeout = setTimeout(() => {
        elements.toast.classList.remove('show');
    }, duration);
}

function updatePagination(tab, total, currentPage) {
    // Simple pagination implementation
    const totalPages = Math.ceil(total / 10);
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
