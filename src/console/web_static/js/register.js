(() => {
    const form = document.getElementById('register-form');
    const errorBox = document.getElementById('register-error');
    const submitButton = document.getElementById('register-submit');

    form.addEventListener('submit', async event => {
        event.preventDefault();
        errorBox.hidden = true;
        const formData = new FormData(form);
        const password = String(formData.get('password') || '');
        const confirmation = String(formData.get('password_confirm') || '');
        if (password !== confirmation) {
            errorBox.textContent = '两次输入的密码不一致';
            errorBox.hidden = false;
            return;
        }

        const username = String(formData.get('username') || '').trim();
        submitButton.disabled = true;
        try {
            const response = await fetch('/api/auth/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username,
                    display_name: String(formData.get('display_name') || '').trim(),
                    password,
                    preferred_weekday: Number(formData.get('preferred_weekday'))
                })
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(formatApiError(payload, '注册失败，请稍后重试'));
            window.location.replace(
                `/login?registered=1&username=${encodeURIComponent(username)}`
            );
        } catch (error) {
            errorBox.textContent = error.message || '注册失败，请稍后重试';
            errorBox.hidden = false;
            submitButton.disabled = false;
        }
    });
})();
