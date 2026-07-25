# 多用户值班协作 —— 实施规格

本文档是实施依据,描述最终方案。所有内容均为待实现的目标状态。

---

## 1. 概述

为 `edu_news_pipeline` 控制台增加多用户支持:7 名值班编辑 + 2 名管理员,单团队,日常并发不超过 5。

继续使用一个 FastAPI 服务和一个 PostgreSQL 数据库。不引入微服务、Redis、消息队列或分布式锁。

工作流分两层:

1. **值班编辑**在自己的班次范围内完成筛选、备选、放弃、排序、摘取、摘要编辑和预览。
2. **管理员**查看全量新闻和所有值班结果,进行复核并执行最终归档。

### 1.1 每日节奏

| 时刻 | 事件 |
| --- | --- |
| 15:30 左右 | 值班编辑提交综报 |
| 17:00 左右 | 管理员归档综报 |
| 20:00–21:00 | 值班编辑做晚报,之后收工 |
| 21:00–22:00 | 新闻仍在流入,由管理员在归档前一并处理 |
| 22:00–23:00 | 管理员归档晚报 |

一天两次产出,由同一名值班编辑在同一个班次内完成,用 `report_type` 区分去向。

### 1.2 范围外

- 不支持多租户,不引入 `team_id`。
- 不为用户建立独立数据库或数据表。
- 不使用 PostgreSQL Row-Level Security。
- 不开放管理员角色注册;内网用户可自行注册 `duty_editor`,正式班次仍由管理员分配。
- 值班工作台不提供查重功能(去重后续由后端流水线实现)。
- 不提供新闻改派、班次重叠、管理员替班。

---

## 2. 角色与权限

只有两个角色:`admin` 和 `duty_editor`。

### 2.1 `duty_editor`

**可以:**

- 在内网自行注册值班编辑账号并修改自己的密码。
- 登录,查看自己的当前班次和历史班次。
- 查看归属于自己班次的新闻。
- 设置 `pending` / `selected` / `backup` / `discarded`。
- 在自己班次内对采纳和备选内容排序。
- 编辑摘取内容、摘要、来源修订和备注。
- 选择新闻的 `report_type`(综报 / 晚报)。
- 预览按当前结果生成的报告文本,下载或复制草稿。

**不能:**

- 查看或修改其他值班编辑的记录。
- 修改不属于自己班次的新闻。
- 执行归档(任何将记录标记为 `exported` 的操作)。
- 管理用户和排班。
- 通过请求参数指定 `actor` 或伪装成其他用户。

### 2.2 `admin`

**可以:**

- 创建、停用、重置用户账号。
- 设置轮值模板,管理排班日历。
- 查看全部新闻、全部班次、所有初审结果和未审新闻。
- 在现有 `manual_filter` 工作台完成全量筛选、排序、摘要编辑、查重、预览和归档。
- 查看审计日志。

**管理员按周轮班**,一周由 A 负责,下一周由 B。管理员不参与值班编辑的轮值表。

**两位管理员各自创建独立账号,不共用。** 理由:账号互为备份(单账号被锁定或密码遗失时无法管理系统);临时换周时归档操作仍可追溯。

系统对 `admin` 数量无限制,所有管理员接口统一用 `require_role('admin')`。

---

## 3. 数据分层与隔离

### 3.1 分层

| 层 | 内容 | 写入者 | 可见范围 |
| --- | --- | --- | --- |
| 共享新闻层 | 原文、来源、机器摘要、评分、地域和情感标签 | 流水线 | 按角色和班次查询 |
| 值班处理层 | `shift_reviews` | 对应值班编辑 | 本人可写,管理员可读 |
| 管理员全量层 | `manual_reviews` | 管理员 | 管理员可写 |
| 审计层 | `review_events` | 系统自动 | 管理员 |

### 3.2 写入隔离(核心性质)

**管理员的任何操作都不改变值班编辑看到的内容,反之亦然。**

| | 值班编辑 | 管理员 |
| --- | --- | --- |
| 写入的表 | `shift_reviews` | `manual_reviews` |
| 数据范围 | 本人班次内的新闻 | 全量池子 |
| 内容字段 | `decision`、`rank`、`excerpt_text`、`edited_summary`、`manual_llm_source`、`notes` | 同名的一整套独立字段 |

两张表之间**没有任何自动同步**。值班处理记录不得直接写入 `manual_reviews`;管理员汇总某条值班结果时,单独更新或创建对应的 `manual_reviews` 记录。

管理员归档只写 `manual_reviews.status`,不改动 `news_summaries.status`,因此不影响值班编辑的候选列表。

必须支持的场景:

- 当天有值班编辑,但管理员不等待,自己直接从候选池重新选一批 —— 值班结果仅作参考。
- 管理员与值班编辑同时在线编辑同一批新闻 —— 各写各的表。
- 管理员归档综报后,编辑继续处理晚报 —— 编辑的列表不变。

