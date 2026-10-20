# 全局记忆

## 项目是什么

教育系统新闻采集流水线：多来源抓取（crawl）→ 主哈希去重（hash-primary）→ 重要性评分（score）→ 摘要（summarize/enrich-summary）→ 地理分类（geo-classify）→ 外部过滤（external-filter，LLM 重要性评分）→ 人工筛选控制台（FastAPI + 原生 JS）→ 报送导出（export，综报/晚报）。

- 技术栈：Python 3.9+（CI 用 3.12；本机 venv 3.14.3，2026-10-20 核对）、FastAPI + uvicorn、PostgreSQL（psycopg 3）、前端原生 JS 按功能平铺（无框架）、pytest + jsdom 行为测试。
- 远端：GitHub `monika2333/edu_news_pipeline`（origin/main）。单人开发 + AI 协作，任务经 worktree 或功能分支交付。
- **生产线在跑**：每小时抓取流水线依赖当前库结构——所以数据库变更只做加法（新表/新列），这是根 AGENTS.md 的硬约束。
- 聚类缓存由部署侧的 Windows 计划任务 `EduNewsManualClusterRefresh` 每 5 分钟重建；本开发机未注册该任务（2026-10-20 用 `schtasks` 核对）。注册/更新脚本 `scripts/tasks/update-manual-cluster-refresh.ps1` 默认找 `.venv\Scripts\python.exe`，本仓库是 `venv`，需显式传 `-PythonPath`。

## 权威文档指路（先读这些，别在这里重复查）

| 想知道 | 去哪 |
|---|---|
| 怎么写代码、命令、目录职责、依赖方向 | 根 `AGENTS.md`；改 `src/console/`、`src/adapters/` 前各读局部 `AGENTS.md` |
| 每张表的职责、生命周期、字段契约 | `docs/data_flow.md` |
| 全部环境变量 | `docs/env_reference.md` |
| 测试怎么跑、worktree 怎么开、合并怎么收尾 | [workflow.md](workflow.md) |
| 踩坑与站点知识 | [pitfalls.md](pitfalls.md) |
| 用户拍板的「别改回去」清单 | [decisions.md](decisions.md) |

## 环境关键事实

- 平台 Windows（PowerShell / Git Bash 均可）。跑测试必须用仓库根 venv，命令与两种 shell 写法见根 `AGENTS.md`「构建与开发命令」（系统默认 python 缺依赖）。
- DB 凭据在仓库根 `.env.local`（未跟踪）；DB 行为测试直连真实 Postgres（临时表模式），无 testcontainers。
- 行尾由 `.gitattributes`（`* text=auto eol=lf`；`*.cmd`/`*.bat` 检出时 CRLF）统一，与各机器的 `core.autocrlf` 无关（系统配 true、本仓库 `.git/config` 配 false，都已被属性覆盖）。Windows 编辑器写 CRLF 不会再造成假 diff。
- **ZCode 环境**下推送 main 会被 worktree-guard 拦截，需 authorize-main → push → revoke-main 三步独立命令；其他工具直接 `git push`（详见 workflow.md）。
