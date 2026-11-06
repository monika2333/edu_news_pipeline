# 技术踩坑与站点知识

代码层面、外部站点层面的坑。操作流程类（测试/worktree/合并）在 [workflow.md](workflow.md)。细节出处见 `archive/` 对应文件。

## Python

- **f-string 表达式内禁写反斜杠转义**（PEP 701，3.12 才放开）：在 f-string 的表达式部分写 `\'` 之类的转义（例如内联带引号的 SQL 字面量）在 3.9~3.11 导入期直接 SyntaxError。SQL 字面量先赋普通变量再进 f-string。注意区分：反斜杠出现在 f-string **字面量部分**（如 `rf'...["\']...'`）一直合法。用户明确要求过这条（4e68c9b 修复过一处）。
- 行尾敏感的文件读写用 `write_bytes`，`write_text` 在 Windows 会 LF→CRLF（详见 workflow.md 提交卫生）。
- Markdown/配置/中文文本统一 UTF-8 无 BOM。

## 数据库

- **plpgsql 的 `RAISE EXCEPTION` 不带括号**：语法是 `RAISE EXCEPTION '格式串 %', 参数`；写成函数调用式 `RAISE EXCEPTION ('...', arg)` 直接 SyntaxError，报错指向括号处，与「DO 块里其他 IF/循环」无关（20261106150000 迁移实测）。jsonb 取值参与比较时两边都要 `->>`（`text = jsonb` 无操作符）。
- **psycopg 3.2：`with conn:` 退出会关闭连接**（`Connection.__exit__` 里非 pool 连接直接 close），不再是旧「事务边界」语义。真实库测试若在 with conn 块外还要用连接（adapter 调用+断言），必须显式 seed + `conn.commit()`，不能包 with conn。
- **app_settings（及任何可能被应用先行写入的表）种子迁移必须带 `ON CONFLICT ... DO NOTHING`**：设置页可以在任何迁移之前建行，长寿命库上裸 INSERT 必撞 pkey（20260923120000 实际炸过，3f1e4cd 修复了 llm_endpoints 与 review_sort_keywords 两条）。
- 诊断 dbmate 失败以 `schema_migrations` 表为准——失败的迁移不留记录；用户终端粘贴的报错可能串行误导。
- **`dbmate up` 回写的 `schema.sql` 只有「迁移版本行」该提交**：pg_dump 18 每次生成不同的 `\restrict`/`\unrestrict` 随机令牌，还会带出 public schema 注释块、以及把 `schema_migrations.version` 写成本机库现状的 `varchar`（无长度）。后两者与 `tests/test_migration_replay.py` 里硬编码 `varchar(128)` 的镜像 DDL 冲突，原样提交该用例立刻变红（实测）。现在由 `scripts/db/refresh-schema.ps1` 统一处理（规程见 [workflow.md](workflow.md)「刷新 schema.sql」），`tests/test_schema_normalizer.py` 把「提交的 `schema.sql` 必须已是规整形态」钉进了 CI。历史上这份文件的形态来回翻转多次（a3a63e4 等）就是每次有人手工清理这类噪音。
- 「清理旧新闻」类多桶写路径：回滚类测试的注入点必须在「写入已执行」之后；「先锁全部再统一写」的实现里，锁定阶段注入是空转（用户抓到过 T3 空转，09e7c96 修订）。
- service 层报别校验用 `NEWS_REPORT_TYPES` 显式判断——`coerce_report_type` 会把非法值静默归综报，不能当守卫用。
- **迁移里要写 `CREATE/DROP INDEX CONCURRENTLY`，就必须让一个 up 块只含一条 DDL**：dbmate 与 `tests/test_migration_replay._migration_up` 都把「`-- migrate:up` 到 `-- migrate:down`」之间的整段文本当**一条语句串**执行，PostgreSQL 会把它包进隐式事务，`CONCURRENTLY` 直接报 `ActiveSqlTransaction`（2026-10-28 实测）。`transaction:false` 只关掉 dbmate 的显式 BEGIN，不改变「多语句被包进隐式事务」这件事。拆成多个 `-- migrate:up` 块 dbmate 能过，但回放只抽取第一段再单次 execute，反而必红。要么接受非并发建索引（小块表秒级、写锁只在该事务内），要么改回放工具——别假设 `transaction:false` 就能并发。
- **改表达式索引时复用索引名，别起新名**：baseline 迁移 `20241101000000` 里有 `create index if not exists news_summaries_search_expr_trgm`，而 `test_migration_replay::test_backfilled_baseline_is_applied_without_changing_existing_schema` 会删掉 baseline 版本号后重放 baseline、要求 schema 逐字不变。换成新索引名 → 重放时旧表达式索引被建回来 → 该用例必红。正确做法是 `drop index if exists <旧名>` + **同名** `create index if not exists`（新表达式）；重放 baseline 时 `if not exists` 按名字判为 no-op。
- **trigram GIN 索引在长正文上很占体积，别留死索引**：`news_summaries` 5331 行就有 138 MB/索引（正文平均两万多字符），`raw_articles` 24734 行约 245 MB。表达式换掉后旧索引不再被任何查询选中，留着只吃写放大。2026-10-28 一开始为绕开上面那条回填用例保留了旧摘要索引，量出体积后改为同名重建，库从 669 MB 收到 534 MB。
- **删除一张 baseline 建过的表时，先想 `test_backfilled_baseline_is_applied_without_changing_existing_schema`**：baseline（20241101000000）对建过的表多有 `create table if not exists` 无条件补建；schema.sql 一旦不含该表（删表 + 刷新后），该用例重放 baseline 会把表原样建回来，`after == before` 必红。也不能直接删 baseline 的建表段——重放链上后续迁移可能依赖 baseline 老表结构才有的列（2026-10-30 删 brief 两表时，`20260729130100` 的索引要 `brief_items.article_id`，该列只在 baseline 建表里有、init 迁移建表没有，直接删会 `UndefinedColumn`）。可行解是给 baseline 建表加「远古库才补建」的守卫（本次以 `submitted_reports` 是否存在为界），细节见 archive/2026-10-30-移除TXT简报导出.md。
- **psql 直连本地库优先用 `.env.local` 的 `DATABASE_URL`**（`psql "$DATABASE_URL"`）：逐个拼 `--host/--user` 时一个变量缺失就会落进交互式等密码，命令静默挂起到超时；`dbmate` 本身吃 `DATABASE_URL` 没这个问题。

