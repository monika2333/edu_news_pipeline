// 单条文章一键复制：标题 + 摘要（来源）。
// 摘要取卡片 .summary-box 当前值（含未保存的手动修改）；
// 来源取 .source-box 当前值，留空时回退按钮 data-source 上的抓取来源。
// 依赖卡片 DOM 结构（.article-card/.article-title/.summary-box/.source-box），
// filter tab 的 renderArticleCard 与 review tab 的 renderReviewCard 共用。
(function () {
    if (window.__copyArticleBound) return;
    window.__copyArticleBound = true;

    function extractTitle(card) {
        const titleEl = card.querySelector('.article-title');
        if (!titleEl) return '';
        const clone = titleEl.cloneNode(true);
        clone.querySelectorAll('button').forEach(btn => btn.remove());
        return clone.textContent.trim();
    }

    async function writeClipboard(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
            return;
        }
        const helper = document.createElement('textarea');
        helper.value = text;
        helper.style.position = 'fixed';
        helper.style.opacity = '0';
        document.body.appendChild(helper);
        helper.select();
        document.execCommand('copy');
        helper.remove();
    }

    document.addEventListener('click', async (event) => {
        const btn = event.target.closest('.copy-article-btn');
        if (!btn) return;
        const card = btn.closest('.article-card');
        if (!card) return;

        const title = extractTitle(card);
        const summaryEl = card.querySelector('.summary-box');
        const sourceEl = card.querySelector('.source-box');
        const summary = summaryEl ? summaryEl.value.trim() : '';
        const source = (sourceEl && sourceEl.value.trim()) || (btn.dataset.source || '').trim();

        const text = [title, source ? `${summary}（${source}）` : summary]
            .filter(line => line)
            .join('\n');
        if (!text) return;

        try {
            await writeClipboard(text);
            showToast('已复制到剪贴板');
        } catch (err) {
            showToast('复制失败', 'error');
        }
    });
})();
