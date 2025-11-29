# 筛选控制台二期计划（状态扩展 / 审阅分层）

## 1. 需求摘要
- 状态扩展：`manual_status` 扩展为 `pending | selected | backup | discarded | exported`，现有 `approved` 统一迁移为 `selected`。
- 筛选页（初审）：展示 bonus keywords、不可编辑摘要，仅做状态标记（采纳=selected、备选=backup、放弃=discarded），保留原文链接。
- 审阅页：分区展示 `selected` 与 `backup`，可编辑摘要，支持状态切换（selected↔backup↔discarded/pending），原文链接可点击；顶部提供导出按钮（仅导出 selected）。
- 放弃审阅页：单独列出 `discarded`，提供“加入备选”操作。
- Bonus keywords 展示：使用 `score_details.matched_rules`（打分时记录的命中规则列表）作为参考关键词列表，若要更友好展示可在服务层提取 `label`/`rule_id` 组成字符串。

## 2. 数据与迁移
- 继续复用 `manual_status` 字段，新增可选值 `selected`、`backup`；保留 `pending`、`discarded`、`exported`。
- 迁移脚本：
  - 将 `manual_status='approved'` 全量更新为 `selected`。
  - 补充约束/检查：`manual_status` 仅允许上述集合（可选）。
- 导出逻辑切换到读取 `manual_status='selected'`。

## 3. 服务层改造（`src/console/services/manual_filter.py`）
- `list_candidates`：返回字段包含 `bonus_keywords`（从 `score_details.matched_rules` 提取 `label/rule_id`），仅查询 `manual_status='pending'` 且 `status='ready_for_export'`。
- `bulk_decide`：支持三态写入（selected/backup/discarded），不写摘要。
- `status_counts`：按新状态聚合。
- 新增：
  - `list_review(decision, limit, offset)`：查询 `manual_status` 为 selected/backup，返回 bonus keywords、摘要、链接等。
  - `save_edits(edits)`：在审阅页保存摘要（仅写 `manual_summary`），不改变状态。
  - `list_discarded(limit, offset)`：查询 `manual_status='discarded'`。
- `reset_to_pending` 保留，用于撤回。
- `export_batch`：改为仅导出 `selected`，section 仍分京内/京外 + 正/负，写 batch/brief_items 后标记为 `exported`。

## 4. 前端（Streamlit）改造
- 页面结构：Tab 或多页
  - 筛选页：列表+单选按钮（采纳/备选/放弃），展示标题、分数、bonus keywords、情感、京内、原文链接；无编辑框；批量提交=写状态。
  - 审阅页：左右/上下分区显示 selected 与 backup；每条有摘要编辑框、状态切换按钮（selected/backup/discarded/pending）、保存按钮（批量或单条）；顶部有导出按钮（指定 tag/路径）。
  - 放弃页：列出 discarded，提供“加入备选”按钮（将状态置为 backup）。
- 交互细节：
  - 分页 30、score desc。
  - 提交/保存成功后 toast + rerun。
  - 导出按钮位于审阅页顶栏；导出后提示输出路径与分组计数。
  - 原文链接在各页均可点击。

## 5. Bonus keywords 展示说明
- 数据来源：打分阶段的 `score_details.matched_rules`，通常包含命中的规则列表（示例：`[{"label": "教育政策", "rule_id": "edu_policy"}]`）。
- 服务层提取规则的 `label`，若无则退回 `rule_id`，作为 bonus keywords 字符串或列表在 UI 显示。
- 若存在后续更友好的展示需求，可扩展字段映射，但当前无需新增 DB 字段。

## 6. 开发顺序
1) 迁移：`approved -> selected`，补充状态检查（可选）。  
2) 服务层：状态三态 + bonus keywords 序列化 + 新增 review/discarded 查询与摘要保存 + 导出改用 selected。  
3) 前端：新增审阅页/放弃页，筛选页去除编辑，加入 bonus keywords 和链接；顶栏导出。  
4) 测试：服务层单测覆盖三态流转、导出、摘要保存；前端手测筛选→审阅→导出→放弃/回退全链路。  
5) 文档：更新使用说明与状态定义。

## 7. 风险与注意
- 迁移必须一次性将 `approved` 置为 `selected`，避免导出遗漏。
- 导出只看 selected，备选/放弃不应被导出。
- 并发编辑：当前无锁，建议单人使用或通过提示约束；必要时在后续版本加入乐观锁。  
