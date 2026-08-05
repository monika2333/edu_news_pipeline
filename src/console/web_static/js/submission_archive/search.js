// Submission Archive JS - Search
/* ---------- 全库搜索 ---------- */

async function searchArchive(event) {
    event?.preventDefault();
    const query = document.getElementById('archive-search-query').value.trim();
    const target = document.getElementById('archive-search-results');
    if (!query) {
        target.innerHTML = '<div class="archive-search-hint">输入关键词开始搜索全部存档条目。</div>';
        return;
    }
    target.innerHTML = '<div class="archive-empty">正在搜索…</div>';
    try {
        const data = await api(`/search?q=${encodeURIComponent(query)}&limit=50`);
        target.innerHTML = data.items.length ? data.items.map(item => `
            <article class="archive-search-item">
                <div class="archive-search-item-head">
                    ${typePill(item.report_type)}
                    <span class="archive-issue">${dateValue(item.report_date)}</span>
                    ${item.section ? `<span class="archive-issue">【${escapeHtml(item.section)}】</span>` : ''}
                </div>
                <h3>${highlight(item.title, query)}</h3>
                ${item.body ? `<p>${highlight(item.body, query)}</p>` : ''}
                <footer>
                    <span>来源：${escapeHtml(item.source || '-')}</span>
                    <a href="/submission-archive/${item.report_id}">查看整份报告</a>
                </footer>
            </article>
        `).join('') : '<div class="archive-empty">没有匹配结果</div>';
    } catch (error) {
        target.innerHTML = `<div class="archive-empty">${escapeHtml(error.message)}</div>`;
    }
}

