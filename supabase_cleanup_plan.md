# Supabase 清理计划

## 目标
- 完全移除 Supabase 依赖，统一使用本地 Postgres 存储。
- 清理无效的配置、脚本以及文档，减少后续维护成本。
- 确保流水线功能（采集、总结、打分、导出、通知）在移除 Supabase 后仍然可用。

## 工作分解
1. **梳理现状与差异**
   - 通读 src/adapters/db_supabase.py、src/adapters/db_postgres.py，确认 Postgres 适配器已经实现 Supabase 所需的接口；记录任何功能差异或缺失的方法。
   - 检查 src/adapters/http_toutiao.py、src/cli/main.py 等是否仍引用 Supabase 名称或配置，并列出待修改点。
   - 整理仓库中 Supabase 相关资产：supabase/ 目录、scripts/migrate_supabase_to_local.py、docs/supabase_*.md、
equirements.txt 中的依赖、.env 模板字段等。

2. **锁定 Postgres 作为唯一数据库后端**
   - 更新 src/config.py：移除 Supabase 相关字段与自动选择逻辑，只保留 Postgres 所需配置。
   - 调整 src/adapters/db.py：删除懒加载 Supabase 适配器的分支，固定返回 Postgres 适配器。
   - 搜索整个代码库中关于 db_backend、USE_SUPABASE 的引用并清理。

3. **删除 Supabase 适配器及调用代码**
   - 移除 src/adapters/db_supabase.py 及任何对它的引用。
   - 清理 src/adapters/http_toutiao.py 中与 Supabase 交互的逻辑（上传脚本、配置类、命令行参数），视需求保留纯抓取能力。
   - 检查 src/workers/export_brief.py、src/workers 目录下其它模块，替换/重命名所有 Supabase 特定文案（例如 
ecord_history 帮助信息）。

4. **更新依赖与环境配置**
   - 在 
equirements.txt 中删除 supabase 相关条目，确认无其它包依赖它。
   - 移除 .env.local / config/abstract.env / README 中的 SUPABASE_* 变量说明，改为仅描述 Postgres 环境变量。
   - 如有 CLI 或脚本参数默认指向 Supabase（例：--record-history 描述），同步更新文案。

5. **清理冗余文件与文档**
   - 删除 supabase/ 目录及所有 Supabase 指南、迁移 SQL、示例文档。
   - 移除 docs/ 下的 Supabase 迁移步骤、指南等文件。
   - 更新主 README.md、console_dashboard_plan.md 等文档，去掉 Supabase 说明，必要时补充 Postgres-only 的部署指引。

6. **移除脚本与工具链耦合**
   - 删除或改写 scripts/migrate_supabase_to_local.py 等仅服务 Supabase 的脚本。
   - 检查任何自动化流程（Makefile、CI、调度脚本）是否包含 Supabase 命令并清理。

7. **回归测试与验证**
   - 运行现有测试（如 pytest、
uff/lake8 等）确保清理未引入回归。
   - 手动执行关键工作流：python -m src.cli.main crawl/summarize/score/export，确认数据库读写正常，导出与通知流程可用。
   - 最后检查 Git 变更，确认无遗留 Supabase 相关文件后再进行提交。

8. **收尾**
   - 更新 CHANGELOG（若有）或在合并描述中记录此次 Supabase 清理工作。
   - 提醒团队同步清理 CI/CD、部署环境中的 Supabase 凭据与资源。

> 以上步骤完成后，我们将逐项执行并验证，确保流水线在纯 Postgres 环境下稳定运行。

## ????
- [x] ?? 1???????????? Postgres ??????????????? Supabase ????????
- [x] ?? 2??? Postgres ????????????? Supabase ????? `db_backend` ???`src/adapters/db.py` ???? Postgres ????
- [x] ?? 3??? Supabase ???????????? `src/adapters/db_supabase.py`??? Toutiao ??????????? CLI ???????????

## 进度更新
- [x] 步骤 1：梳理现状与差异——确认 Postgres 适配器覆盖全部流水线接口，列出 Supabase 存量代码与文档。
- [x] 步骤 2：锁定 Postgres 作为唯一数据库后端——移除 Supabase 配置字段与 `db_backend` 逻辑，`src/adapters/db.py` 固定返回 Postgres 适配器。
- [x] 步骤 3：删除 Supabase 适配器及调用代码——移除 `src/adapters/db_supabase.py`，精简 Toutiao 抓取脚本上传逻辑，并将 CLI 文案更新为数据库措辞。
- [x] 步骤 4：更新依赖与环境配置——移除 `requirements.txt` 中的 Supabase 依赖，清理 `.env.local` 并同步 README 中的环境说明。
- [x] 步骤 5：清理冗余文件与文档——迁移数据库 schema 至 `database/` 目录，删除 `supabase/` 与相关指南，更新控制台规划文档。
