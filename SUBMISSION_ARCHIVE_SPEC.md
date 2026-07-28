# 报送存档与查重功能规格（SUBMISSION_ARCHIVE_SPEC）

版本：v1.0
适用分支基线：`codex/multi-user-spec`
状态：待实现

---

## 0. 阅读须知（给实现方）

本文档是完整的实现规格，不需要再回头询问业务方。所有数据库改动**只新增表、不修改任何现有表结构**。所有阈值和窗口参数走环境变量，不得硬编码。

已确认的既有能力（直接复用，不要重新造）：

- 本地嵌入模型：`src/adapters/title_cluster.py` 已在使用 `BAAI/bge-large-zh`，`sentence-transformers` 已在 `requirements.txt`。本功能复用同一模型，**不引入新的模型依赖，不调用外部向量 API**。
- 迁移工具：dbmate，格式见 `database/migrations/`，SQL 小写风格，`-- migrate:up` / `-- migrate:down`。
- 用户体系：`console_users`、`console_user_sessions` 已存在，`decided_by` 类字段引用 `console_users.id`。
- 现有的"跳过已导出"逻辑（`get_all_exported_article_ids`）只做 `article_id` 精确匹配，**与本功能无关，不要改动它**。

明确不做的事（v1 范围外）：

- 不做 LLM 语义判重（预留 `match_method = 'llm'` 取值，但不实现）
- 不做 CSV / 文件上传导入，只有文本粘贴
- 不做采纳率分析、不做"对方有我们没报"的差集统计
- 不引入 pgvector

---

## 1. 背景与目标

### 1.1 业务背景

团队每天向上级单位报送教育舆情，产出三种文档：

| 类型 | `report_type` | 说明 |
|---|---|---|
| 综报 | `zongbao` | 《首都教育每日舆情综报》，当天整理当天报送 |
| 晚报 | `wanbao` | 《首都教育舆情》，前一晚整理、次日早报送，文档印次日日期 |
| 反馈 | `feedback` | 对方单位的定稿，含我们报送后被采用的条目，也含对方自行补充的条目 |

三份文档的条目**一旦出现，后续一律不再重复报送**（三者之间完全互斥，不区分类型）。

### 1.2 要解决的问题

同一事件会被多家媒体在数日内持续报道。现有系统只能靠 `article_id` 判重，换一家媒体转发就拦不住，值班编辑每天要在已报送过的内容上重复做判断。

### 1.3 目标

1. 把三种报告的历史条目沉淀成一个可检索的**已见刊语料库**
2. 每日管道自动将新新闻与该语料库比对，命中的在控制台打上「已报送」标记
3. 编辑可按「隐藏已报送」筛选，也可人工推翻误判
4. 尽可能把存档条目回链到系统内的 `article_id`（增值项，非阻塞项）

### 1.4 两个必须分清的任务

| | 回链（Link） | 查重（Dedup） |
|---|---|---|
| 方向 | 存档条目 → 系统 `article_id` | 新新闻 → 存档语料库 |
| 文本关系 | 近乎逐字，或完全无关 | 同一事件、措辞完全不同 |
| 技术 | 归一化 + 字符级相似度（纯代码） | 向量余弦相似度 |
| 触发时机 | 录入时一次性 | 每日管道 |
| 是否阻塞 | 否，失败不影响查重 | 是，核心功能 |

**回链失败不影响查重。** 存档条目只要有标题和正文，就能参与向量查重。实现时不得把回链做成录入成功的前置条件。

---

## 2. 数据模型

新增三张表。迁移文件建议命名 `20260728120000_add_submission_archive.sql`。

### 2.1 `submitted_reports`（报告级）

```sql
create table if not exists public.submitted_reports (
    id uuid primary key default gen_random_uuid(),
    report_type text not null,
    report_date date not null,
    compiled_date date not null,
    issue_no text,
    title_line text,
    pasted_text text not null,
    item_count integer not null default 0,
    imported_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint submitted_reports_type_check check (
        report_type in ('zongbao', 'wanbao', 'feedback')
    )
);

create index if not exists submitted_reports_type_date_idx
    on public.submitted_reports (report_type, report_date desc);
create index if not exists submitted_reports_report_date_idx
    on public.submitted_reports (report_date desc);
```

字段说明：

