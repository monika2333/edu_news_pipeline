(() => {
    const weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
    const state = {
        users: [],
        editors: [],
        schedule: [],
        shifts: []
    };
    const elements = {
        usersBody: document.getElementById('users-body'),
        scheduleGrid: document.getElementById('schedule-grid'),
        shiftsBody: document.getElementById('shifts-body'),
        alert: document.getElementById('admin-alert'),
        toast: document.getElementById('toast')
    };

    function escapeHtml(value) {
        const node = document.createElement('div');
        node.textContent = String(value ?? '');
        return node.innerHTML;
    }

    function formatDateTime(value) {
        if (!value) return '—';
        return new Intl.DateTimeFormat('zh-CN', {
            month: 'numeric',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        }).format(new Date(value));
    }

    function showToast(message) {
        elements.toast.textContent = message;
        elements.toast.classList.add('show');
        window.setTimeout(() => elements.toast.classList.remove('show'), 1800);
    }

    async function request(path, options) {
        const response = await fetch(path, options);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.detail || '请求失败');
        return payload;
    }

    function editorOptions(selectedId) {
        return state.editors.map(editor => (
            `<option value="${escapeHtml(editor.id)}" ${editor.id === selectedId ? 'selected' : ''}>${escapeHtml(editor.display_name)}</option>`
        )).join('');
    }

    function renderUsers() {
        elements.usersBody.innerHTML = state.users.map(user => `
            <tr data-user-id="${escapeHtml(user.id)}">
                <td>${escapeHtml(user.display_name)}</td>
                <td>${escapeHtml(user.username)}</td>
                <td>${user.role === 'admin' ? '管理员' : '值班编辑'}</td>
                <td><span class="status-pill ${user.is_active ? '' : 'is-inactive'}">${user.is_active ? '启用' : '停用'}</span></td>
                <td>${escapeHtml(formatDateTime(user.last_login_at))}</td>
                <td><div class="admin-actions">
                    <button class="btn btn-secondary" data-user-action="toggle">${user.is_active ? '停用' : '启用'}</button>
                    <button class="btn btn-secondary" data-user-action="password">重置密码</button>
                </div></td>
            </tr>
        `).join('');
        elements.usersBody.querySelectorAll('[data-user-action]').forEach(button => {
            button.addEventListener('click', () => handleUserAction(button));
        });
    }

    function renderSchedule() {
        const byWeekday = new Map(state.schedule.map(item => [Number(item.weekday), item.user_id]));
        elements.scheduleGrid.innerHTML = weekdays.map((label, weekday) => `
            <label class="schedule-day">${label}
                <select data-weekday="${weekday}">
                    <option value="">请选择</option>
                    ${editorOptions(byWeekday.get(weekday))}
                </select>
            </label>
        `).join('');
    }

    function renderShifts() {
        elements.shiftsBody.innerHTML = state.shifts.map(shift => `
            <tr data-shift-id="${escapeHtml(shift.id)}">
                <td>${escapeHtml(formatDateTime(shift.starts_at))} – ${escapeHtml(formatDateTime(shift.ends_at))}</td>
                <td><select class="shift-assignee">${editorOptions(shift.user_id)}</select></td>
                <td><span class="status-pill ${shift.status === 'cancelled' ? 'is-cancelled' : ''}">${escapeHtml(shift.status)}</span></td>
                <td>${escapeHtml(shift.notes || '')}</td>
                <td><div class="admin-actions">
                    <button class="btn btn-secondary" data-shift-action="save">保存负责人</button>
                    <button class="btn btn-secondary" data-shift-action="cancel">${shift.status === 'cancelled' ? '恢复' : '取消'}</button>
                </div></td>
            </tr>
        `).join('');
        elements.shiftsBody.querySelectorAll('[data-shift-action]').forEach(button => {
            button.addEventListener('click', () => handleShiftAction(button));
        });
    }

    function renderCoverage(coverage) {
        const shouldWarn = coverage?.warning;
        elements.alert.hidden = !shouldWarn;
        if (!shouldWarn) return;
        elements.alert.textContent = coverage.coverage_end
            ? `排班已覆盖至 ${formatDateTime(coverage.coverage_end)}，还剩 ${coverage.remaining_days} 天。`
            : '目前没有有效班次，请先填写完整轮值表并生成班次。';
    }

    async function loadAll() {
        const [users, editors, schedules, shifts] = await Promise.all([
            request('/api/admin/users'),
            request('/api/admin/duty-editors'),
            request('/api/admin/schedules'),
            request('/api/admin/shifts?limit=100')
        ]);
        state.users = users.items || [];
        state.editors = editors.items || [];
        state.schedule = schedules.items || [];
        state.shifts = shifts.items || [];
        renderUsers();
        renderSchedule();
        renderShifts();
        renderCoverage(shifts.coverage || schedules.coverage);
    }

    async function handleUserAction(button) {
        const row = button.closest('[data-user-id]');
        const user = state.users.find(item => item.id === row.dataset.userId);
        if (!user) return;
        try {
            if (button.dataset.userAction === 'toggle') {
                await request(`/api/admin/users/${encodeURIComponent(user.id)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ is_active: !user.is_active })
                });
            } else {
                const newPassword = window.prompt(`为 ${user.display_name} 设置新密码（至少 10 位）`);
                if (!newPassword) return;
                await request(`/api/admin/users/${encodeURIComponent(user.id)}/reset-password`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ new_password: newPassword })
                });
            }
            showToast('用户已更新');
            await loadAll();
        } catch (error) {
            window.alert(error.message);
        }
    }

    async function handleShiftAction(button) {
        const row = button.closest('[data-shift-id]');
        const shift = state.shifts.find(item => item.id === row.dataset.shiftId);
        const body = button.dataset.shiftAction === 'save'
            ? { user_id: row.querySelector('.shift-assignee').value }
            : { cancelled: shift.status !== 'cancelled' };
        try {
            await request(`/api/admin/shifts/${encodeURIComponent(shift.id)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            showToast('班次已更新');
            await loadAll();
        } catch (error) {
            window.alert(error.message);
        }
    }

    document.getElementById('create-user-form').addEventListener('submit', async event => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        try {
            await request('/api/admin/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(Object.fromEntries(form.entries()))
            });
            event.currentTarget.reset();
            showToast('账号已创建');
            await loadAll();
        } catch (error) {
            window.alert(error.message);
        }
    });

    document.getElementById('btn-save-schedule').addEventListener('click', async () => {
        const assignments = {};
        elements.scheduleGrid.querySelectorAll('[data-weekday]').forEach(select => {
            assignments[select.dataset.weekday] = select.value;
        });
        try {
            await request('/api/admin/schedules', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ assignments })
            });
            showToast('轮值表已保存');
            await loadAll();
        } catch (error) {
            window.alert(error.message);
        }
    });

    document.getElementById('btn-generate-shifts').addEventListener('click', async () => {
        try {
            const result = await request('/api/admin/shifts/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ days: 14 })
            });
            showToast(`已补齐班次，本次新增 ${result.inserted} 条`);
            await loadAll();
        } catch (error) {
            window.alert(error.message);
        }
    });

    loadAll().catch(error => {
        elements.alert.hidden = false;
        elements.alert.textContent = error.message;
    });
})();
