(() => {
    const weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
    const state = {
        users: [],
        editors: [],
        schedule: [],
        shifts: [],
        deleteUserId: '',
        deleteTrigger: null,
        calendarMonth: new Date(new Date().getFullYear(), new Date().getMonth(), 1),
        activeShiftId: '',
        shiftTrigger: null,
        shiftBusy: false
    };
    const elements = {
        usersBody: document.getElementById('users-body'),
        userSearch: document.getElementById('admin-user-search'),
        scheduleGrid: document.getElementById('schedule-grid'),
        calendarMonth: document.getElementById('shift-calendar-month'),
        calendarGrid: document.getElementById('shift-calendar-grid'),
        alert: document.getElementById('admin-alert'),
        toast: document.getElementById('toast'),
        deleteModal: document.getElementById('delete-user-modal'),
        deleteForm: document.getElementById('delete-user-form'),
        deleteName: document.getElementById('delete-user-name'),
        deleteInput: document.getElementById('delete-user-confirmation'),
        deleteConfirm: document.getElementById('btn-confirm-delete-user'),
        deleteCancel: document.getElementById('btn-cancel-delete-user'),
        shiftModal: document.getElementById('shift-editor-modal'),
        shiftTitle: document.getElementById('shift-editor-title'),
        shiftCurrent: document.getElementById('shift-editor-current'),
        shiftAssignee: document.getElementById('shift-editor-assignee'),
        shiftClear: document.getElementById('btn-clear-shift-assignee'),
        shiftRestore: document.getElementById('btn-restore-shift-template'),
        shiftClose: document.getElementById('btn-close-shift-editor')
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

    function weekdayLabel(value) {
        if (value === null || value === undefined || value === '') return '未填写';
        const weekday = Number(value);
        return Number.isInteger(weekday) && weekdays[weekday]
            ? weekdays[weekday]
            : '未填写';
    }

    function dateKey(date) {
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function parseDateKey(value) {
        return new Date(`${value}T12:00:00`);
    }

    function shiftDateKey(shift) {
        return String(shift.coverage_date || '').slice(0, 10);
    }

    function calendarStart() {
        const year = state.calendarMonth.getFullYear();
        const month = state.calendarMonth.getMonth();
        const first = new Date(year, month, 1);
        const mondayOffset = (first.getDay() + 6) % 7;
        return new Date(year, month, 1 - mondayOffset);
    }

    function shiftStatusLabel(shift) {
        if (!shift) return '未生成';
        if (shift.status === 'cancelled') return '已清除';
        if (shift.status === 'active') return '今日值班';
        if (shift.status === 'upcoming') return '待值班';
        return '已结束';
    }

    function formatCalendarTitle(value) {
        return new Intl.DateTimeFormat('zh-CN', {
            year: 'numeric',
            month: 'long',
            day: 'numeric',
            weekday: 'long'
        }).format(parseDateKey(value));
    }

    function showToast(message, type = 'success', action = null) {
        showToastAt(elements.toast, message, type, action);
    }

    function setDeleteModalOpen(open) {
        elements.deleteModal.classList.toggle('active', open);
        elements.deleteModal.setAttribute('aria-hidden', String(!open));
        if (open) {
            elements.deleteInput.focus();
            return;
        }
        const trigger = state.deleteTrigger;
        state.deleteUserId = '';
        state.deleteTrigger = null;
        elements.deleteInput.value = '';
        elements.deleteConfirm.disabled = true;
        if (trigger?.isConnected) trigger.focus();
    }

    function openDeleteUserModal(user, trigger) {
        state.deleteUserId = user.id;
        state.deleteTrigger = trigger;
        elements.deleteName.textContent = `${user.display_name}（${user.username}）`;
        elements.deleteInput.value = '';
        elements.deleteConfirm.disabled = true;
        setDeleteModalOpen(true);
    }

    async function confirmDeleteUser() {
        if (
            elements.deleteInput.value !== '确认删除'
            || !state.deleteUserId
        ) return;
        elements.deleteConfirm.disabled = true;
        try {
            await request(
                `/api/admin/users/${encodeURIComponent(state.deleteUserId)}`,
                { method: 'DELETE' }
            );
            setDeleteModalOpen(false);
            showToast('用户已删除');
            await loadAll();
        } catch (error) {
            elements.deleteConfirm.disabled = false;
            window.alert(error.message);
        }
    }

    async function request(path, options) {
        const response = await fetch(path, options);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(formatApiError(payload, '请求失败'));
        return payload;
    }

    function editorOptions(selectedId) {
        return state.editors.map(editor => {
            const preference = weekdayLabel(editor.preferred_weekday);
            const preferenceText = preference === '未填写'
                ? '未填首选'
                : `首选${preference}`;
            return `<option value="${escapeHtml(editor.id)}" ${editor.id === selectedId ? 'selected' : ''}>${escapeHtml(editor.display_name)} · ${escapeHtml(preferenceText)}</option>`;
        }).join('');
    }

    function renderUsers() {
        const query = elements.userSearch.value
            .trim()
            .toLocaleLowerCase('zh-CN');
        const visibleUsers = query
            ? state.users.filter(user => [
                user.display_name,
                user.username
            ].some(value => (
                String(value ?? '')
                    .toLocaleLowerCase('zh-CN')
                    .includes(query)
            )))
            : state.users;
        if (!visibleUsers.length) {
            elements.usersBody.innerHTML = `
                <tr><td class="admin-empty-row" colspan="7">
                    ${query ? '没有找到匹配的用户。' : '暂无控制台用户。'}
                </td></tr>
            `;
            return;
        }
        elements.usersBody.innerHTML = visibleUsers.map(user => `
            <tr data-user-id="${escapeHtml(user.id)}">
                <td>${escapeHtml(user.display_name)}</td>
                <td>${escapeHtml(user.username)}</td>
                <td>${user.role === 'admin' ? '管理员' : '值班编辑'}</td>
                <td><span class="preference-pill">${escapeHtml(weekdayLabel(user.preferred_weekday))}</span></td>
                <td><span class="status-pill ${user.is_active ? '' : 'is-inactive'}">${user.is_active ? '启用' : '停用'}</span></td>
                <td>${escapeHtml(formatDateTime(user.last_login_at))}</td>
                <td><div class="admin-actions">
                    <button class="btn btn-secondary" data-user-action="password">重置密码</button>
                    <button class="btn btn-secondary" data-user-action="toggle">${user.is_active ? '停用' : '启用'}</button>
                    <button class="btn btn-secondary admin-delete-user" data-user-action="delete">删除</button>
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

    function renderShiftCalendar() {
        const year = state.calendarMonth.getFullYear();
        const month = state.calendarMonth.getMonth();
        const start = calendarStart();
        const today = dateKey(new Date());
        const shiftsByDate = new Map(
            state.shifts.map(shift => [shiftDateKey(shift), shift])
        );
        elements.calendarMonth.textContent = new Intl.DateTimeFormat('zh-CN', {
            year: 'numeric',
            month: 'long'
        }).format(state.calendarMonth);
        elements.calendarGrid.innerHTML = Array.from({ length: 42 }, (_, index) => {
            const date = new Date(start);
            date.setDate(start.getDate() + index);
            const key = dateKey(date);
            const shift = shiftsByDate.get(key);
            const outside = date.getMonth() !== month || date.getFullYear() !== year;
            const editable = Boolean(shift) && key >= today;
            const cancelled = shift?.status === 'cancelled';
            const assignee = cancelled
                ? '未安排'
                : (shift?.display_name || '');
            const classes = [
                'shift-calendar-day',
                outside ? 'is-outside' : '',
                key === today ? 'is-today' : '',
                shift ? `is-${shift.status}` : 'is-missing',
                editable ? 'is-editable' : ''
            ].filter(Boolean).join(' ');
            const ariaLabel = [
                formatCalendarTitle(key),
                assignee,
                shiftStatusLabel(shift)
            ].filter(Boolean).join('，');
            return `
                <button class="${classes}" type="button"
                    data-calendar-date="${key}"
                    ${shift ? `data-shift-id="${escapeHtml(shift.id)}"` : ''}
                    ${editable ? '' : 'disabled'}
                    aria-label="${escapeHtml(ariaLabel)}">
                    <span class="shift-calendar-date">${date.getDate()}</span>
                    ${assignee
                        ? `<span class="shift-calendar-assignee">${escapeHtml(assignee)}</span>`
                        : ''}
                    <span class="shift-calendar-status">${escapeHtml(shiftStatusLabel(shift))}</span>
                </button>
            `;
        }).join('');
        elements.calendarGrid.querySelectorAll('[data-shift-id]').forEach(button => {
            button.addEventListener('click', () => openShiftEditor(button));
        });
    }

    function renderCoverage(coverage) {
        const shouldWarn = coverage?.warning;
        elements.alert.hidden = !shouldWarn;
        if (!shouldWarn) return;
        elements.alert.textContent = coverage.coverage_end
            ? `排班已覆盖至 ${window.formatDutyShiftDate(coverage.coverage_end)}，还剩 ${coverage.remaining_days} 天。`
            : '目前没有有效班次，请先填写完整轮值表并生成班次。';
    }

    function applyShiftsPayload(payload) {
        state.shifts = payload.items || [];
        renderShiftCalendar();
        renderCoverage(payload.coverage);
    }

    async function loadShifts() {
        applyShiftsPayload(await request('/api/admin/shifts?limit=500'));
    }

    async function loadAll() {
        const [users, editors, schedules, shifts] = await Promise.all([
            request('/api/admin/users'),
            request('/api/admin/duty-editors'),
            request('/api/admin/schedules'),
            request('/api/admin/shifts?limit=500')
        ]);
        state.users = users.items || [];
        state.editors = editors.items || [];
        state.schedule = schedules.items || [];
        renderUsers();
        renderSchedule();
        applyShiftsPayload(shifts);
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
            } else if (button.dataset.userAction === 'password') {
                const newPassword = window.prompt(`为 ${user.display_name} 设置新密码`);
                if (!newPassword) return;
                await request(`/api/admin/users/${encodeURIComponent(user.id)}/reset-password`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ new_password: newPassword })
                });
            } else {
                openDeleteUserModal(user, button);
                return;
            }
            showToast('用户已更新');
            await loadAll();
        } catch (error) {
            window.alert(error.message);
        }
    }

    function activeShift() {
        return state.shifts.find(shift => shift.id === state.activeShiftId);
    }

    function setShiftEditorBusy(busy) {
        state.shiftBusy = busy;
        elements.shiftAssignee.disabled = busy;
        elements.shiftClear.disabled = busy || activeShift()?.status === 'cancelled';
        elements.shiftRestore.disabled = busy || !templateAssignee();
    }

    function setShiftEditorOpen(open) {
        elements.shiftModal.classList.toggle('active', open);
        elements.shiftModal.setAttribute('aria-hidden', String(!open));
        if (open) {
            elements.shiftAssignee.focus();
            return;
        }
        const trigger = state.shiftTrigger;
        state.activeShiftId = '';
        state.shiftTrigger = null;
        if (trigger?.isConnected) trigger.focus();
    }

    function templateAssignee() {
        const shift = activeShift();
        if (!shift) return null;
        const weekday = (parseDateKey(shiftDateKey(shift)).getDay() + 6) % 7;
        return state.schedule.find(item => Number(item.weekday) === weekday) || null;
    }

    function renderShiftEditor() {
        const shift = activeShift();
        if (!shift) return;
        const cancelled = shift.status === 'cancelled';
        const template = templateAssignee();
        elements.shiftTitle.textContent = formatCalendarTitle(shiftDateKey(shift));
        elements.shiftCurrent.textContent = cancelled
            ? '当前未安排负责人'
            : `当前负责人：${shift.display_name}`;
        elements.shiftAssignee.innerHTML = `
            <option value="">请选择负责人</option>
            ${editorOptions(cancelled ? '' : shift.user_id)}
        `;
        elements.shiftRestore.textContent = template
            ? `恢复轮值模板（${template.display_name}）`
            : '恢复轮值模板';
        setShiftEditorBusy(false);
    }

    function openShiftEditor(trigger) {
        state.activeShiftId = trigger.dataset.shiftId;
        state.shiftTrigger = trigger;
        renderShiftEditor();
        setShiftEditorOpen(true);
    }

    function undoShiftBody(shift) {
        return shift.status === 'cancelled'
            ? { cancelled: true }
            : { user_id: shift.user_id, cancelled: false };
    }

    async function updateActiveShift(body, successMessage) {
        const shift = activeShift();
        if (!shift || state.shiftBusy) return;
        const shiftId = shift.id;
        const undoBody = undoShiftBody(shift);
        setShiftEditorBusy(true);
        try {
            await request(`/api/admin/shifts/${encodeURIComponent(shiftId)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            setShiftEditorOpen(false);
            await loadShifts();
            const undoAction = buildUndoToastAction(async () => {
                try {
                    await request(`/api/admin/shifts/${encodeURIComponent(shiftId)}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(undoBody)
                    });
                    await loadShifts();
                    showToast('已撤销操作');
                } catch (error) {
                    showToast(error.message || '撤销失败', 'error');
                }
            });
            showToast(successMessage, 'success', undoAction);
        } catch (error) {
            setShiftEditorBusy(false);
            showToast(error.message || '班次更新失败', 'error');
        }
    }

    elements.userSearch.addEventListener('input', () => {
        renderUsers();
    });
    window.addEventListener('pageshow', renderUsers);

    function changeCalendarMonth(offset) {
        state.calendarMonth = new Date(
            state.calendarMonth.getFullYear(),
            state.calendarMonth.getMonth() + offset,
            1
        );
        renderShiftCalendar();
    }

    document.getElementById('btn-calendar-previous').addEventListener('click', () => {
        changeCalendarMonth(-1);
    });
    document.getElementById('btn-calendar-next').addEventListener('click', () => {
        changeCalendarMonth(1);
    });
    document.getElementById('btn-calendar-today').addEventListener('click', () => {
        const today = new Date();
        state.calendarMonth = new Date(today.getFullYear(), today.getMonth(), 1);
        renderShiftCalendar();
    });

    elements.shiftAssignee.addEventListener('change', () => {
        const userId = elements.shiftAssignee.value;
        if (!userId) return;
        const editor = state.editors.find(item => item.id === userId);
        updateActiveShift(
            { user_id: userId, cancelled: false },
            `已将负责人更换为 ${editor?.display_name || '所选编辑'}`
        );
    });
    elements.shiftClear.addEventListener('click', () => {
        updateActiveShift({ cancelled: true }, '已清除当天负责人');
    });
    elements.shiftRestore.addEventListener('click', () => {
        const template = templateAssignee();
        if (!template) return;
        updateActiveShift(
            { user_id: template.user_id, cancelled: false },
            `已恢复轮值模板负责人 ${template.display_name}`
        );
    });
    elements.shiftClose.addEventListener('click', () => setShiftEditorOpen(false));
    elements.shiftModal.addEventListener('click', event => {
        if (event.target === elements.shiftModal) setShiftEditorOpen(false);
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
            await loadShifts();
        } catch (error) {
            window.alert(error.message);
        }
    });

    elements.deleteInput.addEventListener('input', () => {
        elements.deleteConfirm.disabled = elements.deleteInput.value !== '确认删除';
    });
    elements.deleteCancel.addEventListener('click', () => {
        setDeleteModalOpen(false);
    });
    elements.deleteForm.addEventListener('submit', event => {
        event.preventDefault();
        confirmDeleteUser();
    });
    elements.deleteModal.addEventListener('click', event => {
        if (event.target === elements.deleteModal) setDeleteModalOpen(false);
    });
    document.addEventListener('keydown', event => {
        if (
            event.key === 'Escape'
            && elements.shiftModal.classList.contains('active')
        ) {
            setShiftEditorOpen(false);
        } else if (
            event.key === 'Escape'
            && elements.deleteModal.classList.contains('active')
        ) {
            setDeleteModalOpen(false);
        }
    });

    const pageParams = new URLSearchParams(window.location.search);
    if (pageParams.get('created') === '1') {
        showToast('账号已创建');
        window.history.replaceState({}, '', '/admin');
    }

    loadAll().catch(error => {
        elements.alert.hidden = false;
        elements.alert.textContent = error.message;
    });
})();
