// Shared API error formatting helper.
// FastAPI puts error information in the response body's `detail` field in
// three shapes: a plain string (HTTPException), an array of validation
// error objects (422, each with loc/msg/type), or occasionally an object
// with a readable message field. formatApiError() normalizes all of them
// into a single displayable string. It must never throw and must never
// return "[object Object]". Loaded as a classic script; the helper is
// exposed on window so a duplicate <script> include cannot break the page.

(function () {
    const LOC_NOISE_SEGMENTS = ['body', 'query', 'path', 'header', 'headers', 'cookie', 'cookies'];

    function formatLocField(loc) {
        // loc may be nested, e.g. ["body", "edits", "<article_id>", "llm_source"];
        // keep only the meaningful tail segment instead of dumping the whole path.
        if (!Array.isArray(loc)) return '';
        const segments = loc.filter(
            seg => typeof seg === 'string' && seg && !LOC_NOISE_SEGMENTS.includes(seg)
        );
        return segments.length ? segments[segments.length - 1] : '';
    }

    function formatValidationItem(item) {
        if (!item || typeof item !== 'object') return '';
        const field = formatLocField(item.loc);
        const type = typeof item.type === 'string' ? item.type : '';
        const ctx = item.ctx && typeof item.ctx === 'object' ? item.ctx : {};
        let message;
        if (type === 'missing') {
            message = '该字段为必填项';
        } else if (type === 'string_too_long' || type === 'too_long') {
            message = Number.isFinite(ctx.max_length)
                ? `长度超出限制（最多 ${ctx.max_length} 字符）`
                : '长度超出限制';
        } else if (type === 'string_too_short' || type === 'too_short') {
            message = Number.isFinite(ctx.min_length)
                ? `长度不足（至少 ${ctx.min_length} 字符）`
                : '长度不足';
        } else if (type.endsWith('_type') || type.endsWith('_parsing')) {
            message = '类型不正确';
        } else {
            // Uncovered types fall back to the raw msg; no full i18n mapping here.
            message = typeof item.msg === 'string' ? item.msg : '';
        }
        if (!message) return '';
        return field ? `字段 ${field}：${message}` : message;
    }

    function formatApiError(payload, fallbackMessage) {
        try {
            const detail = payload && typeof payload === 'object' ? payload.detail : null;
            if (typeof detail === 'string' && detail.trim()) return detail;
            if (Array.isArray(detail)) {
                // Text-level dedup: identical items (e.g. the same field
                // failing on two articles in one request) collapse into one.
                const texts = detail.map(formatValidationItem).filter(Boolean);
                const text = Array.from(new Set(texts)).join('；');
                if (text) return text;
            } else if (detail && typeof detail === 'object') {
                const candidates = [detail.message, detail.msg, detail.error];
                const readable = candidates.find(value => typeof value === 'string' && value.trim());
                if (readable) return readable;
            }
        } catch (ignored) {
            // fall through to the fallback message
        }
        return fallbackMessage;
    }

    window.formatApiError = formatApiError;
})();
