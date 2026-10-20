# 全局记忆

## 项目是什么

教育系统新闻采集流水线：多来源抓取（crawl）→ 主哈希去重（hash-primary）→ 重要性评分（score）→ 摘要（summarize/enrich-summary）→ 地理分类（geo-classify）→ 外部过滤（external-filter，LLM 重要性评分）→ 人工筛选控制台（FastAPI + 原生 JS）→ 报送导出（export，综报/晚报）。

- 技术栈：Python 3.9+（实际 CI/venv 3.12）、FastAPI + uvicorn、PostgreSQL（psycopg 3）、前端原生 JS 按功能平铺（无框架）、pytest + jsdom 行为测试。
- 远端：GitHub `monika2333/edu_news_pipeline`（origin/main）。单人开发 + AI 协作，任务经 worktree 或功能分支交付。
- **生产线在跑**：每小时抓取流水线、Windows 计划任务 EduNewsManualClusterRefresh（每 5 分钟重建聚类缓存）都依赖当前库结构——所以数据库变更只做加法（新表/新列），这是根 AGENTS.md 的硬约束。

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

- 平台 Windows + Git Bash。跑测试必须用仓库根 venv：`./venv/Scripts/python.exe -m pytest`（系统默认 python 缺依赖）。详见 workflow.md。
- DB 凭据在仓库根 `.env.local`（未跟踪）；DB 行为测试直连真实 Postgres（临时表模式），无 testcontainers。
- 本机 core.autocrlf=true 但仓库标准是 LF——工具写文件后要盯行尾（详见 workflow.md 提交卫生）。
- 推送 main 会被 worktree-guard 拦截，需 authorize-main → push → revoke-main 三步独立命令（详见 workflow.md）。