值班编辑将新闻标记为 `discarded` 只表示建议放弃,不从管理员全量视图中隐藏。管理员必须能查看被放弃的新闻并重新采纳。

---

## 4. 班次规则

### 4.1 新闻归属

**分片键:`news_summaries.created_at`。** 该列由数据库 `DEFAULT now()` 管理,所有写入路径均不更新它,天然不可变。

**可见性:`news_summaries.status = 'ready_for_export'`。** 沿用现有 `only_ready` 过滤逻辑,与管理员工作台一致。

候选查询的两个核心条件:

```sql
WHERE ns.status = 'ready_for_export'
  AND ns.created_at >= %(starts_at)s
  AND ns.created_at <  %(ends_at)s
```

归属由 `created_at` 决定,新闻不会跳班次、不会消失。可见性由 `status` 决定,重跑外部筛选时新闻暂时从列表消失,跑完回到原班次,编辑已有的记录仍然有效。

**`publish_time` 不参与班次分配**,但继续用于列表排序和日报的日期归组,`db_postgres_manual_reviews.py` 中现有的 `ORDER BY ... publish_time_iso DESC NULLS LAST` 和按北京时间折算日期的逻辑保持不变。

### 4.2 班次时间范围

`starts_at` / `ends_at` 定义的是**该班次负责处理哪个时间段入库的新闻**,与值班人实际工作时间无关。

- 每天一个班次,覆盖 **22:00 → 次日 22:00**。
- 配置项 `DUTY_SHIFT_BOUNDARY_HOUR=22`,生成班次时读取。
- 时间范围左闭右开。
- 所有班次首尾相接、完整覆盖时间轴。
- 统一使用带时区时间,业务时区 `Asia/Shanghai`。

> **给日后改动此设计的人:** 21:00–22:00 这一小时的覆盖不是由系统保证的,而是由管理员在归档前的日常复查补上的。"每条新闻都有且只有一个归属班次"指的是数据归属,不代表都会被值班编辑看到。若调整 `DUTY_SHIFT_BOUNDARY_HOUR`、改变编辑工作时段,或去掉管理员归档前的复查环节,必须重新评估这一小时的去向。

### 4.3 排班

**两个页面,两个概念:**

| 页面 | 对应表 | 内容 | 修改的含义 |
| --- | --- | --- | --- |
| 轮值表设置 | `duty_schedules` | 七个下拉框,周一到周日各选一人 | 改的是今后生成班次的依据 |
| 排班日历 | `duty_shifts` | 未来一段时间的具体日期 | 改的是那一天的实际负责人 |

**改模板不改动已生成的具体班次。** `duty_shifts` 里的行是不可变的历史事实,`duty_schedules` 只是生成它们的模板,生成后两者脱钩。

原因:`shift_reviews.shift_id` 需要稳定的外键指向;若只存规则、查询时现算,历史记录会被今天的模板重新解释而失真。

- **临时换班**:在排班日历页改那一天的负责人,不动模板。
- **永久换人**:改模板,已生成的 14 天内对应行手动改。

**生成规则:**

```sql
INSERT INTO duty_shifts (...) VALUES (...)
ON CONFLICT (starts_at) DO NOTHING
```

**必须是 `DO NOTHING`,不能是 `DO UPDATE`。** 否则临时换班的调整会在下次生成时被模板值静默抹掉,且无人察觉。

| 项目 | 取值 |
| --- | --- |
| 横跨期 | 滚动未来 14 天 |
| 触发点一 | 每日定时任务,CLI 命令 `python -m src.cli.main generate-shifts --days 14` |
| 触发点二 | 管理员打开汇总页或排班页时自动补齐 |
| 幂等保证 | `duty_shifts` 的 `UNIQUE (starts_at)` + `ON CONFLICT DO NOTHING` |

两个触发点都要实现。生成是幂等的,多跑无副作用;两者失效模式互补(定时任务可能悄悄不跑,页面可能长期不开)。

**模板完整性校验:** 生成逻辑必须检查 `duty_schedules` 是否覆盖全部七天。未覆盖时记录警告并在管理员页面标出,不得静默生成有空档的排班。

**覆盖剩余提醒:** 管理员页面在排班覆盖剩余不足 3 天时显示警告:

```
⚠️ 排班已覆盖至 8月14日,还剩 3 天
```

### 4.4 其他规则

- 班次归属必须指向启用的 `duty_editor`,service 层校验,不接受 `admin`。
- 一天一个班次,不存在重叠。
- 取消的班次不参与新闻分配。
- **值班编辑随时可以修改自己班次的记录,无时间限制。** 所有修改进审计日志。
- 值班编辑缺勤时,管理员直接在 `manual_filter` 全量处理,该班次的 `shift_reviews` 保持为空。

---

## 5. 数据库设计

新建六张表。对现有表只做纯增量改动。

### 5.1 `console_users`

