# Console Agent 指南

本目录包含 FastAPI Web 控制台、控制台 service 层、Jinja 模板和浏览器端资源。修改控制台时，应聚焦在展示、API 契约和人工审阅流程，不要把流水线或 adapter 的职责混入这里。

## 模块边界

- `app.py` 负责创建 FastAPI app、挂载静态文件并注册 routers。
- `*_routes.py` 应保持轻量：解析 HTTP 输入，在合适时声明 route-local request model，然后调用 service 函数。
- `*_service.py` 放控制台工作流逻辑，并调用 adapter 或 domain helper。
- `*_schemas.py` 放可复用的请求/响应模型；如果某个 API 契约开始超出单个 route 的局部使用，应放到这里。
- `manual_filter_service.py` 是为了稳定导入而保留的 public facade；更细的人工筛选逻辑放在相邻的 `manual_filter_*` 模块中。
- `web_templates/` 管理 Jinja markup；`web_static/` 管理 CSS 和 JavaScript。
- 当前 Web 页面入口按角色分发，`web_routes.py` 负责根路径跳转：管理员进入 `/admin/duty-summary`，值班编辑进入 `/duty`。
- `/duty` 复用 `web_templates/manual_filter.html`，通过 `workspace_mode="duty"` 区分。值班编辑页面的可见控件应在这个共享模板及 `web_static/js/manual_filter/` 中维护。
- `web_static/js/manual_filter/workspace.js` 是共享页面与值班 API 之间的转译层，负责把人工筛选工作区请求映射到当前班次的 `/api/duty/shifts/{shift_id}` 接口。新增或修改值班编辑操作时，应同步检查模板、对应 manual-filter JS 模块和该转译层。
- 报送存档的纯逻辑不在本目录：文本解析、回链相似度算法、阈值配置在 `src/domain/submission_archive_*.py`，回链执行在 `src/workers/submission_archive_processing.py`。本目录的 `submission_archive_*.py` 只负责 HTTP 接口、录入界面和人工确认队列。不要把这些逻辑搬回 console。

## 人工筛选规则

- 除非同步更新所有调用方和测试，否则不要破坏 `manual_filter_service.py` 导出的 facade 函数。
- 谨慎处理 review decision 和 report type。状态、排序、归档和编辑操作会影响后续导出行为。
- 聚类和序列化逻辑应与 route handler 分离。route handler 不应直接构造复杂的聚类响应。
- 不要单独重命名 `web_static/js/manual_filter/*` 依赖的 DOM id、`data-*` 属性、CSS class 或 API path；如需修改，必须同步更新模板、JavaScript 和测试。
- 尽量沿用现有 JS 模块边界，分别处理 filter、review、discard、search drawer、export/archive 等行为。原文抽屉（`content_drawer.js` + `css/modules/content_drawer.css`）是无遮罩的右侧抽屉，正文经 `/api/articles/content` 按需单篇获取并做页面会话内内存缓存；抽屉 markup 抽在 `_content_drawer.html`，被 `manual_filter.html`、`submission_archive.html` 与 `duty_summary.html` 共用。筛选页与已选结果页共用 `manual_filter/content_drawer.js`，入口按钮在 `renderArticleCard` 与 `renderReviewCard` 中渲染；存档库页与值班汇总页用独立的 `submission_archive/content_drawer.js`（无侧栏折叠与列表锚定逻辑），入口是已回链（exact/fuzzy/manual）条目的 link pill（`linkPill` 渲染为按钮）和检索卡片的「原文」按钮（触发委托同时匹配 `.archive-link-pill-btn` 与 `.content-drawer-trigger`）。列表宽度变化后的锚定与摘要框重算抽在 `layout_anchor.js`，抽屉开合与侧栏折叠（`sidebar_collapse.js`）共用；筛选页摘要框是固定高度，任何路径都不得对其调用 resize。手动折叠状态存 `localStorage.sidebar_collapsed`；打开抽屉会自动折叠侧栏（`persist: false`，不写 localStorage），关闭抽屉不恢复。
- 修改用户可见的工作流时，同时检查 API service 路径和浏览器端路径。

## API 和安全规则

- 除了有意公开的 health endpoint，受保护的控制台路由应继续通过 `app.py` 中的 `require_console_user` dependencies 注册。
- request body 使用 Pydantic model。
- query 参数在进入数据库 adapter 前，应在 route 或 service 中完成校验和标准化。
- 数据库访问保持在 `get_adapter()` 和 service 层之后；模板和浏览器端资源不应编码数据库假设。

## 前端规则

