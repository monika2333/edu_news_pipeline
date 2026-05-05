# 人工筛选控制台改造实施方案

## 背景

当前人工筛选控制台已经能稳定运行，但第一页 `filter` tab 仍然存在两个明显短板：

1. 缺少按日期处理积压新闻的能力。
2. 缺少针对当前桶内待处理新闻的检索能力。

这两个需求都属于 `manual_filter` 领域，不适合继续依附现有的全库搜索抽屉实现。现有抽屉搜索走 `/api/articles/search`，本质上查询的是 `news_summaries` 全库，而不是当前桶内的 `manual_reviews pending` 集合。

## 当前现状

### 后端

- `manual_filter` 的第一页数据入口是 `/api/manual_filter/candidates`
- 主要逻辑位于 `src/console/manual_filter_service.py`
- 聚类逻辑位于 `src/console/manual_filter_cluster.py`
- manual review 查询 SQL 位于 `src/adapters/db_postgres_manual_reviews.py`
- 全库搜索 SQL 位于 `src/adapters/db_postgres_news_summaries.py`

### 前端

- 第一页逻辑主要在 `src/console/web_static/js/manual_filter/filter_tab.js`
- 审阅页页内搜索在 `src/console/web_static/js/manual_filter/review_tab.js`
- 右侧搜索抽屉在 `src/console/web_static/js/manual_filter/search_drawer.js`
- 全局前端状态在 `src/console/web_static/js/manual_filter/core.js`

### 当前发布日期字段使用情况

当前控制台和搜索相关逻辑里，真正承担“发布日期”语义的主字段是：

- `news_summaries.publish_time_iso`

相关时间字段可区分为：

- `publish_time_iso`
  - 规范化后的发布日期时间
  - 当前最适合做日期筛选和排序
- `publish_time`
  - 同一发布日期的 Unix 时间戳版本
  - 更适合作为兼容回退
- `fetched_at`
  - 抓取时间，不是发布日期
- `detail_fetched_at`
  - 详情页抓取时间，不是发布日期
- `created_at` / `updated_at`
  - 数据库记录创建和更新时间，不是发布日期

当前代码里的实际使用方式也基本一致：

- `manual_filter` 查询排序优先用 `ns.publish_time_iso`
- 全库搜索的日期过滤直接使用 `publish_time_iso`
- 前端展示日期时优先显示 `publish_time_iso`，没有时才回退 `publish_time`

因此本次“日期筛选”和“桶内搜索”里的日期条件，推荐采用：

1. 主字段使用 `publish_time_iso`
2. 对极少数 `publish_time_iso` 为空的数据，再考虑回退 `publish_time`

这样能最大限度保持“按新闻真实发布日期筛选”的语义，而不是混入抓取时间或入库时间。

## 核心判断

### 1. 日期筛选应当是服务器端批量操作

目标不是“前端隐藏旧新闻”，而是“把旧的 pending 新闻从工作台移走”。因此日期筛选应直接落到 `manual_reviews` 批量状态更新，而不是只在页面层做显示过滤。

推荐默认语义：

- 作用范围：当前桶
- 作用状态：`pending`
- 数据前提：`news_summaries.status = 'ready_for_export'`
- 条件：优先按 `publish_time_iso < 指定日期` 判断，必要时再回退 `publish_time`
- 动作：批量转为 `discarded`

### 2. 第一页检索应当是 manual_filter 专属查询

用户想要的是：

- 只搜当前桶
- 只搜第一页待处理集
- 搜到以后直接人工批量判断

这不应复用 `/api/articles/search`。更合适的做法是为 `manual_filter` 新增专门查询接口。

### 3. 先做小整理，不做大重构

现有结构还能继续演进，但不适合继续把新需求直接堆进现有函数参数里。建议先做一轮轻量整理，重点是“理顺边界”，不是“推倒重来”。

## 目标方案

### 产品行为

在第一页 `filter` tab 顶部增加一个轻量工具栏，支持：

- 关键词搜索框
- 日期截止输入框
- 搜索按钮
- 清空按钮
- 批量丢弃旧新闻按钮