```sql
-- migrate:up
create table if not exists public.console_users (
    id uuid primary key default gen_random_uuid(),
    username text not null,
    display_name text not null,
    password_hash text not null,
    role text not null,
    preferred_weekday smallint,
    is_active boolean not null default true,
    password_changed_at timestamptz,
    last_login_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint console_users_role_check check (role in ('admin', 'duty_editor')),
    constraint console_users_preferred_weekday_check
        check (preferred_weekday is null or preferred_weekday between 0 and 6)
);

create unique index if not exists console_users_username_lower_idx
    on public.console_users (lower(username));

-- migrate:down
drop table if exists public.console_users;
```

**Service 层约束:**

- 自注册账号的角色固定为 `duty_editor`,注册时必须填写姓名和首选值班日;首选值班日只供管理员参考,不得自动写入轮值模板或具体班次。
- 至少保留一个启用的管理员账号。停用或降级最后一个管理员时拒绝。有两个管理员时,应允许停用其中一个、拒绝停用两个。
- 停用账号时查询该用户是否有未来班次,有则列出并提示管理员改派。否则那些班次归属一个登不进来的账号,新闻在那些时段对所有值班编辑不可见,且不会报错。

### 5.2 `console_user_sessions`

```sql
-- migrate:up
create table if not exists public.console_user_sessions (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.console_users(id) on delete restrict,
    token_hash text not null,
    csrf_token_hash text not null,
    expires_at timestamptz not null,
    last_seen_at timestamptz,
    revoked_at timestamptz,
    created_at timestamptz not null default now(),
    constraint console_user_sessions_token_hash_unique unique (token_hash)
);

create index if not exists console_user_sessions_user_id_idx
    on public.console_user_sessions (user_id);

create index if not exists console_user_sessions_expires_at_idx
    on public.console_user_sessions (expires_at);

-- migrate:down
drop table if exists public.console_user_sessions;
```

**四样东西必须分开:**

- `id`:内部主键,用于外键关联和审计,**永远不出现在 Cookie 里**。
- **原始 token**:只存在于用户浏览器 Cookie 和服务器处理请求时的内存中,**数据库不保存**。
- `token_hash`:原始 token 的 SHA-256 摘要,数据库存这个。
- **CSRF token**:使用独立随机值,浏览器以可读 Cookie 保存并在写请求头中回传;数据库只保存 `csrf_token_hash`,不得复用会话 token。

校验时对 Cookie 中的 token 重新计算摘要,拿结果查表。

**实现要求:**

- 令牌使用 `secrets.token_urlsafe(32)` 生成。**不得使用 `uuid` 或 `random`**(`random` 是伪随机,序列可预测)。
- 摘要使用 SHA-256,**不要用 bcrypt**。session token 是 32 字节纯随机数据,不存在被穷举的可能,用慢哈希只会让每个 API 请求多花几百毫秒。同理不需要加盐。
- 密码哈希使用 `passlib` 或 `argon2-cffi`,**绝不自己实现**。
- Cookie 设置 `HttpOnly`、`SameSite=Lax`;HTTPS 部署时设置 `Secure`。
- 原始 token 不得写入任何日志。请求日志若记录 header 或 cookie 需脱敏。

**参考实现:**

```python
import secrets, hashlib

def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()

# 登录成功后
raw_token = secrets.token_urlsafe(32)
# INSERT INTO console_user_sessions (id, user_id, token_hash, expires_at, created_at)
# VALUES (gen_random_uuid(), %s, %s, now() + interval '14 days', now())
response.set_cookie("session", raw_token,
                    httponly=True, samesite="lax", secure=True, max_age=14*24*3600)

# 每个受保护请求
raw = request.cookies.get("session")
# SELECT ... FROM console_user_sessions
#  WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > now()
```

**使用服务端会话而非 JWT。** 系统要求账号停用、密码重置或管理员撤销会话后旧会话立即失效,JWT 做不到这一点(签发后在过期前一直有效)。对不到 10 个用户的规模,每请求一次带索引的主键查询开销可忽略。

### 5.3 `duty_schedules`

```sql
-- migrate:up
create table if not exists public.duty_schedules (
    id uuid primary key default gen_random_uuid(),
    weekday smallint not null,
    user_id uuid not null references public.console_users(id) on delete restrict,
    updated_at timestamptz not null default now(),
    constraint duty_schedules_weekday_check check (weekday between 0 and 6),
    constraint duty_schedules_weekday_unique unique (weekday)
);

-- migrate:down
drop table if exists public.duty_schedules;
```

`weekday`:0 = 周一 … 6 = 周日。固定七行。

### 5.4 `duty_shifts`

```sql
-- migrate:up
create table if not exists public.duty_shifts (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.console_users(id) on delete restrict,
    starts_at timestamptz not null,
    ends_at timestamptz not null,
    cancelled_at timestamptz,
    notes text,
    created_by_user_id uuid references public.console_users(id) on delete restrict,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint duty_shifts_range_check check (ends_at > starts_at),
    constraint duty_shifts_starts_at_unique unique (starts_at)
);

create index if not exists duty_shifts_user_id_idx
    on public.duty_shifts (user_id);

create index if not exists duty_shifts_starts_at_idx
    on public.duty_shifts (starts_at desc);

-- migrate:down
drop table if exists public.duty_shifts;
```

