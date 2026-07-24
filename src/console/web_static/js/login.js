(() => {
    const form = document.getElementById('login-form');
    const errorBox = document.getElementById('login-error');
    const submitButton = document.getElementById('login-submit');

    function safeNextPath() {
        const value = new URLSearchParams(window.location.search).get('next');
        if (!value || !value.startsWith('/') || value.startsWith('//')) return '/';
        return value;
    }

    form.addEventListener('submit', async event => {
        event.preventDefault();
        errorBox.hidden = true;
        submitButton.disabled = true;

        const formData = new FormData(form);
        try {
            const response = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username: String(formData.get('username') || '').trim(),
                    password: String(formData.get('password') || '')
                })
            });
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                throw new Error(payload.detail || '登录失败');
            }
            window.location.replace(safeNextPath());
        } catch (error) {
            errorBox.textContent = error.message || '登录失败，请稍后重试';
            errorBox.hidden = false;
            submitButton.disabled = false;
        }
    });
})();
