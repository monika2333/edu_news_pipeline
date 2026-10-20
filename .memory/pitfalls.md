# 技术踩坑与站点知识

代码层面、外部站点层面的坑。操作流程类（测试/worktree/合并）在 [workflow.md](workflow.md)。细节出处见 `archive/` 对应文件。

## Python

- **f-string 表达式内禁写反斜杠转义**（PEP 701，3.12 才放开）：在 f-string 的表达式部分写 `\'` 之类的转义（例如内联带引号的 SQL 字面量）在 3.9~3.11 导入期直接 SyntaxError。SQL 字面量先赋普通变量再进 f-string。注意区分：反斜杠出现在 f-string **字面量部分**（如 `rf'...["\']...'`）一直合法。用户明确要求过这条（4e68c9b 修复过一处）。
- 行尾敏感的文件读写用 `write_bytes`，`write_text` 在 Windows 会 LF→CRLF（详见 workflow.md 提交卫生）。
- Markdown/配置/中文文本统一 UTF-8 无 BOM。

## 数据库

- **psycopg 3.2：`with conn:` 退出会关闭连接**（`Connection.__exit__` 里非 pool 连接直接 close），不再是旧「事务边界」语义。真实库测试若在 with conn 块外还要用连接（adapter 调用+断言），必须显式 seed + `conn.commit()`，不能包 with conn。
- **app_settings（及任何可能被应用先行写入的表）种子迁移必须带 `ON CONFLICT ... DO NOTHING`**：设置页可以在任何迁移之前建行，长寿命库上裸 INSERT 必撞 pkey（20260923120000 实际炸过，3f1e4cd 修复了 llm_endpoints 与 review_sort_keywords 两条）。
- 诊断 dbmate 失败以 `schema_migrations` 表为准——失败的迁移不留记录；用户终端粘贴的报错可能串行误导。
- **`dbmate up` 回写的 `schema.sql` 只有「迁移版本行」该提交**：pg_dump 18 每次生成不同的 `\restrict`/`\unrestrict` 随机令牌，还会带出 public schema 注释块、以及把 `schema_migrations.version` 写成本机库现状的 `varchar`（无长度）。后两者与 `tests/test_migration_replay.py` 里硬编码 `varchar(128)` 的镜像 DDL 冲突，原样提交该用例立刻变红（实测）。做法：`git checkout HEAD -- database/schema.sql`，只手工在 INSERT 列表追加新版本行。仓库里这份文件的形态来回翻转多次（a3a63e4 等）就是每次有人在清理这类噪音。根治办法（未实施）：让镜像 DDL 与 dump 形态一致 + 抽掉随机令牌，否则每次迁移都要手工擦一遍。
- 「清理旧新闻」类多桶写路径：回滚类测试的注入点必须在「写入已执行」之后；「先锁全部再统一写」的实现里，锁定阶段注入是空转（用户抓到过 T3 空转，09e7c96 修订）。
- service 层报别校验用 `NEWS_REPORT_TYPES` 显式判断——`coerce_report_type` 会把非法值静默归综报，不能当守卫用。

## 日志

- **adapters 层要让日志可见必须 `print(..., file=sys.stderr)`**：CLI/定时任务没有 logging handler，`logging.info` 被丢弃（只有 WARNING+ 走 lastResort）。照 `http_toutiao.py`/`http_tencent.py` 先例：`[info]`/`[warn]` 前缀；语义性 warning 可同时 `LOGGER.warning`。adapters 不能 import src.workers（依赖方向），所以不能用 `log_info`。

## 爬虫站点知识（adapters）

通用：
- `src/adapters/http_common.py` 共享 `build_session`/`decode_response`/`html_to_markdown`（统一剥图版）/`strip_site_suffix`；黄金快照 `tests/adapters/test_http_common_golden.py` 锁行为。合法例外已写进 `src/adapters/AGENTS.md`。
- **剥图是用户拍板的决策**：流水线纯文字，图片一律剥离；xinhua 图说文字保留（在 alt/正文中）。
- `decode_response` 分级：服务端头 > HTML meta 声明 > chardet > utf-8（中新网服务器不带 charset 时 chardet 误判 pt154 致整页乱码的存量 bug）。
- trust_env 参数化有实测意义：jyb/劳动午报关代理，其余信任系统代理（本机挂死代理时表现不同）。
- 账号型来源（beijinghao/btime/tencent 走 crawl_accounts）与列表型来源行为不同；worker 每轮先加载 existing_ids 去重。防灌档靠静态上限 + existing 截停 + lookback 窗口（15 源审计结论见 archive/2026-10-01-千龙默认页数1页.md）。