**没有 `status` 字段。** 状态一律在查询时由时间计算:

- `cancelled_at` 非空 → 已取消
- `now() < starts_at` → 未开始
- `starts_at <= now() < ends_at` → 进行中
- `now() >= ends_at` → 已结束

冗余存储状态需要额外机制维护,必然出现"时间已过但状态未更新"的不一致。

### 5.5 `shift_reviews`

```sql
-- migrate:up
create table if not exists public.shift_reviews (
    id uuid primary key default gen_random_uuid(),
    shift_id uuid not null references public.duty_shifts(id) on delete restrict,
    article_id text not null,
    created_by_user_id uuid not null references public.console_users(id) on delete restrict,
    updated_by_user_id uuid not null references public.console_users(id) on delete restrict,
    report_type text,
    decision text not null default 'pending',
    rank integer,
    excerpt_text text,
    edited_summary text,
    manual_llm_source text,
    notes text,
    version integer not null default 1,
    decided_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint shift_reviews_decision_check
        check (decision in ('pending', 'selected', 'backup', 'discarded')),
    constraint shift_reviews_report_type_check
        check (report_type is null or report_type in ('zongbao', 'wanbao')),
    constraint shift_reviews_shift_article_unique unique (shift_id, article_id)
);

create index if not exists shift_reviews_shift_id_idx
    on public.shift_reviews (shift_id);

create index if not exists shift_reviews_article_id_idx
    on public.shift_reviews (article_id);

create index if not exists shift_reviews_created_by_idx
    on public.shift_reviews (created_by_user_id);

create index if not exists shift_reviews_updated_by_idx
    on public.shift_reviews (updated_by_user_id);

-- migrate:down
drop table if exists public.shift_reviews;
```

#### 5.5.1 `report_type` 是路由字段,不进唯一键

同一条新闻**不可能既进综报又进晚报**,不重复报新闻。因此唯一键是 `(shift_id, article_id)`,一篇新闻在一个班次内只有一行。

`report_type` 保持**可空**,语义为"尚未归类"。现有 `manual_reviews` 的三个索引都用 `COALESCE(report_type, 'zongbao')`,空值等同综报,保持一致。

编辑改主意时是一条 `UPDATE ... SET report_type = 'wanbao'`,不需要删行重建。

#### 5.5.2 `article_id` 不加外键

`shift_reviews.article_id` 不加指向 `news_summaries` 的外键。值班记录是"谁在什么时候做了什么判断"的历史事实,不应因为新闻被清理而消失。

其余外键一律 `ON DELETE RESTRICT`,不得使用 `CASCADE`。

#### 5.5.3 `rank` 用 `integer`

前端传完整有序列表,后端 `enumerate` 一遍全量写回:

```python
for index, article_id in enumerate(ordered_ids, start=1):
    # UPDATE shift_reviews SET rank = index WHERE ...
```

现有 `manual_filter_decisions.py:203` 已经是这个语义(`enumerate(selected_ids, start=1)` 配 `float(index)`),新增采纳时也是 `MAX(rank) + 1` 追加,从未使用小数插入。新表不继承这个类型与用法的不匹配。

**排序查询必须写成 `ORDER BY rank, created_at, id`。** 若两行 `rank` 相同,PostgreSQL 不保证顺序,刷新页面顺序就会变。

**排序操作不得 bump `version`。** `version` 只保护内容字段(摘要、摘取、来源、备注、决定)。若排序也加一,用户在另一个标签页编辑摘要时会莫名撞 409。

> **已知限制:** 现有排序接口存在 200 条上限(`db_postgres_manual_reviews.py:183` 与 `:267` 的 `min(int(limit or 30), 200)`,前端 `review_tab_data.js:15` 也写死 `limit: '200'`)。排序提交重写的是已加载的前 200 条,超出部分不参与本次重排。这不会造成数据错乱,但第 201 条以后无法与前 200 条调整相对顺序。单班次很难到 200 条,第一版不处理。

#### 5.5.4 创建人、最后编辑人与换班

**不要把创建人或最后编辑人必须等于班次的 `user_id` 做成数据库约束或触发器。**

两个字段含义不同:

- `duty_shifts.user_id` = **现在**这个班归谁
- `shift_reviews.created_by_user_id` = 最初创建这条班次判断的人,创建后不变
- `shift_reviews.updated_by_user_id` = 最近一次修改当前行的人

换班后这些字段本来就可能不同。若做成约束,管理员改派班次时,已有记录会当场违反约束,后续所有写入被拒。

**正确做法:** 写入时校验"当前登录用户是否等于该班次当前的 `user_id`"。首次创建时同时写入创建人与最后编辑人;后续更新只改变最后编辑人。改派后原编辑写不进去,新编辑能继续修改同一条当前记录。完整的多人参与历史由只追加的 `review_events` 保存,管理员不能仅凭 `shift_reviews` 当前行推断全部历史参与者。

