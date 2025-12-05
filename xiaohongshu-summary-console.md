# 小红书总结控制台页面实现方案

目标：在现有控制台新增“**小红书总结**”页面，支持输入原始文本 → 提取 `http://xhslink.com/o/...` 链接 → 调用 Codex（按照 `xiaohongshu-post-analysis-guide.md` 和指定 prompt）生成总结文本，并把结果回显给用户。新页面需与“人工筛选 / 审阅”“Dashboard”“文章搜索”平级。

## 预期交互与页面骨架
- 新增路由 `/xiaohongshu/summary`（示例），模板放在 `src/console/web/templates/xhs_summary.html`，静态逻辑放在 `src/console/web/static/js/xhs_summary.js`（可复用 `dashboard.css` 的基础样式）。
- 导航入口：在 `landing.html` 增加一张卡片，指向新页面。
- 页面区块：
  1) **输入**：多行文本框，预填或一键加载 `xiaohongshu-summary - origin/input_task.txt`；可选“选择文件路径”文本框（高级）。  
  2) **链接提取结果**：展示去重后的链接列表（无需下载按钮）。  
  3) **Codex 生成**：显示当前使用的 prompt（可折叠），按钮触发 Codex 调用，支持在执行中提示“生成中/完成/失败”。  
  4) **输出**：展示 Codex 生成的总结文本，并标出落盘文件（如 `YYYYMMDDxiaohongshu-summaries.txt`）。可提供“复制”与“重新生成”按钮。  
  5) **运行日志/状态**：简单的状态条或日志区域，展示文件路径、Codex 执行命令、错误信息。

## 后端设计（FastAPI）
- 新建路由模块 `src/console/routes/xhs_summary.py`，挂载前缀 `/api/xhs_summary`；在 `app.py` 里纳入 `protected_dependencies`。
- 服务层放在 `src/console/services/xhs_summary.py`，职责拆分：
  - `extract_links(raw_text: str, source_path: Path | None) -> ExtractionResult`：包装 `xiaohongshu-summary - origin/extract_links.py` 的逻辑，输出唯一链接列表与生成文件路径。
  - `run_codex(prompt: str, workdir: Path, output_file: Path) -> RunResult`：封装 Codex 调用（见下文），记录 stdout/stderr、退出码。
  - `compose_prompt(links_file: Path, summaries_file: Path) -> str`：注入用户提供的固定 prompt：“请调用chrome dev mcp，按照 @xiaohongshu-post-analysis-guide.md ，依次处理{links_file}中的所有帖文，将结果写入{summaries_file}”。
- API 形态建议：
  - `POST /extract`：入参 `raw_text`（必填）或 `source_path`（可选，默认 `xiaohongshu-summary - origin/input_task.txt`）；返回 `{ links: [...], output_path }`。
  - `POST /summarize`：入参 `links` 或 `links_path`；可选 `summaries_filename`；返回任务 id，并立即启动后台任务。
  - `GET /task/{task_id}`：轮询任务状态，返回 `status`/`error`/`output_path`/`content`（内容可选懒加载读取文件）。
- 后台执行：使用 `asyncio.to_thread` 或 `BackgroundTasks` 让 Codex 调用不阻塞请求；任务状态暂存于进程内字典（简单需求即可），重启后可丢失。

## 复用与兼容性要点
- **提取逻辑复用**：`xiaohongshu-summary - origin` 含空格与连字符，不能直接作为包导入。可用 `importlib.machinery.SourceFileLoader` 加载 `extract_links.py`，或在服务层内复制其核心函数 `extract_xhslink_urls`。保留原有输出命名规则 `YYYYMMDD-xiaohongshu-links.txt`。
- **文件落盘目录**：默认落在 `xiaohongshu-summary - origin/` 以方便与现有样例对齐；需要 `Path` 统一处理，避免因空格导致的 shell 失败。必要时增加配置项 `XHS_SUMMARY_ROOT`。
- **认证**：沿用 `require_console_user`，不要暴露未鉴权的命令执行入口。

## Codex 调用方案（先走 CLI）
首选 Codex CLI，若环境不具备再考虑 SDK 备选。
1) **Codex CLI（主线）**：用 `subprocess.run` 执行 `codex exec "<prompt>" --full-auto --sandbox danger-full-access --working-dir "xiaohongshu-summary - origin"`，设置 `CODEX_API_KEY` 环境变量。可选 `-o <summaries_file>` 或让 prompt 指令自行写入文件。需确认服务器已安装 codex CLI（否则部署文档标注先安装）。
2) **TypeScript SDK 服务（备用）**：只有在 CLI 不可用时再落地，在 Node 侧（参考 `Codex-SDK.md`）启动轻量 RPC 或直接 `node -e "..."`。

CLI 路径注意事项：
- 先用 `codex login` 完成一次登录，后续 CLI 调用会复用凭据；也可通过环境变量 `CODEX_API_KEY` 覆盖单次调用。
- 在 prompt 内引用 `@xiaohongshu-post-analysis-guide.md`（同目录存在），并确保 `summaries_file` 落盘在同一目录。
- 对用户提供的文件名做白名单校验（仅允许 `[0-9A-Za-z_-]`）以防注入。

## 页面到接口的数据流
1) 用户粘贴原始文本 → `POST /api/xhs_summary/extract` → 返回链接列表（仅展示，不需要导出下载）。
2) 用户点击“生成总结” → 前端触发 `/api/xhs_summary/summarize`（内部可复用提取到的链接列表），返回 `task_id`。
3) 前端轮询 `GET /api/xhs_summary/task/{id}` → 状态为 `succeeded` 时读取 `content` 与 `output_path`（命名见下），展示并提供复制。
4) 如需重试，可直接复用上一轮的链接列表，不要求落盘文件。

## 开发步骤清单
1) 新建服务与路由文件 `src/console/services/xhs_summary.py`、`src/console/routes/xhs_summary.py`；在 `app.py` 注册。  
2) 在 `web.py` 增加页面路由 `/xiaohongshu/summary`，同时给 `landing.html` 增加入口卡片。  
3) 新建前端模板与 JS，完成输入区、提取区、生成区、状态区，以及调用上述 API 的 fetch 逻辑。  
4) 落地 Codex CLI 调用（必要时再提供 SDK 兜底），在服务层实现安全的命令组装与错误捕获，并校验 CLI 是否已安装。  
5) 为 `xiaohongshu-summary - origin` 目录路径增加配置项或常量，确保引用一致。  
6) 自测流程：  
   - 用 `xiaohongshu-summary - origin/input_task.txt` 作为输入，验证能提取链接并正常显示。  
   - 模拟 Codex 调用（可先用 `echo "fake output" > YYYYMMDDxiaohongshu-summaries.txt(1)` 代替）验证前端状态流转与文件命名。  
   - 正式串起 Codex，确认能生成并回显 `YYYYMMDDxiaohongshu-summaries.txt(1)` 形态文件。
7) 补充 README 或控制台帮助文案，说明需要的环境变量与 CLI 依赖。

## 已确认的约束与约定
- Node/TypeScript SDK 兜底暂不需要，先以 Codex CLI 为唯一实现路径，不通时再补兜底。
- 产物只需要总结文件，命名固定为 `YYYYMMDDxiaohongshu-summaries.txt`，存在同日多次生成时以 `(1)`, `(2)` 递增后缀保存；不需要产出或保留 links 文件。
- 历史任务状态不落库，页面内存保留最近一次即可。
- 全部功能开发完成后，请删除 `xiaohongshu-summary - origin` 目录（在删除前确认所需文件已迁移或归档）。
