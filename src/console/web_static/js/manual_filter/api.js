// Manual Filter JS - API Service
// Handles all network requests

const MANUAL_FILTER_API_BASE = '/api/manual_filter';

const api = {
    // --- Candidates (Filter Tab) ---
    async fetchCandidates(params = {}) {
        const query = new URLSearchParams(params);
        const res = await fetch(`${MANUAL_FILTER_API_BASE}/candidates?${query.toString()}`);
        if (!res.ok) throw new Error(`Fetch candidates failed: ${res.status}`);
        return await res.json();
    },

    // --- Stats & Counts ---
    async fetchFilterCounts(categories) {
        // Run parallel requests for each category to get counts
        // Note: This matches original logic which did individual fetches per category (limit=1)
        // Optimization: Could be one batch API in future, but keeping parity for now.
        const promises = categories.map(async (cat) => {
            const params = new URLSearchParams({ limit: '1', offset: '0', cluster: 'false' });
            if (cat.startsWith('internal')) params.set('region', 'internal');
            if (cat.startsWith('external')) params.set('region', 'external');
            if (cat.endsWith('positive')) params.set('sentiment', 'positive');
            if (cat.endsWith('negative')) params.set('sentiment', 'negative');

            try {
                const res = await fetch(`${MANUAL_FILTER_API_BASE}/candidates?${params.toString()}`);
                const data = await res.json();
                return { category: cat, total: data.total || 0 };
            } catch (e) {
                console.error(`Failed to fetch count for ${cat}`, e);
                return { category: cat, total: 0 };
            }
        });
        return await Promise.all(promises);
    },

    // --- Edits (Summary/Source) ---
    async postEdits(edits, actor, reportType) {
        if (!Object.keys(edits || {}).length) return;
        const payload = { edits, actor };
        if (reportType) payload.report_type = reportType;
        const res = await fetch(`${MANUAL_FILTER_API_BASE}/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!res.ok) throw new Error('Failed to save edits');
        return res;
    },

    // --- Decisions (Accept/Reject/Backup) ---
    async postDecisions(ids, status, actor, reportType) {
        const payload = {
            selected_ids: status === 'selected' ? ids : [],
            backup_ids: status === 'backup' ? ids : [],
            discarded_ids: status === 'discarded' ? ids : [],
            pending_ids: status === 'pending' ? ids : [],
            actor: actor
        };
        if (reportType) payload.report_type = reportType;

        const res = await fetch(`${MANUAL_FILTER_API_BASE}/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!res.ok) throw new Error('Failed to submit decisions');
        return res;
    },

    // --- Review Data ---
    async fetchReviewItems(reportType) {
        const params = new URLSearchParams({
            decision: 'selected',
            limit: '200',
            offset: '0'
        });
        if (reportType) params.set('report_type', reportType);
        const selectedRes = await fetch(`${MANUAL_FILTER_API_BASE}/review?${params.toString()}`);
        if (!selectedRes.ok) throw new Error(`Fetch review items failed: ${selectedRes.status}`);

        params.set('decision', 'backup');
        const backupRes = await fetch(`${MANUAL_FILTER_API_BASE}/review?${params.toString()}`);
        if (!backupRes.ok) throw new Error(`Fetch review items failed: ${backupRes.status}`);

        const selectedData = await selectedRes.json();
        const backupData = await backupRes.json();
        return {
            selected: selectedData.items || [],
            backup: backupData.items || []
        };
    },

    async postReviewOrder(payload) {
        // payload: { type: 'zongbao'|'wanbao', status: 'selected'|'backup', order: [id1, id2...] }
        const reportType = payload.report_type || payload.type;
        const status = payload.status === 'backup' ? 'backup' : 'selected';
        const body = {
            selected_order: status === 'selected' ? payload.order : [],
            backup_order: status === 'backup' ? payload.order : [],
            actor: payload.actor
        };
        if (reportType) body.report_type = reportType;
        const res = await fetch(`${MANUAL_FILTER_API_BASE}/order`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        if (!res.ok) throw new Error('Failed to save order');
        return res;
    }
};

// Expose globally or as module
window.manualFilterApi = api; 