### 5.6 `review_events`

```sql
-- migrate:up
create table if not exists public.review_events (
    id bigserial primary key,
    actor_user_id uuid,
    action text not null,
    target_type text not null,
    target_id text,
    before_data jsonb,
    after_data jsonb,
    request_id text,
    created_at timestamptz not null default now()
);

create index if not exists review_events_created_at_idx
    on public.review_events (created_at desc);

create index if not exists review_events_target_idx
    on public.review_events (target_type, target_id);

create index if not exists review_events_actor_idx
    on public.review_events (actor_user_id);

-- migrate:down
drop table if exists public.review_events;
```

- **不加任何外键。** 审计记录必须在被审计对象消失后依然存在。
- 只允许追加,不提供更新和删除接口。
- 至少记录:登录结果、排班变更、值班状态/排序/编辑变化、管理员全量修改及归档。
- 由于不设编辑宽限期,这张表承担"值班记录当时是什么样"的全部举证责任。

### 5.7 现有 `manual_reviews` 的增量改动

```sql
-- migrate:up
alter table public.manual_reviews
    add column if not exists decided_by_user_id uuid references public.console_users(id) on delete restrict;

alter table public.manual_reviews
    add column if not exists version integer not null default 1;

-- migrate:down
alter table public.manual_reviews drop column if exists version;
alter table public.manual_reviews drop column if exists decided_by_user_id;
```

**只加这两列,其余一律不动:**

- **不改唯一约束。** 现有 `manual_reviews_article_id_key UNIQUE (article_id)` 正确表达了"一篇新闻只报一次"的业务规则,保持原样。
- 不改 `report_type` 的可空性。
- **不改 `rank` 的 `double precision` 类型。** 两张表类型不一致不是错误,但管理员从值班结果汇总到全量工作区时,应意识到这是两套独立排序,不能直接搬数字。
- 保留现有 `decided_by` 文本字段作为历史兼容。

### 5.8 迁移顺序

```
console_users → console_user_sessions → duty_schedules → duty_shifts → shift_reviews → review_events → manual_reviews 加列
```

用 `dbmate new` 生成时注意时间戳顺序。

---

## 6. 认证与会话

- 新增登录、内网注册和个人改密页面及对应接口。
- 自注册成功后异步发送飞书提醒,内容仅包含姓名、用户名和首选值班日,不得包含密码;飞书失败不得回滚已创建的账号。
- 登录成功后建立服务端可撤销会话。
- 每个受保护请求通过统一依赖得到包含 `user_id`、`username`、`display_name`、`role` 的 `ConsoleUser`。
- 账号停用、密码重置或管理员撤销会话后,旧会话立即失效。
- 修改状态的请求增加 CSRF 防护,至少校验同源请求和 CSRF token。
- 登录失败限速。
- 定期清理过期会话,审计事件长期保留。

**与现有认证的兼容:**

- 浏览器页面切换到用户会话认证。
- `CONSOLE_API_TOKEN` 暂时保留给受控自动化调用,映射为无业务用户 UUID 的专用系统管理员身份;不得用它访问要求具体班次归属的值班接口。
- **不再允许客户端提交 `actor` 决定审计身份。**
- 生产环境禁止未配置认证时自动匿名访问。
- 完成迁移后再移除共享 Basic 认证。

---

## 7. 后端模块与 API

新增功能采用独立模块,不继续扩大 `manual_filter` 的职责。现有 `src/console/manual_filter_*` 继续负责管理员全量工作流。

**新增文件:**

```
src/console/auth_routes.py / auth_service.py / auth_schemas.py
src/console/users_routes.py / users_service.py
src/console/shifts_routes.py / shifts_service.py
src/console/duty_review_routes.py / duty_review_service.py / duty_review_schemas.py
src/adapters/db_postgres_users.py
src/adapters/db_postgres_shifts.py
src/adapters/db_postgres_shift_reviews.py
src/adapters/db_postgres_audit.py
```

**认证与当前用户:**

```
POST /api/auth/login
POST /api/auth/register
POST /api/auth/logout
GET  /api/me
POST /api/me/change-password
```

**值班编辑:**

```
GET /api/duty/shifts
GET /api/duty/shifts/{shift_id}/stats
GET /api/duty/shifts/{shift_id}/candidates
GET /api/duty/shifts/{shift_id}/reviews
PUT /api/duty/shifts/{shift_id}/reviews/{article_id}
PUT /api/duty/shifts/{shift_id}/order
GET /api/duty/shifts/{shift_id}/preview
```

`stats` 返回:班次新闻总数、已决定数、未处理数,以及**两个报告类型各自的归档状态**。

第一版归档状态从该班次时间范围内 `manual_reviews.status = 'exported'` 的记录推导,时间取 `max(decided_at)`。因此管理员归档至少一条新闻后会显示归档时间;空报告不产生归档状态。