第一页存在两种工作模式：

1. 浏览模式
   - 维持当前按桶浏览和聚类浏览的逻辑
2. 搜索模式
   - 对当前桶执行关键词和日期条件查询
   - 返回平铺卡片列表
   - 不启用聚类

原因：

- 聚类适合浏览重复内容
- 搜索适合精确定位和批量处理
- 两者混在同一结果视图里会增加理解成本

## 推荐交互

### A. 日期批量处理

工具栏提供：

- `截止日期` 输入框
- `丢弃该日期前旧新闻` 按钮

点击后流程：

1. 前端先请求预览接口，返回命中数量
2. 弹出确认提示，例如“将丢弃当前桶中 126 条 2025-12-31 之前的待处理新闻”
3. 用户确认后执行批量更新
4. 刷新当前页、计数和聚类

### B. 桶内搜索

工具栏提供：

- `关键词` 输入框
- `截止日期` 可作为附加过滤条件

行为：

- 搜索只针对当前桶
- 搜索只针对 `pending + ready_for_export`
- 返回第一页可操作卡片
- 卡片继续复用当前单条决策和“放弃本页剩余内容”能力

## API 设计

### 1. 扩展候选查询接口

保留现有：

- `GET /api/manual_filter/candidates`

新增查询参数：

- `q: Optional[str]`
- `published_before: Optional[date]`
- `view_mode: Optional[str]`

建议语义：

- 当 `q` 或 `published_before` 存在时，走“搜索模式”
- 搜索模式默认 `cluster = false`
- 普通浏览模式维持原逻辑

说明：

`view_mode` 不是必须项，但建议保留，后续可以明确区分：

- `browse`
- `search`

### 2. 新增日期批量处理接口

建议新增：

- `POST /api/manual_filter/discard_before_date`

请求体：

```json
{
  "region": "internal",
  "sentiment": "positive",
  "published_before": "2025-12-31",
  "actor": "xxx",
  "report_type": "zongbao",
  "dry_run": true
}
```

响应体：

```json
{
  "matched": 126,
  "updated": 0
}
```

当 `dry_run=false` 时：

```json
{
  "matched": 126,
  "updated": 126
}
```

这样可以先预览再执行，交互更稳。

## 后端设计

### 1. 在 `manual_filter_service.py` 中拆出“第一页查询”边界

当前 `list_candidates(...)` 既负责入口，又间接负责普通分页和聚类分页。建议引入更清晰的职责划分：

- `list_candidates(...)`
- `_list_candidate_browse(...)`
- `_list_candidate_search(...)`
- `_serialize_manual_filter_item(...)`

目标：

- service 不再重复写整形逻辑
- 聚类和非聚类结果都复用统一的 item serializer

### 2. 在 adapter 层新增 manual filter 专属搜索查询

建议在 `src/adapters/db_postgres_manual_reviews.py` 新增函数：

- `search_manual_candidates(...)`
- `count_manual_candidates_before_date(...)`
- `discard_manual_candidates_before_date(...)`

推荐查询条件：

- `mr.status = 'pending'`
- `ns.status = 'ready_for_export'`
- `ns.is_beijing_related = ...`
- `ns.sentiment_label = ...`
- `publish_time_iso < ...`
- 文本命中 `title / llm_summary / content_markdown`

这里不要复用 `search_news_summaries(...)`，因为后者的实体边界是全库文章，不是人工筛选工作集。

日期字段策略建议固定在这一层：

- 默认只按 `publish_time_iso` 过滤
- 如果确认线上存在一定比例的空值，再增加兼容回退：
  - `publish_time_iso IS NOT NULL AND publish_time_iso < ...`
  - `publish_time_iso IS NULL AND publish_time < ...`

不建议一开始直接混用 `fetched_at` 或 `created_at`，否则筛选语义会从“发布日期”偏移到“抓取时间”或“入库时间”。

### 3. 统一 serializer

当前这些字段组装逻辑在多个位置重复出现：

- `summary`
- `manual_status`
- `bonus_keywords`
- `report_type`
- `llm_source_display`
- `group_key`

