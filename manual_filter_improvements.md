# Manual Filter 改进建议
## 2. 代码质量与工程化

### 2.1 安全性：避免直接 `innerHTML`
- **位置**: `src/console/web_static/js/manual_filter/search_drawer.js` (`renderDrawerSearchResults`)
- **问题**: 直接使用 template literal 拼接 HTML 字符串并赋值给 `innerHTML` 存在风险。
- **推荐方案**: **使用 `document.createElement`**。
    - **理由**: 项目当前采用原生 JavaScript (Vanilla JS) 开发，并未引入 Vue/React 等构建流程。为了保持项目轻量且无需增加复杂的 Build Chain，直接使用原生的 DOM API 是最稳妥、最标准的做法。
    - **示例**: 编写一个简单的 `createEl(tag, class, text)` 辅助函数来减少代码冗余。

### 2.3 配置硬编码
- **问题**: `src/console/web_templates/manual_filter.html` 中直接硬编码了如 `src="/static/js/..."`，版本号 `v=20250110153000` 也是硬编码的。
- **建议**:
    - 版本号可以通过后端模板变量注入，避免每次修改 JS 都需要手动更新 HTML 中的版本号来清除缓存。

## 3. 功能增强

### 3.1 撤销功能 (Undo)
- **场景**: 误操作点击了 "放弃" 或移动了错误的分组。
- **建议**: 在 Toast 提示中增加 "撤销" 按钮，允许回滚上一步操作（需要后端 API 支持或前端暂存）。

### 3.2 导出逻辑重构 (Export Optimization)
- **目标**: 简化导出流程，移除弹窗，实现“所见即所得”的快捷导出。
- **变更点**:
    - **移除导出弹窗 (`Export Modal`)**: 不再需要选择模板（综报/晚报）或输入期号。
    - **上下文敏感的操作栏**: 在 `Review` 页面的“综报采纳”、“晚报采纳”、“晚报备选”三个视图下，操作栏（Toolbar）应分别提供以下两个按钮：
        1.  **预览并复制 (Preview & Copy)**: 
            - 点击后直接生成当前视图对应的正文（综报视图用综报格式，晚报视图用晚报格式）。
            - 自动复制到剪贴板。
            - 仅展示文本内容，不再包含期号等元数据输入框。
        2.  **归档 (Archive)**:
            - **文案**: 按钮文案设为“归档”。
            - **行为**: 点击后将当前列表中的文章标记为 `exported` 并从列表中移除（视觉上清空当前视图）。这一操作即视为“导出完成”。