## 日志

- **adapters 层要让日志可见必须 `print(..., file=sys.stderr)`**：CLI/定时任务没有 logging handler，`logging.info` 被丢弃（只有 WARNING+ 走 lastResort）。照 `http_toutiao.py`/`http_tencent.py` 先例：`[info]`/`[warn]` 前缀；语义性 warning 可同时 `LOGGER.warning`。adapters 不能 import src.workers（依赖方向），所以不能用 `log_info`。

## LLM / 外部 API

- **切换接入点后只有 geo_classify 400？先查结构化输出：`response_format` 的 `json_schema` 是 OpenAI 扩展，DeepSeek 等原生 provider 只支持 `json_object`**，会回 `400 This response_format type is unavailable now`。2026-11-06 下午控制台把七个步骤从 OpenRouter 切到 `api.deepseek.com`（`deepseek-flash`）后，只有带 `json_schema` 请求的京内判定步骤失败（241 篇待判定全灭），其余步骤不带 `response_format` 所以照常。`llm_beijing_gate.build_response_format()` 现在按 `api_style` 选格式：`openrouter` 保留 strict `json_schema`，其余降级为 `json_object`（字段契约写在提示词里，解析失败仍会重试）。新增接入点时如果它其实支持 `json_schema`，不会自动启用——需要显式扩展 `JSON_SCHEMA_CAPABLE_API_STYLES`。
- **4xx 默认不再重试，错误按归属分四类（2026-11-06）**：`post_chat_completion` 现在只重试 408/429/5xx 与网络异常；400/401/403/404/409/422/425 只发一次。`classify_llm_failure` 的优先级是额度 > 临时 > article（仅 400/413 且命中上下文/长度/体积词）> systematic（其余）。geo 的 systematic、额度和配置类错误不写 `beijing_gate_fail_count`、不兜底、熔断本轮并飞书告警；article 级只计一次、达上限保持 `pending_beijing_gate` 作为死信；临时错误保留原重试/计数/兜底。**不要再把 400 计入文章失败，也不要恢复「重试所有非 2xx」的默认值**。
- **配置类错误也算 systematic，不能只看 HTTP 400**：缺少 API Key（`LLMEndpointKeyMissingError`）、接入点主机不在 `LLM_ALLOWED_HOSTS`（`LLMEndpointBlockedError`）、业务配置缺步骤/接入点（`BusinessConfigError`）发生在请求发出前，没有 HTTP 状态。geo 已把它们纳入熔断，飞书消息显示 `no HTTP response`、provider code 用异常类名；只按 HTTP 状态判断会漏掉这些，继续记成文章失败。
- **京内判定的「默认按北京放行」兜底仍然存在，但现在只对临时错误生效**：`beijing_gate_fail_count` 达到 `BEIJING_GATE_MAX_RETRIES`（默认 3）时，只有 5xx/408/429/网络超时这类临时错误才会把稿件标成 `ready_for_export` 且 `is_beijing_related=True`；systematic/article/配置类错误不会走这条路。看到 gate 大批失败仍要按「可能误放行」核查，先看日志里的 `scope` 和是否 `systematic=1`。
- **`LLMQuotaError` 曾被 frozen dataclass 吞掉**：RuntimeError 子类经过 `with worker_session(...)` 这类 contextlib 上下文时，contextlib 会做 `exc.__traceback__ = ...` 赋值；frozen dataclass 的 `__setattr__` 抛 `FrozenInstanceError`，真正的额度错误被替换成 `FrozenInstanceError`。`LLMQuotaError` / `LLMRequestError` 必须保持普通 dataclass（非 frozen），新增异常子类不要随手加 `frozen=True`。
- **全量 pytest 偶发 `test_postgres_adapter_core_roundtrip` 失败**：该用例会向生产库插一行 `pending_enrichment`，再断言它出现在 `fetch_pending_enrichments(20)` 的前 20 条里；定时流水线并发运行时可能把这一行消费掉，导致断言失败。单独重跑或错开流水线窗口即可通过，先别怀疑本次改动。彻底修法是让测试用临时表/fixture 隔离（未做）。

