// Manual Filter JS - Export Modal

// --- Export Logic ---

async function openExportModal() {
    elements.modal.classList.add('active');
    await triggerExport(true);
}

function closeModal() {
    elements.modal.classList.remove('active');
}

function buildExportPayload(dryRun) {
    const tag = new Date().toISOString().split('T')[0];
    const templateValue = elements.exportTemplate ? elements.exportTemplate.value : 'zongbao';
    const payload = {
        report_tag: tag,
        template: templateValue,
        period: undefined,
        total_period: undefined,
        dry_run: dryRun,
        mark_exported: !dryRun,
        report_type: templateValue === 'wanbao' ? 'wanbao' : 'zongbao',
    };
    if (elements.exportPeriod && elements.exportPeriod.value) {
        const val = Number(elements.exportPeriod.value);
        if (!Number.isNaN(val)) payload.period = val;
    }
    if (elements.exportTotal && elements.exportTotal.value) {
        const val = Number(elements.exportTotal.value);
        if (!Number.isNaN(val)) payload.total_period = val;
    }
    return payload;
}

async function triggerExport(dryRun = true) {
    const payload = buildExportPayload(dryRun);
    try {
        const res = await fetch(`${API_BASE}/export`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        if (elements.exportPeriod && result.period !== undefined) {
            elements.exportPeriod.value = result.period;
        }
        if (elements.exportTotal && result.total_period !== undefined) {
            elements.exportTotal.value = result.total_period;
        }
        if (elements.modalText) {
            elements.modalText.value = result.content || 'No content generated';
        }
        const toastMsg = dryRun ? '已生成预览' : `已导出${result.count || 0} 条`;
        showToast(toastMsg);
    } catch (e) {
        showToast(dryRun ? '预览失败' : '导出失败', 'error');
    }
}

async function copyPreviewText() {
    if (!elements.modalText) return;
    const text = elements.modalText.value || '';
    if (!text) return;
    try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
        } else {
            elements.modalText.select();
            document.execCommand('copy');
        }
        showToast('已复制到剪贴板');
    } catch (err) {
        showToast('复制失败，请手动复制', 'error');
    }
}

async function refreshPreviewAndCopy() {
    await triggerExport(true);
    await copyPreviewText();
}

async function confirmExportAndCopy() {
    await triggerExport(false);
    await copyPreviewText();
    if (state.currentTab === 'review') {
        loadReviewData();
    }
}