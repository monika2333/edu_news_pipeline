// Manual Filter JS - UI Templates
// Handles HTML string generation

const uiTemplates = {
    // --- Shared ---
    safeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    },

    getSentimentClass(label) {
        if (!label) return 'badge-neutral';
        if (label === 'positive') return 'badge-success';
        if (label === 'negative') return 'badge-danger';
        return 'badge-neutral';
    },

    // --- Filter Tab Rendering ---

    // Renders a single article card for the Filter List
    renderFilterArticleCard(item, options = {}) {
        const { showStatus = true, collapsed = false } = options;
        const sentimentClass = this.getSentimentClass(item.sentiment_label);

        return `
        <div class="article-card ${collapsed ? 'collapsed' : ''}" 
             data-id="${item.article_id}" 
             data-cluster-id="${item.cluster_id || ''}"
             data-status="${item.status || 'pending'}"
             style="${collapsed ? 'display: none;' : ''}">
            <div class="card-header">
                <h3 class="article-title">
                    ${item.title || '(No Title)'}
                    ${item.url ? `<a href="${item.url}" target="_blank" rel="noopener noreferrer">🔗</a>` : ''}
                </h3>
                ${showStatus ? this._renderRadioGroup(item.article_id, item.status) : ''}
            </div>
            
            <div class="meta-row">
                <div class="meta-item">来源: ${item.source || '-'}</div>
                <div class="meta-item">分数: ${item.score || '-'}</div>
                <div class="meta-item">
                    <span class="badge ${sentimentClass}">${item.sentiment_label || '-'}</span>
                </div>
                <div class="meta-item">京内: ${item.is_beijing_related ? '是' : '否'}</div>
                ${item.bonus_keywords && item.bonus_keywords.length ?
                `<div class="meta-item">Bonus: ${item.bonus_keywords.join(', ')}</div>` : ''
            }
            </div>

            <textarea class="summary-box" id="summary-${item.article_id}" placeholder="摘要">${item.summary || ''}</textarea>
            <input class="source-box" id="source-${item.article_id}" value="${item.llm_source_display || ''}" placeholder="${item.llm_source_raw ? `(LLM: ${item.llm_source_raw})` : '留空则回退抓取来源'}">
        </div>
        `;
    },

    _renderRadioGroup(id, currentStatus = 'pending') {
        const isSel = currentStatus === 'selected';
        const isBak = currentStatus === 'backup';
        const isDis = currentStatus === 'discarded';
        // If pending, none is checked usually, but original code checked 'discarded' by default in some places?
        // Actually original `renderCard` checked `discarded` by default if it was hardcoded HTML in the example, 
        // but for dynamic items we should probably check nothing or match status.
        // Let's stick to matching status.

        return `
        <div class="radio-group" role="radiogroup">
            <div class="radio-option">
                <input type="radio" name="status-${id}" value="selected" id="sel-${id}" ${isSel ? 'checked' : ''}>
                <label for="sel-${id}" class="radio-label">采纳</label>
            </div>
            <div class="radio-option">
                <input type="radio" name="status-${id}" value="backup" id="bak-${id}" ${isBak ? 'checked' : ''}>
                <label for="bak-${id}" class="radio-label">备选</label>
            </div>
            <div class="radio-option">
                <input type="radio" name="status-${id}" value="discarded" id="dis-${id}" ${isDis ? 'checked' : ''}>
                <label for="dis-${id}" class="radio-label">放弃</label>
            </div>
        </div>
        `;
    },

    renderCluster(cluster) {
        const items = cluster.items || [];
        const size = items.length;
        const clusterStatus = cluster.status || 'pending';

        // Single-item cluster -> Plain card
        if (size <= 1) {
            return this.renderFilterArticleCard(items[0], { showStatus: true, collapsed: false });
        }

        const [first, ...rest] = items;
        const hiddenCount = rest.length;

        return `
        <div class="filter-cluster" data-cluster-id="${cluster.cluster_id}" data-size="${size}" data-status="${clusterStatus}">
            <div class="cluster-header">
                <div class="radio-group cluster-radio" data-cluster="${cluster.cluster_id}">
                    <div class="radio-option">
                        <input type="radio" name="cluster-${cluster.cluster_id}" value="selected" id="cluster-sel-${cluster.cluster_id}" ${clusterStatus === 'selected' ? 'checked' : ''}>
                        <label for="cluster-sel-${cluster.cluster_id}" class="radio-label">采纳</label>
                    </div>
                    <div class="radio-option">
                        <input type="radio" name="cluster-${cluster.cluster_id}" value="backup" id="cluster-bak-${cluster.cluster_id}" ${clusterStatus === 'backup' ? 'checked' : ''}>
                        <label for="cluster-bak-${cluster.cluster_id}" class="radio-label">备选</label>
                    </div>
                    <div class="radio-option">
                        <input type="radio" name="cluster-${cluster.cluster_id}" value="discarded" id="cluster-dis-${cluster.cluster_id}" ${clusterStatus === 'discarded' ? 'checked' : ''}>
                        <label for="cluster-dis-${cluster.cluster_id}" class="radio-label">放弃</label>
                    </div>
                </div>
            </div>
            <div class="cluster-items">
                ${this.renderFilterArticleCard(first, { showStatus: false, collapsed: false })}
                ${rest.map(item => this.renderFilterArticleCard(item, { showStatus: false, collapsed: true })).join('')}
            </div>
            ${hiddenCount ? `<div class="cluster-toggle-row"><button type="button" class="btn btn-link cluster-toggle" data-target="${cluster.cluster_id}">展开其余${hiddenCount}条</button></div>` : ''}
        </div>
        `;
    },

    // --- Review Tab Rendering ---

    renderReviewCard(item) {
        // Shared logic with renderFilterArticleCard? 
        // Review cards have drag handles and slightly different layout in original code.
        // Let's keep them separate for now to avoid breaking changes, but could be unified later.

        return `
        <div class="review-card" data-id="${item.article_id}" data-status="${item.status}">
            <div class="drag-handle" title="拖拽排序">⋮⋮</div>
            <div class="review-card-content">
                <div class="review-header-row">
                    <div class="review-title-section">
                        <a href="${item.url}" target="_blank" class="review-title-link">${item.title}</a>
                        <span class="review-meta-tag">[${item.source}]</span>
                    </div>
                    <div class="review-actions">
                        <select class="status-select" data-id="${item.article_id}">
                            <option value="selected" ${item.status === 'selected' ? 'selected' : ''}>采纳</option>
                            <option value="backup" ${item.status === 'backup' ? 'selected' : ''}>备选</option>
                            <option value="discarded">放弃</option>
                        </select>
                    </div>
                </div>
                <div class="review-body">
                    <textarea class="review-summary-edit" data-id="${item.article_id}">${item.summary || ''}</textarea>
                    <input class="review-source-edit" data-id="${item.article_id}" value="${item.llm_source_display || ''}" placeholder="Source">
                </div>
            </div>
        </div>
        `;
    },

    renderReviewGroupHeader(groupKey, label, count) {
        // Optional: helper for group headers
        return `<h3 class="group-header">${label} (${count})</h3>`;
    }
};

window.manualFilterUi = uiTemplates;