- `report_date`：**文档上印刷的日期**，解析器直接读取，不做任何加减
- `compiled_date`：**实际整理日期**。录入界面预填（综报同日、晚报和反馈减一天），允许人工修改。v1 不参与任何计算，仅作留存
- `issue_no`：原样保存（如 `2026年第488期（总第3766期）`）。反馈无期号，为 `NULL`
- `title_line`：文档首行标题（如 `首都教育每日舆情综报`）
- `pasted_text`：**用户粘贴进来的那份报告文档的原样文本**（含标题行、期号行、章节标记等解析后会被拆散的内容）。注意不是新闻原文。唯一用途是支持「重新解析」——解析器规则变更后可以拿它重跑，不需要人工重新粘贴。用条目字段拼不回来，因为头部和格式标记已丢失
- 无 `imported_by` 字段：录入固定由单一人员操作，记录操作者无信息量

**不加唯一约束。** 重复导入的防护在应用层做（见 4.5）。

### 2.2 `submitted_report_items`（条目级）

```sql
create table if not exists public.submitted_report_items (
    id uuid primary key default gen_random_uuid(),
    report_id uuid not null references public.submitted_reports(id) on delete cascade,
    section text,
    marker text,
    order_index integer not null default 0,
    title text not null,
    body text not null default '',
    source text,
    urls text[] not null default '{}'::text[],
    norm_title text not null,
    norm_title_hash text not null,
    embedding bytea,
    embedding_model text,
    embedded_at timestamptz,
    article_id text,
    link_status text not null default 'pending',
    link_title_score numeric(5,4),
    link_body_score numeric(5,4),
    link_combined_score numeric(5,4),
    best_candidate_article_id text,
    link_matched_at timestamptz,
    link_decided_by uuid references public.console_users(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint submitted_report_items_link_status_check check (
        link_status in ('pending', 'exact', 'fuzzy', 'manual', 'unmatched', 'rejected')
    )
);

create index if not exists submitted_report_items_report_idx
    on public.submitted_report_items (report_id, order_index);
create index if not exists submitted_report_items_norm_hash_idx
    on public.submitted_report_items (norm_title_hash);
create index if not exists submitted_report_items_article_idx
    on public.submitted_report_items (article_id)
    where article_id is not null;
create index if not exists submitted_report_items_link_pending_idx
    on public.submitted_report_items (link_status)
    where link_status = 'pending';
```

关键约束说明：

- **`article_id` 上不得建唯一约束。** 同一条新闻可能既进了晚报、又出现在反馈里，一对多是预期行为，不是数据错误。
- `article_id` **不设外键**到 `news_summaries`。存档条目的生命周期独立于管道数据。
- `link_status` 取值语义：
  - `pending`：尚未跑回链，或落在灰区待人工确认
  - `exact`：归一化标题哈希完全相等，自动绑定
  - `fuzzy`：相似度超过自动阈值，自动绑定
  - `manual`：人工在确认队列中确认绑定
  - `unmatched`：相似度低于复核阈值，判定为系统未覆盖（第 3 类）
  - `rejected`：人工在确认队列中否决
- `best_candidate_article_id` 与三个 score 字段：**即使判定为 `unmatched` 也要写入**。这是后续调阈值的唯一数据来源，缺了就只能重跑全量回链。

### 2.3 `submission_duplicate_matches`（查重结果）

```sql
create table if not exists public.submission_duplicate_matches (
    id uuid primary key default gen_random_uuid(),
    article_id text not null,
    item_id uuid not null references public.submitted_report_items(id) on delete cascade,
    similarity numeric(5,4) not null,
    match_method text not null,
    state text not null default 'suspected',
    decided_by uuid references public.console_users(id) on delete set null,
    decided_at timestamptz,
    detected_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint submission_duplicate_matches_method_check check (
        match_method in ('exact', 'vector', 'llm', 'manual')
    ),
    constraint submission_duplicate_matches_state_check check (
        state in ('suspected', 'confirmed', 'dismissed')
    ),
    constraint submission_duplicate_matches_unique unique (article_id, item_id)
);

create index if not exists submission_duplicate_matches_article_idx
    on public.submission_duplicate_matches (article_id);
create index if not exists submission_duplicate_matches_state_idx
    on public.submission_duplicate_matches (state);
```

**不在 `news_summaries` 或 `manual_reviews` 上增加任何「是否已报送」的列。** 三态在查询时用 `LEFT JOIN` 计算（见 6.4）。

---

## 3. 文本格式与解析规则

