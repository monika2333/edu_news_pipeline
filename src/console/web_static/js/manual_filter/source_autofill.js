// 双击卡片 meta 行的「来源」项，把抓取来源填入人工来源框（.source-box）。
// 填入后派发 change 事件，复用 filter/review 两个 tab 已有的编辑持久化逻辑与提示。
// 依赖卡片 DOM 结构（.article-card/.meta-item-source/.source-box），
// filter tab 的 renderArticleCard 与 review tab 的 renderReviewCard 共用。
(function () {
    if (window.__sourceAutofillBound) return;
    window.__sourceAutofillBound = true;

    document.addEventListener('dblclick', (event) => {
        const meta = event.target.closest('.meta-item-source');
        if (!meta) return;
        const card = meta.closest('.article-card');
        if (!card) return;
        const sourceBox = card.querySelector('.source-box');
        if (!sourceBox) return;

        const source = (meta.dataset.sourceFill || '').trim();
        if (!source || sourceBox.value === source) return;

        sourceBox.value = source;
        sourceBox.dispatchEvent(new Event('change', { bubbles: true }));
    });
})();