- CSS 保持现有 core/module 拆分：共享样式放在 `base.css`、`layout.css`、`components.css`、`utilities.css`，页面专属样式放在 `css/modules/`。
- 前端 JS 与 CSS 按功能拆分为目录下的多个平铺脚本，不使用 ES module 或打包工具。
  共享状态与 DOM 引用集中在该目录的 `core.js`，启动逻辑集中在 `init.js`，
  加载顺序由模板中的 `<script>` / `<link>` 标签顺序决定：core 最前，init 最后。
  新增前端功能时沿用这个模式，参考 `web_static/js/manual_filter/`。
- 前端文件一律 UTF-8 无 BOM。
- 除非样式确实很小且只服务于局部元素，否则避免在模板中写 inline style。
- 人工筛选页的搜索抽屉依赖 `/api/articles/search`（链上归因）与 `/api/submission-archive/search`（报送存档命中）两个接口，相关 API、JS 和 CSS 变更需要一起检查。抽屉 markup 由 `_search_drawer.html` 提供，被 `manual_filter.html`、`duty_summary.html` 与 `submission_archive.html` 同时引用；JS 拆为 `search_drawer.js`（抽屉本体与检索请求）、`search_drawer_attribution.js`（归因呈现）、`search_drawer_archive.js`（存档命中）三个文件，后两个模块的 `<script>` 标签写在 `_search_drawer.html` 内，改动会同时影响三个页面。抽屉本体只依赖 `manual_filter/utils.js` 的纯函数（`createEl`、`clearEl`、`formatLocalDateTime` 等），引用页需自行加载 `utils.js` + `search_drawer.js` 与 `search.css`。全库检索卡片的「原文」按钮复用内容抽屉（各页面内容抽屉脚本的 `.content-drawer-trigger` 委托），因此引用搜索抽屉的页面必须同时引入 `_content_drawer.html`、`content_drawer.css` 和一个内容抽屉脚本实现。两个抽屉是叠加而非互斥：检索抽屉打开时 `body.search-drawer-open` 把原文抽屉抬到其上（`content_drawer.css`），关掉原文后检索结果原样保留；此状态下 Escape 与遮罩点击只作用于原文抽屉，检索抽屉的对应处理器会跳过。
- 报别切换（采纳/备选归入综报还是晚报）是页面右缘的固定小标签 + 弹出面板，markup 直接在 `manual_filter.html`（admin review 模式不渲染），交互在 `report_type_tab.js`，样式在 `filter.css` 的 report-type-dock 段。报别状态与数据刷新仍统一由 `utils.js` 的 `setReviewReportType` 处理，面板按钮沿用 `.report-type-btn` class 以保持既有绑定和同步逻辑生效；新增报别相关入口时也必须走 `setReviewReportType`，不要自行改 `state.reviewReportType`。
- JavaScript 状态变更尽量集中在现有 manual-filter 模块中，避免多个模块重复发起同类 API 请求。
- `web_static/js/manual_filter/utils.js` 会被 `duty_summary.html` 一并加载（因为共享搜索抽屉组件），其中被跨页面复用的是 `createEl`、`clearEl`、`renderSkeleton`、`formatScore`、`getSentimentClass`、`showToastAt`、`formatLocalDateTime` 这类纯函数。新增跨页面复用的工具函数时，不要依赖 `elements` 或 `state` 全局；需要读写页面状态的函数放 `manual_filter/core.js` 或对应功能文件。文件里现存的其他带状态函数是历史遗留，暂不调整。
- 控制台面向用户的时间展示统一走 `formatLocalDateTime`（`new Date` + 本地时区取值）：后端返回的是带 `Z` 的 UTC 时间，直接截取 ISO 字符串会把 UTC 当本地时间显示，凌晨入库的记录日期会差一天。不带时区的 `date` 字段（如报送存档的 `report_date`）不适用此函数，按字符串截取即可。
- 模板和 JS 通过 DOM id 与 `data-*` 属性紧密耦合；修改时必须一起处理。

## 建议测试

- 人工筛选 service 或 decision 变更：`python -m pytest tests/test_manual_filter_service.py`
- 人工筛选 route/API 变更：`python -m pytest tests/test_manual_filter_routes.py`
- 控制台 Web 入口或页面路由变更：`python -m pytest tests/test_console_web_routes.py`
- export、run 或 article service 变更：运行最接近的 `tests/test_*` 文件；如果影响 CLI 触发行为，再运行 `python -m pytest tests/test_cli_parser.py`
- 较大的控制台重构：`python -m pytest tests/test_manual_filter_service.py tests/test_manual_filter_routes.py`