**管理员:**

```
GET   /api/admin/users
POST  /api/admin/users
PATCH /api/admin/users/{user_id}
POST  /api/admin/users/{user_id}/reset-password
GET   /api/admin/schedules
PUT   /api/admin/schedules
GET   /api/admin/shifts
POST  /api/admin/shifts
PATCH /api/admin/shifts/{shift_id}
GET   /api/admin/duty-summary
GET   /api/admin/audit-events
```

归档接口限制为 `admin`。任何接口都从当前登录用户得到真实操作人,不信任请求体的 `actor`。

---

## 8. 查询与权限校验

权限不能只靠前端隐藏按钮,所有写入必须在后端重新校验。

值班候选新闻查询必须同时满足:

1. 当前用户有效且角色允许访问。
2. 请求的 `shift_id` 属于当前用户。
3. 班次 `cancelled_at` 为空。
4. `news_summaries.created_at` 落在班次左闭右开范围内。
5. `news_summaries.status = 'ready_for_export'`。

管理员读取值班结果时可跨用户、跨班次查询,但写入管理员全量工作区仍通过对应 service,不得伪装成值班编辑改写其历史 `shift_reviews`。

---

## 9. 并发控制

- `shift_reviews` 用 `UNIQUE (shift_id, article_id)` 防止重复记录。
- 更新时携带客户端最后看到的 `version`。
- SQL 使用 `WHERE id = %s AND version = %s`,成功后 `version` 加一。
- 受影响行数为 0 时返回 HTTP `409`,前端提示"该记录已被其他操作更新,请刷新后重试"。
- **排序操作不 bump `version`。**
- 状态修改和审计事件写入同一数据库事务。
- 管理员汇总或归档时,工作区更新和审计事件也在同一事务完成。
- 不采用静默的最后写入覆盖。

---

## 10. 前端

### 10.1 公共部分

- 新增登录页。
- 顶部显示当前用户、角色、当前班次和退出按钮。
- 根据服务端返回的权限渲染入口,后端仍执行最终校验。
- 所有写操作统一处理 `401`、`403`、`409`。

### 10.2 值班编辑工作台

**基本策略:照抄现有 `manual_filter` 的卡片展示和状态交互**,只换数据层。请求走 `/api/duty/shifts/{shift_id}/...`,后端强制绑定当前用户和班次,归档按钮隐藏。

页面展示:

- **班次覆盖范围必须写全**,编辑第一次用会困惑于跨自然日:

  ```
  7月23日班次 · 覆盖 7月22日 22:00 – 7月23日 22:00
  ```

- **明显的刷新按钮。** 编辑 20:00–21:00 工作时班次尚未封口,新闻仍在流入,需要能主动拉取最新列表。
- 采纳、备选、放弃、待处理四种状态。
- 综报 / 晚报归属选择(单选)。
- 采纳和备选列表排序。
- 摘取、摘要、来源和备注编辑。
- 按当前结果预览或下载草稿文本。
- **按报告类型分别显示的归档状态提示:**

  ```
  综报  已于 7月23日 17:05 归档
  晚报  未归档
  ```

  数据来自 `GET /api/duty/shifts/{shift_id}/stats`。综报已归档时,禁用"归档到综报"这个路由选项或给出明确警告 —— 编辑仍能标记,但标了不会生效。

- 仅查看当前班次或本人历史班次。

**不提供:**

- 查重按钮。
- 完成度、完成率或进度条。现有 `manual_filter` 控制台没有这个东西,值班工作台也不加。编辑把列表处理到没有未处理项为止即可。

### 10.3 聚类展示

系统已有聚类功能,由一个每 5 分钟运行的定时任务刷新,结果缓存在 `manual_clusters` 表,控制台读缓存不实时计算。

**值班工作台读取同一份全量缓存,按班次范围过滤 `item_ids`,不做任何额外聚类计算:**

```python
shift_article_ids = {...}   # 本班次候选新闻的 article_id 集合
filtered = [aid for aid in cluster.item_ids if aid in shift_article_ids]
# filtered 为空的簇直接丢弃;顺序天然保持
```

**不改动 `manual_clusters` 表结构,不改动 5 分钟定时任务,不改动 `refresh_clusters` / `_collect_pending` / `title_cluster.py`。** 只在值班工作台的查询层加一次列表过滤。

> **不要改成按班次重新聚类。** `refresh_clusters(report_type=...)` 一次只处理一个 report_type,单次耗时 1–3 分钟。若为班次单独聚类,定时任务变成 2 个 report_type × (1 份全量 + 1 份当前班次) = 4 次 = 4–12 分钟,跑不完 5 分钟周期。过滤方案的额外计算量为零,且编辑看到的结果与重新聚类基本一致。

**已知行为(不处理):**