### 3.1 输入形态

一次粘贴**一份**报告，不含 `## 综报` 一类的外层分隔标记。典型结构：

```
首都教育每日舆情综报                     ← 标题行
2026年第488期（总第3766期）              ← 期号行（反馈无此行）
2026年7月27日                            ← 日期行

【重点关注舆情】                          ← 章节
★ 网民发帖称北京航空航天大学大运村施工甲醛超标    ← 条目标题
7月26日，小红书用户……（小红书 http://xhslink.cn/o/gIPnEWpbeo）   ← 正文 + 来源

★ 网民发帖指控中央美术学院学生毕设作品像素级抄袭
……
```

三种类型的差异：

| | 标题行 | 期号行 | 章节 | 条目标记 |
|---|---|---|---|---|
| 综报 | 含「综报」 | 有 | 重点关注舆情 / 新闻信息纵览 / 国内教育热点 | `★` `■` `▲` |
| 晚报 | 首都教育舆情 | 有（`总第X期`） | 舆情速览 / 舆情参考 | `一、` `二、`… |
| 反馈 | 首都教育舆情 | **无** | 舆情速览（可能有舆情参考） | `一、` `二、`… |

### 3.2 解析步骤

1. 按行切分，逐行 `strip()`，丢弃空行（但保留段落边界信息）。
2. **头部**：从第一行起，到第一个以 `【` 开头的行为止。从头部中提取：
   - `title_line` = 第一个非空行
   - `issue_no` = 匹配 `期` 字的行，原样保存；无则 `NULL`
   - `report_date` = 匹配 `(\d{4})年(\d{1,2})月(\d{1,2})日` 的行，转 date；**匹配不到则解析失败，报错提示用户检查**
3. **章节**：匹配 `^【(.+?)】$` 的行，捕获组存入 `section`（去掉方括号）。
   - **不要用白名单校验章节名**。原样存储任何 `【...】` 内容，格式微调不会导致解析失败。
4. **条目起始**：满足以下任一即为新条目起始行
   - `^[★■▲]\s*(.+)$`
   - `^([一二三四五六七八九十百]+、)\s*(.+)$`

   标记存入 `marker`，其余部分为 `title`。
5. **正文**：条目标题行之后，到下一个条目起始行或下一个章节行之前的所有行，用 `\n` 连接为 `body`。
6. **`order_index`**：在整份报告内全局递增，从 0 开始。**不按章节重置**（晚报的「舆情参考」章节序号会从「一、」重新开始，全局索引必须连续）。

### 3.3 来源与 URL 抽取

只处理 `body` **末尾**的全角括号组，正则锚定行尾：`（[^（）]*）\s*$`

括号内可能的形态：

- `（北京日报）` → source = `北京日报`，urls = `[]`
- `（小红书 http://xhslink.cn/o/gIPnEWpbeo）` → source = `小红书`，urls = 一条
- `（小红书 http://a、http://b）` → source = `小红书`，urls = 两条
- `（微博 https://weibo.com/x、哔哩哔哩 https://b23.tv/y）` → source = `微博 哔哩哔哩`，urls = 两条

**实现方式**：先用 `https?://[^\s、）]+` 把括号内所有 URL 全部提取到 `urls`；把 URL 从字符串中移除；剩余文本按 `、` 和空白切分、去空，用空格连接得到 `source`。这样对来源数量和排列顺序都不敏感。

抽取后从 `body` 中**移除**该括号组。

注意：正文中间也会出现全角括号（如 `《Wasteland Nomads（荒野游牧者）》`），必须锚定行尾，不得全局匹配。

### 3.4 归一化（`norm_title` / `norm_title_hash`）

对标题依次执行：

1. 去掉开头的 `marker`（`★■▲` 或 `一、` 等）
2. 全角转半角（数字、字母、标点）
3. 删除所有标点符号和空白字符（Unicode 类别 `P*`、`S*`、`Z*`、`C*`）
4. 结果存入 `norm_title`
5. `norm_title_hash` = `md5(norm_title)` 的十六进制字符串

**不做简繁转换**（业务语料全是简体，无收益）。

正文比对时也用同一套归一化函数，但不存储归一化后的正文。

### 3.5 类型识别

录入界面提供类型下拉框，但解析器要自动预判并预选：

```
if '综报' in title_line:            -> zongbao
elif issue_no is not None:          -> wanbao
else:                               -> feedback
```

