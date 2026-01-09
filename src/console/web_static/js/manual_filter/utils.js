// Manual Filter JS - Utils

function renderArticleCard(item, { showStatus = true, collapsed = false } = {}) {
    const safe = item || {};
    const currentStatus = safe.manual_status || safe.status || 'pending';
    const importanceScoreRaw = safe.external_importance_score ?? safe.score;
    const importanceScore = (importanceScoreRaw === undefined || importanceScoreRaw === null) ? '-' : importanceScoreRaw;
    const sourcePlaceholder = safe.llm_source_raw ? `(LLM: ${safe.llm_source_raw})` : '留空则回退抓取来源';
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
        <div class="article-card${collapsed ? ' collapsed' : ''}" data-id="${safe.article_id || ''}" data-status="${currentStatus}" ${collapsed ? 'style="display:none;"' : ''}>
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

function showToast(msg, type = 'success') {
    elements.toast.textContent = msg;
    elements.toast.className = `toast show ${type}`;
    setTimeout(() => {
        elements.toast.classList.remove('show');
    }, 3000);
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

window.changePage = function (tab, page) {
    if (tab === 'filter') state.filterPage = page;
    else if (tab === 'discard') state.discardPage = page;
    reloadCurrentTab();
};

function setupPagination() {
    // Handled dynamically
}