## Windows 计划任务 / PowerShell 脚本

- **`.ps1` 里出现非 ASCII 又没存 UTF-8 BOM → Windows PowerShell 5.1 按 ANSI(cp936) 解码，解析期就炸**：中文注释最后一个字节会把后面的 `\r\n` 当双字节字符吃掉，下一行 `param(` 被并进注释，报错却指向 `param` 块的 `)`（`Unexpected token ')'`）。计划任务的表现只是「启动即结束」+ Last Result=1，**连脚本自己的日志都不会生成**——`Tee-Object` 在解析失败之后才轮到执行。2026-10-30 `EduNewsPromptOptimizer` 连续两晚空跑就是这个原因（`scripts/run_optimize_prompts.ps1` 首行中文注释，2026-10-29 新增）。规矩：`scripts/**/*.ps1` 一律纯 ASCII，要中文就存 UTF-8 BOM；`tests/test_powershell_script_encoding.py` 已钉住。**给既有纯 ASCII 的 .ps1 加中文注释同样触发**（2026-11-03 给 `run_feishu_archive_bot_service.ps1` 加中文注释，CI 红一次后 31bf7b1 改回英文）——AI 会话改任何 `.ps1` 前先 `grep -cP "[^\x00-\x7F]"` 查非 ASCII 或跑一遍该用例。
- 排查「秒退、无日志、Last Result=1」的顺序：① `schtasks /query /tn <名字> /v /fo LIST` 看 Last Run/Last Result 与 Task To Run；② 把 Task To Run 整条**原样**在任务用的解释器下跑一遍——必须是 `powershell.exe`（5.1），用 `pwsh` 7 复现不出来（PS7 默认按 UTF-8 读无 BOM 脚本）；③ 再看数据库里有没有本次运行的记录，区分「脚本没跑起来」与「跑起来后失败」。
- 任务日志是 `Tee-Object` 写的 UTF-16LE，本机用 `Get-Content` 读正常；用非 Windows 工具或 python 按 UTF-8 读会显示乱码，不要当成日志损坏。

## 爬虫站点知识（adapters）

