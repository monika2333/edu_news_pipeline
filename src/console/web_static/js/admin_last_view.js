(() => {
    const script = document.currentScript;
    if (!script) return;

    const allowedViews = new Set([
        '/admin/duty-summary',
        '/manual_filter',
        '/admin/review',
        '/submission-archive'
    ]);
    const userId = script.dataset.adminViewUser;
    const storageKey = userId ? `admin_last_view:${userId}` : '';
    const currentView = script.dataset.adminViewCurrent;
    const defaultView = script.dataset.adminViewDefault;

    if (currentView && allowedViews.has(currentView)) {
        if (!storageKey) return;
        try {
            localStorage.setItem(storageKey, currentView);
        } catch (error) {
            // localStorage 不可用时只放弃记忆，不影响当前页面。
        }
        return;
    }

    if (!defaultView || !allowedViews.has(defaultView)) return;

    let target = defaultView;
    if (storageKey) {
        try {
            const savedView = localStorage.getItem(storageKey);
            if (savedView && allowedViews.has(savedView)) {
                target = savedView;
            }
        } catch (error) {
            // localStorage 不可用时沿用服务端给出的默认页面。
        }
    }
    window.location.replace(target);
})();