如果用户手选的类型与自动预判不一致，**在预览页给出黄色警告但允许继续**（例如「你选了综报，但文本里没有期号，看起来像反馈」）。这能挡掉一整类静默的数据污染，成本极低。

### 3.6 解析失败的处理

解析器返回结构化结果，包含 `items` 和 `warnings`。以下情况计入 warnings 但不阻断：

- 某条目 `body` 为空
- 某条目未抽到 `source`
- 条目总数为 0（此时应阻断，提示格式不符）
- 未找到任何 `【章节】`（`section` 全部为 `NULL`，允许）

`report_date` 解析失败是**唯一的硬失败**。

---

## 4. 录入流程

### 4.1 交互步骤

1. 用户在控制台「报送存档 → 新增」页粘贴全文
2. 点击「解析」→ 前端调 `POST /api/submission-archive/parse`，后端返回解析结果，**不落库**
3. 预览页展示：
   - 头部信息：类型（下拉，预选）、`report_date`（预填，可改）、`compiled_date`（预填，可改）、`issue_no`
   - 条目列表：章节 / 标记 / 标题 / 正文 / 来源 / URL，**每条均可编辑，可删除，可手工新增**
   - warnings 区
4. 确认后 `POST /api/submission-archive/reports` 落库
5. 落库成功后**同步触发回链**，返回统计：`exact` / `fuzzy` 各多少、待确认多少、未命中多少
6. 页面提示「有 N 条待确认」，附跳转链接到确认队列

### 4.2 `compiled_date` 预填规则

| 类型 | 预填值 |
|---|---|
| zongbao | `report_date` |
| wanbao | `report_date - 1 天` |
| feedback | `report_date - 1 天` |

**这条规则只存在于录入界面的预填逻辑中，不得进入任何业务查询。** 反馈的实际间隔会因节假日变化，由人工在录入时修正。

### 4.3 落库事务

一次事务内完成：插入 `submitted_reports` → 批量插入 `submitted_report_items`（含归一化字段）→ 回填 `item_count`。

`embedding` 字段在此阶段留空，由第二期的向量任务异步填充（见 6.1）。

### 4.4 编辑与删除

- 支持整份报告删除（`ON DELETE CASCADE` 会连带清理条目和查重结果）
- 支持整份报告「重新解析」：以 `pasted_text` 为输入重跑解析，删除旧条目后重建。**已人工确认的回链结果会丢失**，界面上必须明确警告。
- 不支持落库后单条编辑正文（改了会导致向量与文本不一致）。要改就整份重新解析。

### 4.5 重复导入防护

落库前检查是否已存在相同 `(report_type, report_date)` 的报告。存在则返回冲突提示，让用户二选一：

- 取消
- 覆盖（先删除旧报告及其级联数据，再插入新的）

不做数据库唯一约束，因为极端情况下同一天同类型可能有增刊。

---

## 5. 回链算法（存档条目 → `article_id`）

### 5.1 候选池

对报告的 `compiled_date = C`，取 `news_summaries.created_at` 落在 `[C - LINK_WINDOW_DAYS, C + 1 天]` 的全部文章。默认 `LINK_WINDOW_DAYS = 3`。

为每个候选构建 `(cand_title, cand_body)`：

- `cand_title` = `news_summaries.title`
- `cand_body` = 以下第一个非空值：
  1. `manual_export_items.final_summary`
  2. `brief_items.final_summary`
  3. `manual_reviews.summary`
  4. `news_summaries.llm_summary`

顺序理由：越靠前越接近实际导出去的那份文本，第 1 类（逐字一致）应在第 1、2 项上直接命中。

### 5.2 两阶段计算

候选池可能有数千条，直接对每条算编辑距离过慢。分两步：

**粗筛**：对归一化标题做 2-gram 集合，算 Jaccard 相似度（纯集合运算，极快），保留 Top 20 候选。

**精算**：对这 20 条计算

```
title_score = SequenceMatcher(None, norm(archive_title), norm(cand_title)).ratio()
body_score  = SequenceMatcher(None, norm(archive_body)[:120], norm(cand_body)[:120]).ratio()
combined    = 0.6 * title_score + 0.4 * body_score      # 两者都有时
combined    = title_score                                # cand_body 为空时
```

正文只取前 120 字：报告里的正文是人工压缩过的，越往后差异越大，前段的判别力最高。

