// Manual Filter JS - Filter Tab Actions

function describeFilterDecision(status, count) {
    const items = count > 1 ? ` ${count} 条新闻` : '';
    if (status === 'selected' || status === 'backup') {
        const actionLabel = status === 'selected' ? '采纳' : '备选';
        const reportLabel = state.reviewReportType === 'wanbao' ? '晚报' : '综报';
        return `已${actionLabel}到${reportLabel}${items}`;
    }
    if (status === 'discarded') return `已放弃${items || '该条新闻'}`;
    return '已更新';
}

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
        showToast('已保存');
    } catch (error) {
        showToast(error.message || '保存失败', 'error');
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
        const mutation = await submitDecisions([articleId], status);
        const decisionMessage = describeFilterDecision(status, 1);
        if (IS_DUTY_WORKSPACE) {
            const removal = captureDutyFilterRemoval([card]);
            const pageEmptied = detachDutyFilterRemoval(removal);
            updateDutyFilterDecisionCounts(status, 1, 1);
            if (pageEmptied) await reloadFilterPageAfterRemoval();
            attachDutyUndo(
                removal,
                [articleId],
                status,
                mutation,
                decisionMessage,
                { reloadOnUndo: pageEmptied }
            );
        } else {
            removeCardAndMaybeCluster(card);
            loadStats();
        }

        if (!IS_DUTY_WORKSPACE) {
            const undoAction = buildUndoToastAction(
                async () => {
                    try {
                        await submitDecisions(
                            [articleId],
                            'pending',
                            mutation.versions || {}
                        );
                        showToast('已撤销');
                        await loadFilterData();
                        loadStats();
                    } catch (error) {
                        showToast(error.message || '撤销失败', 'error');
                    }
                }
            );
            showToast(decisionMessage, 'success', undoAction);
        }
    } catch (error) {
        revertRadioSelection(radios, previousStatus);
        card.dataset.status = previousStatus;
        showToast(error.message || '更新失败', 'error');
    } finally {
        setInputsDisabled(radios, false);
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
        const mutation = await submitDecisions(ids, status);
        const decisionMessage = describeFilterDecision(status, ids.length);
        if (IS_DUTY_WORKSPACE) {
            const removal = captureDutyFilterRemoval(cards);
            const pageEmptied = detachDutyFilterRemoval(removal);
            updateDutyFilterDecisionCounts(status, ids.length, 1);
            if (pageEmptied) await reloadFilterPageAfterRemoval();
            attachDutyUndo(
                removal,
                ids,
                status,
                mutation,
                decisionMessage,
                { reloadOnUndo: pageEmptied }
            );
        } else {
            cluster.remove();
            loadStats();
        }

        if (!IS_DUTY_WORKSPACE) {
            const undoAction = buildUndoToastAction(
                async () => {
                    try {
                        await submitDecisions(ids, 'pending', mutation.versions || {});
                        showToast('已撤销');
                        await loadFilterData();
                        loadStats();
                    } catch (error) {
                        showToast(error.message || '撤销失败', 'error');
                    }
                }
            );
            showToast(decisionMessage, 'success', undoAction);
        }
    } catch (error) {
        revertRadioSelection(radios, previousStatus);
        cluster.dataset.status = previousStatus;
        showToast(error.message || '更新失败', 'error');
    } finally {
        setInputsDisabled(radios, false);
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

function captureDutyFilterRemoval(cards) {
    const targets = [];
    const seen = new Set();
    Array.from(cards || []).forEach(card => {
        const target = card.closest('.filter-cluster') || card;
        if (!target || seen.has(target)) return;
        seen.add(target);
        targets.push({
            node: target,
            parent: target.parentNode,
            nextSibling: target.nextSibling
        });
    });
    return targets;
}

function detachDutyFilterRemoval(removal) {
    removal.forEach(entry => entry.node.remove());
    const pageEmptied = !elements.filterList.querySelector('.article-card');
    if (pageEmptied) {
        elements.filterList.insertAdjacentHTML(
            'beforeend',
            '<div class="empty empty-state duty-local-empty">当前页新闻已处理完</div>'
        );
    }
    return pageEmptied;
}

function restoreDutyFilterRemoval(removal, versions) {
    elements.filterList.querySelector('.duty-local-empty')?.remove();
    [...removal].reverse().forEach(entry => {
        if (!entry.parent) return;
        const anchor = entry.nextSibling?.parentNode === entry.parent
            ? entry.nextSibling
            : null;
        entry.parent.insertBefore(entry.node, anchor);
    });
    removal.forEach(entry => {
        entry.node.querySelectorAll('input[type="radio"]').forEach(radio => {
            radio.checked = false;
        });
        entry.node.querySelectorAll('.article-card').forEach(card => {
            card.dataset.status = 'pending';
        });
        if (entry.node.classList.contains('article-card')) {
            entry.node.dataset.status = 'pending';
        }
    });
    applyManualReviewVersions(versions);
}

function adjustVisibleStat(key, delta) {
    const target = elements.stats[key];
    const current = Number(target?.textContent);
    if (!target || !Number.isFinite(current)) return;
    target.textContent = String(Math.max(0, current + delta));
}

function updateDutyFilterDecisionCounts(status, itemCount, direction) {
    const delta = Math.max(0, Number(itemCount) || 0) * direction;
    const { cat } = getCurrentFilterBucket();
    state.filterCounts[cat] = Math.max(
        0,
        (Number(state.filterCounts[cat]) || 0) - delta
    );
    if (isFilterSearchMode()) {
        state.filterSearchTotal = Math.max(
            0,
            (Number(state.filterSearchTotal) || 0) - delta
        );
    }
    adjustVisibleStat('pending', -delta);
    if (status === 'selected' || status === 'backup') {
        adjustVisibleStat(status, delta);
        const reportType = state.reviewReportType === 'wanbao' ? 'wanbao' : 'zongbao';
        state.reviewCounts[reportType][status] = Math.max(
            0,
            (Number(state.reviewCounts[reportType][status]) || 0) + delta
        );
    }
    updateFilterCountsUI();
    updateReviewRailCounts();
    syncFilterToolbarState();
}

function attachDutyUndo(
    removal,
    ids,
    status,
    mutation,
    successMessage,
    options = {}
) {
    const reloadOnUndo = Boolean(options.reloadOnUndo);
    const undoAction = buildUndoToastAction(async () => {
        try {
            const undoMutation = await submitDecisions(
                ids,
                'pending',
                mutation.versions || {}
            );
            if (reloadOnUndo) {
                await Promise.all([loadFilterData(), loadStats()]);
            } else {
                restoreDutyFilterRemoval(removal, undoMutation.versions || {});
                updateDutyFilterDecisionCounts(status, ids.length, -1);
            }
            showToast('已撤销');
        } catch (error) {
            showToast(error.message || '撤销失败，原操作保持不变', 'error');
        }
    });
    showToast(successMessage, 'success', undoAction);
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

        await reloadFilterPageAfterRemoval();
    }, 120);
}