各站特记：
- **btime（北京时间）**：`refresh` 必须固定 1——refresh=0 接口会静默返回 2020 年存档；`pageRow:10` 单包、`list_items` 显式 `del pages` 不翻页。视频稿（news_type=3）正文只在页面内嵌 `topic_data` JSON（`modules[*].modules[*][*].data.content` 取 `type=="txt"`），可见 article 节点是标题复读 SEO 样板；实现为 topic_data 优先、HTML 兜底。
- **stdaily（科技日报）**：只有「滚动新闻」一个栏目；列表解析**必须圈定 `div.f_lieb_list` 容器**，全页抓锚点会混入侧栏 ~25 条「热门点击」旧稿（xinhua 是全页抓，别照抄）；ID `stdaily:<content编号>` 不含频道路径——TRS 同稿跨频道编号相同天然去重；图片说明在 `topic` 属性而非 alt。
- **xinhua（新华网北京频道）**：ID `xinhua:<32hex>`；15 栏目在 `COLUMNS` 维护（/zt 专题已删——只链专题页非文章列表）。**测试里合成 URL 的 slug 种子只能含十六进制字符**（beef/fade/cafe 这类），含 o/u/r 等字母的 seed 会让链接不被识别、测试静默测不到目标行为。
- **bbtnews（北京商报）**：ID `bbtnews:<CMS编号>`；站方时间粒度只到日（publish_time 落零点）；列表只认左栏 `.c-l-wrap` 内三区，页脚「新闻排行」是同构 li 放宽就混入；URL 强制 https；扩栏目往 `COLUMNS` 加行即可。
- **qianlong（千龙）**：`DEFAULT_MAX_PAGES = 1`，`QIANLONG_PAGES` 仅作上调覆盖（防环境变量漏配整档灌入；existing 截停对新栏目永不触发）。注意变异验证时「默认改回 None」会让测试**无限挂起**（404 不计空页 → `_iter_listing_urls` 死循环），做此变异须带超时。
- **bjrb（北京日报数字版）**：副标题在 `#sub-title`，按原连接符合并进标题（冒号前缀→「主：副」，破折号/无前缀→「主——副」）；只爬当天刊期（`BJRB_DATE` 可指定）。
- **jyb（中国教育报/神州学人）**：文章经 jyb 搜索 API 入库（`jyb:` 前缀）；副题是 `.title` **后面**的 `.subtitle`（同名 div 是引题位）；发布时间在 `var customtime` JS 变量里（bs4 `get_text()` 默认排除 script）。

## 前端 / jsdom 测试

- `createEl` 的 attributes 走 `setAttribute`：`hidden: false` 按存在性生效照样隐藏——hidden 必须事后属性赋值。
- 变异逃逸①（判别数据）：顺序敏感断言里，含拉丁字母的中文（「AI 课程」）在 zh collation 下排序可能与页面顺序重合使排序变异恒等。判别数据用**纯 CJK** 且拼音序/码点序都与页面顺序不同。
- 变异逃逸②（节点引用）：断言「重载只重置某块」必须重新 querySelector——重渲染后旧引用的 `.value` 不变，会放过整面板重渲染类变异；容器身份不变（只 clearEl 子节点），别拿容器比对。
- 伪元素 `::before` 的 content 不进 `textContent`，jsdom 断言不到——分隔符要写在标记里。
- grid/flex 溢出：卡片（grid item）与内部 span（flex item）**两处都要 `min-width: 0`**，缺一个就撑破面板。
- 值班汇总页是「渲染即过滤」：搜掉的条目不进 DOM，断言用渲染出的卡片集合而非 display；jsdom 夹具 `bootPage` **必须至少给一个 shiftRow**（ends_at 取边界时刻如 05:00+08:00 而非午夜，否则日期键滚到下一天 chooseInitialShift 选不中）。
- 审阅页初始视图只渲染「综报×采纳」桶，fake `/review` 要按 decision+report_type 双过滤，卡片数按桶算。
- 页面 `loadStats()` 常是 fire-and-forget：用例 close 前要 `waitFor(server.inflight === 0)`，否则 showToast 撞已销毁 document 变 unhandled rejection；夹具原地改数据的必须是工厂函数逐用例新建。
- `window.confirm` jsdom 未实现（返回 undefined），测试用桩替换并记录参数。
- 跨页复用组件只能复刻样式、**类名不能共用**：`filter-refine-clear-link` 等类名会被 filter_refine.js 的委托劫持点击；禁用 `.filter-refine-row`/`data-refine-field` 命名同理。
- 浮层分档现状：sticky 工具条最高 100（review-toolbar），账号菜单 150，modal/content_drawer 1000，检索抽屉 1050-1200，toast 2000。改层级先对照这张表。
- `--test-name-pattern` 配中文用例名可定向跑 node --test（全角＋号曾匹配超时，换类名定位）；`page.window.eval('state.dirty.xxx')` 可读页面全局 const。

## 已知「等价变异」/ 测试盲区备忘

变异测试里存活过、后来定性为等价或需特殊用例的：
- Pydantic 响应模型字段默认值 None 会补回缺失键——API 层测不出 service 兜底 dict 的缺键（抽屉徽章 M15）。
- `^` 锚定下 `re.match ≡ re.search`（bjrb 合并逻辑 M8）。
- `Number('') === 0`：空筛选值经 Number 归一会被当成 hour=0（细化筛选时被夹具抓出的真缺陷，注意此类归一）。
- 「行为被后续代码掩盖」的变异要在实现里安排可区分观察点（如清空选择提前到 toast 之前、测试包装 showToast 捕获时刻），纯时序轮询抓不住同步代码块内的差异。
- 「一词命中两类归高优先级」类排序优先级变异，普通各归各类用例测不出，需要专门的跨类用例（review_sort R4，别删）。
