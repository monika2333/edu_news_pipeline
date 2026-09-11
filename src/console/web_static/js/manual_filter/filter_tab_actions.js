// Manual Filter JS - Filter Tab Actions

function describeFilterDecision(status, count, reportType = null) {
    const items = count > 1 ? ` ${count} 条新闻` : '';
    if (status === 'selected' || status === 'backup') {
        const actionLabel = status === 'selected' ? '采纳' : '备选';
        const effectiveReportType = reportType || state.filterAssignReportType;
        const reportLabel = effectiveReportType === 'wanbao' ? '晚报' : '综报';
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
    // 编辑保存串行化：决定操作前通过 pendingFilterEditPromise 等待队列排空，
    // 避免失焦保存与决定预存带同一版本号并发导致 409
    const saveTask = pendingFilterEditPromise.then(async () => {
        try {
            await persistEdits(edits);
            showToast('已保存');
        } catch (error) {
            showToast(error.message || '保存失败', 'error');
        }
    });
    pendingFilterEditPromise = saveTask;
    await saveTask;
}

// 决定前让焦点中的摘要/来源框失焦（触发 change 保存入队），再等待进行中的编辑保存完成
async function settlePendingFilterEdits() {
    const activeElement = document.activeElement;
    if (activeElement && typeof activeElement.matches === 'function'
        && activeElement.matches('.summary-box, .source-box')) {
        activeElement.blur();
    }
    await pendingFilterEditPromise;
}

async function handleCardDecisionChange(input, reportType = null) {
    const card = input.closest('.article-card');
    if (!card) return;

    const articleId = card.dataset.id;
    const status = input.value;
    const previousStatus = card.dataset.status || 'pending';
    if (!articleId || status === previousStatus) return;

    const radios = card.querySelectorAll('input[type="radio"][name^="status-"]');
    setInputsDisabled(radios, true);

    await settlePendingFilterEdits();
    const edits = {};
    collectCardEdits(card, edits);

    try {
        await persistEdits(edits);
        const mutation = await submitDecisions([articleId], status, null, reportType);
        const decisionMessage = describeFilterDecision(status, 1, reportType);
        if (IS_DUTY_WORKSPACE) {
            const removal = captureDutyFilterRemoval([card]);
            const pageEmptied = detachDutyFilterRemoval(removal);
            updateDutyFilterDecisionCounts(status, 1, 1, reportType);
            attachDutyUndo(
                removal,
                [articleId],
                status,
                mutation,
                decisionMessage,
                { reloadOnUndo: pageEmptied, reportType }
            );
            if (pageEmptied) {
                // 后台补下一页，不阻塞提示；加载函数内部吞错，catch 兜底不产生未处理 rejection
                reloadFilterPageAfterRemoval().catch(() => {});
            }
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
                            mutation.versions || {},
                            reportType
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

async function handleClusterDecisionChange(input, reportType = null) {
    const cluster = input.closest('.filter-cluster');
    if (!cluster) return;

    const status = input.value;
    const previousStatus = cluster.dataset.status || 'pending';
    if (status === previousStatus) return;

    const cards = cluster.querySelectorAll('.article-card');
    if (!cards.length) return;

    const radios = cluster.querySelectorAll('.cluster-radio input[type="radio"]');
    setInputsDisabled(radios, true);

    await settlePendingFilterEdits();
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
        const mutation = await submitDecisions(ids, status, null, reportType);
        const decisionMessage = describeFilterDecision(status, ids.length, reportType);
        if (IS_DUTY_WORKSPACE) {
            const removal = captureDutyFilterRemoval(cards);
            const pageEmptied = detachDutyFilterRemoval(removal);
            updateDutyFilterDecisionCounts(status, ids.length, 1, reportType);
            attachDutyUndo(
                removal,
                ids,
                status,
                mutation,
                decisionMessage,
                { reloadOnUndo: pageEmptied, reportType }
            );
            if (pageEmptied) {
                reloadFilterPageAfterRemoval().catch(() => {});
            }
        } else {
            cluster.remove();
            loadStats();
        }

        if (!IS_DUTY_WORKSPACE) {
            const undoAction = buildUndoToastAction(
                async () => {
                    try {
                        await submitDecisions(ids, 'pending', mutation.versions || {}, reportType);
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

function readCardEditValues(card) {
    const summaryBox = card.querySelector('.summary-box');
    const sourceBox = card.querySelector('.source-box');
    return {
        summary: summaryBox ? summaryBox.value : '',
        llm_source: sourceBox ? sourceBox.value : ''
    };
}

// 改动判定：与「最近一次被服务端确认的值」（基准）不同才算改过；没有基准时按改过处理，漏存比多存严重
function isFilterCardEditDirty(card) {
    const articleId = card.dataset.id;
    if (!articleId) return false;
    const baseline = filterEditBaselines.get(articleId);
    if (!baseline) return true;
    const current = readCardEditValues(card);
    return baseline.summary !== current.summary || baseline.llm_source !== current.llm_source;
}

function collectCardEdits(card, edits) {
    if (!isFilterCardEditDirty(card)) return;
    const articleId = card.dataset.id;
    edits[articleId] = readCardEditValues(card);
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

function updateDutyFilterDecisionCounts(status, itemCount, direction, reportType = null) {
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
        const effectiveReportType = reportType
            || (state.filterAssignReportType === 'wanbao' ? 'wanbao' : 'zongbao');
        state.reviewCounts[effectiveReportType][status] = Math.max(
            0,
            (Number(state.reviewCounts[effectiveReportType][status]) || 0) + delta
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
    const reportType = options.reportType || null;
    const undoAction = buildUndoToastAction(async () => {
        try {
            const undoMutation = await submitDecisions(
                ids,
                'pending',
                mutation.versions || {},
                reportType
            );
            if (reloadOnUndo) {
                await Promise.all([loadFilterData(), loadStats()]);
            } else {
                restoreDutyFilterRemoval(removal, undoMutation.versions || {});
                updateDutyFilterDecisionCounts(status, ids.length, -1, reportType);
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
    const applied = await loadFilterData();
    // 自己那次加载被更新的请求取代时，退页与滚动都交给生效的那次加载处理
    if (!applied) return;
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

    await settlePendingFilterEdits();
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
            attachDutyUndo(
                removal,
                ids,
                'discarded',
                mutation,
                `已放弃 ${ids.length} 条新闻`,
                { reloadOnUndo: true }
            );
            reloadFilterPageAfterRemoval().catch(() => {});
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
    const dutyUnprocessedOnly = state.filterDutyScope === 'unprocessed';
    try {
        const previewRes = await workspaceFetch(`${API_BASE}/bulk-discard`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                region,
                sentiment,
                q: query || null,
                created_before: null,
                dry_run: true,
                duty_unprocessed_only: dutyUnprocessedOnly
            })
        });
        if (!previewRes.ok) throw new Error('failed preview');
        const preview = await previewRes.json();
        if (!preview.matched) {
            showToast('当前没有可放弃的待处理新闻');
            return;
        }

        const scopeSuffix = dutyUnprocessedOnly ? '值班编辑未处理的' : '';
        const scopeText = query
            ? `检索到的 ${preview.matched} 条${scopeSuffix}`
            : `全部 ${preview.matched} 条${scopeSuffix}`;
        const confirmed = window.confirm(`确定放弃${scopeText}待处理新闻吗？`);
        if (!confirmed) return;

        const applyRes = await workspaceFetch(`${API_BASE}/bulk-discard`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                region,
                sentiment,
                q: query || null,
                created_before: null,
                dry_run: false,
                duty_unprocessed_only: dutyUnprocessedOnly
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

const CLEANUP_CATEGORY_LABELS = {
    internal_positive: '京内正面',
    internal_negative: '京内负面',
    external_positive: '京外正面',
    external_negative: '京外负面'
};

function getCleanupRows() {
    if (!elements.cleanupCategoryList) return [];
    return Array.from(elements.cleanupCategoryList.querySelectorAll('.cleanup-category-row'));
}

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

function setCleanupFinalizedNote(count) {
    if (!elements.cleanupFinalizedNote) return;
    if (count > 0) {
        elements.cleanupFinalizedNote.textContent = `其中有 ${count} 条已定稿，不会被清理`;
        elements.cleanupFinalizedNote.hidden = false;
    } else {
        elements.cleanupFinalizedNote.textContent = '';
        elements.cleanupFinalizedNote.hidden = true;
    }
}

function resetCleanupCategories() {
    getCleanupRows().forEach((row) => {
        const checkbox = row.querySelector('.cleanup-category-check');
        const countEl = row.querySelector('.cleanup-category-count');
        row.classList.remove('is-empty');
        delete row.dataset.count;
        if (checkbox) {
            checkbox.checked = true;
            checkbox.disabled = true;
        }
        if (countEl) countEl.textContent = '';
    });
}

function updateCleanupTotal() {
    let total = 0;
    getCleanupRows().forEach((row) => {
        const checkbox = row.querySelector('.cleanup-category-check');
        if (checkbox && checkbox.checked && !checkbox.disabled) {
            total += Number(row.dataset.count) || 0;
        }
    });
    setCleanupStats(`将放弃 ${total} 条`);
    if (total > 0) {
        setCleanupConfirmState(true, `放弃这 ${total} 条`);
    } else {
        setCleanupConfirmState(false, '确认放弃');
    }
}

function openCleanupModal() {
    if (!elements.cleanupModal) return;
    cleanupPreviewSeq += 1;
    if (elements.cleanupDateInput) elements.cleanupDateInput.value = '';
    resetCleanupCategories();
    setCleanupStats('');
    setCleanupFinalizedNote(0);
    setCleanupConfirmState(false, '确认放弃');
    if (elements.cleanupCancelBtn) elements.cleanupCancelBtn.disabled = false;
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
    const createdBefore = elements.cleanupDateInput ? elements.cleanupDateInput.value : '';
    if (!createdBefore) {
        cleanupPreviewSeq += 1;
        resetCleanupCategories();
        setCleanupStats('');
        setCleanupFinalizedNote(0);
        setCleanupConfirmState(false, '确认放弃');
        return;
    }
    const seq = ++cleanupPreviewSeq;
    setCleanupStats('正在统计…');
    setCleanupConfirmState(false, '确认放弃');
    try {
        const results = await Promise.all(FILTER_CATEGORIES.map(async (cat) => {
            const { region, sentiment } = filterCategoryToBucket(cat);
            const res = await workspaceFetch(`${API_BASE}/bulk-discard`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    region,
                    sentiment,
                    q: null,
                    created_before: createdBefore,
                    dry_run: true
                })
            });
            if (!res.ok) throw new Error('failed preview');
            return { cat, result: await res.json() };
        }));
        if (seq !== cleanupPreviewSeq) return;
        let skippedTotal = 0;
        const counts = {};
        results.forEach(({ cat, result }) => {
            const matched = Number(result.matched) || 0;
            const skipped = Number(result.skipped_finalized) || 0;
            counts[cat] = Math.max(0, matched - skipped);
            skippedTotal += skipped;
        });
        getCleanupRows().forEach((row) => {
            const count = counts[row.dataset.category] || 0;
            const checkbox = row.querySelector('.cleanup-category-check');
            const countEl = row.querySelector('.cleanup-category-count');
            row.dataset.count = String(count);
            row.classList.toggle('is-empty', count === 0);
            if (countEl) countEl.textContent = `${count} 条`;
            if (checkbox) {
                checkbox.disabled = count === 0;
                checkbox.checked = count > 0;
            }
        });
        setCleanupFinalizedNote(skippedTotal);
        updateCleanupTotal();
    } catch (error) {
        if (seq !== cleanupPreviewSeq) return;
        setCleanupStats('统计失败，请重试', true);
        setCleanupConfirmState(false, '确认放弃');
    }
}

async function confirmCleanupDiscard() {
    const createdBefore = elements.cleanupDateInput ? elements.cleanupDateInput.value : '';
    if (!createdBefore || !elements.cleanupConfirmBtn || elements.cleanupConfirmBtn.disabled) return;
    const targets = getCleanupRows()
        .filter((row) => {
            const checkbox = row.querySelector('.cleanup-category-check');
            return checkbox && checkbox.checked && !checkbox.disabled;
        })
        .map((row) => row.dataset.category);
    if (!targets.length) return;
    setCleanupConfirmState(false, '正在放弃…');
    if (elements.cleanupCancelBtn) elements.cleanupCancelBtn.disabled = true;
    try {
        const settled = await Promise.allSettled(targets.map(async (cat) => {
            const { region, sentiment } = filterCategoryToBucket(cat);
            const res = await workspaceFetch(`${API_BASE}/bulk-discard`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    region,
                    sentiment,
                    q: null,
                    created_before: createdBefore,
                    dry_run: false
                })
            });
            if (!res.ok) throw new Error('failed apply');
            return { cat, result: await res.json() };
        }));
        const succeeded = [];
        const failedCats = [];
        settled.forEach((entry, index) => {
            if (entry.status === 'fulfilled') {
                succeeded.push(entry.value);
            } else {
                failedCats.push(targets[index]);
            }
        });
        const updatedTotal = succeeded.reduce(
            (sum, item) => sum + (Number(item.result.updated) || 0), 0
        );
        const skippedTotal = succeeded.reduce(
            (sum, item) => sum + (Number(item.result.skipped_finalized) || 0), 0
        );
        closeCleanupModal();
        state.filterPage = 1;
        await Promise.all([loadFilterData(), loadStats(), loadFilterCounts()]);
        if (!failedCats.length) {
            let message = `已放弃 ${updatedTotal} 条新闻`;
            if (skippedTotal > 0) message += `，另有 ${skippedTotal} 条已定稿未清理`;
            showToast(message);
        } else {
            const failedNames = failedCats
                .map((cat) => CLEANUP_CATEGORY_LABELS[cat] || cat)
                .join('、');
            let message = `${failedNames} 放弃失败，请重跑清理`;
            if (succeeded.length) message += `；其余分类已放弃 ${updatedTotal} 条`;
            showToast(message, 'error');
        }
    } catch (error) {
        setCleanupStats('放弃失败，请重试', true);
    } finally {
        if (elements.cleanupCancelBtn) elements.cleanupCancelBtn.disabled = false;
    }
}
