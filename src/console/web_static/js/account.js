(() => {
    const form = document.getElementById('change-password-form');
    const successBox = document.getElementById('password-success');
    const errorBox = document.getElementById('password-error');
    const submitButton = document.getElementById('password-submit');

    form.addEventListener('submit', async event => {
        event.preventDefault();
        successBox.hidden = true;
        errorBox.hidden = true;
        const formData = new FormData(form);
        const newPassword = String(formData.get('new_password') || '');
        const confirmation = String(formData.get('new_password_confirm') || '');
        if (newPassword !== confirmation) {
            errorBox.textContent = '两次输入的新密码不一致';
            errorBox.hidden = false;
            return;
        }

        submitButton.disabled = true;
        try {
            const response = await fetch('/api/me/change-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    current_password: String(formData.get('current_password') || ''),
                    new_password: newPassword
                })
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(formatApiError(payload, '密码修改失败，请稍后重试'));
            form.reset();
            successBox.textContent = '密码已更新，当前设备保持登录。';
            successBox.hidden = false;
        } catch (error) {
            errorBox.textContent = error.message || '密码修改失败，请稍后重试';
            errorBox.hidden = false;
        } finally {
            submitButton.disabled = false;
        }
    });
})();
