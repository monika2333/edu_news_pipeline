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
        content += '\n'; // Separator between sections
    });

    return content.trim(); // Clean up trailing newlines
}

async function handlePreviewCopy() {
    try {
        const text = generatePreviewText();

        if (!text) {
            showToast('当前列表为空，无内容可生成', 'error');
            return;
        }

        const modal = document.getElementById('preview-modal');
        const textarea = document.getElementById('preview-text');
        if (modal && textarea) {
            textarea.value = text;
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
