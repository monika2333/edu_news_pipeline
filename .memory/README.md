# 项目协作记忆（.memory/）

这里是本仓库的**跨会话协作记忆**：记录代码和 git 历史看不出来的东西——决策原因、用户拍板的约束、踩坑经验、操作规程、进行中事项。任何 AI 工具或人在开始任务前，**先读本文件和 [global.md](global.md)**，再按任务主题取用下述文件。

## 文件清单

| 文件 | 内容 | 何时读 |
|---|---|---|
| [global.md](global.md) | 项目速览、权威文档指路、环境关键事实 | **任何任务必读** |
| [open-tasks.md](open-tasks.md) | 进行中任务、部署侧待办、未排期候选 | 开始新任务前（避免撞车/重复探索） |
| [workflow.md](workflow.md) | 协作约定与操作规程：委托书流程、测试、worktree、合并推送、变异测试 | 动手改代码前 |
| [pitfalls.md](pitfalls.md) | 技术踩坑清单与爬虫站点知识，按主题分节 | 改对应模块前（尤其 adapters、jsdom 测试、DB 迁移） |
| [decisions.md](decisions.md) | 用户拍板的约束，含「不要做」清单（防止好心的"优化"改回去） | 涉及 UI 形态、检索语义、功能口径时必读 |
| [delivery-log.md](delivery-log.md) | 交付流水：功能 → 合入 commit → 日期 | 需要了解某功能的来龙去脉时按图索骥 |
| [archive/](archive/) | 已闭环任务的详细记忆（按日期命名，**只读**，完成即归档不再编辑） | 需要某次交付的完整细节时 |

## 维护规则

1. **任务开始前**：读 global.md + open-tasks.md；任务涉及某模块时，读 pitfalls.md 对应节和 delivery-log.md 里的相关条目（进而按需看 archive 细节）。
2. **任务完成后**：只更新概述层（global/open-tasks/workflow/pitfalls/decisions/delivery-log），把该次任务的详细经验写成 `archive/YYYY-MM-DD-主题.md`，delivery-log 加一行指向它。已闭环的内容不要留在概述层——概述层只保持「当前状态」，过期即删。
3. **只记代码看不出来的**：决策原因、拍板约束、环境坑、验证方法论。机械性的事实（代码结构、git log 能查到的提交）不记。
4. **同一事实只记一处**：分工是——根 `AGENTS.md` 及局部 AGENTS.md = 怎么写代码的规范约束；`docs/data_flow.md` = 表结构与契约；`docs/env_reference.md` = 环境变量；本目录 = 经验、决策与状态。发现重复时收敛到权威位置。
5. 记忆起点是 2026-09-17；更早的历史只有 git log。

## 快速判断：这次改动要不要动 .memory/

- 新增/修改了用户可感知的行为且用户拍过板 → `decisions.md`
- 踩了新的坑（环境、SQL、站点、前端）→ `pitfalls.md`
- 交付了一个功能 → `delivery-log.md` 加一行 + `archive/` 写细节
- 发现遗留事项变化（合并了、过期了、新增了）→ `open-tasks.md`
