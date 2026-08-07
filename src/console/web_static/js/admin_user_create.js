(() => {
    const form = document.getElementById('admin-create-user-form');
    const errorBox = document.getElementById('admin-create-user-error');
    const submitButton = document.getElementById('admin-create-user-submit');

    form.addEventListener('submit', async event => {
        event.preventDefault();
        errorBox.hidden = true;
        const formData = new FormData(form);
        const weekday = String(formData.get('preferred_weekday') || '');
        submitButton.disabled = true;
        try {
            const response = await fetch('/api/admin/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username: String(formData.get('username') || '').trim(),
                    display_name: String(
                        formData.get('display_name') || ''
                    ).trim(),
                    password: String(formData.get('password') || ''),
                    role: String(formData.get('role') || 'duty_editor'),
                    preferred_weekday: weekday === '' ? null : Number(weekday)
                })
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(formatApiError(payload, '账号创建失败，请稍后重试'));
            window.location.assign('/admin?created=1');
        } catch (error) {
            errorBox.textContent = error.message || '账号创建失败，请稍后重试';
            errorBox.hidden = false;
            submitButton.disabled = false;
        }
    });
})();
