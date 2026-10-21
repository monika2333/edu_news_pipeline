// Manual Filter JS - Review Tab

// --- Review Tab Preview ---

function toChineseNum(num) {
    const chineseNums = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九'];
    if (num < 10) return chineseNums[num];
    if (num < 20) return '十' + (num % 10 !== 0 ? chineseNums[num % 10] : '');
    if (num < 100) {
        const ten = Math.floor(num / 10);
        const unit = num % 10;
        return chineseNums[ten] + '十' + (unit !== 0 ? chineseNums[unit] : '');
    }
    return num.toString();
}

function generatePreviewText() {
    const reportType = state.reviewReportType;
    const isWanbao = reportType === 'wanbao';
    const view = state.reviewView || 'selected';
    const items = state.reviewData[view] || [];

    if (!items.length) return '';

    // Grouping
    const groups = {
        'internal_negative': [],
        'internal_positive': [],
        'external_negative': [],
        'external_positive': []
    };

    items.forEach(item => {
        const key = resolveGroupKey(item);
        if (groups[key]) groups[key].push(item);
        else {
            if (!groups['other']) groups['other'] = [];
            groups['other'].push(item);
        }
    });

    let content = '';

    const sections = [];
    if (isWanbao) {
        // Wanbao: Internal (Pos+Neg) -> 【舆情速览】, External (Pos+Neg) -> 【舆情参考】
        sections.push({
            label: '【舆情速览】',
            items: [...groups['internal_positive'], ...groups['internal_negative']],
            numbered: true
        });
        sections.push({
            label: '【舆情参考】',
            items: [...groups['external_positive'], ...groups['external_negative']],
            numbered: true
        });
    } else {
        // Zongbao
        // 1. Internal Negative -> 【重点关注舆情】
        sections.push({
            label: '【重点关注舆情】',
            items: groups['internal_negative'],
            marker: '★ '
        });
        // 2. Internal Positive + External Positive -> 【新闻信息纵览】
        const mergedPositive = [...groups['internal_positive'], ...groups['external_positive']];
        sections.push({
            label: '【新闻信息纵览】',
            items: mergedPositive,
            marker: '■ '
        });
        // 3. External Negative -> 【国内教育热点】
        sections.push({
            label: '【国内教育热点】',
            items: groups['external_negative'],
            marker: '▲ '
        });
    }

    sections.forEach(section => {
        const sectionItems = section.items || [];
        if (!sectionItems.length) return;

        content += `${section.label}\n`;
        sectionItems.forEach((item, index) => {
            const title = (item.title || '').trim();
            // Use manual_summary if available, else llm_summary, else summary
            // Assuming order: manual > llm > raw. The backend usually normalizes this into 'summary' but we check properties.
            const summary = (item.manual_summary || item.summary || '').trim();
            const source = (item.llm_source_display || item.source || '').trim();

            let prefix = '';
            if (section.marker) {
                prefix = `${section.marker}`;
            } else if (section.numbered) {
                prefix = `${toChineseNum(index + 1)}、`;
            }

            content += `${prefix}${title}\n`;
            if (summary) content += `${summary}`;
            if (source) content += `（${source}）`;
            content += '\n\n'; // Empty line between items for readability? User example shows distinct blocks.
        });
    });

    return content.trim(); // Clean up trailing newlines
}

function syncReviewDomEditsToState() {
    const scope = getActiveReviewContainer();
    if (!scope) return;
    const cards = scope.querySelectorAll('.article-card');
    cards.forEach(card => {
        const articleId = card.dataset.id;
        if (!articleId) return;

        const summaryBox = card.querySelector('.summary-box');
        const sourceBox = card.querySelector('.source-box');
        if (!summaryBox && !sourceBox) return;

        applyReviewEditsToState(
            articleId,
            summaryBox ? summaryBox.value : undefined,
            sourceBox ? sourceBox.value : undefined
        );
    });
}

async function handlePreviewCopy() {
    try {
        syncReviewDomEditsToState();
        const text = generatePreviewText();

        if (!text) {
            showToast('当前列表为空，无内容可生成', 'error');
            return;
        }

        const modal = document.getElementById('preview-modal');
        const textarea = document.getElementById('preview-text');
        if (modal && textarea) {
            previewBodyText = text;
            await loadPreviewHeader();
            renderPreviewHeader();
            modal.classList.add('active');
        } else {
            // Fallback if modal not present
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(text);
            }
            showToast('已复制到剪贴板(弹窗未找到)');
        }
    } catch (e) {
        console.error(e);
        showToast('预览生成失败', 'error');
    }
}