async function reloadFilterPageAfterRemoval() {
    const currentPage = state.filterPage;
    await loadFilterData();
    const afterReload = elements.filterList.querySelectorAll('.article-card');
    if ((!afterReload || !afterReload.length) && currentPage > 1) {
        state.filterPage = currentPage - 1;
        await loadFilterData();
    }
    window.scrollTo({ top: 0, behavior: 'auto' });
}

async function discardRemainingItems() {
    const cards = elements.filterList ? elements.filterList.querySelectorAll('.article-card') : [];
    if (!cards || !cards.length) {
        showToast('当前没有可放弃的可见新闻');
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
        showToast('当前没有可放弃的可见新闻');
        return;
    }

    try {
        await persistEdits(edits);
        const mutation = await submitDecisions(ids, 'discarded');
        if (IS_DUTY_WORKSPACE) {
            const removal = captureDutyFilterRemoval(cards);
            detachDutyFilterRemoval(removal);
            updateDutyFilterDecisionCounts('discarded', ids.length, 1);
            await reloadFilterPageAfterRemoval();
            attachDutyUndo(
                removal,
                ids,
                'discarded',
                mutation,
                `已放弃 ${ids.length} 条新闻`,
                { reloadOnUndo: true }
            );
        } else {
            removeCardsAndClusters(cards);
            loadStats();
        }

        if (!IS_DUTY_WORKSPACE) {
            const undoAction = buildUndoToastAction(
                async () => {
                    try {
                        await submitDecisions(ids, 'pending', mutation.versions || {});
                        showToast('已撤销');
                        await loadFilterData();
                        loadStats();
                    } catch (error) {
                        showToast(error.message || '撤销失败', 'error');
                    }
                }
            );
            showToast(`已放弃 ${ids.length} 条新闻`, 'success', undoAction);
        }
    } catch (error) {
        showToast(error.message || '批量放弃失败', 'error');
    }
}

