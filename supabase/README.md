# Supabase 数据库初始化指南

本项目暂未直接接入 Supabase 实例。若要在自己的 Supabase 项目中创建数据库结构，可按以下步骤操作：

## 1. 本地准备
1. 安装 [Supabase CLI](https://supabase.com/docs/guides/cli)。
2. 登录 CLI：
   ```bash
   supabase login
   ```
3. 在项目根目录执行初始化（如尚未有 `supabase/` 目录，可选择保留现有结构）：
   ```bash
   supabase init
   ```

## 2. 应用数据库 Schema
1. 将 `schema.sql` 拷贝/合并到 `supabase/migrations` 或 `supabase/config` 中：
   - 推荐方式：
     ```bash
     cp supabase/schema.sql supabase/migrations/$(date +%Y%m%d%H%M%S)_init.sql
     ```
2. 推送到远程 Supabase：
   ```bash
   supabase db push
   ```
   或者在 Supabase Studio > SQL Editor 中粘贴 `schema.sql` 执行。

## 3. 配置 RLS 与 Policy（示例）
Schema 中尚未启用 Row Level Security。创建表后，可根据业务需要执行如下命令：
```sql
alter table public.news_summaries enable row level security;
create policy "Editors can manage news summaries" on public.news_summaries
    for all using (auth.role() = 'authenticated');
```
> 具体策略应结合实际的角色设计进行细化。

## 4. 后续扩展建议
- 在 `supabase/seed.sql` 中补充示例数据，便于本地开发调试。
- 将自动化迁移整合进 CI/CD 流程，确保 Schema 变更可追踪。

如需进一步自动化（例如脚本化创建表、初始化数据），可在该目录继续补充脚本。当前仓库未包含 Supabase 凭据，请在本地或 CI 环境自行配置 `SUPABASE_ACCESS_TOKEN` 等敏感信息。
