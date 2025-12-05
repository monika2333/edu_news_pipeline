const API_BASE = "/api/xhs_summary";

const state = {
    links: [],
    taskId: null,
    pollingTimer: null
};

const elements = {
    rawText: document.getElementById("raw-text"),
    sourcePath: document.getElementById("source-path"),
    btnLoadDefault: document.getElementById("btn-load-default"),
    btnExtract: document.getElementById("btn-extract"),
    btnSummarize: document.getElementById("btn-summarize"),
    btnCopyOutput: document.getElementById("btn-copy-output"),
    linksList: document.getElementById("links-list"),
    linksCount: document.getElementById("links-count"),
    statusChip: document.getElementById("status-chip"),
    outputPath: document.getElementById("output-path"),
    outputText: document.getElementById("output-text"),
    logArea: document.getElementById("log-area"),
    promptPreview: document.getElementById("prompt-preview")
};

document.addEventListener("DOMContentLoaded", () => {
    if (elements.btnLoadDefault) {
        elements.btnLoadDefault.addEventListener("click", fillDefaultPath);
    }
    if (elements.btnExtract) {
        elements.btnExtract.addEventListener("click", handleExtract);
    }
    if (elements.btnSummarize) {
        elements.btnSummarize.addEventListener("click", handleSummarize);
    }
    if (elements.btnCopyOutput) {
        elements.btnCopyOutput.addEventListener("click", copyOutput);
    }
});

function fillDefaultPath() {
    const defaultPath = 'xiaohongshu-summary - origin/input_task.txt';
    elements.sourcePath.value = defaultPath;
    appendLog(`已填入默认路径: ${defaultPath}`);
}

function setStatus(text, tone = "idle") {
    elements.statusChip.textContent = text;
    elements.statusChip.className = `badge status-${tone}`;
}

function appendLog(message) {
    const ts = new Date().toISOString();
    const existing = elements.logArea.textContent === "等待操作..." ? "" : elements.logArea.textContent + "\n";
    elements.logArea.textContent = `${existing}[${ts}] ${message}`;
}

function renderLinks(links) {
    state.links = Array.isArray(links) ? links : [];
    elements.linksCount.textContent = `${state.links.length} 个`;
    if (!state.links.length) {
        elements.linksList.classList.add("empty-state");
        elements.linksList.textContent = "尚未提取";
        return;
    }
    elements.linksList.classList.remove("empty-state");
    elements.linksList.innerHTML = state.links
        .map(link => `<span class="tag">${escapeHtml(link)}</span>`)
        .join(" ");
}

function escapeHtml(str) {
    return String(str || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

async function handleExtract() {
    const rawText = elements.rawText.value.trim();
    const sourcePath = elements.sourcePath.value.trim();
    if (!rawText && !sourcePath) {
        appendLog("请提供原始文本或文件路径后再提取。");
        setStatus("待输入", "idle");
        return;
    }
    setStatus("提取中", "pending");
    appendLog("开始提取链接...");
    try {
        const res = await fetch(`${API_BASE}/extract`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                raw_text: rawText,
                source_path: sourcePath || null
            })
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data?.detail || "提取失败");
        }
        renderLinks(data.links || []);
        setStatus("已提取", "success");
        appendLog(`提取完成，获得 ${state.links.length} 个链接`);
    } catch (err) {
        setStatus("提取失败", "error");
        appendLog(`提取失败: ${err.message || err}`);
    }
}

async function handleSummarize() {
    if (!state.links.length) {
        appendLog("请先完成链接提取。");
        setStatus("待提取", "idle");
        return;
    }
    setStatus("排队中", "pending");
    appendLog(`提交 ${state.links.length} 个链接生成总结...`);
    clearPolling();
    try {
        const res = await fetch(`${API_BASE}/summarize`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                links: state.links,
                summaries_filename: null
            })
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data?.detail || "触发生成失败");
        }
        if (!data.task_id) {
            throw new Error("未返回任务 ID");
        }
        state.taskId = data.task_id;
        if (data.prompt && elements.promptPreview) {
            elements.promptPreview.textContent = data.prompt;
        }
        setStatus("执行中", "pending");
        appendLog(`任务已创建: ${state.taskId}，开始轮询...`);
        state.pollingTimer = window.setInterval(pollTask, 2000);
    } catch (err) {
        setStatus("触发失败", "error");
        appendLog(`生成失败: ${err.message || err}`);
    }
}

async function pollTask() {
    if (!state.taskId) return;
    try {
        const res = await fetch(`${API_BASE}/task/${encodeURIComponent(state.taskId)}`);
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data?.detail || "轮询失败");
        }
        const status = data.status || "unknown";
        if (status === "pending" || status === "running") {
            setStatus("执行中", "pending");
            return;
        }
        if (status === "succeeded") {
            setStatus("完成", "success");
            renderOutput(data);
            if (data.prompt && elements.promptPreview) {
                elements.promptPreview.textContent = data.prompt;
            }
            appendLog("生成完成。");
            clearPolling();
            return;
        }
        setStatus("失败", "error");
        appendLog(`任务失败: ${data.error || "未知错误"}`);
        clearPolling();
    } catch (err) {
        appendLog(`轮询异常: ${err.message || err}`);
    }
}

function renderOutput(data) {
    const content = data.content || "";
    const outputPath = data.output_path || data.outputPath || "";
    elements.outputText.value = content;
    elements.outputPath.textContent = outputPath ? `输出文件：${outputPath}` : "";
}

function copyOutput() {
    const text = elements.outputText.value;
    if (!text) {
        appendLog("没有可复制的内容。");
        return;
    }
    navigator.clipboard.writeText(text).then(() => {
        appendLog("已复制输出到剪贴板。");
    }).catch(() => {
        appendLog("复制失败，请手动选择复制。");
    });
}

function clearPolling() {
    if (state.pollingTimer) {
        clearInterval(state.pollingTimer);
        state.pollingTimer = null;
    }
}
