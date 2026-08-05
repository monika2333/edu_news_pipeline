// Submission Archive JS - Create
/* ---------- 新增存档（粘贴 → 核对保存） ---------- */

function blankPreviewItem() {
    return { section: '', marker: '', title: '', body: '', source: '', urls: [], editing: true };
}

function itemCardView(item, index) {
    const meta = [];
    if (item.marker) meta.push(`标记 ${escapeHtml(item.marker)}`);
    meta.push(`来源：${escapeHtml(item.source || '未抽取')}`);
    if (item.urls?.length) meta.push(`${item.urls.length} 个链接`);
    return `
        <article class="archive-item" data-index="${index}">
            <div class="archive-item-head">
                <span class="archive-item-order">${index + 1}</span>
                <h4 class="archive-item-title">${escapeHtml(item.title || '（无标题）')}</h4>
                <div class="archive-item-tools">
                    <button class="archive-btn-text" data-action="edit" type="button">编辑</button>
                    <button class="archive-btn-text is-danger" data-action="remove" type="button">删除</button>
                </div>
            </div>
            ${item.body ? `<p class="archive-item-body">${escapeHtml(item.body)}</p>` : ''}
            <div class="archive-item-meta">${meta.map(part => `<span>${part}</span>`).join('')}</div>
        </article>
    `;
}

function itemCardEdit(item, index) {
    const urls = (item.urls || []).join('\n');
    return `
        <article class="archive-item-editor" data-index="${index}">
            <div class="archive-item-editor-grid">
                <label class="archive-field is-wide">
                    <span>标题</span>
                    <input data-field="title" value="${escapeHtml(item.title)}">
                </label>
                <label class="archive-field">
                    <span>章节</span>
                    <input data-field="section" value="${escapeHtml(item.section || '')}">
                </label>
                <label class="archive-field">
                    <span>标记</span>
                    <input data-field="marker" value="${escapeHtml(item.marker || '')}">
                </label>
                <label class="archive-field">
                    <span>来源</span>
                    <input data-field="source" value="${escapeHtml(item.source || '')}">
                </label>
                <label class="archive-field is-wide">
                    <span>正文</span>
                    <textarea data-field="body" rows="4">${escapeHtml(item.body || '')}</textarea>
                </label>
                <label class="archive-field is-wide">
                    <span>URL（每行一个）</span>
                    <textarea data-field="urls" rows="2">${escapeHtml(urls)}</textarea>
                </label>
            </div>
            <div class="archive-item-editor-actions">
                <button class="archive-btn-text is-danger" data-action="remove" type="button">删除</button>
                <button class="archive-btn-text" data-action="done" type="button">完成</button>
            </div>
        </article>
    `;
}

function renderPreviewItems() {
    const target = document.getElementById('archive-preview-items');
    const parts = [];
    let lastSection = null;
    let visibleIndex = 0;
    previewItems.forEach((item, index) => {
        const section = item.section || '未分章节';
        if (section !== lastSection) {
            parts.push(`<h3 class="archive-section-heading">【${escapeHtml(section)}】</h3>`);
            lastSection = section;
        }
        parts.push(item.editing ? itemCardEdit(item, index) : itemCardView(item, index));
        visibleIndex += 1;
    });
    target.innerHTML = parts.length
        ? parts.join('')
        : '<div class="archive-empty">还没有条目，点击下方「手工新增一条」。</div>';
    const count = document.getElementById('archive-save-count');
    if (count) count.textContent = `共 ${previewItems.length} 条`;
}

function bindPreviewItems() {
    const target = document.getElementById('archive-preview-items');
    target.addEventListener('click', event => {
        const button = event.target.closest('[data-action]');
        if (!button) return;
        const card = button.closest('[data-index]');
        const index = Number(card.dataset.index);
        const action = button.dataset.action;
        if (action === 'edit') {
            previewItems[index].editing = true;
            renderPreviewItems();
        } else if (action === 'done') {
            previewItems[index].editing = false;
            renderPreviewItems();
        } else if (action === 'remove') {
            previewItems.splice(index, 1);
            renderPreviewItems();
        }
    });
    target.addEventListener('input', event => {
        const field = event.target.dataset.field;
        if (!field) return;
        const card = event.target.closest('[data-index]');
        const index = Number(card.dataset.index);
        const value = event.target.value;
        if (field === 'urls') {
            previewItems[index].urls = value
                .split(/\r?\n/)
                .map(part => part.trim())
                .filter(Boolean);
        } else {
            previewItems[index][field] = value;
        }
    });
}