- 簇结构会随新闻流入而变化,编辑和管理员都会遇到。班次边界只作用于编辑视图,管理员的全量池子从不封口。这与现有控制台行为一致。
- 缓存最多滞后 5 分钟,编辑和管理员看到同一份。
- 聚类只覆盖 `pending` 状态的新闻,已决定的条目离开缓存。这是现有行为。

### 10.4 管理员工作台

现有 `/manual_filter` 保持全量主入口,不重写已经稳定的筛选、复核、预览和归档流程。

新增管理员入口:

- **每日和每班的计数**:班次新闻总数、已决定数、未处理数。**用计数,不用百分比** —— 班次 22:00 封口而编辑 21:00 收工,百分比永远到不了 100%,会让指标失去管理价值。
- 查看每名值班编辑的初审结果。
- 快捷筛选:值班建议采纳 / 备选 / 放弃 / 尚未处理;**无有效班次覆盖**;管理员全量结果与值班结论不同。
- 将选中的值班结果送入现有全量工作流。
- 轮值表设置页与排班日历页。
- 标出归属已停用账号的班次。
- 排班覆盖剩余不足 3 天时的警告。

**"无有效班次覆盖"视图必须实现**,作为分片规则的兜底。即便生成班次的无空档校验有疏漏,至少新闻不会彻底消失。

---

## 11. 初始化与迁移

### 11.1 创建管理员账号

通过一次性管理命令创建,**不在迁移 SQL 中硬编码密码**。命令从安全输入或环境变量读取初始密码,创建成功后提示立即修改。

**创建两个管理员账号。** 命令应支持重复调用,不是只能跑一次。第二位管理员的账号现在就建出来,密码由他首次登录时修改。

### 11.2 现有 `manual_reviews` 数据

- 现有记录全部视为历史管理员终审结果。
- 旧 `decided_by` 文本保留,不强行猜测具体用户。
- 对能明确归属当前管理员的记录,可通过一次性脚本选择性回填 `decided_by_user_id`。
- 迁移后运行综报、晚报查询和导出回归测试。

### 11.3 单人模式过渡

管理员登录后仍可不创建班次,直接使用现有全量人工筛选工作台。多人功能逐步启用。

---

## 12. 分阶段实施

### 阶段一:真实用户身份

纯新增,完全不碰现有表。**建议先单独上线跑一两周确认稳定,再进入阶段二。**

- 新增 `console_users`、`console_user_sessions` 表和迁移。
- 新增登录、内网注册、退出、当前用户、修改密码接口。
- 将 `ConsoleUser` 扩展为真实业务用户。
- 建立 `require_role('admin')` 等统一权限依赖。
- 创建两个管理员账号。
- 移除修改接口对请求体 `actor` 的信任。

**完成标准:** 两个不同账号登录后后端能稳定区分用户;普通用户无法调用管理员接口。

### 阶段二:排班与值班初审

- 新增 `duty_schedules`、`duty_shifts`、`shift_reviews` 表和相关 adapter / service / routes。
- 同期新增最小可用的 `review_events`,从第一条值班记录开始保存换班前后的真实参与历史。
- 实现班次时间范围和归属校验(注意 5.5.4 的换班场景)。
- 实现轮值表设置页、排班日历页、滚动 14 天自动生成(双触发 + `ON CONFLICT DO NOTHING` + 模板完整性校验)。
- 新增值班编辑工作台。
- 实现筛选状态、排序、摘取、摘要、来源、备注保存。
- 实现值班范围内的综报/晚报预览。
- 实现班次计数统计,仅供管理员汇总页使用。
- 值班工作台的聚类展示:过滤现有全量缓存。

**建议顺序:** 先手动建三五天班次把值班工作台跑通,确认班次字段设计不用推翻,再做模板表和生成逻辑。

**完成标准:** 值班编辑只能查看和修改自己的班次,可以完成筛选、排序、编辑和预览。

### 阶段三:管理员汇总与归档衔接

- 新增管理员班次汇总页面。
- 支持按班次、用户和初审结果筛选。
- 将值班结果作为现有人工筛选的输入条件。
- 归档操作限制为管理员。
- 为 `manual_reviews` 新增 `decided_by_user_id` 和 `version`。

**完成标准:** 管理员能完整看到值班成果和漏审新闻,继续全量编辑和预览,并独立执行最终归档。

### 阶段四:审计扩展与并发保护

- 将阶段二已启用的 `review_events` 扩展到用户管理、管理员全量修改和归档等全部关键操作。
- 为值班处理和管理员全量编辑增加 `version` 校验。
- 实现 `409` 冲突处理和前端提示。
- 实现会话撤销、账号停用、密码重置。

**完成标准:** 关键操作可追溯;并发修改不会静默覆盖。

> 由于不设编辑宽限期,`review_events` 承担"值班记录当时是什么样"的全部举证责任。若此阶段要推迟,至少先把 `shift_reviews` 的变更事件记录起来。

---

## 13. 测试清单

### 13.1 认证与权限

