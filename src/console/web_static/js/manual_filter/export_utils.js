// Manual Filter JS - Export Utils
// Handles export text generation and formatting

const exportUtils = {
    // Helper to Convert Number to Chinese
    toChineseNum(num) {
        const chineseNums = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九', '十'];
        if (num <= 10) return chineseNums[num];
        return num.toString();
    },

    // Main Preview Generation Logic
    // logic extracted from review_tab.js `generatePreviewText`
    generatePreviewText(state) {
        const reportType = state.reviewReportType || 'zongbao';
        const isZongbao = reportType === 'zongbao';
        const data = state.reviewData; // { selected: [], backup: [] }

        // Gather all items
        // Important: Original code iterated existing DOM elements to get order.
        // A pure state-based approach is better, but to preserve "WYSIWYG" (drag order),
        // we might need to rely on the `state.reviewData` being up-to-date with order 
        // OR rely on the passed-in list being sorted.
        // Assumption: `state.reviewData.selected` and `state.reviewData.backup` ARE the sorted lists.
        // The `review_tab.js` syncs logic updates this state on drag end.

        // However, the original code had complex logic to filter by group keys (internal_positive etc).
        // Let's implement that categorization here.

        const categorize = (items) => {
            const buckets = {
                internal_positive: [],
                internal_negative: [],
                external_positive: [],
                external_negative: []
            };
            items.forEach(item => {
                const isInternal = !!item.is_beijing_related;
                const sentiment = (item.sentiment_label || '').toLowerCase() === 'negative' ? 'negative' : 'positive';
                let key = '';
                if (isInternal && sentiment === 'positive') key = 'internal_positive';
                else if (isInternal && sentiment === 'negative') key = 'internal_negative';
                else if (!isInternal && sentiment === 'positive') key = 'external_positive';
                else key = 'external_negative';
                buckets[key].push(item);
            });
            return buckets;
        };

        const selectedBuckets = categorize(data.selected || []);
        const backupBuckets = categorize(data.backup || []);

        let text = '';

        // Helper to format a single item
        const formatItem = (item, index, includeSource = true) => {
            const numStr = index + 1; // 1, 2, 3... 
            // Original code used `toChineseNum` for top-level sections maybe? 
            // Use 1. 2. 3. for items.
            let line = `${numStr}. ${item.summary || item.title}`;
            if (includeSource) {
                // If LLM source is edited, use it. But usually we display what's in text box.
                // Here we fallback to item properties.
                const source = item.llm_source_display || item.llm_source_raw || item.source;
                if (source) line += `（${source}）`;
            }
            return line;
        };

        // Construct the Report
        // Structure matches `generatePreviewText` in existing review_tab.js

        if (isZongbao) {
            // --- Part 1: Beijing Positive ---
            if (selectedBuckets.internal_positive.length) {
                text += `一、北京教育\n`;
                selectedBuckets.internal_positive.forEach((item, idx) => {
                    text += `${formatItem(item, idx)}\n`;
                });
                text += `\n`;
            }

            // --- Part 2: National Positive ---
            if (selectedBuckets.external_positive.length) {
                text += `二、全国教育\n`;
                selectedBuckets.external_positive.forEach((item, idx) => {
                    text += `${formatItem(item, idx)}\n`;
                });
                text += `\n`;
            }

            // --- Part 3: Negative (Internal + External) ---
            const allNegative = [...selectedBuckets.internal_negative, ...selectedBuckets.external_negative];
            if (allNegative.length) {
                text += `三、负面/敏感舆情\n`;
                allNegative.forEach((item, idx) => {
                    text += `${formatItem(item, idx)}\n`;
                });
                text += `\n`; // Is this needed?
            }
        } else {
            // Wanbao (Evening Report) - usually simpler structure or different
            // Original code logic:
            // "【今日主要关注】"
            // Then internal positive
            // Then external positive
            // Then negative

            text += `【今日主要关注】\n`;

            let count = 0;
            const groups = [
                { items: selectedBuckets.internal_positive, label: 'Beijing' },
                { items: selectedBuckets.external_positive, label: 'National' },
                { items: selectedBuckets.internal_negative, label: 'Negative' },
                { items: selectedBuckets.external_negative, label: 'Ext Negative' }
            ];

            groups.forEach(g => {
                g.items.forEach(item => {
                    count++;
                    text += `${formatItem(item, count - 1)}\n`; // 0-indexed passed to formatItem? No wait, formatItem uses index+1
                });
            });
        }

        // --- Backup Section ---
        const allBackup = [
            ...backupBuckets.internal_positive,
            ...backupBuckets.external_positive,
            ...backupBuckets.internal_negative,
            ...backupBuckets.external_negative
        ];

        if (allBackup.length) {
            text += `\n【备选】\n`;
            allBackup.forEach((item, idx) => {
                text += `${formatItem(item, idx)}\n`;
            });
        }

        return text;
    },

    // Copy to clipboard helper
    async copyToClipboard(text) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (err) {
            console.error('Copy failed', err);
            return false;
        }
    }
};

window.manualFilterExport = exportUtils;
