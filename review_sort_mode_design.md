# 审阅页排序模式改造设计

目标：在排序模式下仅展示分组名称 + 新闻标题，隐藏其余内容，并支持跨组自由拖拽排序；退出排序模式后仍按分组展示。

## 现状与问题
- DOM 结构：`renderGroupedReviewItems` 输出 `.review-group > .review-group-body > .article-card`。Sortable 绑定到 `#review-items`，但可拖拽元素不在容器的直接子级，导致拖拽/排序失效。
- 样式：`compact-mode` 类挂在 `#review-items`，CSS 仅针对 `.review-grid.compact-mode`，即使初始化了 Sortable，把手仍隐藏。

## 目标交互
- 非排序模式：保持现有按【京内负面→京内正面→京外正面→京外负面】分组展示。
- 排序模式：
  - 仅显示「分组名称 + 标题」行，其余摘要/来源/分数/状态/选择框全部隐藏。
  - 支持跨组拖拽；排序结果按当前视图（报型 + selected/backup）持久化。
  - 按钮文案切换为“退出排序”，拖拽把手可见。

## 前端实现方案
1) **渲染模式拆分**
   - 在 `renderReviewView` 中，根据 `isSortMode` 分支：排序模式走新的渲染函数 `renderSortableReviewItems`，输出“扁平列表”；非排序模式仍用 `renderGroupedReviewItems`。
   - 排序模式的 DOM：`#review-items` 下直接渲染若干 `.article-card`，每个包含：
     - 分组标签：从 `GROUP_ORDER` 找 label 或根据 `resolveGroupKey` 映射。
     - 标题（附链接，可选）。
     - 拖拽把手 `<span class="drag-handle">⋮⋮</span>`。
   - 退出排序模式时重新调用 `renderReviewView()` 以恢复分组视图。

2) **Sortable 绑定修正**
   - 仅在排序模式下初始化 Sortable，`draggable` 依旧指向 `.article-card`，此时其为容器直接子元素，可跨组拖拽。
   - `handle` 按桌面/移动逻辑维持现状，但排序模式下 `.drag-handle` 需显示。

3) **样式**
   - 新增 `.sort-mode` 或复用 `.compact-mode` 挂在 `.review-grid`（而非 `#review-items`），隐藏摘要/元信息/状态/选择框，仅保留把手、分组标签、标题。
   - 分组标签样式可沿用 badge 风格，保持轻量。

4) **排序数据同步**
   - `persistReviewOrder` 逻辑不变：从容器读取 `data-id` 顺序，写入 `state.reviewData[selected|backup]`，调用 `/api/manual_filter/order`。
   - 跨组拖拽后，重新渲染非排序模式时按新顺序展示（即分组内顺序随拖拽结果变化，不再按原组内分桶顺序重排）。

5) **切换流程**
   - `toggleSortMode`：翻转 `isSortMode` → `renderReviewView()` → `applySortModeState()`。
   - `applySortModeState` 更新按钮文案/激活态，同时确保排序模式下重新初始化 Sortable。

## 验证用例
- 切换综报/晚报、采纳/备选后进入排序模式：仅显示分组标签+标题，可拖拽。
- 拖拽跨组后保存顺序，退出排序模式，分组视图按新顺序展示。
- 重新进入排序模式，顺序保持；保存成功提示正常，失败提示不影响前端状态（现有逻辑）。
- 移动端：无 handle 时仍可拖拽（forceFallback=true），标题行保持单列简洁展示。

## 不涉及的范围
- 后端接口、数据模型无需改动。
- 筛选页逻辑不变；排序仅作用于审阅页当前视图的顺序。