// --- Preview Header (标题 / 期号 / 日期) ---
// 期号由后端按报送存档推算（规则见 src/console/issue_sequence.py）：前端只负责
// 拼装，不参与任何期号计算，避免两边各算一套后互相漂移。
// 题头不是独立区块——它直接是预览文本（textarea）的开头三行，与正文同字体。

const PREVIEW_HEADER_STORAGE_KEY = 'manual_filter_preview_header';

let previewHeaderData = null;
let previewBodyText = '';

function readPreviewHeaderEnabled() {
    try {
        return localStorage.getItem(PREVIEW_HEADER_STORAGE_KEY) !== 'false';
    } catch (error) {
        // 隐私模式下 localStorage 不可读，按默认「包含题头」处理
        return true;
    }
}

function storePreviewHeaderEnabled(enabled) {
    try {
        localStorage.setItem(
            PREVIEW_HEADER_STORAGE_KEY,
            enabled ? 'true' : 'false'
        );
    } catch (error) {
        // 写不进去不影响本次预览，只是下次打开回到默认
    }
}

function previewHeaderUsable() {
    return Boolean(
        previewHeaderData
        && previewHeaderData.available
        && (state.reviewView || 'selected') === 'selected'
    );
}

function getPreviewHeaderLines() {
    if (!previewHeaderUsable() || !readPreviewHeaderEnabled()) return [];
    return Array.isArray(previewHeaderData.lines) ? previewHeaderData.lines : [];
}

function renderPreviewHeader() {
    const toggle = document.getElementById('preview-header-toggle');
    const notices = document.getElementById('preview-doc-notices');
    const textarea = document.getElementById('preview-text');
    if (!toggle || !notices || !textarea) return;

    const selectionOnly = (state.reviewView || 'selected') === 'selected';
    const usable = previewHeaderUsable();
    const enabled = usable && readPreviewHeaderEnabled();

    toggle.hidden = !usable;
    const checkbox = document.getElementById('preview-include-header');
    if (checkbox) checkbox.checked = enabled;

    // 题头就是正文文本的前三行：关掉开关只是不拼进文本，不另做一套展示
    const header = getPreviewHeaderLines();
    textarea.value = header.length
        ? `${header.join('\n')}\n\n${previewBodyText}`
        : previewBodyText;

    // 提示只在「正在显示题头」时出现；基准缺失是例外——那时没有题头可显示，
    // 但仍然要告诉用户为什么没有。
    let messages = [];
    if (selectionOnly && previewHeaderData) {
        const headerVisible = previewHeaderData.available && enabled;
        const missingBaseline = !previewHeaderData.available;
        if (headerVisible || missingBaseline) {
            messages = Array.isArray(previewHeaderData.notices)
                ? previewHeaderData.notices
                : [];
        }
    }
    notices.textContent = messages.join('；');
    notices.hidden = messages.length === 0;
}

async function loadPreviewHeader() {
    if ((state.reviewView || 'selected') !== 'selected') {
        previewHeaderData = null;
        return;
    }
    const reportType = state.reviewReportType;
    try {
        const response = await workspaceFetch(
            `${API_BASE}/preview-header?report_type=${encodeURIComponent(reportType)}`
        );
        if (!response.ok) throw new Error('题头加载失败');
        previewHeaderData = await response.json();
    } catch (error) {
        // 题头是增强信息，取不到就不显示，绝不能因此让预览失败
        previewHeaderData = null;
    }
}

function handlePreviewHeaderToggle(event) {
    storePreviewHeaderEnabled(Boolean(event.target.checked));
    renderPreviewHeader();
}

function buildPreviewCopyText() {
    // 预览文本本身就是待复制内容（题头已在里面），不再二次拼装
    const textarea = document.getElementById('preview-text');
    return textarea ? textarea.value : '';
}

function closePreviewModal() {
    const modal = document.getElementById('preview-modal');
    if (modal) modal.classList.remove('active');
}

// Escape 关闭预览弹窗。
// 用捕获阶段注册并由本处截断：预览弹窗盖在所有浮层（含原文抽屉）之上，
// 抽屉的 Escape 处理器在 document 冒泡阶段注册得更早，不拦下会先把抽屉关掉。
document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const modal = document.getElementById('preview-modal');
    if (!modal || !modal.classList.contains('active')) return;
    event.preventDefault();
    event.stopPropagation();
    closePreviewModal();
}, true);