通用：
- `src/adapters/http_common.py` 共享 `build_session`/`decode_response`/`html_to_markdown`（统一剥图版）/`strip_site_suffix`；黄金快照 `tests/adapters/test_http_common_golden.py` 锁行为。合法例外已写进 `src/adapters/AGENTS.md`。
- **剥图是用户拍板的决策**：流水线纯文字，图片一律剥离；xinhua 图说文字保留（在 alt/正文中）。
- `decode_response` 分级：服务端头 > HTML meta 声明 > chardet > utf-8（中新网服务器不带 charset 时 chardet 误判 pt154 致整页乱码的存量 bug）。
- trust_env 参数化有实测意义：jyb/劳动午报关代理，其余信任系统代理（本机挂死代理时表现不同）。
- 账号型来源（beijinghao/btime/tencent 走 crawl_accounts）与列表型来源行为不同；worker 每轮先加载 existing_ids 去重。防灌档靠静态上限 + existing 截停 + lookback 窗口（15 源审计结论见 archive/2026-10-01-千龙默认页数1页.md）。
- **账号型来源补历史时，`existing_ids` 只能过滤、不能截停翻页**：`--pages N` 补历史时第 1 页往往全是已入库条目，若把「过滤后为空/不足一页」当停止条件，page 2 以下老稿永远抓不到。停止只看原始 `data` 数组：空、原始条数小于页大小、或 `pages` 硬上限；`existing_ids` 只过滤。新京号 2026-11-06 第一版实现踩过，修复见 [archive/2026-11-06-新京号接入.md](archive/2026-11-06-新京号接入.md)。

