// 加分词典：即时保存。行按分值正负分组（加分在上、减分在下），
// 编辑在焦点离开输入框后自动整段写回，删除立即生效；没有保存/放弃按钮。
// 禁止拼接用户输入到 innerHTML。
'use strict';

// 行状态的类名集合；is-error 由校验单独维护，不在这里清掉
const BONUS_ROW_STATES = ['positive', 'negative', 'zero', 'empty', 'invalid'];

function bonusValueKind(rawValue) {
    const text = String(rawValue == null ? '' : rawValue).trim();
    if (!text) return 'empty';
    const value = Number(text);
    if (!Number.isFinite(value)) return 'invalid';
    if (value > 0) return 'positive';
    if (value < 0) return 'negative';
    return 'zero';
}

// 行所属分组：负数进减分组，正数/0/空值/非法值都留在加分组（非负侧）
function bonusGroupForKind(kind) {
    return kind === 'negative' ? 'negative' : 'positive';
}

function bonusSignGlyph(kind) {
    if (kind === 'positive') return '＋';
    if (kind === 'negative') return '−';
    return '·';
}

function bonusSignTitle(kind) {
    if (kind === 'positive') return '加分';
    if (kind === 'negative') return '减分';
    return '未填分值';
}

function formatBonusSum(value) {
    if (value > 0) return `+${value}`;
    if (value < 0) return `−${Math.abs(value)}`;
    return '0';
}

// 行首的加减徽标与整行配色：只由当前分值决定，空行/非法值回到中性灰
function syncBonusRowState(row) {
    const kind = bonusValueKind(row.querySelector('.bonus-value').value);
    row.classList.remove(...BONUS_ROW_STATES.map((state) => `is-${state}`));
    row.classList.add(`is-${kind}`);
    const sign = row.querySelector('.bonus-sign');
    if (sign) {
        sign.textContent = bonusSignGlyph(kind);
        sign.title = bonusSignTitle(kind);
        sign.dataset.kind = kind;
    }
    return kind;
}

function bonusLegendItem(kind, label) {
    const item = createEl('span', `bonus-legend-item is-${kind}`);
    item.append(createEl('span', 'bonus-legend-dot'), document.createTextNode(label));
    return item;
}

function bonusStatTile(kind, label, footText) {
    const tile = createEl('div', kind ? `bonus-stat is-${kind}` : 'bonus-stat');
    const number = createEl('span', 'bonus-stat-number', '0');
    const value = createEl('span', 'bonus-stat-value');
    value.append(number, createEl('span', 'bonus-stat-unit', '条'));
    const foot = createEl('span', 'bonus-stat-foot', footText);
    tile.append(createEl('span', 'bonus-stat-label', label), value, foot);
    return { tile, number, foot };
}

// 两个分组区块共用一套 markup：标题 + 条数徽标 + 说明 + 行列表 + 空分组提示
function createBonusGroup(key, title, hint, emptyText) {
    const group = createEl('section', `bonus-group is-${key}`, '', { 'data-bonus-group': key });
    const head = createEl('div', 'bonus-group-head');
    const count = createEl('span', 'bonus-group-count', '0 条');
    head.append(
        createEl('span', 'bonus-group-title', title),
        count,
        createEl('span', 'bonus-group-hint', hint),
    );
    const list = createEl('div', 'bonus-list');
    const empty = createEl('p', 'bonus-group-empty', emptyText);
    group.append(head, list, empty);
    return { group, list, count, empty };
}

