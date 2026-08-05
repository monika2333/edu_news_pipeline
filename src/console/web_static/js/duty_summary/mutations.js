// Duty Summary JS - Mutations
async function setManualReviewStatus(item, status, version = item.admin_version) {
    if (!version) {
        throw new Error('缺少撤回所需的记录版本，请刷新后重试');
    }
    const payload = {
        selected_ids: [],
        backup_ids: [],
        discarded_ids: [],
        pending_ids: [],
        versions: { [item.article_id]: version },
        report_type: item.admin_report_type === 'wanbao' ? 'wanbao' : 'zongbao'
    };
    payload[`${status}_ids`] = [item.article_id];
    return request('/api/manual_filter/decide', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
}

async function patchAdminDiscard(articleId, discarded) {
    return request('/api/admin/duty-summary/discard', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            shift_id: state.shiftId,
            article_id: articleId,
            discarded
        })
    });
}

function captureImportUndoTargets(articleIds) {
    const itemsById = new Map(state.items.map(item => [item.article_id, item]));
    const groups = new Map();
    articleIds.forEach(articleId => {
        const item = itemsById.get(articleId);
        const status = ['selected', 'backup', 'discarded'].includes(item?.admin_status)
            ? item.admin_status
            : 'pending';
        const reportType = item?.admin_report_type === 'wanbao' ? 'wanbao' : 'zongbao';
        const key = `${reportType}:${status}`;
        if (!groups.has(key)) groups.set(key, { reportType, status, articleIds: [] });
        groups.get(key).articleIds.push(articleId);
    });
    return [...groups.values()];
}

async function undoDutyImport(importedItems, undoTargets) {
    const versions = Object.fromEntries(
        (importedItems || []).map(item => [item.article_id, item.version])
    );
    for (const target of undoTargets) {
        const targetVersions = Object.fromEntries(
            target.articleIds.map(articleId => [articleId, versions[articleId]])
        );
        if (Object.values(targetVersions).some(version => !version)) {
            throw new Error('缺少撤销所需的记录版本，请刷新后重试');
        }
        const body = {
            selected_ids: [],
            backup_ids: [],
            discarded_ids: [],
            pending_ids: [],
            versions: targetVersions,
            report_type: target.reportType
        };
        body[`${target.status}_ids`] = target.articleIds;
        await request('/api/manual_filter/decide', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
    }
    showToast('已撤销操作');
    await Promise.all([loadSummary(), loadResults()]);
}