各站特记：
- **btime（北京时间）**：`refresh` 必须固定 1——refresh=0 接口会静默返回 2020 年存档；`pageRow:10` 单包、`list_items` 显式 `del pages` 不翻页。视频稿（news_type=3）正文只在页面内嵌 `topic_data` JSON（`modules[*].modules[*][*].data.content` 取 `type=="txt"`），可见 article 节点是标题复读 SEO 样板；实现为 topic_data 优先、HTML 兜底。
- **stdaily（科技日报）**：只有「滚动新闻」一个栏目；列表解析**必须圈定 `div.f_lieb_list` 容器**，全页抓锚点会混入侧栏 ~25 条「热门点击」旧稿（xinhua 是全页抓，别照抄）；ID `stdaily:<content编号>` 不含频道路径——TRS 同稿跨频道编号相同天然去重；图片说明在 `topic` 属性而非 alt。
- **xinhua（新华网北京频道）**：ID `xinhua:<32hex>`；15 栏目在 `COLUMNS` 维护（/zt 专题已删——只链专题页非文章列表）。**测试里合成 URL 的 slug 种子只能含十六进制字符**（beef/fade/cafe 这类），含 o/u/r 等字母的 seed 会让链接不被识别、测试静默测不到目标行为。
- **xinhua_edu（新华网教育）**：4 频道（mryw/jcjy/gdjy/eduzyjy）首屏 `#content-list > .item`；条目第一个链接是图片（无文字），`.share` 区有一条微博分享链接（非文章，**不计入**任何计数——汇总日志的 `items_without_article` 统计「成不了稿的 .item」：无文章链接**或**取不到标题，正常页面为 0）；侧栏「每日要闻」「热点专题」在容器外但**复用 `.item` 模板形态**，圈定容器不是可有可无。频道失败口径：HTTP 失败、容器缺失、**窗口过滤前一条成稿文章都解析不出**（无 .item / .item 均无文章链接或标题）三者同级——[warn] + 其余频道照常 + 全部失败 raise；解析出文章但窗口内 0 条是正常。列表日期与 `.time` 双向错位 ±1 天（两个真实样本方向相反：jcjy 一篇详情时间跟显示日期、mryw 一篇跟 URL 日期）——口径是 URL 日期为准、详情精确时间覆盖。「权威发布」首屏不按时间倒序（置顶旧稿），首页已出现的新稿不在首屏前 15 条内，只抓首屏会漏，不能接；国际教育、商科教育等已停更。`ds_*.json` 翻页探测 404（站方按需生成）；gdjy 首屏 15 条跨 12 天 ≈1.2 条/天，六成是院校宣传稿。
- **多来源共用爬虫回调时的注册表测试**：xinhua_edu 复用 xinhua 的四个回调名，registry 测试桩的 marker 必须按**回调名所属来源**绑定（`CALLBACK_OWNER_OVERRIDES`），不能从 item.url 推导——从 URL 推导时注册表 name 字段接错到任何来源的桩都返回同样 marker，接线变异（接到 stdaily 等）会静默通过（2026-11-02 补丁实测）。
- **bbtnews（北京商报）**：ID `bbtnews:<CMS编号>`；站方时间粒度只到日（publish_time 落零点）；列表只认左栏 `.c-l-wrap` 内三区，页脚「新闻排行」是同构 li 放宽就混入；URL 强制 https；扩栏目往 `COLUMNS` 加行即可。
- **qianlong（千龙）**：`DEFAULT_MAX_PAGES = 1`，`QIANLONG_PAGES` 仅作上调覆盖（防环境变量漏配整档灌入；existing 截停对新栏目永不触发）。注意变异验证时「默认改回 None」会让测试**无限挂起**（404 不计空页 → `_iter_listing_urls` 死循环），做此变异须带超时。
- **bjrb（北京日报数字版）**：副标题在 `#sub-title`，按原连接符合并进标题（冒号前缀→「主：副」，破折号/无前缀→「主——副」）；只爬当天刊期（`BJRB_DATE` 可指定）。
- **jyb（中国教育报/神州学人）**：文章经 jyb 搜索 API 入库（`jyb:` 前缀）；副题是 `.title` **后面**的 `.subtitle`（同名 div 是引题位）；发布时间在 `var customtime` JS 变量里（bs4 `get_text()` 默认排除 script）。
- **21jingji（21经济网）**：频道页第一页 20 条，主列表是 `div.col-l div.news`（侧栏「热文排行」「智库」同页，全页抓会混入）；「查看更多」的前端接口 `m.21jingji.com/channel/politics?page=..&type=json` 现已 404，**没有服务端翻页**。文章在 `m.21jingji.com/article/<日期>/herald/<hash>.html`，正文 `.txtContent .main_content`，发布时间在 `.newsAuthor .author-infos span`。账号名取频道导航 `#navWrap a.cur`——`navWrap` 是 **id 不是 class**，`.navWrap` 选不中，会跌到标题兜底拿到「宏观财经新闻」。列表只有相对时间：feed 行 publish_time 为空、由详情补；`list_items` 必须跳过 existing_ids，否则 feed upsert 的 `EXCLUDED.publish_time=NULL` 会把详情精确时间冲掉。
- **xinjinghao（新京号）**：文章列表 `https://api.bjnews.com.cn/api/v101/xjh/xjh_article.php?xjh_id=<id>&page=N`，每页 10 条、按时间倒序，`row.content` 自带完整正文（不用再抓详情），`share_url` 详情页 `m.bjnews.com.cn/detail/<uuid>.html`，`publish_timestamp` 是 UTC 秒；号名取 `xjh_info.php` 的 `institution_name`。主页 `/mp/<id>/` 和 `www.bjnews.com.cn/mp/<id>` 都 404，解析要保留无斜杠的 `m.bjnews.com.cn/mp/<id>`。正文结尾模板含「供稿：教育援藏团队」等，必须剥结尾连续元信息行，否则「教育」会命中关键词闸门；`article_id` 统一由 URL 派生（`xinjinghao:<uuid>`），不要一半用 `row.uuid`、一半用 `share_url`。

## 前端 / jsdom 测试