function renderBonusesTab() {
    const panel = elements.panels.bonuses;
    const section = 'score_keyword_bonuses';
    clearEl(panel);
    const saved = settingsSection(section);
    if (!saved) {
        renderImportNotice(panel, '加分词典');
        return;
    }

    const head = createEl('div', 'bonus-head');
    const headText = createEl('div', 'bonus-head-text');
    headText.appendChild(createEl('h2', 'bonus-title', '关键词加减分'));
    const legend = createEl('div', 'bonus-legend');
    legend.append(bonusLegendItem('positive', '加分'), bonusLegendItem('negative', '减分'));
    head.append(headText, legend);

    // 状态行：自动保存结果 + 最后修改人；冲突时出现「载入最新配置」
    const status = createEl('span', 'bonus-save-status', '', {
        role: 'status', 'aria-live': 'polite',
    });
    const updatedLine = createEl('span', 'bonus-updated');
    const reloadBtn = createEl('button', 'btn btn-secondary bonus-reload', '载入最新配置', { type: 'button' });
    reloadBtn.hidden = true;
    const statusRow = createEl('div', 'bonus-status-row');
    statusRow.append(status, updatedLine, reloadBtn);

    // 统计条随草稿实时重算：一眼看清词典里各有几条加分词、减分词
    const positiveStat = bonusStatTile('positive', '加分词', '合计 0');
    const negativeStat = bonusStatTile('negative', '减分词', '合计 0');
    const entryStat = bonusStatTile('', '关键词总数', '含未填分值或 0 分的行');
    const stats = createEl('div', 'bonus-stats');
    stats.append(positiveStat.tile, negativeStat.tile, entryStat.tile);

    const table = createEl('div', 'bonus-table');
    const heading = createEl('div', 'bonus-row bonus-heading');
    ['加减', '关键词', '分值（−100～100）', '操作'].forEach((text) => {
        heading.appendChild(createEl('span', '', text));
    });
    // 展示顺序与写入顺序一致：加分词在上、减分词在下（先加分、后减分）
    const positiveGroup = createBonusGroup('positive', '加分词', '命中即加分',
        '还没有加分词；点击下方「添加加分词」录入第一条。');
    const negativeGroup = createBonusGroup('negative', '减分词', '命中即扣分',
        '还没有减分词；点击下方「添加减分词」录入，或把加分词的分值改成负数。');
    const groupLists = { negative: negativeGroup.list, positive: positiveGroup.list };
    // 每组末尾各有一个添加入口：新行直接落在本组，不用先加再加负号
    const addButtons = {
        positive: createEl('button', 'btn btn-secondary bonus-add', '＋ 添加加分词', { type: 'button' }),
        negative: createEl('button', 'btn btn-secondary bonus-add', '＋ 添加减分词', { type: 'button' }),
    };
    positiveGroup.group.append(addButtons.positive);
    negativeGroup.group.append(addButtons.negative);
    table.append(heading, positiveGroup.group, negativeGroup.group);

    // 加分词在前、减分词在后：DOM 顺序即写入顺序
    const allRows = () => [
        ...groupLists.positive.children,
        ...groupLists.negative.children,
    ];

    const refreshUpdatedLine = () => {
        const current = settingsSection(section);
        if (!current) return;
        const name = (current.updated_by && current.updated_by.display_name) || '未知';
        updatedLine.textContent = `最后修改：${name} · ${formatLocalDateTime(current.updated_at)}`;
    };

    const refreshStats = () => {
        const rows = allRows();
        let positive = 0;
        let negative = 0;
        let positiveSum = 0;
        let negativeSum = 0;
        rows.forEach((row) => {
            const input = row.querySelector('.bonus-value');
            const kind = bonusValueKind(input.value);
            if (kind === 'positive') {
                positive += 1;
                positiveSum += Number(input.value);
            } else if (kind === 'negative') {
                negative += 1;
                negativeSum += Number(input.value);
            }
        });
        positiveStat.number.textContent = String(positive);
        positiveStat.foot.textContent = `合计 ${formatBonusSum(positiveSum)}`;
        negativeStat.number.textContent = String(negative);
        negativeStat.foot.textContent = `合计 ${formatBonusSum(negativeSum)}`;
        entryStat.number.textContent = String(rows.length);
        positiveGroup.count.textContent = `${positive} 条`;
        negativeGroup.count.textContent = `${negative} 条`;
        negativeGroup.empty.hidden = groupLists.negative.children.length > 0;
        positiveGroup.empty.hidden = groupLists.positive.children.length > 0;
    };

    // 分值改了正负就换组：等焦点离开整行再挪（Tab 到行内「删除」时不动），
    // 挪动作放到下一个任务，避免打断 Tab 的焦点顺序
    const regroupRow = (row) => {
        if (!row.isConnected) return;
        const input = row.querySelector('.bonus-value');
        const target = groupLists[bonusGroupForKind(bonusValueKind(input.value))];
        if (row.parentElement === target) return;
        target.appendChild(row);
        refreshStats();
    };

    // 一次编辑的取值：整行完全空着（关键词和分值都空）视为未落地的草稿行，
    // 不参与保存，也不报错；只填了一半的行才会拦下整段保存
    const collectEntries = () => allRows().map((row) => {
        const keyword = row.querySelector('.bonus-keyword').value.trim();
        const rawBonus = row.querySelector('.bonus-value').value.trim();
        return {
            row,
            keyword,
            rawBonus,
            bonus: Number(row.querySelector('.bonus-value').value),
        };
    });

    const isBlankEntry = (entry) => !entry.keyword && !entry.rawBonus;

    const setRowError = (row, message, badKeyword, badBonus) => {
        row.classList.toggle('is-error', Boolean(message));
        row.querySelector('.bonus-keyword').setAttribute('aria-invalid', String(badKeyword));
        row.querySelector('.bonus-value').setAttribute('aria-invalid', String(badBonus));
        row.querySelector('.bonus-row-error').textContent = message;
    };

    const validateEntries = (entries) => {
        const active = entries.filter((entry) => !isBlankEntry(entry));
        let invalid = false;
        entries.forEach((entry) => {
            if (isBlankEntry(entry)) {
                setRowError(entry.row, '', false, false);
                return;
            }
            const duplicate = active.some(
                (other) => other !== entry && other.keyword === entry.keyword,
            );
            const badKeyword = !entry.keyword || duplicate;
            const badBonus = !entry.rawBonus
                || !Number.isInteger(entry.bonus) || entry.bonus < -100 || entry.bonus > 100;
            const message = !entry.keyword ? '关键词不能为空。' : duplicate ? '关键词重复。' :
                badBonus ? '分值必须是 -100 到 100 之间的整数。' : '';
            setRowError(entry.row, message, badKeyword, badBonus);
            invalid = invalid || badKeyword || badBonus;
        });
        return invalid;
    };

    // 写入值固定为「加分词在前、减分词在后」，组内顺序按归组后的样子排：
    // 已在目标组的行保持原顺序，刚改符号的行接在目标组末尾（与 regroupRow 一致），
    // 这样保存与随后归组的顺序相同，不会因为归组再触发第二次保存
    const groupKeyOf = (row) => {
        const group = row.closest('.bonus-group');
        return group ? group.dataset.bonusGroup : 'positive';
    };
    const futureOrder = (entries, wantNegative) => {
        const target = wantNegative ? 'negative' : 'positive';
        const matching = entries.filter((entry) => (entry.bonus < 0) === wantNegative);
        return [
            ...matching.filter((entry) => groupKeyOf(entry.row) === target),
            ...matching.filter((entry) => groupKeyOf(entry.row) !== target),
        ];
    };
    const buildValue = (entries) => {
        const active = entries.filter((entry) => !isBlankEntry(entry));
        return [...futureOrder(active, false), ...futureOrder(active, true)]
            .map(({ keyword, bonus }) => ({ keyword, bonus }));
    };

    const normalizeValue = (value) => [
        ...value.filter((item) => !(Number(item.bonus) < 0)),
        ...value.filter((item) => Number(item.bonus) < 0),
    ].map((item) => `${item.keyword}\u0000${Number(item.bonus)}`);

    const sameAsSaved = (value) => {
        const current = settingsSection(section);
        if (!current) return true;
        return JSON.stringify(normalizeValue(value))
            === JSON.stringify(normalizeValue(current.value || []));
    };

    // 界面上还有没落库的内容（输入中、半行、校验错误）时保持未保存标记
    const hasLocalChanges = () => {
        if (!settingsSection(section)) return false;
        return !sameAsSaved(buildValue(collectEntries()));
    };

    const setStatus = (message, kind = '') => {
        status.classList.remove('is-ok', 'is-error');
        if (kind === 'ok') status.classList.add('is-ok');
        if (kind === 'error') status.classList.add('is-error');
        status.textContent = message;
    };

    let saveInFlight = false;
    let saveQueued = false;

    // 整段保存：校验通过且有变化才发请求；请求进行中再来的编辑排一次队，
    // 避免并发 PUT 用同一个版本号互相 409
    const flush = async () => {
        if (saveInFlight) {
            saveQueued = true;
            return;
        }
        const entries = collectEntries();
        if (validateEntries(entries)) {
            state.dirty[section] = entries.some((entry) => !isBlankEntry(entry));
            setStatus('有未保存的修改：请补全标红的行。', 'error');
            return;
        }
        const value = buildValue(entries);
        if (sameAsSaved(value)) {
            state.dirty[section] = false;
            setStatus('已自动保存。', 'ok');
            return;
        }
        const current = settingsSection(section);
        if (!current) return;
        saveInFlight = true;
        setStatus('保存中…');
        let savedOk = false;
        try {
            const { response, payload } = await apiRequest(`/api/admin/settings/${section}`, {
                method: 'PUT',
                body: { value, expected_version: current.version },
            });
            if (response.ok) {
                applySectionItem(section, payload.item);
                // 服务端会 trim 关键词：把本地输入框同步成保存后的值（保存期间
                // 又被改过的行不动，避免覆盖用户输入）
                entries.forEach((entry) => {
                    const input = entry.row.querySelector('.bonus-keyword');
                    if (input.value.trim() === entry.keyword) input.value = entry.keyword;
                });
                // 请求期间可能又有输入；按当前界面与已保存值再判一次，不能直接清标记
                state.dirty[section] = hasLocalChanges();
                reloadBtn.hidden = true;
                setStatus('已自动保存。', 'ok');
                refreshUpdatedLine();
                savedOk = true;
            } else if (response.status === 409) {
                state.dirty[section] = true;
                reloadBtn.hidden = false;
                setStatus(`保存冲突：${formatApiError(payload, '配置已在别处被修改')}。`
                    + '本地修改仍保留，可载入最新配置后重试。', 'error');
            } else {
                state.dirty[section] = true;
                setStatus(`保存失败：${formatApiError(payload, '请检查填写内容')}`, 'error');
            }
        } catch (error) {
            state.dirty[section] = true;
            setStatus(`保存失败：${error.message || '网络错误'}`, 'error');
        } finally {
            saveInFlight = false;
        }
        if (savedOk && saveQueued) {
            saveQueued = false;
            await flush();
            return;
        }
        saveQueued = false;
    };

    const addRow = (item, targetGroup = '') => {
        const row = createEl('div', 'bonus-row');
        const sign = createEl('span', 'bonus-sign', '', { 'aria-hidden': 'true' });
        const keyword = createEl('input', 'bonus-keyword', '', {
            type: 'text', 'aria-label': '关键词', placeholder: '输入关键词',
        });
        const valueWrap = createEl('span', 'bonus-value-wrap');
        const bonus = createEl('input', 'bonus-value', '', {
            type: 'number', min: '-100', max: '100', step: '1', 'aria-label': '分值',
        });
        keyword.value = item.keyword;
        bonus.value = item.bonus;
        valueWrap.append(bonus, createEl('span', 'bonus-value-unit', '分'));
        const remove = createEl('button', 'btn btn-secondary bonus-delete', '删除', { type: 'button' });
        const error = createEl('span', 'bonus-row-error');
        row.append(sign, keyword, valueWrap, remove, error);
        remove.addEventListener('click', () => {
            const rows = allRows();
            const isBlankRow = (target) => !target.querySelector('.bonus-keyword').value.trim()
                && !target.querySelector('.bonus-value').value.trim();
            const current = settingsSection(section);
            const savedCount = current && Array.isArray(current.value) ? current.value.length : 0;
            // 当前词典非空、删的又不是空草稿行、且删完后没有别的有效行时，
            // 这次删除会把词典清空，先确认
            const wouldEmpty = savedCount > 0 && !isBlankRow(row)
                && !rows.some((target) => target !== row && !isBlankRow(target));
            if (wouldEmpty && !window.confirm('删除后所有文章都不再有关键词加分。确定清空加分词典吗？')) {
                return;
            }
            const index = rows.indexOf(row);
            const emptyGroupAdd = row.closest('.bonus-group').querySelector('.bonus-add');
            row.remove();
            refreshStats();
            const next = rows[index + 1] || rows[index - 1];
            (next ? next.querySelector('input') : emptyGroupAdd).focus();
            flush();
        });
        const kind = syncBonusRowState(row);
        // 已保存的行按分值归组；新增行落在点击的那个分组里
        groupLists[targetGroup || bonusGroupForKind(kind)].appendChild(row);
        return keyword;
    };

    saved.value.forEach((item) => addRow(item));
    table.addEventListener('input', (event) => {
        const row = event.target.closest('.bonus-row');
        if (!row || row.classList.contains('bonus-heading')) return;
        state.dirty[section] = true;
        syncBonusRowState(row);
        refreshStats();
    });
    // 值一落定就保存（Tab 到本行「删除」也算）；关键词先填、分值还空时先不动，
    // 等焦点离开整行再校验，避免半行状态先弹一次错误
    table.addEventListener('change', (event) => {
        const input = event.target;
        if (!input.closest('.bonus-keyword, .bonus-value')) return;
        const row = input.closest('.bonus-row');
        if (input.classList.contains('bonus-keyword')
            && !row.querySelector('.bonus-value').value.trim()) {
            return;
        }
        flush();
    });
    // 焦点离开整行：先把行挪到对应分组，再做一次保存/校验（覆盖半行与空行）
    table.addEventListener('focusout', (event) => {
        const row = event.target.closest('.bonus-row');
        if (!row || row.classList.contains('bonus-heading')) return;
        const next = event.relatedTarget;
        if (next && row.contains(next)) return;
        window.setTimeout(() => regroupRow(row), 0);
        flush();
    });
    table.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        const input = event.target.closest('.bonus-keyword, .bonus-value');
        if (input) input.blur();
    });
    Object.entries(addButtons).forEach(([groupKey, button]) => {
        button.addEventListener('click', () => {
            addRow({ keyword: '', bonus: '' }, groupKey).focus();
            refreshStats();
        });
    });
    reloadBtn.addEventListener('click', async () => {
        reloadBtn.disabled = true;
        try {
            const { response, payload } = await apiRequest('/api/admin/settings');
            if (!response.ok) throw new Error(formatApiError(payload, '加载设置失败'));
            state.payload.sections[section] = payload.sections[section];
            clearDirty(section);
            renderBonusesTab();
            showSettingsToast('已载入最新配置');
        } catch (error) {
            setStatus(`载入失败：${error.message}`, 'error');
            reloadBtn.disabled = false;
        }
    });

    const guide = createEl('aside', 'bonus-guide');
    const guideList = createEl('ul', 'bonus-guide-list');
    [
        '关键词命中标题、正文或已记录的关键词即计分，多个命中累加。',
        '填完一行、焦点离开该行即自动保存；保存只影响此后打分的文章，已经打过分的不会重算。',
    ].forEach((text) => guideList.appendChild(createEl('li', '', text)));
    guide.append(
        createEl('h3', 'bonus-guide-title', '生效说明'),
        guideList,
        createEl('p', 'bonus-guide-foot', '分值必须是 −100～100 的整数；0 只登记、不影响得分。'),
    );

    const main = createEl('div', 'bonus-main');
    main.append(stats, table);
    const layout = createEl('div', 'bonus-layout');
    layout.append(main, guide);
    panel.append(head, statusRow, layout);
    refreshStats();
    refreshUpdatedLine();
}
