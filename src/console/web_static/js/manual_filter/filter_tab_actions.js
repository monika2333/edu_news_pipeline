// Manual Filter JS - Filter Tab Actions

function setupFilterRealtimeDecisionHandlers() {
    if (!elements.filterList) return;
    elements.filterList.addEventListener('change', (event) => {
        const target = event.target;
        if (target instanceof HTMLTextAreaElement && target.classList.contains('summary-box')) {
            handleFilterEditChange(target);
            return;
        }
        if (target instanceof HTMLInputElement && target.classList.contains('source-box')) {
            handleFilterEditChange(target);
            return;
        }
        if (!(target instanceof HTMLInputElement) || target.type !== 'radio') return;

        if (target.name.startsWith('cluster-')) {
            handleClusterDecisionChange(target);
        } else if (target.name.startsWith('status-')) {
            handleCardDecisionChange(target);
        }
    });
}

async function handleFilterEditChange(target) {
    const card = target.closest('.article-card');
    if (!card) return;
    const edits = {};
    collectCardEdits(card, edits);
    try {
        await persistEdits(edits);
        showToast('Saved');
    } catch (error) {
        showToast('Save failed', 'error');
    }
}

async function handleCardDecisionChange(input) {
    const card = input.closest('.article-card');
    if (!card) return;

    const articleId = card.dataset.id;
    const status = input.value;
    const previousStatus = card.dataset.status || 'pending';
    if (!articleId || status === previousStatus) return;

    const radios = card.querySelectorAll('input[type="radio"][name^="status-"]');
    setInputsDisabled(radios, true);

    const edits = {};
    collectCardEdits(card, edits);

    try {
        await persistEdits(edits);
        await submitDecisions([articleId], status);
        removeCardAndMaybeCluster(card);
        loadStats();

        const undoAction = {
            text: 'Undo',
            callback: async () => {
                try {
                    await submitDecisions([articleId], 'pending');
                    showToast('Undone');
                    await loadFilterData();
                    loadStats();
                } catch (error) {
                    showToast('Undo failed', 'error');
                }
            }
        };

        showToast('Updated', 'success', undoAction);
    } catch (error) {
        revertRadioSelection(radios, previousStatus);
        card.dataset.status = previousStatus;
        showToast('Update failed', 'error');
    } finally {
        if (card.isConnected) setInputsDisabled(radios, false);
    }
}

async function handleClusterDecisionChange(input) {
    const cluster = input.closest('.filter-cluster');
    if (!cluster) return;

    const status = input.value;
    const previousStatus = cluster.dataset.status || 'pending';
    if (status === previousStatus) return;

    const cards = cluster.querySelectorAll('.article-card');
    if (!cards.length) return;

    const radios = cluster.querySelectorAll('.cluster-radio input[type="radio"]');
    setInputsDisabled(radios, true);

    const edits = {};
    const ids = [];
    cards.forEach((card) => {
        const articleId = card.dataset.id;
        if (!articleId) return;
        ids.push(articleId);
        collectCardEdits(card, edits);
    });

    if (!ids.length) {
        setInputsDisabled(radios, false);
        return;
    }

    try {
        await persistEdits(edits);
        await submitDecisions(ids, status);
        cluster.remove();
        loadStats();

        const undoAction = {
            text: 'Undo',
            callback: async () => {
                try {
                    await submitDecisions(ids, 'pending');
                    showToast('Undone');
                    await loadFilterData();
                    loadStats();
                } catch (error) {
                    showToast('Undo failed', 'error');
                }
            }
        };

        showToast('Updated', 'success', undoAction);
    } catch (error) {
        revertRadioSelection(radios, previousStatus);
        cluster.dataset.status = previousStatus;
        showToast('Update failed', 'error');
    } finally {
        if (cluster.isConnected) setInputsDisabled(radios, false);
    }
}

function collectCardEdits(card, edits) {
    const articleId = card.dataset.id;
    if (!articleId) return;
    const summaryBox = card.querySelector('.summary-box');
    const sourceBox = card.querySelector('.source-box');
    const summary = summaryBox ? summaryBox.value : '';
    const llm_source = sourceBox ? sourceBox.value : '';
    edits[articleId] = { summary, llm_source };
}

function setInputsDisabled(nodes, disabled) {
    nodes.forEach((node) => {
        node.disabled = disabled;
    });
}