- `createEl` 的 attributes 走 `setAttribute`：`hidden: false` 按存在性生效照样隐藏——hidden 必须事后属性赋值。
- 变异逃逸①（判别数据）：顺序敏感断言里，含拉丁字母的中文（「AI 课程」）在 zh collation 下排序可能与页面顺序重合使排序变异恒等。判别数据用**纯 CJK** 且拼音序/码点序都与页面顺序不同。
- 变异逃逸②（节点引用）：断言「重载只重置某块」必须重新 querySelector——重渲染后旧引用的 `.value` 不变，会放过整面板重渲染类变异；容器身份不变（只 clearEl 子节点），别拿容器比对。
- 伪元素 `::before` 的 content 不进 `textContent`，jsdom 断言不到——分隔符要写在标记里。
- grid/flex 溢出：卡片（grid item）与内部 span（flex item）**两处都要 `min-width: 0`**，缺一个就撑破面板。
- 设置页高级关键词芯片化后，`block.querySelector('input, textarea')` 会命中内联「添加关键词」input，而不是隐藏的批量 textarea；测试要显式用 `.settings-textarea`，否则改的是添加框、脏标记也不会动。
- 值班汇总页是「渲染即过滤」：搜掉的条目不进 DOM，断言用渲染出的卡片集合而非 display；jsdom 夹具 `bootPage` **必须至少给一个 shiftRow**（ends_at 取边界时刻如 05:00+08:00 而非午夜，否则日期键滚到下一天 chooseInitialShift 选不中）。
- 审阅页初始视图只渲染「综报×采纳」桶，fake `/review` 要按 decision+report_type 双过滤，卡片数按桶算。
- 页面 `loadStats()` 常是 fire-and-forget：用例 close 前要 `waitFor(server.inflight === 0)`，否则 showToast 撞已销毁 document 变 unhandled rejection；夹具原地改数据的必须是工厂函数逐用例新建。
- `window.confirm` jsdom 未实现（返回 undefined），测试用桩替换并记录参数。
- 跨页复用组件只能复刻样式、**类名不能共用**：`filter-refine-clear-link` 等类名会被 filter_refine.js 的委托劫持点击；禁用 `.filter-refine-row`/`data-refine-field` 命名同理。
- 浮层分档现状：sticky 工具条最高 100（review-toolbar），账号菜单 150，modal/content_drawer 1000，检索抽屉 1050-1200，toast 2000。改层级先对照这张表。
- `--test-name-pattern` 配中文用例名可定向跑 node --test（全角＋号曾匹配超时，换类名定位）；`page.window.eval('state.dirty.xxx')` 可读页面全局 const。
- 项目**没有 `.btn-sm` 定义**：`btn btn-primary btn-sm` 实际仍套 `.btn` 的 `10px 24px` + `min-height: 40px`，放在检索卡片标题行会变成大蓝块（用户 2026-10-21 明确嫌突兀）。小按钮用专属 class 自己定尺寸（试跑选文章已改用 `.search-pick-btn`）。
- 校验类测试（空正文/未知文章）即使预期在调用 LLM 前抛错，也必须把 LLM 调用换成「一被调用就失败」的桩；否则校验被变异掉时测试会真实请求模型（实测空正文变异跑了约 40s 真实调用）。设置页 `test_prompt_test_service._patch_common` 已默认装桩，需要 LLM 的用例再覆盖。
- **值班页请求走 `workspaceFetch` 的分流，不是透明转发**：2026-10-26 起规则是「需要改写载荷/失效缓存的写操作显式登记；其余 GET 透传到班次前缀；未登记的写操作返回合成 403 并 `console.warn`」。历史坑：早期它是纯白名单 + 默认 403，漏登记的后端接口会静默降级，症状是「管理员能看到、值班编辑看不到」——`/preview-header` 就这么漏过一次（`ca68db0` 先补白名单，随后按上面的规则改成 GET 透传）。新增值班可见接口时：GET 无需登记，写操作必须想清楚载荷映射与缓存失效。配套要点：① 新功能要把 duty 模式也写进 jsdom 用例（此前只有 admin 用例，所以漏检）；② `bootPage('duty')` 在 `articleCount: 0` 时可能在 `setupTabs()` 之前返回，切页签前必须先等 init 完成（给 `articleCount: 1` 最省事）；③ 夹具的 `/reviews` 要能返回值班审阅列表（`loadAllDutyItems` 走它分页聚合）。行为由 `tests/js/workspace_proxy.test.js` 锁定。
- 提示词试跑复用共享搜索抽屉时，选择模式必须跳过 `search_drawer_open` / `search_drawer_tab` / `search_filters` 三个共享 localStorage 键；用 `body[data-search-drawer-mode="pick"]` 切模式，不要改三页默认行为。
- **给 node 子进程传 env 必须基于 `os.environ` 拷贝再增改**：整个替换成最小字典（只留 `CONSOLE_STATIC_DIR` 等）会让 Node 启动即崩 `Assertion failed: ncrypto::CSPRNG(nullptr, 0)`（rc=134），像测试全崩但其实是进程没起来。变异测试驱动里实际踩过（2026-11-04）。
- jsdom 没有 `TouchEvent`：用普通可冒泡 `Event` + `Object.defineProperty(event, 'touches'/'changedTouches', ...)` 就能驱动页面里的手势逻辑（页面只读这两个字段与 `cancelable`）。但「长按抬手后浏览器补发一次 click」只在真实浏览器发生，jsdom 里手动补发 click 测的是兜底守卫，不是浏览器语义；真实触屏行为用 Playwright + CDP `Input.dispatchTouchEvent`（context 开 `has_touch=True`）验证。jsdom 的 label 激活行为尊重 `defaultPrevented`（捕获阶段 `preventDefault` 能拦住 label→radio）。
- **「常驻节点 + hidden 切换」配行内流布局会自己把布局搞坏**（设置页模型步骤表，2026-11-06）：提示 span 与控件平铺在同一个 `td` 里，`hidden` 只负责显隐、不负责不占位——一旦它没被隐藏，长文本就把后面的 select/input 挤到第二行，且换行后的内容从单元格左缘起排，看起来像「错位」。现行形态（默认模型跟随接入点改造后）是：模型留空灰字 `.step-model-hint` 常驻渲染、靠 `:empty { display: none }` 消失，文本更新只改 textContent 不重建节点（重建会抢焦点）。顺带两个表格布局事实：`table-layout: auto` 下列宽由单元格内容决定，长提示会把别的列压到逐字断行（中文可在任意字间断行），要 `width: 1%` + `nowrap` 保列、把 `width: 100%` 给需要吸收剩余空间的列；行内流里的 `select`/`input` 宽度要显式给（`flex: 1 1 14em; min-width: 0`），否则一个长文本邻居就能让它们换行。
- **jsdom：节点未挂载前不要走「容器.querySelector」刷新它**：`updateStepModelHint(row, …)` 在 td 尚未 append 到 row 时调用，`row.querySelector` 找不到目标、提示留空——挂载完成后再调用，或直接持有节点引用赋值（2026-11-06 实测，症状是「函数单跑正确、页面渲染后为空」）。
- **jsdom：分区保存成功路径会重渲染页签，保存状态节点被重建且为空**：成功文案写在旧节点上，保存后断言 `models-save-status` 永远超时；成功断言走 `#toast`（「模型配置已保存」），失败分支（422/409 不重渲染）才能断言状态节点（2026-11-06 实测）。

