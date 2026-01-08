# Manual Filter 改进建议

## 1. 用户体验 UX & 界面 UI

### 1.1 语言国际化 (i18n) 一致性
- **问题**: 界面语言存在中英混杂。主界面（标题、Tab）是中文（例如“人工筛选控制台”、“审阅”），而搜索侧边栏内部使用了英文（"Search Articles", "Keywords", "Source", "Sentiment"）。
- **建议**: 
    - 统一将搜索侧边栏的文案改为中文，例如 "Title" -> "标题", "Source" -> "来源", "Sentiment" -> "情感倾向"。
    - 所有的 "Loading...", "Operation successful" 等提示语也应汉化，保持体验一致。

### 1.2 状态反馈优化
- **问题**: 数据加载时通常只显示 "Loading..." 纯文本或简单的 toast。
- **建议**:
    - **骨架屏 (Skeleton)**: 在加载列表数据时使用骨架屏代替 "Loading..." 文字，减少视觉跳变。
    - **更明确的 Toast**: 操作成功提示可以更具体，例如 "已移动 3 篇文章到综报采纳"。

### 1.3 移动端/响应式适配
- **问题**: 在窄屏下，顶部的 Header 和 Tab 可能会拥挤换行；Review Tab 的操作栏 (Search/Bulk Actions) 在移动端布局可能过于局促。
- **建议**:
    - 优化 `actions-bar` 和 `review-toolbar` 在移动端的堆叠样式，确保按钮易于点击且不遮挡内容。

## 2. 代码质量与工程化

### 2.1 安全性：避免直接 `innerHTML`
- **位置**: `src/console/web_static/js/manual_filter/search_drawer.js` (`renderDrawerSearchResults`)
- **问题**: 直接使用 template literal 拼接 HTML 字符串并赋值给 `innerHTML` 存在风险。
- **推荐方案**: **使用 `document.createElement`**。
    - **理由**: 项目当前采用原生 JavaScript (Vanilla JS) 开发，并未引入 Vue/React 等构建流程。为了保持项目轻量且无需增加复杂的 Build Chain，直接使用原生的 DOM API 是最稳妥、最标准的做法。
    - **示例**: 编写一个简单的 `createEl(tag, class, text)` 辅助函数来减少代码冗余。

### 2.2 CSS 组织
- **问题**: `dashboard.css` 文件较大（1000+ 行），包含了全局样式、组件样式、布局样式等。
- **建议**:
    - 考虑拆分 CSS 文件，例如 `layout.css`, `components.css`, `utilities.css`。
    - 或者将特定模块的 CSS（如 `search-drawer`）放在单独的文件中，或者与组件 JS 同名。
    - 统一颜色变量引用（目前已有 `:root` 定义，很好，继续保持）。

### 2.3 配置硬编码
- **问题**: `src/console/web_templates/manual_filter.html` 中直接硬编码了如 `src="/static/js/..."`，版本号 `v=20250110153000` 也是硬编码的。
- **建议**:
    - 版本号可以通过后端模板变量注入，避免每次修改 JS 都需要手动更新 HTML 中的版本号来清除缓存。

## 3. 功能增强

### 3.1 撤销功能 (Undo)
- **场景**: 误操作点击了 "放弃" 或移动了错误的分组。
- **建议**: 在 Toast 提示中增加 "撤销" 按钮，允许回滚上一步操作（需要后端 API 支持或前端暂存）。