// Submission Archive JS - Link Queue
/* ---------- 回链确认队列 ---------- */

function linkCard(item) {
    const combined = Number(item.link_combined_score);
    const percent = Number.isFinite(combined) ? Math.round(combined * 100) : 0;
    return `
        <article class="archive-link-card" data-item-id="${item.id}">
            <div class="archive-link-grid">
                <section class="archive-link-col">
                    <p class="archive-link-col-label">
                        存档条目 ${typePill(item.report_type)} <span>${shortDate(item.report_date)}</span>
                    </p>
                    <h3>${escapeHtml(item.title)}</h3>
                    <p class="archive-link-body">${escapeHtml(item.body || '')}</p>
                    <footer><span>来源：${escapeHtml(item.source || '-')}</span></footer>
                </section>
                <section class="archive-link-col is-candidate">
                    <p class="archive-link-col-label">系统最佳候选</p>
                    <h3>${escapeHtml(item.candidate_title || '候选已不存在')}</h3>
                    <p class="archive-link-body">${escapeHtml(item.candidate_body || '')}</p>
                    <footer>
                        <span>来源：${escapeHtml(item.candidate_source || '-')}</span>
                        ${item.candidate_url ? `<a href="${escapeHtml(item.candidate_url)}" target="_blank" rel="noopener noreferrer">打开原文</a>` : ''}
                    </footer>
                </section>
            </div>
            <div class="archive-link-score">
                <div class="archive-score-bar" role="img" aria-label="综合相似度 ${scoreValue(item.link_combined_score)}">
                    <span style="width: ${percent}%"></span>
                </div>
                <div class="archive-score-nums">
                    <span>综合 <strong>${scoreValue(item.link_combined_score)}</strong></span>
                    <span>标题 <strong>${scoreValue(item.link_title_score)}</strong></span>
                    <span>正文 <strong>${scoreValue(item.link_body_score)}</strong></span>
                </div>
            </div>
            <div class="archive-link-actions">
                <button class="btn btn-secondary archive-link-reject" type="button">不是同一条</button>
                <button class="btn btn-primary archive-link-accept" type="button">确认绑定</button>
            </div>
        </article>
    `;
}

async function loadLinkQueue() {
    const target = document.getElementById('archive-link-queue');
    const countTarget = document.getElementById('archive-queue-count');
    target.innerHTML = '<div class="archive-empty">正在加载…</div>';
    try {
        const data = await api('/link-queue?limit=100');
        let remaining = Number(data.total) || 0;
        const renderCount = () => {
            countTarget.textContent = remaining > 0 ? `剩余 ${remaining} 条` : '';
            setNavPending(remaining);
        };
        renderCount();
        target.innerHTML = data.items.length
            ? data.items.map(linkCard).join('')
            : '<div class="archive-empty">当前没有待确认条目</div>';
        target.addEventListener('click', async event => {
            const button = event.target.closest('.archive-link-accept, .archive-link-reject');
            if (!button) return;
            const card = button.closest('.archive-link-card');
            button.disabled = true;
            try {
                await api(`/items/${encodeURIComponent(card.dataset.itemId)}/link-decision`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ accepted: button.classList.contains('archive-link-accept') })
                });
                card.remove();
                remaining = Math.max(remaining - 1, 0);
                renderCount();
                if (!target.querySelector('.archive-link-card')) {
                    target.innerHTML = '<div class="archive-empty">当前没有待确认条目</div>';
                }
            } catch (error) {
                button.disabled = false;
                toast(error.message, 'error');
            }
        });
    } catch (error) {
        target.innerHTML = `<div class="archive-empty">${escapeHtml(error.message)}</div>`;
    }
}