建议抽成单一函数，例如：

- `_serialize_manual_filter_record(record, *, fallback_status, report_type)`

这样：

- `_paginate_by_status(...)`
- `_collect_pending(...)`
- 新的搜索模式

都能复用同一套输出格式。

## 前端设计

### 1. 在 `manual_filter.html` 中为第一页新增工具栏

建议放在当前 `filter-content` 顶部，位于 `#filter-list` 之前。

建议控件：

- `input#filter-search-input`
- `input#filter-date-before`
- `button#btn-filter-search`
- `button#btn-filter-clear`
- `button#btn-filter-discard-before-date`

### 2. 第一页前端状态独立维护

在 `core.js` 的 `state` 中增加：

- `filterQuery`
- `filterPublishedBefore`
- `filterMode`

推荐默认值：

- `filterQuery: ''`
- `filterPublishedBefore: ''`
- `filterMode: 'browse'`

### 3. `filter_tab.js` 增加两条明确路径

- `loadFilterBrowseData()`
- `loadFilterSearchData()`

再由 `loadFilterData()` 统一分发。

这样可以避免把浏览逻辑和搜索逻辑继续写在同一个大函数里。

### 4. 搜索结果仍复用现有卡片交互

第一页已有这些能力：

- 单条采纳/备选/放弃
- 卡片摘要修改
- 来源修改
- 放弃当前页剩余内容
- 撤销

搜索模式应全部复用，不新增另一套卡片组件。

## 实施顺序

### Phase 1: 小整理

目标：

- 补出 manual filter 第一页的独立查询边界
- 抽出统一 serializer
- 保持现有功能不变

产出：

- 更清晰的 `manual_filter_service.py`
- adapter 层新增 manual filter 专用查询函数骨架

### Phase 2: 日期批量处理

目标：

- 支持按当前桶和截止日期批量丢弃旧新闻

产出：

- 新后端接口
- 前端确认流程
- 计数刷新与聚类刷新
- 覆盖服务层测试

### Phase 3: 第一页桶内搜索

目标：

- 在当前桶内按关键词和日期查询待处理新闻

产出：

- 搜索模式 UI
- 搜索模式 API
- 搜索结果可直接批量决策

### Phase 4: 细节优化

可选项：

- 记住第一页最近一次搜索条件
- 搜索状态栏展示“当前命中数量”
- 搜索模式增加“仅标题”“标题加摘要全文”切换

## 测试建议

### 服务层

扩展 `tests/test_manual_filter_service.py`，至少补：

- 日期预览数量正确
- 日期批量丢弃只影响当前桶
- 搜索模式只返回 `pending + ready_for_export`
- 搜索模式不会串到其他桶
- 搜索模式结果仍带齐前端依赖字段

### adapter 层

如已有 adapter 测试模式，建议补：

- 文本查询条件拼接
- 日期条件边界
- `dry_run` 与真实更新的行为差异

### 前端

如果暂时不引入前端自动化测试，至少做手工回归：

1. 浏览模式加载正常
2. 搜索模式能切换回浏览模式
3. 搜索结果中单条决策正常
4. 日期批量丢弃后统计数字刷新正常
5. 丢弃后重新刷新不会重复出现

## 已知风险

### 1. 当前测试环境可能不完整

本地会话里 `python -m pytest` 无法直接执行，提示 Python 可执行路径异常。开始真实改功能前，需要先确认本地测试命令是否可用。

### 2. 控制台存在历史编码问题

部分模板和注释内容存在乱码痕迹。此次改造不建议顺手大面积清理编码问题，否则会扩大变更面。

### 3. `manual_filter_service.py` 仍承担兼容门面角色

这一轮不建议彻底重构为完整 package，但建议逐步把第一页查询、决策、聚类继续向明确模块边界靠拢。

## 最终建议

这次改造的正确方向不是“先全面重构”，而是：

1. 先做小整理，明确第一页查询边界。
2. 先上日期批量丢弃，快速解决积压问题。
3. 再上桶内搜索，提升人工处理效率。

这样能在不显著放大风险的前提下，把最痛的两个使用问题先解决掉。
