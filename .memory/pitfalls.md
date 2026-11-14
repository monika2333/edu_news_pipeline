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
- **4xx 默认不再重试，错误按归属分四类（2026-11-06，2026-11-07 扩展）**：`post_chat_completion` 现在只重试 408/429/5xx 与网络异常；400/401/403/404/409/422/425 只发一次。`classify_llm_failure` 的优先级是额度 > 临时 > article（仅 400/413 且命中上下文/长度/体积词）> systematic（其余）。`geo-classify`、`external-filter`、`summarize` 的 systematic、额度和配置类错误不写文章失败计数、不兜底、不提前停止、不让整轮报错，每轮最多一条请求错误告警；article 级只计一次、达上限保持 `pending_beijing_gate` 作为死信；临时错误保留原重试/计数/兜底。**不要再把 400 计入文章失败，也不要恢复“重试所有非 2xx”的默认值，或“系统性错误取消剩余请求 + 整轮报错”的旧熔断做法**。
- **配置类错误也算 systematic，不能只看 HTTP 400**：缺少 API Key（`LLMEndpointKeyMissingError`）、接入点主机不在 `LLM_ALLOWED_HOSTS`（`LLMEndpointBlockedError`）、业务配置缺步骤/接入点（`BusinessConfigError`）发生在请求发出前，没有 HTTP 状态。判定与告警统一在 `src/workers/llm_failures.py`，三个步骤共用；飞书消息显示 `no HTTP response`、provider code 用异常类名。只按 HTTP 状态判断会漏掉这些，继续记成文章失败。
- **系统性错误取消计数后，两个循环/计数坑必须保留**（2026-11-07）：`external-filter` 的 `while True` 原本靠“失败计数涨到上限后候选取不出来”自然结束；系统性错误不计数后同一批候选会被反复取回，必须像 `geo-classify` 一样用本轮 `attempted_article_ids` 去重，否则死循环。`summarize` 的 `mark_attempt` 是“先加后请求”（兼作认领），系统性错误必须退回这次预加，且退回 SQL 只能作用于仍 `summary_status='pending' AND status='pending'` 且计数 > 0 的行，否则会把别的状态或 0 计数改坏。
- **京内判定的「默认按北京放行」兜底仍然存在，但现在只对临时错误生效**：`beijing_gate_fail_count` 达到 `BEIJING_GATE_MAX_RETRIES`（默认 3）时，只有 5xx/408/429/网络超时这类临时错误才会把稿件标成 `ready_for_export` 且 `is_beijing_related=True`；systematic/article/配置类错误不会走这条路。看到 gate 大批失败仍要按「可能误放行」核查，先看日志里的 `scope` 和 `systematic=N`（本轮受系统性错误影响的篇数，文章仍保持待处理）。
- **`LLMQuotaError` 曾被 frozen dataclass 吞掉**：RuntimeError 子类经过 `with worker_session(...)` 这类 contextlib 上下文时，contextlib 会做 `exc.__traceback__ = ...` 赋值；frozen dataclass 的 `__setattr__` 抛 `FrozenInstanceError`，真正的额度错误被替换成 `FrozenInstanceError`。`LLMQuotaError` / `LLMRequestError` 必须保持普通 dataclass（非 frozen），新增异常子类不要随手加 `frozen=True`。
- **全量 pytest 偶发 `test_postgres_adapter_core_roundtrip` 失败**：该用例会向生产库插一行 `pending_enrichment`，再断言它出现在 `fetch_pending_enrichments(20)` 的前 20 条里；定时流水线并发运行时可能把这一行消费掉，导致断言失败。单独重跑或错开流水线窗口即可通过，先别怀疑本次改动。彻底修法是让测试用临时表/fixture 隔离（未做）。
- **测试断言 `os.environ` 全量扫描的结果时，必须先把环境变量隔离**（2026-11-12 修）：`_configured_api_key_envs()` 扫的是整个 `os.environ`，而 `get_settings()` 会把 `.env.local` 灌进去——本机配了真 Key 的机器上名单会多出几项，`assert configured == ["LLM_DEEPSEEK_API_KEY"]` 这种严格等值断言就只在 CI（没有 `.env.local`）绿、本机恒红，且与改动无关（stash 掉同样红，容易被误判成回归）。修法是让测试自洽而不是放宽断言：先 `load_environment()`（幂等，保证文件值已经落进 `os.environ`），再用 `monkeypatch.delenv` 清掉所有匹配 `LLM_API_KEY_ENV_PATTERN` 的变量，最后 `setenv` 本用例要的那一个——`monkeypatch` 会还原，跨用例不泄漏。用 `in` / `not in` 写的同类断言本来就不受影响。

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
- **beijinghao（北京号）同一个 source 承载两种栏目页（2026-11-14）**：① 传统北京号栏目 `https://peking.bjd.com.cn/bjhrootcolumn/system/<24hex>`——列表是 HTML（`.picTxt`）、翻页路径 `more_N`、正文 `peking.bjd.com.cn/content/<id>.json`；② 北京日报客户端「栏目分类页」`https://static.bjd.com.cn/static/category/?categoryId=…&channelId=…&columnId=…`——页面是 JS 渲染的空壳、**没有 bjhrootcolumn 栏目编号**，列表走检索接口 `https://jiansuo.bjd.com.cn/cass/search?channelId=…&categoryId=…&page=N`（page 从 **0** 起、每页 10 条、`data.totalElements` 是全量、按发布时间倒序），`…/columns/<columnId>/category/<categoryId>/pages/N/stories.json` 的页号**不是分页**（实测 N=0 与 N=1 返回同一批 10 条），正文在 `xinwen.bjd.com.cn/content/<id>.json`（与 peking 那份同构，所以 `_detail_json_url` 必须按稿件 URL 的域名取而不是写死 peking）。账号名：客户端栏目页的 `<title>` 是空串（JS 事后写入），名称取自页面自己渲染标题用的 `https://static.bjd.com.cn/mdms-res/category/json/<channelId>/category.json`（按 columnId → categories → categoryId 取 `name`）。账号标识用规整后的栏目页地址（同 21jingji/chinadaily「账号 = 栏目 URL」），参数顺序固定。VIDEO/LIVE 稿不入库（没有可用文字正文）。`existing_ids` 只过滤不截停翻页——判断「这页还有没有内容」必须看检索接口的**原始行数**，拿过滤后的条数判末页会在「整页都已入库」时提前停。
- **btime（北京时间）**：`refresh` 必须固定 1——refresh=0 接口会静默返回 2020 年存档；`pageRow:10` 单包、`list_items` 显式 `del pages` 不翻页。视频稿（news_type=3）正文只在页面内嵌 `topic_data` JSON（`modules[*].modules[*][*].data.content` 取 `type=="txt"`），可见 article 节点是标题复读 SEO 样板；实现为 topic_data 优先、HTML 兜底。
- **stdaily（科技日报）**：只有「滚动新闻」一个栏目；列表解析**必须圈定 `div.f_lieb_list` 容器**，全页抓锚点会混入侧栏 ~25 条「热门点击」旧稿（xinhua 是全页抓，别照抄）；ID `stdaily:<content编号>` 不含频道路径——TRS 同稿跨频道编号相同天然去重；图片说明在 `topic` 属性而非 alt。
- **xinhua（新华网北京频道）**：ID `xinhua:<32hex>`；15 栏目在 `COLUMNS` 维护（/zt 专题已删——只链专题页非文章列表）。**测试里合成 URL 的 slug 种子只能含十六进制字符**（beef/fade/cafe 这类），含 o/u/r 等字母的 seed 会让链接不被识别、测试静默测不到目标行为。
- **xinhua_edu（新华网教育）**：4 频道（mryw/jcjy/gdjy/eduzyjy）首屏 `#content-list > .item`；条目第一个链接是图片（无文字），`.share` 区有一条微博分享链接（非文章，**不计入**任何计数——汇总日志的 `items_without_article` 统计「成不了稿的 .item」：无文章链接**或**取不到标题，正常页面为 0）；侧栏「每日要闻」「热点专题」在容器外但**复用 `.item` 模板形态**，圈定容器不是可有可无。频道失败口径：HTTP 失败、容器缺失、**窗口过滤前一条成稿文章都解析不出**（无 .item / .item 均无文章链接或标题）三者同级——[warn] + 其余频道照常 + 全部失败 raise；解析出文章但窗口内 0 条是正常。列表日期与 `.time` 双向错位 ±1 天（两个真实样本方向相反：jcjy 一篇详情时间跟显示日期、mryw 一篇跟 URL 日期）——口径是 URL 日期为准、详情精确时间覆盖。「权威发布」首屏不按时间倒序（置顶旧稿），首页已出现的新稿不在首屏前 15 条内，只抓首屏会漏，不能接；国际教育、商科教育等已停更。`ds_*.json` 翻页探测 404（站方按需生成）；gdjy 首屏 15 条跨 12 天 ≈1.2 条/天，六成是院校宣传稿。
- **多来源共用爬虫回调时的注册表测试**：xinhua_edu 复用 xinhua 的四个回调名，registry 测试桩的 marker 必须按**回调名所属来源**绑定（`CALLBACK_OWNER_OVERRIDES`），不能从 item.url 推导——从 URL 推导时注册表 name 字段接错到任何来源的桩都返回同样 marker，接线变异（接到 stdaily 等）会静默通过（2026-11-02 补丁实测）。
- **bbtnews（北京商报）**：ID `bbtnews:<CMS编号>`；站方时间粒度只到日（publish_time 落零点）；列表只认左栏 `.c-l-wrap` 内三区，页脚「新闻排行」是同构 li 放宽就混入；URL 强制 https；扩栏目往 `COLUMNS` 加行即可。
- **qianlong（千龙）**：`DEFAULT_MAX_PAGES = 1`，`QIANLONG_PAGES` 仅作上调覆盖（防环境变量漏配整档灌入；existing 截停对新栏目永不触发）。注意变异验证时「默认改回 None」会让测试**无限挂起**（404 不计空页 → `_iter_listing_urls` 死循环），做此变异须带超时。
- **laodongwubao（劳动午报）有两层人机验证，纯 requests 抓不到正文**（2026-09-22 起，2026-11-12 修复）：第一层是 `stampPrefix`/`zeroLen` 的**前缀零工作量证明，算法已从 SHA-1 改成 SHA-256**（用 sha1 算的 counter 会被判无效回 403）；第二层是创天 WAF 的环境指纹页（`g_ctwaf_env_check_info` / `g_ctwaf_dyntoken_info`，混淆 JS + WebAssembly 采 `navigator`/canvas/WebGL），Python 侧复算不了。现行做法：`ensure_access()` 先探测，只有拿到验证页才起一次 headless chromium 换 `ctwj_token_*` 等 cookie，正文仍用 requests 抓（`LDWB_WAF_BROWSER` 可关）。三个必须记住的细节：① **chromium 必须带 `--disable-blink-features=AutomationControlled`**，否则 `navigator.webdriver === true`，WAF 直接 403（不需要注入 JS 覆盖 webdriver，那反而更难维护）；② 验证通过后页面整页 reload，轮询 `page.title()` 会撞「Execution context was destroyed」，要 try/except；成功与否看 token cookie 不看标题；③ 站点 https 端点恒定 `tlsv1 alert internal error`（边缘行为，非证书问题），刊期页与正文都只在 http 上。故障定位顺序：日志里「浏览器握手失败」= 浏览器/依赖问题，「握手后仍是验证页」= 站方又改了验证。细节见 [archive/2026-11-12-劳动午报WAF恢复.md](archive/2026-11-12-劳动午报WAF恢复.md)。
- **bjrb（北京日报数字版）**：副标题在 `#sub-title`，按原连接符合并进标题（冒号前缀→「主：副」，破折号/无前缀→「主——副」）；只爬当天刊期（`BJRB_DATE` 可指定）。**「今天这一期还没上线」的实测形态（2026-11-14 用 20261115 / 20261201 请求）**：① `period/YYYYMM/period.js` 当月返回 200，`datelist` 只列到已出刊的日期（含当天、不含明天）；当月还没有任何一期时该文件 **404**；② 未出刊日期的 `mobile/YYYY/YYYYMMDD/YYYYMMDD_m.html` 返回 **404**。所以判定口径是「月份索引 404，或日期不在索引里，或索引已列出但刊期页 404」= 尚未上线（`BjrbIssueNotPublished`，流程层只记一行后跳过、不计失败）；而 5xx、超时/网络错误、刊期页 200 却解析不出稿件都按**真实故障**抛出（计入 `failed_sources`）。**不要把 `requests.RequestException` 一律折成「尚未上线」**——那样站点 5xx 会被永久吞掉、日志里再也看不到抓取失败。电子报任务 00:00 那轮几乎必然命中「尚未上线」，这是正常现象。
- **bjwb（北京晚报数字报，2026-11-14 接入）**：与 bjrb 同一平台、同一套 mobile 版式，`http_bjwb` 复用 `http_bjrb` 的解析器（`parse_issue_index` / `parse_article` / 行构造函数传 `source_key` / `source_name`）。实测：① 刊期入口 `https://bjrbdzb.bjd.com.cn/bjwb/paperindex.htm` 是 200 的纯 JS 跳转页（`window.location.href="./mobile/2026/20261114/20261114_m.html?v="+new Date().valueOf()`），指向**当前最新一期**、Last-Modified 随一期上传更新；② `period/YYYYMM/period.js` 也存在，但 `datelist` 顺序不是日期序（202611 实测 `01,03,02,04…`），别依赖它的顺序；当月还没有任何一期时 404；③ 未出刊日期的 `mobile/YYYY/YYYYMMDD/YYYYMMDD_m.html` 返回 404；④ 正文页选择器与 bjrb 完全一致，`data-newid` 形如 `bjwb2026111400101`（article_id 前缀固定 `bjwb:`，得到 `bjwb:bjwb…`，与 bjrb 的双前缀同构）；⑤ **正文图片一律 `data-src` 懒加载、没有 `src`**，而 `_render_image` 只读 `src`，所以晚报正文里的图片不会进 `content_markdown`（与「图片剥离」口径一致，不要为它给 data-src 加渲染）；⑥ 每期有 5 个左右**纯图片版面**（`■广告`、结婚启事、电视节目预告、找不同的答案、一步棋答案），`#content` 只有一个 `data-src` 图，按 bjrb 的严格解析会抛 `Empty … content`；晚报用 `allow_empty_content=True` 把正文留空写入，不算抓取失败；⑦ 这些空正文行的 `detail_fetched_at` 已写。详情补抓的判据已改为「正文为空**且** `detail_fetched_at` 为空」（2026-11-14 修正 `get_raw_articles_missing_content`，见 data_flow 的 `detail_fetched_at` 契约），所以纯图片版只在第一次抓详情，之后每轮只 upsert 列表行（`already populated: 46`、`ok=0 failed=None skipped=46`），不再重抓；抓取失败不写 `detail_fetched_at`，仍会逐轮重试。
- **jyb（中国教育报/神州学人）**：文章经 jyb 搜索 API 入库（`jyb:` 前缀）；副题是 `.title` **后面**的 `.subtitle`（同名 div 是引题位）；发布时间在 `var customtime` JS 变量里（bs4 `get_text()` 默认排除 script）。
- **21jingji（21经济网）**：频道页第一页 20 条，主列表是 `div.col-l div.news`（侧栏「热文排行」「智库」同页，全页抓会混入）；「查看更多」的前端接口 `m.21jingji.com/channel/politics?page=..&type=json` 现已 404，**没有服务端翻页**。文章在 `m.21jingji.com/article/<日期>/herald/<hash>.html`，正文 `.txtContent .main_content`，发布时间在 `.newsAuthor .author-infos span`。账号名取频道导航 `#navWrap a.cur`——`navWrap` 是 **id 不是 class**，`.navWrap` 选不中，会跌到标题兜底拿到「宏观财经新闻」。列表只有相对时间：feed 行 publish_time 为空、由详情补；`list_items` 必须跳过 existing_ids，否则 feed upsert 的 `EXCLUDED.publish_time=NULL` 会把详情精确时间冲掉。
- **xinjinghao（新京号）**：文章列表 `https://api.bjnews.com.cn/api/v101/xjh/xjh_article.php?xjh_id=<id>&page=N`，每页 10 条、按时间倒序，`row.content` 自带完整正文（不用再抓详情），`share_url` 详情页 `m.bjnews.com.cn/detail/<uuid>.html`，`publish_timestamp` 是 UTC 秒；号名取 `xjh_info.php` 的 `institution_name`。主页 `/mp/<id>/` 和 `www.bjnews.com.cn/mp/<id>` 都 404，解析要保留无斜杠的 `m.bjnews.com.cn/mp/<id>`。正文结尾模板含「供稿：教育援藏团队」等，必须剥结尾连续元信息行，否则「教育」会命中关键词闸门；`article_id` 统一由 URL 派生（`xinjinghao:<uuid>`），不要一半用 `row.uuid`、一半用 `share_url`。
- **zgjsb（中国教师报数字报）**：**周报，每周三出刊、一期 16 版**（2025-07～2026-11 共 59 期全是周三），电子版约**北京时间 09:00** 整期上线（`node_22.htm` 的 `Last-Modified` 实测 01:00–01:16 UTC），少数期当天 11:26/14:27/14:46/23:30 补传、也有次日 09:00 才补上的；`paperindex.htm` 的 `META REFRESH` 恒指向最新一期（非出版日指向上周三），所以默认抓「最新一期」而非「今天一期」。**版面文件名不递增**（前 4 版 `node_22~25`、第 5–16 版 `node_82~93`），必须读 `#pageList1 a#pageLink`；正文页左侧栏的 `pagelist1`（小写 L）是不同 id。版面页 `#titleList1` 之外，`#btdh` 与版面图 usemap 会重复列出同一批稿件（按 content 编号去重；`?div=` 对正文无影响）。正文 `td.content_tt` 是 `<P><p>…</p></P>` 双层嵌套（自己遍历 `<p>` 会重复首段），缩进是 U+00A0（`html_to_markdown` 只折叠 `[ \t\r\f\v]`，必须先归一）；元数据在 `</html>` 之后的 `<!--mpproperty-->` 里，`founder-nodename` 恒空、版名只能从版面页取。每期第 01 版末尾固定一条标题「日期」的报头块、图片版有一条「无标题」，都不是稿件。站点**只支持 http**（https 直接断连），jyb.cn 家族要 `trust_env=False`。

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
- **按钮显示态用 `display: inline-flex` 时 `.btn` 的 `text-align: center` 失效**（2026-11-09）：按钮成了 flex 容器、文字是 flex item，默认从主轴起点排。宽度被内容撑满时看不出来（桌面 95px 宽 / 文字 61px 时左右留白正好对称 17/17），一旦被拉伸（手机端整行）就露馅（17/84）。要显式写 `align-items: center; justify-content: center`。审阅页「自动排序」（`.sort-only`）踩过。
- **原生 `<select>` 的选中值在「箭头左侧文本区」居中，`text-align: center` 不等于整个控件居中**（2026-11-11）：主题外观下拉框先只加 `text-align`，用户仍看到文字偏左；再改自绘箭头 + 不对称 padding 后用户认为更差，相关提交已删除。要严格居中只能换自定义下拉，不要在原生 select 上继续用 padding 补偿。
- **没有 `?v=` 的 CSS 链接会长期用旧缓存**（2026-11-11）：主题改造后核心 CSS 和 `search.css` 未带版本号，查重弹窗 `.archive-item` 仍用旧白底，而磁盘上的 CSS/token 已经是暗色。浏览器按完整 URL 缓存，不会因为文件内容变了就自动重读；所有 stylesheet 必须带版本号，版本值用静态文件指纹（mtime/size），不要再用每次请求的当前时间（会让缓存完全失效）。
- 校验类测试（空正文/未知文章）即使预期在调用 LLM 前抛错，也必须把 LLM 调用换成「一被调用就失败」的桩；否则校验被变异掉时测试会真实请求模型（实测空正文变异跑了约 40s 真实调用）。设置页 `test_prompt_test_service._patch_common` 已默认装桩，需要 LLM 的用例再覆盖。
- **值班页请求走 `workspaceFetch` 的分流，不是透明转发**：2026-10-26 起规则是「需要改写载荷/失效缓存的写操作显式登记；其余 GET 透传到班次前缀；未登记的写操作返回合成 403 并 `console.warn`」。历史坑：早期它是纯白名单 + 默认 403，漏登记的后端接口会静默降级，症状是「管理员能看到、值班编辑看不到」——`/preview-header` 就这么漏过一次（`ca68db0` 先补白名单，随后按上面的规则改成 GET 透传）。新增值班可见接口时：GET 无需登记，写操作必须想清楚载荷映射与缓存失效。配套要点：① 新功能要把 duty 模式也写进 jsdom 用例（此前只有 admin 用例，所以漏检）；② `bootPage('duty')` 在 `articleCount: 0` 时可能在 `setupTabs()` 之前返回，切页签前必须先等 init 完成（给 `articleCount: 1` 最省事）；③ 夹具的 `/reviews` 要能返回值班审阅列表（`loadAllDutyItems` 走它分页聚合）。行为由 `tests/js/workspace_proxy.test.js` 锁定。
- 提示词试跑复用共享搜索抽屉时，选择模式必须跳过 `search_drawer_open` / `search_drawer_tab` / `search_filters` 三个共享 localStorage 键；用 `body[data-search-drawer-mode="pick"]` 切模式，不要改三页默认行为。
- **给 node 子进程传 env 必须基于 `os.environ` 拷贝再增改**：整个替换成最小字典（只留 `CONSOLE_STATIC_DIR` 等）会让 Node 启动即崩 `Assertion failed: ncrypto::CSPRNG(nullptr, 0)`（rc=134），像测试全崩但其实是进程没起来。变异测试驱动里实际踩过（2026-11-04）。
- jsdom 没有 `TouchEvent`：用普通可冒泡 `Event` + `Object.defineProperty(event, 'touches'/'changedTouches', ...)` 就能驱动页面里的手势逻辑（页面只读这两个字段与 `cancelable`）。但「长按抬手后浏览器补发一次 click」只在真实浏览器发生，jsdom 里手动补发 click 测的是兜底守卫，不是浏览器语义；真实触屏行为用 Playwright + CDP `Input.dispatchTouchEvent`（context 开 `has_touch=True`）验证。jsdom 的 label 激活行为尊重 `defaultPrevented`（捕获阶段 `preventDefault` 能拦住 label→radio）。
- **「常驻节点 + hidden 切换」配行内流布局会自己把布局搞坏**（设置页模型步骤表，2026-11-06）：提示 span 与控件平铺在同一个 `td` 里，`hidden` 只负责显隐、不负责不占位——一旦它没被隐藏，长文本就把后面的 select/input 挤到第二行，且换行后的内容从单元格左缘起排，看起来像「错位」。现行形态（默认模型跟随接入点改造后）是：模型留空灰字 `.step-model-hint` 常驻渲染、靠 `:empty { display: none }` 消失，文本更新只改 textContent 不重建节点（重建会抢焦点）。顺带两个表格布局事实：`table-layout: auto` 下列宽由单元格内容决定，长提示会把别的列压到逐字断行（中文可在任意字间断行），要 `width: 1%` + `nowrap` 保列、把 `width: 100%` 给需要吸收剩余空间的列；行内流里的 `select`/`input` 宽度要显式给（`flex: 1 1 14em; min-width: 0`），否则一个长文本邻居就能让它们换行。
- **jsdom：节点未挂载前不要走「容器.querySelector」刷新它**：`updateStepModelHint(row, …)` 在 td 尚未 append 到 row 时调用，`row.querySelector` 找不到目标、提示留空——挂载完成后再调用，或直接持有节点引用赋值（2026-11-06 实测，症状是「函数单跑正确、页面渲染后为空」）。
- **保存时的校验/实测必须作用于「待保存的值」，不能回读数据库**（2026-11-06 补丁）：保存 `llm_endpoints` 时的模型探针曾拿 key 回库里查接入点定义——库里还是旧值，新增接入点必报「接入点不存在」（回归：控制台无法新增任何接入点），改 base_url/api_key_env 时实测的也是旧地址/旧 Key。修法是探针直接接收待保存的定义（`_probe_model(endpoint_definition=…)`），按钮路径才传库中已保存值。
- **打桩位置太高会把「用错配置」藏起来**（同上）：组合断言类测试替换 `test_llm_model` / `_probe_model` 整个函数，探针内部查库这一步从未执行，回归全绿。涉及「实际发什么请求」的验收，只替换最底下的 HTTP 发送层（`post_chat_completion`），断言 URL 与 Authorization 头。
- **jsdom：分区保存成功路径会重渲染页签，保存状态节点被重建且为空**：成功文案写在旧节点上，保存后断言 `models-save-status` 永远超时；成功断言走 `#toast`（「模型配置已保存」），失败分支（422/409 不重渲染）才能断言状态节点（2026-11-06 实测）。
- **设置页「展示字段」在顶层 endpoints 负载，不在分区值里**（2026-11-07）：`configured_api_key_envs` 与 `host_allowed` 挂在 `state.payload.endpoints`（`endpointsPayloadInfo()`），而只读列表、步骤行常常拿着 `savedEndpointsValue()`（`sections.llm_endpoints.value`）渲染——分区值是已保存的原始结构，没有这些字段，第一次实现就把标红写到了分区值上导致 E24/E25 全空。改展示字段相关代码先分清读的是哪一份。
- **「删除某行为」类变异会打在冗余分支上存活**（2026-11-07）：K6 第一版删 `endpointWillBeTested` 里的 `item.isNew ||`，新接入点随即被后面的 `!savedItem` 兜底判 true，变异等价、测试无法击杀；改打保存拦截里 `blocking = true` 那一行（行为真正被观察的赋值点）立即被 E20 击杀。变异存活先查实现里有没有语义冗余的兜底路径，再考虑补测试。另注意同一表达式会在生产请求与探针各出现一份（`llm_beijing_gate` 的 `"response_format": build_response_format(endpoint)` 逐字节相同两处），变异定位串必须带足够上下文。
- **叫「复用」就要真的只有一份定义**（2026-11-07 补丁）：`probe_request_extras` 第一版只让探针取用了构造函数，真实请求里的 `temperature` / `response_format` 仍是另抄的一份——把 `call_beijing_gate` 真实请求的 `response_format` 改成写死的 `json_object`，全部测试照过：S2 只证明了「探针复用 build_response_format」，没证明「探针与真实请求一致」。修复：真实请求也用同一函数（改名 `request_extras`）拼请求体。以后写「复用」类代码，检查被复用的值是否**只有一处字面定义**，别让调用方各自再写一遍。
- **「两边相等」的测试，两边都必须来自真实代码路径**（同上补丁）：探针与真实请求一致性的验收，任何一边写成测试里手工拼的期望 dict，就只会锁住「与测试作者的想象一致」，锁不住两份实现的漂移。做法：真实路径调 adapter 入口函数、探针路径调 `test_llm_model`，各只替换最底下的 `post_chat_completion` 捕获请求体，再互相对照（忽略 model/messages/max_tokens）。另两条同批教训：中间件这类「接线」必须有端到端用例（TestClient + 真实临时 env 文件，函数级测试守不住接线被拆）；「跟随默认」这类解析分支要专门的用例钉住（只看显式选择的变异不会被显式选择的用例抓到）。