function revertRadioSelection(radios, status) {
    radios.forEach((radio) => {
        radio.checked = radio.value === status;
    });
}

function removeCardAndMaybeCluster(card) {
    const cluster = card.closest('.filter-cluster');
    card.remove();
    if (cluster && !cluster.querySelector('.article-card')) {
        cluster.remove();
    }
    scheduleReloadIfFilterPageEmpty();
}

function removeCardsAndClusters(cards) {
    const clusters = new Set();
    cards.forEach((card) => {
        const cluster = card.closest('.filter-cluster');
        if (cluster) clusters.add(cluster);
        card.remove();
    });
    clusters.forEach((cluster) => {
        if (!cluster.querySelector('.article-card')) cluster.remove();
    });
    scheduleReloadIfFilterPageEmpty();
}

function scheduleReloadIfFilterPageEmpty() {
    if (emptyFilterPageReloadTimer) clearTimeout(emptyFilterPageReloadTimer);
    emptyFilterPageReloadTimer = setTimeout(async () => {
        emptyFilterPageReloadTimer = null;
        if (!elements.filterList) return;
        const remaining = elements.filterList.querySelectorAll('.article-card');
        if (remaining && remaining.length) return;

        const currentPage = state.filterPage;
        await loadFilterData();
        const afterReload = elements.filterList.querySelectorAll('.article-card');
        if ((!afterReload || !afterReload.length) && currentPage > 1) {
            state.filterPage = currentPage - 1;
            await loadFilterData();
        }
    }, 120);
}

async function discardRemainingItems() {
    const cards = elements.filterList ? elements.filterList.querySelectorAll('.article-card') : [];
    if (!cards || !cards.length) {
        showToast('No visible articles to discard');
        return;
    }

    const edits = {};
    const ids = [];
    cards.forEach((card) => {
        const articleId = card.dataset.id;
        if (!articleId) return;
        ids.push(articleId);
        collectCardEdits(card, edits);
    });

    if (!ids.length) {
        showToast('No visible articles to discard');
        return;
    }

    try {
        await persistEdits(edits);
        await submitDecisions(ids, 'discarded');
        removeCardsAndClusters(cards);
        loadStats();

        const undoAction = {
            text: 'Undo',
            callback: async () => {
                try {
                    await submitDecisions(ids, 'pending');
                    showToast('Undone');
                    await loadFilterData();
                    loadStats();
                } catch (error) {
                    showToast('Undo failed', 'error');
                }
            }
        };

        showToast(`Discarded ${ids.length} articles`, 'success', undoAction);
    } catch (error) {
        showToast('Bulk discard failed', 'error');
    }
}

async function discardBeforeDate() {
    const publishedBefore = elements.filterDateBefore ? elements.filterDateBefore.value : '';
    const { region, sentiment } = getCurrentFilterBucket();
    const query = state.filterQuery || (elements.filterSearchInput ? elements.filterSearchInput.value.trim() : '');
    try {
        const previewRes = await fetch(`${API_BASE}/discard_before_date`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                region,
                sentiment,
                q: query || null,
                published_before: publishedBefore || null,
                actor: state.actor,
                dry_run: true
            })
        });
        if (!previewRes.ok) throw new Error('failed preview');
        const preview = await previewRes.json();
        if (!preview.matched) {
            showToast('No matched articles for current filters');
            return;
        }

        const filterSummary = [];
        if (query) filterSummary.push(`keyword "${query}"`);
        if (publishedBefore) filterSummary.push(`published before ${publishedBefore}`);
        const summaryText = filterSummary.length ? filterSummary.join(' and ') : 'the current bucket';
        const confirmed = window.confirm(`Discard ${preview.matched} pending articles matching ${summaryText}?`);
        if (!confirmed) return;

        const applyRes = await fetch(`${API_BASE}/discard_before_date`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                region,
                sentiment,
                q: query || null,
                published_before: publishedBefore || null,
                actor: state.actor,
                dry_run: false
            })
        });
        if (!applyRes.ok) throw new Error('failed apply');
        const result = await applyRes.json();
        showToast(`Discarded ${result.updated} articles`);
        state.filterPage = 1;
        await loadFilterCounts();
        await loadFilterData({ forceClusterRefresh: true });
        loadStats();
    } catch (error) {
        showToast('Discard before date failed', 'error');
    }
}
