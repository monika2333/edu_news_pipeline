# Edu News Pipeline

面向教育新闻的自动化采集、评分、摘要与导出流水线，并提供 Web 控制台进行人工筛选与复核。

## 功能总览
- **流水线**：抓取 → 去重 → 评分 → 摘要 → 情感/来源补充 → 北京/外地分流与重要性评分 → 与历史报送查重 → 导出简报。
- **Web 控制台**：管理员使用 `/manual_filter` 全量复核，值班编辑使用 `/duty` 处理本人班次，用户与排班在 `/admin` 管理。
- **报送存档与查重**：`/submission-archive` 录入已报送稿件并回链到系统内新闻；新入库的新闻会与存档比对，在复核界面提示"已报过"。
- **导出/预览**：支持在审阅页按综报/晚报预览文本并归档为已导出；流水线 `export` 命令仍可生成 TXT 简报并推送飞书。

## 快速开始
1) 安装依赖
```bash
pip install -r requirements.txt
```
2) 应用数据库迁移并创建至少两个管理员账号
```bash
dbmate --migrations-dir database/migrations --schema-file database/schema.sql up
python -m src.cli.main create-console-user --username admin-a --display-name "管理员 A" --role admin
python -m src.cli.main create-console-user --username admin-b --display-name "管理员 B" --role admin
```

3) 启动控制台（默认 8000）
```bash
python run_console.py
```

4) 运行流水线单步（示例）
```bash
python -m src.cli.main crawl --sources toutiao,tencent --limit 5000
python -m src.cli.main hash-primary
python -m src.cli.main score
python -m src.cli.main summarize
python -m src.cli.main enrich-summary
python -m src.cli.main geo-classify
python -m src.cli.main external-filter
python -m src.cli.main submission-dedup
python -m src.cli.main export
```
可用 `-h` 查看每个步骤的参数。

## 命令行一览

`python -m src.cli.main <command>`，全部命令可用 `-h` 查看参数。

### 主流水线（按顺序执行，由计划任务串联）

| 命令 | 作用 |
| --- | --- |
| `crawl` | 从配置的来源抓取新文章 |
| `hash-primary` | 计算指纹、去重选主 |
| `score` | 为主文打相关性分 |
| `summarize` | 生成摘要 |
| `enrich-summary` | 补充情感与来源信息 |
| `geo-classify` | 判定北京/外地 |
| `external-filter` | 外地新闻的重要性评分 |
| `submission-dedup` | 与近期报送存档比对查重 |
| `export` | 生成 TXT 简报并推送飞书 |

### 计划任务后台调用

| 命令 | 作用 |
| --- | --- |
| `refresh-manual-clusters` | 刷新人工筛选页的标题聚类缓存 |
| `generate-shifts` | 按周排班模板生成后续班次 |
| `cleanup-console-sessions` | 清理过期与长期失效的登录会话 |

### 管理员按需手工执行

| 命令 | 作用 |
| --- | --- |
| `create-console-user` | 创建控制台账号 |
| `geo-tag` | 为存量摘要回填北京相关标记 |
| `backfill-submission-embeddings` | 补齐报送存档条目缺失的向量 |

## Web 控制台
- 默认地址：`http://127.0.0.1:8000`，未登录时进入登录页，登录后按角色进入对应工作台。
- **/manual_filter**
  - 默认按地域/情感聚类展示，可切换桶（京内正/京内负/京外正/京外负/全部）。
  - 卡片摘要可编辑，状态下拉/批量设置会自动保存并移动到对应列，放弃/待处理会移出视图。
  - 审阅页支持排序模式（紧凑卡片 + 拖拽），导出弹窗支持预览/正式导出。
- **/duty**
  - 值班编辑只能读取和修改本人班次，可筛选、排序、编辑综报/晚报归属并预览草稿，不能归档。
- **/submission-archive**
  - 粘贴已经报送出去的稿件，系统自动拆分条目并回链到系统内对应的新闻。
  - 相似度落在中间地带的条目进入人工确认队列，由管理员判定是否为同一条。
  - 存档内容用于查重：新入库的新闻与历史报送比对后，会在复核界面显示重复标记。
- **/admin** 与 **/admin/duty-summary**
  - 管理账号、七天轮值模板与具体班次，查看值班结果、未覆盖新闻并选择性送入管理员工作区。

## 配置要点（.env / .env.local）
完整环境变量说明、必填项和示例模板见 [docs/env_reference.md](docs/env_reference.md)。

## 数据库迁移 (Database)

我们使用 **Dbmate** 进行数据库版本管理。请确保设置了 `DATABASE_URL` 环境变量，以便 dbmate 识别。

### 常用操作
```powershell
# 设置环境变量 (PowerShell)
$env:DATABASE_URL="postgres://postgres:Postgres2025@localhost:5432/edu_news_pipeline?sslmode=disable"

# 查看迁移状态
dbmate status

# 执行迁移 (升级)
dbmate up

# 创建新迁移文件
dbmate new <migration_name>
# 示例: dbmate new add_users_table

# 回滚迁移 (撤销)
dbmate down
```

### 注意事项
- 迁移文件保存在 `database/migrations/`。
- 如果 `DATABASE_URL` 格式不正确，dbmate 会提示 "invalid url"。
- 数据库变更遵循只增不改的原则：新增表或新增列，不修改、不删除现有结构。表的职责与不可变字段见 [docs/data_flow.md](docs/data_flow.md)。

## 目录结构

见 [AGENTS.md](AGENTS.md) 的「文件结构」章节。各数据表的职责与流转路径见 [docs/data_flow.md](docs/data_flow.md)。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [AGENTS.md](AGENTS.md) | 开发约束、目录结构、依赖方向、数据库访问约定 |
| [docs/data_flow.md](docs/data_flow.md) | 数据表职责、流转路径、契约级约束 |
| [docs/env_reference.md](docs/env_reference.md) | 全部环境变量说明与示例模板 |
| [docs/console_auth.md](docs/console_auth.md) | 控制台认证机制 |

## 说明
- 控制台访问默认仅监听本机，部署到外网时务必开启认证。
- 如果数据库不可用，部分接口会降级为空结果以保证页面可访问。
