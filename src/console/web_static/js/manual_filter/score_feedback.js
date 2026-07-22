// Manual Filter JS - Score Feedback

const SCORE_FEEDBACK_MAX_NOTES = 500;
let activeScoreFeedbackControl = null;

function escapeScoreFeedbackHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function getScoreFeedbackPresentation(feedbackType) {
    if (feedbackType === 'too_high') {
        return { symbol: '▲', label: '评分反馈：偏高', className: 'is-too-high' };
    }
    if (feedbackType === 'too_low') {
        return { symbol: '▼', label: '评分反馈：偏低', className: 'is-too-low' };
    }
    return { symbol: 'ⓘ', label: '评分反馈', className: 'is-empty' };
}

function renderScoreFeedbackControl(item) {
    const safe = item || {};
    const feedback = safe.score_feedback || null;
    const feedbackType = feedback && ['too_high', 'too_low'].includes(feedback.feedback_type)
        ? feedback.feedback_type
        : '';
    const notes = feedbackType ? (feedback.notes || '') : '';
    const presentation = getScoreFeedbackPresentation(feedbackType);
    const escapedArticleId = escapeScoreFeedbackHtml(safe.article_id || '');
    const escapedNotes = escapeScoreFeedbackHtml(notes);
    const score = escapeScoreFeedbackHtml(formatScore(safe.external_importance_score));
    return `
        <div class="meta-item score-feedback-control ${presentation.className}"
            data-article-id="${escapedArticleId}" data-feedback-type="${feedbackType}">
            <span class="score-feedback-value">分数: ${score}</span>
            <button type="button" class="score-feedback-trigger" aria-label="${presentation.label}"
                aria-haspopup="dialog" aria-expanded="false">${presentation.symbol}</button>
            <div class="score-feedback-popover" role="dialog" aria-label="评分反馈" hidden>
                <div class="score-feedback-title">这条分数</div>
                <div class="score-feedback-directions">
                    <button type="button" class="score-feedback-direction is-high" data-feedback-type="too_high"
                        aria-pressed="${feedbackType === 'too_high'}">偏高</button>
                    <button type="button" class="score-feedback-direction is-low" data-feedback-type="too_low"
                        aria-pressed="${feedbackType === 'too_low'}">偏低</button>
                </div>
                <label class="score-feedback-notes-label">
                    备注（选填）
                    <textarea class="score-feedback-notes" maxlength="${SCORE_FEEDBACK_MAX_NOTES}"
                        placeholder="${feedbackType ? '说明你判断偏高或偏低的理由…' : '请先选择偏高或偏低'}"
                        ${feedbackType ? '' : 'disabled'}>${escapedNotes}</textarea>
                </label>
                <div class="score-feedback-count" aria-live="polite">${notes.length}/${SCORE_FEEDBACK_MAX_NOTES}</div>
            </div>
        </div>
    `;
}

function updateScoreFeedbackCount(control) {
    const notes = control.querySelector('.score-feedback-notes');
    const count = control.querySelector('.score-feedback-count');
    if (!notes || !count) return;
    count.textContent = `${notes.value.length}/${SCORE_FEEDBACK_MAX_NOTES}`;
    notes.style.height = 'auto';
    notes.style.height = `${Math.max(72, notes.scrollHeight)}px`;
}

function setScoreFeedbackBusy(control, busy, disableDirections = true) {
    control.dataset.busy = busy ? 'true' : 'false';
    if (disableDirections) {
        control.querySelectorAll('.score-feedback-direction').forEach(button => {
            button.disabled = busy;
        });
    }
}

function setScoreFeedbackState(control, feedbackType, notes) {
    const normalizedType = ['too_high', 'too_low'].includes(feedbackType) ? feedbackType : '';
    const normalizedNotes = normalizedType ? String(notes || '') : '';
    const presentation = getScoreFeedbackPresentation(normalizedType);
    const trigger = control.querySelector('.score-feedback-trigger');
    const notesBox = control.querySelector('.score-feedback-notes');
    control.dataset.feedbackType = normalizedType;
    control.classList.remove('is-empty', 'is-too-high', 'is-too-low');
    control.classList.add(presentation.className);
    if (trigger) {
        trigger.textContent = presentation.symbol;
        trigger.setAttribute('aria-label', presentation.label);
    }
    control.querySelectorAll('.score-feedback-direction').forEach(button => {
        button.setAttribute('aria-pressed', String(button.dataset.feedbackType === normalizedType));
    });
    if (notesBox) {
        notesBox.disabled = !normalizedType;
        notesBox.value = normalizedNotes;
        notesBox.dataset.savedNotes = normalizedNotes;
        notesBox.placeholder = normalizedType
            ? '说明你判断偏高或偏低的理由…'
            : '请先选择偏高或偏低';
    }
    updateScoreFeedbackCount(control);
}

async function requestScoreFeedback(path, method, payload) {
    const response = await fetch(`${API_BASE}${path}`, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(data.detail || '评分反馈保存失败');
    }
    return data;
}

