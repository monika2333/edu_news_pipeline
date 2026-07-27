# 值班编辑控制台按钮布局 QA

- source visual truth:
  - `C:\Users\huanghc\AppData\Local\Temp\codex-clipboard-7578171c-3367-4f48-a504-db79fe07de5f.png`
  - `C:\Users\huanghc\AppData\Local\Temp\codex-clipboard-b0bce0d6-8fe6-40fe-b999-4232554002f2.png`
- implementation screenshots:
  - `C:\Users\huanghc\.codex\visualizations\2026\07\27\019fa399-257a-7dc0-a95f-f07ce532e1be\duty-review-implementation.png`
  - `C:\Users\huanghc\.codex\visualizations\2026\07\27\019fa399-257a-7dc0-a95f-f07ce532e1be\duty-filter-target.png`
- focused comparisons:
  - `C:\Users\huanghc\.codex\visualizations\2026\07\27\019fa399-257a-7dc0-a95f-f07ce532e1be\review-comparison.png`
  - `C:\Users\huanghc\.codex\visualizations\2026\07\27\019fa399-257a-7dc0-a95f-f07ce532e1be\filter-comparison.png`
- viewport and density:
  - 已选结果：CSS viewport `1246 x 600`，source/implementation `2492 x 1200`，按 2x 捕获密度直接比较。
  - 筛选：CSS viewport `1224 x 446`，source `2447 x 891`，implementation `2448 x 892`；focused comparison 裁去右侧和底部各 1 px。
- state:
  - 值班编辑当前班次。
  - 分别检查“筛选”和“已选结果”激活状态。

## Full-view comparison evidence

- “查看已定稿批次”位于“已选结果”标签行右端，与标签栏垂直居中；底部定稿操作区不再重复显示该按钮。
- “刷新”位于“筛选”标签行右端，位置与管理员“全量新闻筛选”页面的操作位一致；顶栏不再显示刷新按钮。
- “放弃”标签激活时，两个页面专属操作均隐藏。

## Focused comparison evidence

- 已选结果顶部操作位与参考图红框位置一致。
- 筛选顶部操作位与参考图目标位置一致。
- 按钮文案、边框、圆角、字号和现有二级按钮样式保持一致。

## Findings

- P0/P1/P2：无。
- 字体与排版：沿用现有控制台字体、字重和按钮层级。
- 间距与布局节奏：复用 `workspace-tabs-row`，左右操作垂直居中。
- 颜色与视觉 token：沿用现有 `btn-secondary` 和标签选中态。
- 图像与资源：本次不涉及新增或替换图像资源。
- 文案与内容：“刷新”“查看已定稿批次”文案未变化。

## Comparison history

- 首次目标尺寸对照即未发现可执行的 P0/P1/P2 差异，无需视觉修正迭代。

## Interaction checks

- “筛选 / 已选结果 / 放弃”切换后，对应右侧操作正确显示或隐藏。
- “查看已定稿批次”可正常打开历史批次弹窗并关闭。
- 浏览器控制台无 error 或 warning。

final result: passed