### 5.3 判定级别

按顺序判定，先命中先返回：

| 级别 | 条件 | `link_status` | 是否自动绑定 |
|---|---|---|---|
| L1 | `norm_title_hash` 与候选完全相等 | `exact` | 是 |
| L2 | `combined >= LINK_AUTO_THRESHOLD` 且 `title_score >= LINK_TITLE_MIN` | `fuzzy` | 是 |
| L3 | `LINK_REVIEW_THRESHOLD <= combined < LINK_AUTO_THRESHOLD` | `pending` | 否，进确认队列 |
| L4 | `combined < LINK_REVIEW_THRESHOLD` | `unmatched` | 否 |

L2 的 `title_score >= LINK_TITLE_MIN`（默认 0.70）是必要的守卫：教育舆情里「某某大学成立某某学院」这类标题高度同构，只靠合成分会把不同学校的新闻绑到一起。

**无论落在哪一级，都必须写入 `link_title_score`、`link_body_score`、`link_combined_score`、`best_candidate_article_id`**（取最高分候选）。L4 时 `article_id` 保持 `NULL`，但分数照记。

### 5.4 阈值默认值与调参路径

```
LINK_AUTO_THRESHOLD   = 0.85
LINK_REVIEW_THRESHOLD = 0.55
LINK_TITLE_MIN        = 0.70
```

这三个数字是**基于经验的初始值，不是实测结果**。灰区下界特意放宽到 0.55：上界定紧了只是多点几下鼠标，下界定紧了会静默丢弃真匹配。

录入两周存量后，用下面这类查询看分布并回调：

```sql
select link_status,
       width_bucket(link_combined_score, 0, 1, 20) as bucket,
       count(*)
from public.submitted_report_items
where link_combined_score is not null
group by 1, 2
order by 1, 2;
```

### 5.5 人工确认队列

独立页面，**不嵌在录入流程里**。录入一份报告要连点十几次确认会直接劝退录入行为，而语料库没人录就等于没有。

队列列出所有 `link_status = 'pending'` 的条目，左右对照展示（左：存档条目标题+正文；右：最佳候选的标题+摘要+来源+链接），显示三个分数。操作：

- 「确认绑定」→ `link_status = 'manual'`，写入 `article_id`、`link_decided_by`、`link_matched_at`
- 「不是同一条」→ `link_status = 'rejected'`，`article_id` 保持 `NULL`
- 「换一个候选」→ 展开 Top 5 候选让用户选（可选功能，可延后）

---

## 6. 查重算法（新新闻 → 存档语料库）

### 6.1 向量生成

模型：`BAAI/bge-large-zh`，复用 `src/adapters/title_cluster.py` 的模型加载单例（建议把 `_get_model()` 提取为可复用的公共函数，不要重复加载模型进内存）。

**存档条目**：编码文本 = `title + "\n" + body[:400]`
**新新闻**：编码文本 = `title + "\n" + llm_summary[:400]`

编码时 `normalize_embeddings=True`，得到 1024 维 float32 单位向量。

存储：`struct.pack(f'<{len(vec)}f', *vec)` 打包为 `bytea`。同时写入 `embedding_model` 和 `embedded_at`。

**不使用 pgvector。** 按每天约 40–50 条存档条目计算，15 天窗口内约 700 条，全年约 1.6 万条。1024 维 float32 全量载入内存仅约 60 MB，numpy 矩阵乘法暴力比对是毫秒级。引入 pgvector 在这个量级上是净负收益。

向量填充由一个补齐任务负责：查询 `embedding IS NULL` 的条目，批量编码写回。录入完成后触发一次，另在每日管道开始前兜底跑一次。

### 6.2 每日比对

在每日管道中，位置在**外部重要性评分之后、控制台可见之前**。

1. 载入窗口内存档向量：`report_date >= CURRENT_DATE - DEDUP_LOOKBACK_DAYS`（默认 15），且 `embedding IS NOT NULL`，堆叠为矩阵 `M`（形状 `N × 1024`）
2. 取当天待比对的新新闻，编码为矩阵 `Q`
3. 余弦相似度 = `Q @ M.T`（向量已归一化，点积即余弦）
4. 对每条新新闻取 Top `DEDUP_TOP_K`（默认 3）中相似度 `>= DEDUP_RECALL_THRESHOLD`（默认 0.72）的匹配
5. 每个匹配写一行 `submission_duplicate_matches`，`match_method = 'vector'`，`state = 'suspected'`
6. 额外规则：若某存档条目的 `article_id` 恰好等于新新闻的 `article_id`，直接写一行 `match_method = 'exact'`, `state = 'confirmed'`（罕见但零成本）