async function saveScoreFeedbackNotes(control, { showSuccess = true } = {}) {
    const feedbackType = control.dataset.feedbackType || '';
    const notesBox = control.querySelector('.score-feedback-notes');
    if (!feedbackType || !notesBox || notesBox.value === (notesBox.dataset.savedNotes || '')) {
        return true;
    }
    if (control.scoreFeedbackPromise) {
        await control.scoreFeedbackPromise;
        return notesBox.value === (notesBox.dataset.savedNotes || '');
    }
    const requestedNotes = notesBox.value;
    setScoreFeedbackBusy(control, true, false);
    control.scoreFeedbackPromise = requestScoreFeedback('/score-feedback', 'PUT', {
        article_id: control.dataset.articleId,
        feedback_type: feedbackType,
        notes: requestedNotes
    });
    try {
        const data = await control.scoreFeedbackPromise;
        const saved = data.score_feedback || {};
        notesBox.value = saved.notes || '';
        notesBox.dataset.savedNotes = notesBox.value;
        updateScoreFeedbackCount(control);
        if (showSuccess) showToast('备注已保存');
        return true;
    } catch (error) {
        showToast('备注保存失败', 'error');
        return false;
    } finally {
        control.scoreFeedbackPromise = null;
        setScoreFeedbackBusy(control, false, false);
    }
}

async function handleScoreFeedbackDirection(control, targetType) {
    if (control.dataset.directionBusy === 'true') return;
    if (control.scoreFeedbackPromise) {
        await control.scoreFeedbackPromise.catch(() => null);
        await Promise.resolve();
    }
    if (control.dataset.directionBusy === 'true') return;
    control.dataset.directionBusy = 'true';
    const previousType = control.dataset.feedbackType || '';
    const notesBox = control.querySelector('.score-feedback-notes');
    const previousNotes = notesBox ? notesBox.value : '';
    setScoreFeedbackBusy(control, true);
    try {
        if (targetType === previousType) {
            await requestScoreFeedback('/score-feedback/clear', 'POST', {
                article_id: control.dataset.articleId
            });
            setScoreFeedbackState(control, '', '');
        } else {
            const requestedNotes = previousType ? '' : previousNotes;
            const data = await requestScoreFeedback('/score-feedback', 'PUT', {
                article_id: control.dataset.articleId,
                feedback_type: targetType,
                notes: requestedNotes
            });
            const saved = data.score_feedback || {};
            setScoreFeedbackState(control, targetType, saved.notes || requestedNotes);
            if (notesBox) notesBox.focus();
        }
    } catch (error) {
        setScoreFeedbackState(control, previousType, previousNotes);
        showToast(error.message || '评分反馈保存失败', 'error');
    } finally {
        setScoreFeedbackBusy(control, false);
        control.dataset.directionBusy = 'false';
    }
}

async function closeScoreFeedback(control, { restoreFocus = true } = {}) {
    if (!control) return true;
    if (control.scoreFeedbackPromise) await control.scoreFeedbackPromise;
    const saved = await saveScoreFeedbackNotes(control);
    if (!saved) return false;
    const popover = control.querySelector('.score-feedback-popover');
    const trigger = control.querySelector('.score-feedback-trigger');
    if (popover) popover.hidden = true;
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
    if (activeScoreFeedbackControl === control) activeScoreFeedbackControl = null;
    if (restoreFocus && trigger) trigger.focus();
    return true;
}

async function openScoreFeedback(control) {
    if (activeScoreFeedbackControl && activeScoreFeedbackControl !== control) {
        const closed = await closeScoreFeedback(activeScoreFeedbackControl, { restoreFocus: false });
        if (!closed) return;
    }
    const popover = control.querySelector('.score-feedback-popover');
    const trigger = control.querySelector('.score-feedback-trigger');
    if (!popover || !trigger) return;
    const opening = popover.hidden;
    if (!opening) {
        await closeScoreFeedback(control);
        return;
    }
    const notesBox = control.querySelector('.score-feedback-notes');
    if (notesBox && notesBox.dataset.savedNotes === undefined) {
        notesBox.dataset.savedNotes = notesBox.value;
    }
    popover.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    activeScoreFeedbackControl = control;
    const selected = control.querySelector('.score-feedback-direction[aria-pressed="true"]');
    (selected || control.querySelector('.score-feedback-direction'))?.focus();
    updateScoreFeedbackCount(control);
}

function setupScoreFeedback() {
    document.addEventListener('click', async event => {
        const trigger = event.target.closest('.score-feedback-trigger');
        if (trigger) {
            event.stopPropagation();
            await openScoreFeedback(trigger.closest('.score-feedback-control'));
            return;
        }
        const direction = event.target.closest('.score-feedback-direction');
        if (direction) {
            event.stopPropagation();
            await handleScoreFeedbackDirection(
                direction.closest('.score-feedback-control'),
                direction.dataset.feedbackType
            );
            return;
        }
        if (event.target.closest('.score-feedback-popover')) return;
        if (activeScoreFeedbackControl) {
            await closeScoreFeedback(activeScoreFeedbackControl, { restoreFocus: false });
        }
    });
    document.addEventListener('change', async event => {
        if (!event.target.matches('.score-feedback-notes')) return;
        await saveScoreFeedbackNotes(event.target.closest('.score-feedback-control'));
    });
    document.addEventListener('input', event => {
        if (!event.target.matches('.score-feedback-notes')) return;
        updateScoreFeedbackCount(event.target.closest('.score-feedback-control'));
    });
    document.addEventListener('keydown', async event => {
        if (event.key !== 'Escape' || !activeScoreFeedbackControl) return;
        event.preventDefault();
        await closeScoreFeedback(activeScoreFeedbackControl);
    });
}
