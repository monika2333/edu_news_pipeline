// Submission Archive JS - Export Modal (报送存档导出)
//
// 页面级导出动作：管理员选日期区间与报别，预览计数确认后下载 CSV。
// 预览（/export/preview）只是便利功能不是门禁：请求失败时提示区留空、
// 导出按钮保持可用，真超限由导出接口的 422 走错误提示。
// 上限数值只读接口返回的 max_rows，不在前端写死——那是后端的业务规则，
// 前端复制一份必然漂移。
// 启动逻辑在 init.js（本目录约定），本文件不自启动。

const archiveExportState = {
    open: false,
    busy: false,
    previewTimer: null
};

const ARCHIVE_EXPORT_PREVIEW_DEBOUNCE_MS = 300;

function getArchiveExportEls() {
    return {
        modal: document.getElementById('archive-export-modal'),
        closeBtn: document.getElementById('archive-export-modal-close'),
        dateFrom: document.getElementById('archive-export-date-from'),
        dateTo: document.getElementById('archive-export-date-to'),
        types: document.getElementById('archive-export-types'),
        hint: document.getElementById('archive-export-hint'),
        cancelBtn: document.getElementById('archive-export-cancel'),
        exportBtn: document.getElementById('archive-export-submit')
    };
}

// 报别复选框按 core.js 的 typeLabels 渲染：value 用后端枚举，显示文字复用同一份映射
function renderArchiveExportTypes(container) {
    container.innerHTML = Object.entries(typeLabels).map(([value, label]) => `
        <label class="archive-export-type-option">
            <input type="checkbox" value="${escapeHtml(value)}" checked>
            <span>${escapeHtml(label)}</span>
        </label>
    `).join('');
}

function selectedArchiveExportTypes() {
    return Array.from(
        getArchiveExportEls().types.querySelectorAll('input[type="checkbox"]:checked')
    ).map(input => input.value);
}

function buildArchiveExportParams() {
    const els = getArchiveExportEls();
    const params = new URLSearchParams();
    if (els.dateFrom.value) params.set('date_from', els.dateFrom.value);
    if (els.dateTo.value) params.set('date_to', els.dateTo.value);
    selectedArchiveExportTypes().forEach(type => params.append('report_types', type));
    return params;
}

async function refreshArchiveExportPreview() {
    const els = getArchiveExportEls();
    // 一个报别都没勾时不发请求，本地直接进入禁用态
    if (!selectedArchiveExportTypes().length) {
        els.hint.textContent = '请至少选择一个报别';
        els.exportBtn.disabled = true;
        return;
    }
    try {
        const data = await api(`/export/preview?${buildArchiveExportParams().toString()}`);
        if (!archiveExportState.open) return;
        const itemCount = Number(data.item_count) || 0;
        const maxRows = Number(data.max_rows) || 0;
        if (itemCount === 0) {
            els.hint.textContent = '所选范围内没有条目';
            els.exportBtn.disabled = true;
        } else if (maxRows > 0 && itemCount > maxRows) {
            els.hint.textContent = `超过单次导出上限 ${maxRows} 条，请收窄日期范围`;
            els.exportBtn.disabled = true;
        } else {
            els.hint.textContent = `将导出 ${Number(data.report_count) || 0} 份报告 · ${itemCount} 个条目`;
            els.exportBtn.disabled = archiveExportState.busy;
        }
    } catch (error) {
        if (!archiveExportState.open) return;
        // 预览失败不阻断导出：提示区留空、按钮保持可用，真超限由后端 422 兜底
        els.hint.textContent = '';
        els.exportBtn.disabled = archiveExportState.busy;
    }
}

// 日期输入约 300ms 防抖，避免敲日期时每按一个数字发一次请求
function scheduleArchiveExportPreview() {
    if (archiveExportState.previewTimer) {
        window.clearTimeout(archiveExportState.previewTimer);
    }
    archiveExportState.previewTimer = window.setTimeout(() => {
        archiveExportState.previewTimer = null;
        refreshArchiveExportPreview();
    }, ARCHIVE_EXPORT_PREVIEW_DEBOUNCE_MS);
}