async function bulkDiscard() {
    const { region, sentiment } = getCurrentFilterBucket();
    const query = state.filterQuery || (elements.filterSearchInput ? elements.filterSearchInput.value.trim() : '');
    try {
        const previewRes = await workspaceFetch(`${API_BASE}/bulk-discard`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                region,
                sentiment,
                q: query || null,
                published_before: null,
                dry_run: true
            })
        });
        if (!previewRes.ok) throw new Error('failed preview');
        const preview = await previewRes.json();
        if (!preview.matched) {
            showToast('当前没有可放弃的待处理新闻');
            return;
        }

        const scopeText = query ? `检索到的 ${preview.matched} 条` : `全部 ${preview.matched} 条`;
        const confirmed = window.confirm(`确定放弃${scopeText}待处理新闻吗？`);
        if (!confirmed) return;

        const applyRes = await workspaceFetch(`${API_BASE}/bulk-discard`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                region,
                sentiment,
                q: query || null,
                published_before: null,
                dry_run: false
            })
        });
        if (!applyRes.ok) throw new Error('failed apply');
        const result = await applyRes.json();
        showToast(`已放弃 ${result.updated} 条新闻`);
        state.filterPage = 1;
        await Promise.all([loadFilterData(), loadStats()]);
    } catch (error) {
        showToast('批量放弃失败', 'error');
    }
}

let cleanupPreviewSeq = 0;

function setCleanupConfirmState(enabled, label) {
    if (!elements.cleanupConfirmBtn) return;
    elements.cleanupConfirmBtn.disabled = !enabled;
    elements.cleanupConfirmBtn.textContent = label;
}

function setCleanupStats(message, isError = false) {
    if (!elements.cleanupStats) return;
    elements.cleanupStats.textContent = message;
    elements.cleanupStats.classList.toggle('is-error', Boolean(isError));
}

function openCleanupModal() {
    if (!elements.cleanupModal) return;
    cleanupPreviewSeq += 1;
    if (elements.cleanupDateInput) elements.cleanupDateInput.value = '';
    setCleanupStats('');
    setCleanupConfirmState(false, '确认放弃');
    elements.cleanupModal.classList.add('active');
    elements.cleanupModal.setAttribute('aria-hidden', 'false');
}

function closeCleanupModal() {
    if (!elements.cleanupModal) return;
    cleanupPreviewSeq += 1;
    elements.cleanupModal.classList.remove('active');
    elements.cleanupModal.setAttribute('aria-hidden', 'true');
}

async function handleCleanupDateChange() {
    const publishedBefore = elements.cleanupDateInput ? elements.cleanupDateInput.value : '';
    if (!publishedBefore) {
        cleanupPreviewSeq += 1;
        setCleanupStats('');
        setCleanupConfirmState(false, '确认放弃');
        return;
    }
    const seq = ++cleanupPreviewSeq;
    const { region, sentiment } = getCurrentFilterBucket();
    setCleanupStats('正在统计…');
    setCleanupConfirmState(false, '确认放弃');
    try {
        const res = await workspaceFetch(`${API_BASE}/bulk-discard`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                region,
                sentiment,
                q: null,
                published_before: publishedBefore,
                dry_run: true
            })
        });
        if (!res.ok) throw new Error('failed preview');
        const result = await res.json();
        if (seq !== cleanupPreviewSeq) return;
        const matched = Number(result.matched) || 0;
        if (matched > 0) {
            setCleanupStats(`将放弃 ${matched} 条`);
            setCleanupConfirmState(true, `放弃这 ${matched} 条`);
        } else {
            setCleanupStats('该日期之前没有待处理新闻');
            setCleanupConfirmState(false, '确认放弃');
        }
    } catch (error) {
        if (seq !== cleanupPreviewSeq) return;
        setCleanupStats('统计失败，请重试', true);
        setCleanupConfirmState(false, '确认放弃');
    }
}

async function confirmCleanupDiscard() {
    const publishedBefore = elements.cleanupDateInput ? elements.cleanupDateInput.value : '';
    if (!publishedBefore || !elements.cleanupConfirmBtn || elements.cleanupConfirmBtn.disabled) return;
    const { region, sentiment } = getCurrentFilterBucket();
    setCleanupConfirmState(false, '确认放弃');
    try {
        const res = await workspaceFetch(`${API_BASE}/bulk-discard`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                region,
                sentiment,
                q: null,
                published_before: publishedBefore,
                dry_run: false
            })
        });
        if (!res.ok) throw new Error('failed apply');
        const result = await res.json();
        closeCleanupModal();
        showToast(`已放弃 ${result.updated} 条新闻`);
        state.filterPage = 1;
        await Promise.all([loadFilterData(), loadStats(), loadFilterCounts()]);
    } catch (error) {
        setCleanupStats('放弃失败，请重试', true);
    }
}