写入用 `ON CONFLICT (article_id, item_id) DO UPDATE`，只更新 `similarity` 和 `detected_at`；**已有 `state = 'dismissed'` 或 `'confirmed'` 的行不得覆盖 `state`**，人工决策优先于机器判定。

### 6.3 窗口语义

`DEDUP_LOOKBACK_DAYS = 15` 是**比对范围，不是保留范围**。语料库全量永久保留，窗口只限制每天自动拉取哪一段。因此调整窗口只需改环境变量，不需要重算任何数据。

窗口锚点用 `submitted_reports.report_date`，不用条目的 `created_at`——回填历史存档时入库时间全挤在同一天，用 `created_at` 会算错。

已知局限：重要政策文件（如「十五五」规划）的媒体跟进周期可能超过 15 天。业务方已确认这类由人工处理，不为此增加特殊逻辑。全量检索功能（6.5）覆盖这个需求。

### 6.4 三态计算

**不存储状态，查询时计算。** 在控制台待审列表的查询中加一段：

```sql
left join lateral (
    select
        bool_or(m.state = 'confirmed') as has_confirmed,
        bool_or(m.state = 'suspected') as has_suspected,
        max(m.similarity)              as top_similarity
    from public.submission_duplicate_matches m
    where m.article_id = ns.article_id
      and m.state <> 'dismissed'
) dup on true
```

三态映射：

| 条件 | 徽标 |
|---|---|
| `has_confirmed` | 已报送（实心） |
| `has_suspected and not has_confirmed` | 疑似已报送（空心） |
| 其余 | 无徽标 |

### 6.5 存档全量搜索

独立页面，按关键词搜 `title` 和 `body`，**不受 15 天窗口限制**。

```sql
select ... from public.submitted_report_items i
join public.submitted_reports r on r.id = i.report_id
where i.title ilike '%' || :kw || '%' or i.body ilike '%' || :kw || '%'
order by r.report_date desc
limit 50;
```

用途：编辑遇到跨度超过 15 天的持续性事件时，手工确认「这事以前报过没有」。实现成本极低但必须做——它是 15 天窗口这个简化决策的配套兜底。

---

## 7. 控制台改动

### 7.1 新增页面

| 路径 | 功能 |
|---|---|
| `/submission-archive` | 存档列表：按类型/日期筛选，显示条目数、回链统计 |
| `/submission-archive/new` | 粘贴录入 + 解析预览 |
| `/submission-archive/{id}` | 报告详情：条目列表、回链状态、重新解析、删除 |
| `/submission-archive/link-queue` | 回链人工确认队列 |
| `/submission-archive/search` | 全量关键词搜索 |

### 7.2 现有待审列表的改动

在条目卡片上增加：

- **已报送徽标**：三态，hover 显示 tooltip「7月26日 晚报：{存档条目标题}（相似度 0.83）」，多个匹配时按 `report_date` 升序展示，取最早的一条为主
- **「不是重复」按钮**：仅在有徽标时出现，点击后把该条相关的所有 `suspected` 匹配置为 `dismissed`，记录 `decided_by` / `decided_at`，徽标即时消失
- **筛选开关「隐藏已报送」**：过滤掉 `has_confirmed or has_suspected` 的条目，默认关闭

### 7.3 反馈条目重复展示的处理

我们自己报送过的新闻，会同时出现在综报/晚报和对方的反馈里，语料库中因此存在两行近似记录。

- **数据层保留两行**，不在入库时合并——合并后无法再算「哪些被采用了」
- **展示层去重**：徽标 tooltip 按 `article_id`（无则按 `norm_title_hash`）分组，只显示 `report_date` 最早的那一条，其余折叠为「另有 N 条记录」

### 7.4 权限

所有存档相关页面要求已登录。录入、删除、重新解析限管理员；回链确认、「不是重复」值班编辑亦可操作。具体角色对应现有权限体系。

---

## 8. 参数与配置

### 8.1 原则

**全部参数以代码常量为默认值**，集中定义在一个模块内（建议 `src/console/submission_archive_config.py` 或等价位置），不散落在各处。

