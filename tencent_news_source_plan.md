# 腾讯网新闻来源集成计划

## 目标
- 将腾讯网作者文章抓取能力融合进现有 EDU News Pipeline 的多数据源框架。
- 保留并吸收 `newsqq_crawl` 脚本中的有效逻辑，同时迁移配置与数据文件至项目正式结构。
- 移除临时脚本目录，避免后续维护分歧。

## 步骤计划

### 1. 现有实现审查
- [x] 阅读 `src/workers/crawl_sources.py` 当前各来源（今日头条/中新网/光明网等）流程，确定适配 Tencent 所需的抽象接口与数据库交互节点。
- [x] 分析 `newsqq_crawl/qq_author_crawler.py`，提炼作者信息拉取、分页列表、正文抓取与 Markdown 转换的核心逻辑及重试/速率控制要求。

### 2. HTTP 适配器与配置支持
- [x] 在 `src/adapters/` 下新增 `http_tencent.py`，实现作者 ID 解析、作者列表与详情 API 调用、内容 Markdown 化和时间戳标准化。
- [x] 设计作者配置加载入口：新增 `config/qq_author.txt`（UTF-8），支持环境变量覆盖路径，借鉴头条作者配置解析。
- [x] 处理请求限速、错误重试与临时失败回退，保持与现有风格一致。

### 3. 抓取 Worker 集成
- [x] 在 `crawl_sources` 中新增 `_run_tencent_flow`，复用“读作者→拉取 feed→写入 raw_articles→补全正文→关键词过滤→入库 filtered_articles”流程。
- [x] 将 `tencent`（必要时含 `qq` 别名）纳入 `run()` 的 `sources` 参数解析，并在 CLI `crawl` 子命令文档中说明用法。
- [x] 确保关键词过滤、去重、数据库调用与其他来源保持一致。

### 4. 文档与目录清理
- [ ] 更新 README 或相关文档，介绍腾讯来源配置、运行方式与环境变量。
- [ ] 将 `newsqq_crawl/qq_author.txt` 迁至 `config/`，确认引用位置更新。
- [ ] 删除 `newsqq_crawl/` 目录及其文件，保证仓库无冗余脚本。

### 5. 验证与收尾
- [ ] 运行最小化集成测试（例如：`python -m src.cli.main crawl --sources=tencent --limit=<小值>`），验证功能闭环。
- [ ] 检查 `git status`，确保只有计划内的改动。
- [ ] 根据测试结果修正问题，准备提交或交付。
