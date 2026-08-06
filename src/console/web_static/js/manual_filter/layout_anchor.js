// Manual Filter JS - Layout Anchor
//
// 列表宽度变化（原文抽屉开合、侧栏折叠）后的通用处理：
// 重算可见 review 摘要框高度，并把锚定卡片补偿回原视口位置。

// 找到视口顶部第一张可见卡片：底边仍在视口内的最靠上一张。
// 无可见卡片时返回 null，调用方据此跳过滚动补偿。
function findTopVisibleArticleCard() {
    const lists = [elements.filterList, elements.reviewList]
        .filter(list => list && list.getClientRects().length);
    let best = null;
    lists.forEach(list => {
        list.querySelectorAll('.article-card').forEach(card => {
            const rect = card.getBoundingClientRect();
            if (!rect.height) return; // display:none（聚类折叠、页内搜索过滤）
            if (rect.bottom <= 0) return; // 已滚出视口上方
            if (!best || rect.top < best.top) {
                best = { card, top: rect.top };
            }
        });
    });
    return best;
}

// previousTop 为 null 或锚定卡片已不在 DOM 时只重算高度，不做滚动补偿。
function relayoutListsAfterWidthChange(anchorCard, previousTop) {
    // 非当前标签页的列表处于 display:none，scrollHeight 为 0，跳过重算避免高度被算没。
    // 筛选页摘要框是固定高度，从不自动撑高，不参与重算。
    if (elements.reviewList && elements.reviewList.getClientRects().length) {
        resizeReviewSummaryBoxes();
    }
    if (!anchorCard || !anchorCard.isConnected || previousTop === null) return;
    const delta = anchorCard.getBoundingClientRect().top - previousTop;
    if (delta) window.scrollBy(0, delta);
}