- 纯 CSS/布局改动 jsdom 测不到，用 headless Chrome 直接渲染静态夹具核对几何：`chrome --headless=new --window-size=W,H --screenshot=out.png file:///...` 出图，`--dump-dom` 配合页内 `window.onload` 脚本把 `getBoundingClientRect()` 写进 `<pre>` 再解析，能量到「各行第一列左缘是否一致、`table.scrollWidth` 是否超出 `.admin-table-wrap`」。CLI 的 headless Chrome **可以**开 `file://` 夹具（workflow.md §5 那条 `file:` 限制只针对浏览器技能/Playwright）。夹具放临时目录，不要把预览 HTML 提交进仓库。

## 已知「等价变异」/ 测试盲区备忘

变异测试里存活过、后来定性为等价或需特殊用例的：
- Pydantic 响应模型字段默认值 None 会补回缺失键——API 层测不出 service 兜底 dict 的缺键（抽屉徽章 M15）。
- `^` 锚定下 `re.match ≡ re.search`（bjrb 合并逻辑 M8）。
- `Number('') === 0`：空筛选值经 Number 归一会被当成 hour=0（细化筛选时被夹具抓出的真缺陷，注意此类归一）。
- 「行为被后续代码掩盖」的变异要在实现里安排可区分观察点（如清空选择提前到 toast 之前、测试包装 showToast 捕获时刻），纯时序轮询抓不住同步代码块内的差异。
- 「一词命中两类归高优先级」类排序优先级变异，普通各归各类用例测不出，需要专门的跨类用例（review_sort R4，别删）。