// 与后端 submission_archive_export.build_export_filenames 的规则保持一致：
// 起止都留空是「全部」，否则按 YYYYMMDD 拼接区间；两边必须一致
function archiveExportFallbackFilename() {
    const els = getArchiveExportEls();
    const compact = value => String(value || '').replace(/-/g, '');
    const start = compact(els.dateFrom.value);
    const end = compact(els.dateTo.value);
    if (!start && !end) return '报送存档_全部.csv';
    return `报送存档_${start}-${end}.csv`;
}

// 文件名优先取 Content-Disposition 的 filename*=UTF-8'' 部分并做百分号解码；
// 解析失败（无头部或解码失败）按后端约定规则本地兜底拼一个
function archiveExportFilename(response) {
    const disposition = response.headers.get('Content-Disposition') || '';
    const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match) {
        try {
            return decodeURIComponent(utf8Match[1]);
        } catch (error) {
            // 解码失败走本地兜底
        }
    }
    return archiveExportFallbackFilename();
}

// 不用 api()（它会 response.json() 拿不到二进制），也不用 window.location /
// <a href> 直指接口：出错时接口返回 JSON，直接导航会把 JSON 甩到浏览器窗口、
// 把用户从存档页面冲走。错误提示仍走 toast()，与页面其他部分一致
async function runArchiveExport() {
    const els = getArchiveExportEls();
    if (archiveExportState.busy) return;
    archiveExportState.busy = true;
    els.exportBtn.disabled = true;
    try {
        const response = await window.fetch(
            `/api/submission-archive/export?${buildArchiveExportParams().toString()}`
        );
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            toast(formatApiError(payload, '导出失败，请稍后重试'), 'error');
            return;
        }
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = archiveExportFilename(response);
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(objectUrl);
    } catch (error) {
        toast('导出失败，请稍后重试', 'error');
    } finally {
        archiveExportState.busy = false;
        els.exportBtn.disabled = false;
    }
}

function openArchiveExportModal() {
    const els = getArchiveExportEls();
    if (!els.modal) return;
    archiveExportState.open = true;
    els.modal.classList.add('active');
    els.modal.setAttribute('aria-hidden', 'false');
    els.exportBtn.disabled = false;
    refreshArchiveExportPreview();
}

function closeArchiveExportModal() {
    const els = getArchiveExportEls();
    if (!els.modal) return;
    archiveExportState.open = false;
    if (archiveExportState.previewTimer) {
        window.clearTimeout(archiveExportState.previewTimer);
        archiveExportState.previewTimer = null;
    }
    els.modal.classList.remove('active');
    els.modal.setAttribute('aria-hidden', 'true');
}

function initExportModal() {
    const els = getArchiveExportEls();
    if (!els.modal) return;
    renderArchiveExportTypes(els.types);
    document.getElementById('archive-export-open')
        ?.addEventListener('click', openArchiveExportModal);
    els.closeBtn.addEventListener('click', closeArchiveExportModal);
    els.cancelBtn.addEventListener('click', closeArchiveExportModal);
    els.exportBtn.addEventListener('click', runArchiveExport);
    els.modal.addEventListener('click', event => {
        if (event.target === els.modal) closeArchiveExportModal();
    });
    [els.dateFrom, els.dateTo].forEach(input => {
        input.addEventListener('input', scheduleArchiveExportPreview);
    });
    els.types.addEventListener('change', event => {
        if (event.target.matches('input[type="checkbox"]')) refreshArchiveExportPreview();
    });
    // 冒泡阶段注册，只在本弹窗打开时处理，处理后阻止继续传播。
    // 本弹窗没有打开原文抽屉的入口，与抽屉不叠加，不参与 manual_link.js /
    // prior_matches.js 的注册顺序约定；但也不能用捕获阶段注册，会打断既有处理链
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || !archiveExportState.open) return;
        closeArchiveExportModal();
        event.stopPropagation();
    });
}