- 未登录用户不能访问受保护页面和 API。
- 停用用户的现有会话失效。
- **数据库中不存在可直接使用的原始令牌**(`console_user_sessions` 只有 `token_hash` 和 `csrf_token_hash`)。
- `duty_editor` 可在本人班次内筛选、排序、编辑、预览,但不能调用用户管理、排班管理和归档接口。
- `admin` 能访问全部管理能力。
- 请求体伪造 `actor`、`user_id`、`created_by_user_id` 或 `updated_by_user_id` 不改变真实操作人。
- 有两个管理员时允许停用其中一个、拒绝停用两个。

### 13.2 班次边界与排班

- `created_at` 恰好等于 `starts_at` 的新闻属于该班次。
- `created_at` 恰好等于 `ends_at` 的新闻不属于该班次。
- 所有班次首尾相接,不存在覆盖空档。
- 取消的班次不再分配新闻。
- 重复执行生成动作不产生重复班次。
- **手动改过负责人的班次,再次生成时不被模板值覆盖。**
- 模板未覆盖七天时生成动作发出警告,不静默生成。

### 13.3 数据隔离

- 用户 A 无法读取或修改用户 B 的班次初审记录。
- 管理员能读取全部初审结果。
- 值班初审状态变化不改变 `manual_reviews`。
- **管理员在 `manual_filter` 的编辑、排序、归档操作不改变任何 `shift_reviews` 记录。**
- 管理员归档后,值班编辑的候选列表不变。

### 13.4 内容操作与归档兼容

- 值班编辑在本人班次内的筛选、备选、放弃、排序、摘要编辑、来源修订和预览行为可用。
- 排序提交后 `rank` 为从 1 开始的连续整数,无重复、无空洞。
- 值班编辑不能通过页面或直接调用 API 执行归档。
- **管理员现有筛选、备选、放弃、排序、摘要编辑、查重、预览和归档行为保持可用**(重点回归)。
- 现有历史 `manual_reviews` 在迁移后仍可查询和导出。

### 13.5 并发与审计

- 两个请求使用同一旧版本更新时,只有一个成功,另一个返回 `409`。
- **拖动排序不导致其他标签页的内容编辑撞 409。**
- 状态更新失败时不写入伪成功审计事件。
- 成功操作的审计用户、目标、前后状态和时间准确。

### 13.6 换班场景

- 管理员改派班次后,原编辑无法继续写入。
- 新编辑可以写入,当前行保留最初创建人并更新最后编辑人。
- 管理员可从审计事件看到该班次的全部历史参与者。

---

## 14. 上线与安全

- 上线前备份 PostgreSQL,并验证恢复流程。
- 数据库迁移先在测试库或数据库副本执行。
- 控制台通过 HTTPS 暴露,数据库不直接暴露给普通用户。
- 会话密钥和 API token 通过环境变量或 secret 管理,不提交 Git。
- **日志不得记录密码、原始会话令牌和敏感认证头。**
- 阶段一上线时保留管理员单人使用路径,确认稳定后再逐步创建值班账号和排班。

---

## 15. 验收标准

1. 管理员能创建 9 个账号(7 名值班编辑 + 2 名管理员)并安排班次。
2. 每名值班编辑只能看到归属自己班次的新闻。
3. 每名值班编辑可独立完成筛选、备选、放弃、排序、摘取、摘要、来源修订和预览。
4. 管理员能查看所有班次的未处理条数、初审结果和漏审新闻。
5. 值班处理记录不直接覆盖管理员全量工作区或既有归档历史。
6. 管理员的编辑和归档操作不影响值班编辑看到的内容。
7. 值班编辑不能通过前端或直接调用 API 执行归档。
8. 管理员能继续完成综报、晚报的筛选、排序、编辑、预览和最终归档。
9. 关键操作能追溯到真实登录用户。
10. 并发修改不会静默覆盖。
11. 现有历史人工筛选数据在迁移后保持完整可用。
12. 每条新闻都有且只有一个归属班次,不存在覆盖空档。

---

## 16. 后续增强(不在本次范围)

- **后端流水线去重**(pgvector 向量相似度),解决跨班次重复。判定重复的应**标记而非删除** —— 相似度必然有误判,而误判代价不对称:漏掉一条重复,编辑手动划掉即可;误删一条独家,没有任何人会发现。
- 排班日历页的「按模板重置」:选日期范围,把范围内尚未开始的班次按模板刷新。需二次确认,绝不能碰已开始或已结束的班次。
- 统一 `manual_reviews.rank` 为 `integer`。应在本项目全部上线稳定后单独进行,先检查库中是否遗留非整数 `rank`,并同步调整 `Optional[float]`、`float(index)`、`manual_review_max_rank() -> float` 等类型签名。不要与多用户改造混在一起。
- PostgreSQL Row-Level Security,作为应用层权限之外的纵深防御。
- 班次交接留言和通知。
- 管理员向值班编辑退回新闻重新处理。
- 值班质量和漏审率统计。
- 飞书或其他统一身份登录。
