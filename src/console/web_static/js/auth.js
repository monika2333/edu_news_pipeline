(() => {
    const originalFetch = window.fetch.bind(window);

    function readCookie(name) {
        const prefix = `${encodeURIComponent(name)}=`;
        const entry = document.cookie
            .split(';')
            .map(value => value.trim())
            .find(value => value.startsWith(prefix));
        return entry ? decodeURIComponent(entry.slice(prefix.length)) : '';
    }

    window.fetch = async (input, init = {}) => {
        const request = input instanceof Request ? input : null;
        const url = new URL(request ? request.url : String(input), window.location.href);
        const method = String(init.method || request?.method || 'GET').toUpperCase();
        const nextInit = { ...init };

        if (url.origin === window.location.origin && !['GET', 'HEAD', 'OPTIONS', 'TRACE'].includes(method)) {
            const csrfToken = readCookie('console_csrf');
            if (csrfToken) {
                const headers = new Headers(request?.headers || undefined);
                new Headers(init.headers || undefined).forEach((value, key) => headers.set(key, value));
                headers.set('X-CSRF-Token', csrfToken);
                nextInit.headers = headers;
            }
        }

        const response = await originalFetch(input, nextInit);
        if (response.status === 401) {
            const next = encodeURIComponent(`${window.location.pathname}${window.location.search}`);
            window.location.assign(`/login?next=${next}`);
        }
        return response;
    };

    document.addEventListener('DOMContentLoaded', () => {
        const accountMenus = [...document.querySelectorAll('details.account-menu')];
        accountMenus.forEach(menu => {
            menu.addEventListener('toggle', () => {
                if (!menu.open) return;
                accountMenus.forEach(other => {
                    if (other !== menu) other.open = false;
                });
            });
        });
        document.addEventListener('click', event => {
            accountMenus.forEach(menu => {
                if (menu.open && !menu.contains(event.target)) menu.open = false;
            });
        });
        document.addEventListener('keydown', event => {
            if (event.key !== 'Escape') return;
            accountMenus.forEach(menu => {
                if (!menu.open) return;
                menu.open = false;
                menu.querySelector('summary')?.focus();
            });
        });

        const logoutButton = document.getElementById('btn-logout');
        if (!logoutButton) return;
        logoutButton.addEventListener('click', async () => {
            logoutButton.disabled = true;
            try {
                await window.fetch('/api/auth/logout', { method: 'POST' });
            } finally {
                window.location.replace('/login');
            }
        });
    });
})();