function renderWarnings(warnings) {
    const target = document.getElementById('archive-warnings');
    const selectedType = document.getElementById('archive-report-type')?.value;
    const messages = [...(warnings || [])];
    if (parsedState && selectedType !== parsedState.detected_report_type) {
        messages.unshift(
            `当前选择“${typeLabels[selectedType]}”，文本格式更像“${typeLabels[parsedState.detected_report_type]}”；仍可继续保存。`
        );
    }
    target.hidden = !messages.length;
    target.innerHTML = messages.map(message => `<div>${escapeHtml(message)}</div>`).join('');
}

async function parsePastedReport() {
    const pastedText = document.getElementById('archive-pasted-text').value.trim();
    if (!pastedText) {
        toast('请先粘贴报告全文', 'error');
        return;
    }
    const button = document.getElementById('archive-parse');
    button.disabled = true;
    button.textContent = '解析中…';
    try {
        parsedState = await api('/parse', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pasted_text: pastedText })
        });
        document.getElementById('archive-report-type').value = parsedState.detected_report_type;
        document.getElementById('archive-report-date').value = dateValue(parsedState.report_date);
        document.getElementById('archive-compiled-date').value = dateValue(parsedState.compiled_date);
        document.getElementById('archive-issue-no').value = parsedState.issue_no || '';
        previewItems = parsedState.items.map(item => ({
            section: item.section || '',
            marker: item.marker || '',
            title: item.title || '',
            body: item.body || '',
            source: item.source || '',
            urls: Array.isArray(item.urls) ? item.urls : [],
            editing: false
        }));
        renderPreviewItems();
        document.getElementById('archive-preview').hidden = false;
        renderWarnings(parsedState.warnings);
        document.getElementById('archive-preview').scrollIntoView({ behavior: 'smooth' });
    } catch (error) {
        toast(error.message, 'error');
    } finally {
        button.disabled = false;
        button.textContent = '解析';
    }
}

async function saveReport(overwrite = false) {
    const items = previewItems.map((item, index) => ({
        section: (item.section || '').trim() || null,
        marker: (item.marker || '').trim() || null,
        order_index: index,
        title: (item.title || '').trim(),
        body: (item.body || '').trim(),
        source: (item.source || '').trim() || null,
        urls: item.urls || []
    }));
    if (!items.length) {
        toast('报告至少需要一个条目', 'error');
        return;
    }
    const missing = items.findIndex(item => !item.title);
    if (missing >= 0) {
        toast(`第 ${missing + 1} 条还没有标题`, 'error');
        previewItems[missing].editing = true;
        renderPreviewItems();
        return;
    }
    const payload = {
        report_type: document.getElementById('archive-report-type').value,
        report_date: document.getElementById('archive-report-date').value,
        compiled_date: document.getElementById('archive-compiled-date').value,
        issue_no: document.getElementById('archive-issue-no').value.trim() || null,
        title_line: parsedState.title_line,
        pasted_text: document.getElementById('archive-pasted-text').value,
        items,
        overwrite
    };
    const button = document.getElementById('archive-save');
    button.disabled = true;
    button.textContent = '保存中…';
    try {
        const result = await api('/reports', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        sessionStorage.setItem(
            'archiveNotice',
            '保存成功，系统正在后台判断回链'
        );
        window.location.assign(`/submission-archive/${result.report.id}`);
    } catch (error) {
        if (error.status === 409 && !overwrite) {
            const confirmed = window.confirm(
                '同一类型和日期的报告已存在。覆盖会删除旧条目及其人工确认结果，是否继续？'
            );
            if (confirmed) await saveReport(true);
            return;
        }
        toast(error.message, 'error');
    } finally {
        button.disabled = false;
        button.textContent = '确认保存';
    }
}