其中少数几个通过环境变量**可选覆盖**——即读取 ENV，读不到就用代码默认值。`.env` 里**不需要预先写出这些项**，需要调的时候再加。

### 8.2 代码常量（不可通过 ENV 覆盖）

```python
EMBED_MODEL        = "BAAI/bge-large-zh"   # 见下方警告
EMBED_BODY_CHARS   = 400                   # 存档条目/新闻编码时截取的正文长度
DEDUP_TOP_K        = 3                     # 每条新闻最多记录几个匹配
LINK_WINDOW_DAYS   = 3                     # 回链候选池的日期半径
LINK_TITLE_MIN     = 0.70                  # 自动关联的标题相似度守卫
LINK_BODY_CHARS    = 120                   # 回链正文比对截取长度
LINK_COARSE_TOP_K  = 20                    # 粗筛保留的候选数
```

> **`EMBED_MODEL` 严禁做成可配置项。**
> 更换模型会使新算出的向量与库中已存向量落在不同的语义空间，比对结果**静默失效**——不报错，只是判断全部变成噪声。
> 如果确有更换需求，正确流程是：改代码常量 → 清空全部 `embedding` 字段 → 重跑向量补齐任务。
> `submitted_report_items.embedding_model` 字段的作用就是留存判据：比对前应校验库中向量的 `embedding_model` 与当前常量一致，不一致时拒绝比对并告警，而不是继续算。

### 8.3 ENV 可覆盖项（共 4 个）

| ENV 名 | 默认值 | 何时会调 |
|---|---|---|
| `SUBMISSION_DEDUP_LOOKBACK_DAYS` | `15` | 业务规则，可能随实际热度周期调整 |
| `SUBMISSION_LINK_AUTO_THRESHOLD` | `0.85` | 录入两周存量后按分数分布校准（计划内） |
| `SUBMISSION_LINK_REVIEW_THRESHOLD` | `0.55` | 同上 |
| `SUBMISSION_DEDUP_RECALL_THRESHOLD` | `0.72` | 上线后按编辑点「不是重复」的频率调整 |

只有这 4 项需要在 `docs/env_reference.md` 中登记，并注明"不配置则使用代码默认值"。

### 8.4 后续新增

若日后发现 8.2 中某个常量确实需要调整，把它改成"读 ENV、缺省回落到常量"即可，是一行改动。不要为了预留可能性提前把全部参数环境变量化。

---

## 9. 实施顺序

### 第一期：录入闭环

1. 迁移文件（三张表）
2. 解析器模块（纯函数，无 IO，可单测）
3. 归一化工具函数（回链和解析共用）
4. 录入 API + 预览页 + 存档列表页 + 详情页
5. 回链模块（粗筛 + 精算 + 判定）
6. 回链确认队列页
7. 全量搜索页

**验收标准**：能把最近两周的三种报告全部粘贴入库；回链统计中 `exact + fuzzy` 占比合理（预期六成以上，具体看实测）；灰区条目能在队列中逐条确认。

第一期完成后先投入使用，把存量录入，为第二期积累比对基线。

### 第二期：查重闭环

1. 向量补齐任务
2. 每日管道比对步骤
3. 徽标三态查询与前端渲染
4. 「隐藏已报送」筛选
5. 「不是重复」人工覆盖

**验收标准**：当天新闻中确实与近 15 天报送重复的条目能被标出；误判可一键清除；编辑开启筛选后待审量有可观察的下降。

### 不在本规格内（第三期候选）

- LLM 语义精判（只跑向量灰区）
- 采纳率分析：反馈里有、我们未报送且发布时间落在值班窗口内的条目 → 接入评分改进闭环
- 事件指纹（在现有 summary prompt 中附带产出结构化事件要素）

---

## 10. 单元测试要求

解析器和归一化是纯函数，必须有测试覆盖：

- 三种类型各一份完整样例的解析（条目数、章节、标记、来源、URL 全部断言）
- 来源括号的四种形态（无 URL / 单 URL / 多 URL / 多来源多 URL）
- 正文中间含全角括号时不被误判为来源
- 晚报「舆情参考」章节序号重新从「一、」开始时 `order_index` 仍全局连续
- 归一化：全角半角、标点删除、感叹号差异归一后哈希相等
- 缺日期行时抛出解析错误
- 回链判定：L1/L2/L3/L4 四条分支各一例，含 `title_score` 守卫生效的反例
