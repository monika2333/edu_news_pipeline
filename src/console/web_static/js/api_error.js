// Shared API error formatting helper.
// FastAPI puts error information in the response body's `detail` field in
// three shapes: a plain string (HTTPException), an array of validation
// error objects (422, each with loc/msg/type), or occasionally an object
// with a readable message field. formatApiError() normalizes all of them
// into a single displayable string. It must never throw and must never
// return "[object Object]".

const API_ERROR_LOC_NOISE_SEGMENTS = ['body', 'query', 'path', 'header', 'headers', 'cookie', 'cookies'];

function formatApiErrorLocField(loc) {
    // loc may be nested, e.g. ["body", "edits", "<article_id>", "llm_source"];
    // keep only the meaningful tail segment instead of dumping the whole path.
    if (!Array.isArray(loc)) return '';
    const segments = loc.filter(
        seg => typeof seg === 'string' && seg && !API_ERROR_LOC_NOISE_SEGMENTS.includes(seg)
    );
    return segments.length ? segments[segments.length - 1] : '';
}

function formatApiErrorValidationItem(item) {
    if (!item || typeof item !== 'object') return '';
    const field = formatApiErrorLocField(item.loc);
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
            const text = detail.map(formatApiErrorValidationItem).filter(Boolean).join('；');
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