- 纯 CSS/布局改动 jsdom 测不到，用 headless Chrome 直接渲染静态夹具核对几何：`chrome --headless=new --window-size=W,H --screenshot=out.png file:///...` 出图，`--dump-dom` 配合页内 `window.onload` 脚本把 `getBoundingClientRect()` 写进 `<pre>` 再解析，能量到「各行第一列左缘是否一致、`table.scrollWidth` 是否超出 `.admin-table-wrap`」。CLI 的 headless Chrome **可以**开 `file://` 夹具（workflow.md §5 那条 `file:` 限制只针对浏览器技能/Playwright）。夹具放临时目录，不要把预览 HTML 提交进仓库。

- **变异要打在「唯一决定行为」的位置**（2026-11-07）：设置页栏目面板「重渲染重置展开态」的变异先打在 `insertColumnsPanel` 的 `panel.hidden` 上，被紧接着的 `syncSourcePanels()` 覆盖而存活；改到 `renderSourcesPanel` 开头重置 `state.expandedSource` 才真正模拟丢状态。变异存活时先确认它是否真的改变了最终状态，再判断是不是测试盲区。
- **前端比较分区值不能直接 `JSON.stringify`：jsonb 会重排对象键**（2026-11-11 实测）：`app_settings.value` 是 jsonb，读回来对象键按「长度 + 字节序」重排——来源别名是 `{aliases, suffixes[, fallback_names]}`，自动排序关键词是 `{高校, 中小学, 市教委}`，旧配置行还可能整个缺 `fallback_names`。即时保存在保存后用 `JSON.stringify` 比对「响应值 vs DOM 解析值」，刚保存成功的值会被判成「还有未保存的修改」，并让每次失焦都重复 PUT、版本号空转（用户看到的就是那句假提示）。比较必须递归排序对象键（`section_editor.js` 的 `canonicalSectionJson`）并补齐缺失的可选字段。jsdom 夹具有同一个陷阱：`respond` 原样回对象会让测试永远比线上宽松，夹具要按真实 jsonb 重排键序（`settings_harness.js` 的 `jsonbOrder`）；重排时用 `Object.create(null)`，普通 `{}` 赋值会把 `__proto__` 这种合法别名键当成原型设置丢掉。

## 已知「等价变异」/ 测试盲区备忘

变异测试里存活过、后来定性为等价或需特殊用例的：
- Pydantic 响应模型字段默认值 None 会补回缺失键——API 层测不出 service 兜底 dict 的缺键（抽屉徽章 M15）。
- `^` 锚定下 `re.match ≡ re.search`（bjrb 合并逻辑 M8）。
- `Number('') === 0`：空筛选值经 Number 归一会被当成 hour=0（细化筛选时被夹具抓出的真缺陷，注意此类归一）。
- 「行为被后续代码掩盖」的变异要在实现里安排可区分观察点（如清空选择提前到 toast 之前、测试包装 showToast 捕获时刻），纯时序轮询抓不住同步代码块内的差异。
- 「一词命中两类归高优先级」类排序优先级变异，普通各归各类用例测不出，需要专门的跨类用例（review_sort R4，别删）。
