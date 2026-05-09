# Console Agent 指南

本目录包含 FastAPI Web 控制台、控制台 service 层、Jinja 模板和浏览器端资源。修改控制台时，应聚焦在展示、API 契约和人工审阅流程，不要把流水线或 adapter 的职责混入这里。

## 模块边界

- `app.py` 负责创建 FastAPI app、挂载静态文件并注册 routers。
- `*_routes.py` 应保持轻量：解析 HTTP 输入，在合适时声明 route-local request model，然后调用 service 函数。
- `*_service.py` 放控制台工作流逻辑，并调用 adapter 或 domain helper。
- `*_schemas.py` 放可复用的请求/响应模型；如果某个 API 契约开始超出单个 route 的局部使用，应放到这里。
- `manual_filter_service.py` 是为了稳定导入而保留的 public facade；更细的人工筛选逻辑放在相邻的 `manual_filter_*` 模块中。
- `web_templates/` 管理 Jinja markup；`web_static/` 管理 CSS 和 JavaScript。
- 当前 Web 页面入口以人工筛选为主：`/` 重定向到 `/manual_filter`，`web_routes.py` 负责这两个页面入口。

## 人工筛选规则

- 除非同步更新所有调用方和测试，否则不要破坏 `manual_filter_service.py` 导出的 facade 函数。
- 谨慎处理 review decision 和 report type。状态、排序、归档和编辑操作会影响后续导出行为。
- 聚类和序列化逻辑应与 route handler 分离。route handler 不应直接构造复杂的聚类响应。
- 不要单独重命名 `web_static/js/manual_filter/*` 依赖的 DOM id、`data-*` 属性、CSS class 或 API path；如需修改，必须同步更新模板、JavaScript 和测试。
- 尽量沿用现有 JS 模块边界，分别处理 filter、review、discard、search drawer、export/archive 等行为。
- 修改用户可见的工作流时，同时检查 API service 路径和浏览器端路径。

## API 和安全规则

- 除了有意公开的 health endpoint，受保护的控制台路由应继续通过 `app.py` 中的 `require_console_user` dependencies 注册。
- request body 使用 Pydantic model。
- query 参数在进入数据库 adapter 前，应在 route 或 service 中完成校验和标准化。
- 数据库访问保持在 `get_adapter()` 和 service 层之后；模板和浏览器端资源不应编码数据库假设。

## 前端规则

- CSS 保持现有 core/module 拆分：共享样式放在 `base.css`、`layout.css`、`components.css`、`utilities.css`，页面专属样式放在 `css/modules/`。
- 除非样式确实很小且只服务于局部元素，否则避免在模板中写 inline style。
- 人工筛选页的搜索抽屉依赖 `/api/articles/search`，相关 API、JS 和 CSS 变更需要一起检查。
- JavaScript 状态变更尽量集中在现有 manual-filter 模块中，避免多个模块重复发起同类 API 请求。
- 模板和 JS 通过 DOM id 与 `data-*` 属性紧密耦合；修改时必须一起处理。

## 建议测试

- 人工筛选 service 或 decision 变更：`python -m pytest tests/test_manual_filter_service.py`
- 人工筛选 route/API 变更：`python -m pytest tests/test_manual_filter_routes.py`
- 控制台 Web 入口或页面路由变更：`python -m pytest tests/test_console_web_routes.py`
- export、run 或 article service 变更：运行最接近的 `tests/test_*` 文件；如果影响 CLI 触发行为，再运行 `python -m pytest tests/test_cli_parser.py`
- 较大的控制台重构：`python -m pytest tests/test_manual_filter_service.py tests/test_manual_filter_routes.py`
