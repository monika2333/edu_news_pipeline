# 北京时间与北京号来源可行性侦察

侦察日期：2026-08-31（Asia/Shanghai）。本次仅使用浏览器只读观察、普通 HTTP GET 和一次性本地解析命令；未绕过访问限制，未在仓库中保留试探脚本，也未编写任何生产代码。

## 结论摘要

| 来源 | 列表呈现方式 | 可直接请求的数据入口 | 是否需要 Playwright |
| --- | --- | --- | --- |
| 北京时间账号页 | 初始 HTML 只有账号资料和空列表容器，JavaScript 再加载列表 | `https://record.btime.com/getNews` 返回 JSON | **不需要**。列表 JSON 和服务端渲染的详情页都可由普通 HTTP 请求获取 |
| 现代教育报北京号 | 列表首屏就是服务端渲染 HTML；滚动加载的是 HTML 分页片段 | 列表用栏目页及 `/more_N`；详情另有公开 `.json` | **不需要**。列表、分页和详情均可直接请求 |

前期“两个页面都很可能是必须使用浏览器的 SPA”的判断不成立。北京时间只有列表部分由 JavaScript 填充；北京号连列表本身也是服务端渲染。

## 北京时间

目标页：`https://record.btime.com/show?uid=2874221`

### 1. 服务端渲染还是 JavaScript 渲染

普通 GET 返回 `200`，HTML 中直接包含账号资料（账号 ID `2874221`，页面显示名为“BRTV新闻 社会新闻”），但 `#feed-list-all`、`#feed-list-news` 等列表容器为空。因此账号资料是服务端下发，文章列表由 JavaScript 渲染。

页面底部加载的 `record.js` 把列表组件配置为请求 `https://record.btime.com/getNews`。这意味着获取文章列表需要后台接口，但不需要完整浏览器。

复核更正：初次侦察曾把脚本配置中的 `refresh=0` 当成页面的真实首包参数，因此拿到了 2020 年旧数据，并错误判断账号已经停更。浏览器资源记录显示，信息流组件发送首包前会补齐并改写参数；以下记录以复核后的真实请求为准。

### 2. JSON 接口

已确认接口：

```text
GET https://record.btime.com/getNews
    ?tab=all
    &pageRow=10
    &uid=2874221
    &refresh=1
    &target=v4
    &offset=0
    &refresh_type=1
    &req_count=1
    &page=1
```

浏览器还会附带 jQuery JSONP 的 `callback` 和防缓存时间戳 `_`。实测省略这两个传输层参数、保留上面的业务参数，普通 GET、不带登录态 Cookie 仍返回 `200 application/json`，`code=0`。未观察到鉴权、签名或必需 Cookie；请求带普通 User-Agent 和来源页 Referer 即可成功。

`refresh` 不是文章 `score` 游标。相同账号使用 `refresh=0` 会返回一批 2020 年旧数据，不能代表页面当前列表；页面真实首包使用 `refresh=1`。

首层结构是 `data.data[]`，已观察到的关键字段如下：

| 含义 | JSON 字段 |
| --- | --- |
| 稳定文章标识 | `data.data[].gid`（同一条记录中也有相同的 `dupid`） |
| 标题 | `data.data[].data.title` |
| 链接 | `data.data[].open_url` 或 `data.data[].url` |
| 发布时间 | `data.data[].data.pdate`（Unix 秒）及 `data.data[].data.pdate_str` |
| 来源/账号 | `data.data[].data.source`、`author_uid`、`create_uid` |
| 摘要 | `data.data[].data.summary` |

列表响应不含完整正文。抽查的详情 URL `https://item.btime.com/476gm0np5ta9mgrtu04jqc21tma` 用普通 GET 返回 `200`，标题、描述和页面正文已在 HTML 中，因此正文可从详情页二次请求，不需要浏览器。该样本是视频稿，正文较短，主要由封面和简短说明构成。

### 3. 分页方式

页面使用信息流/滚动加载，不显示传统分页控件。真实首包同时包含 `pageRow=10`、`page=1` 和 `offset=0`，可确认接口暴露的是页码与偏移量参数，而不是响应下发的游标；`refresh=1`、`refresh_type=1` 和 `req_count=1` 是首包刷新状态。

**未能确认滚动加载下一页时 `page`、`offset`、`refresh_type` 和 `req_count` 的精确变化规则。** 响应没有 `next_cursor` 或 `has_more` 字段；初次侦察把末条 `score` 填进 `refresh` 的试探方式是错误的，不能用于判断账号总量或是否还有下一页。本次没有继续枚举猜测参数。

### 4. 能否不用 Playwright

**能，且实现时不应使用 Playwright。** 账号页 HTML 已公开接口地址和参数，列表接口无需登录即可直接返回 JSON；详情页也能普通 GET。浏览器只适合作为初次发现接口的工具，不应成为后续固定运行依赖。

### 5. 详情正文来源

列表 JSON 只有摘要，没有完整正文，需要按 `open_url`/`url` 二次请求详情页。抽查详情页是服务端渲染 HTML，正文位于页面的 SEO/文章内容区域。视频稿可能只有简短文字，后续设计需要接受“正文很短但并非抓取失败”的情况。

### 6. 稳定文章标识

`gid` 是最明确的稳定标识，同时出现在：

- JSON 的 `gid`；
- `open_url` 和 `url` 的最后一个路径段；
- 详情页的 `App.config.p_gid`。

复核时最新一条的样本值为 `43r9knd9fc388q9qfgtr1q0k5i0`。后续 `article_id` 应以 `gid` 为核心，并使用独立的北京时间命名空间；本报告不实现该决定。

### 7. 更新频率与每日条数

复核后的真实首批 10 条覆盖 2026-08-29 12:41 至 2026-08-31 07:07：8 月 29 日有 2 条、8 月 30 日有 7 条、8 月 31 日截至 07:07 有 1 条。浏览器复核时首条标题为“珍稀标本被当成玩具：孩子抓踢，父亲竟还亲手递上！”，页面显示“6小时前”；其 Unix 发布时间 `1788131274` 对应北京时间 `2026-08-31 07:07:54`。

因此，这个 **uid 当前仍然活跃**。首批约 42 小时内共有 10 条，折算约 5.7 条/24 小时；三个涉及的自然日分别有 2 条、7 条和截至早晨的 1 条，可见一天内会多次更新。样本窗口较短，不宜把该数字视为长期固定均值，但足以否定“当前更新频率为零”的旧结论。

## 现代教育报（北京号）

目标页：`https://peking.bjd.com.cn/bjhrootcolumn/system/6142fe79e4b0a8b3e76510d9`

### 1. 服务端渲染还是 JavaScript 渲染

普通 GET 返回 `200`，HTML 中直接包含 10 条文章的标题、详情链接和日期，不需要执行 JavaScript 才能看到列表。因此它是服务端渲染页面，不是必须依赖浏览器的 SPA。

页面内还嵌入了栏目对象 `column`，其中栏目名为“现代教育报”、栏目 code 为 `6142fe79e4b0a8b3e76510d9`。

### 2. JSON 接口

未观察到专门供列表首屏使用的 JSON：首屏直接返回完整 HTML，后续分页也返回 HTML 片段。

详情页同时提供公开 JSON。HTML 内明确给出：

```text
https://peking.bjd.com.cn/content/s6a943dc1e4b03fa51a83960a.json
```

普通 GET、不带登录态 Cookie 返回 `200 application/json`，结构为 `{"code": 0, "data": {...}, "message": "success"}`。`data` 中已确认有：

| 含义 | JSON 字段 |
| --- | --- |
| 文章标识 | `id` |
| 标题 | `title` |
| 完整正文 HTML | `content` |
| 发布时间 | `publishTime` |
| 作者/栏目 | `authors`、`columnName`、`columnId` |
| 页面 URL | `url`、`webPreviewUrl` |
| 站内原始稿件号 | `originalId` |

不需要鉴权、签名或 Cookie。

### 3. 分页方式

页面采用无限滚动，但协议是清晰的数字页码，不是游标：

```text
GET https://peking.bjd.com.cn/bjhrootcolumn/system/6142fe79e4b0a8b3e76510d9/more_2
```

首屏隐藏字段 `pageNo=2`；滚动到底部后请求 `/more_<pageNo>`，把返回的 HTML 片段追加到列表，再将页码加一。实测 `/more_2` 返回另外 10 条 HTML 记录。

### 4. 能否不用 Playwright

**能，且没有使用 Playwright 的必要。** 首屏与 `/more_N` 都能普通 GET，详情正文还有公开 JSON。后续 adapter 可只使用 `requests` 和 HTML/JSON 解析。

### 5. 详情正文来源

列表只含标题、详情 URL、日期和展示信息，不含正文，需要二次请求详情。推荐的事实入口是详情 `.json` 的 `data.content`；对应 HTML 详情页也把完整文章对象嵌入变量 `a`，但 JSON 更稳定、解析成本更低。

### 6. 稳定文章标识

详情路径形如：

```text
/content/s6a943dc1e4b03fa51a83960a.html
```

路径末段（去掉 `.html`）与详情 JSON 的 `data.id` 相同，样本为 `s6a943dc1e4b03fa51a83960a`，是最直接的站内稳定标识。JSON 另有 `originalId=CO6a943af7d5de5a8cf8a79be2`，但它没有出现在公开 URL 中。

### 7. 更新频率与每日条数

首屏 10 条覆盖 2026-08-23 至 2026-08-30；第二页 10 条把范围扩展到 2026-08-15。20 条在 16 个自然日内发布，平均约 1.25 条/日；有稿件的日期通常 1–3 条，样本中的单日最高为 4 条（2026-08-20），中间也有无更新日。最新稿为侦察日前一天发布。

事实观察支持“每天检查数次即可覆盖”的量级；具体调度频率留给后续任务决定。

### 8. 与数字报、头条镜像 URL 的对应关系

已确认三条通路使用不同的 URL/ID 空间：

- 北京号：`peking.bjd.com.cn/content/s<id>.html`，并有北京号内部的 `originalId=CO...`；
- 北京日报数字报：现有 adapter 面向 `bjrbdzb.bjd.com.cn/bjrb/mobile/<year>/<YYYYMMDD>/...`，ID 优先取数字报页面的 `data-newid`，前缀为 `bjrb:`；
- 头条镜像：现有通路以头条 `group_id`/`item_id` 解析出的数字文章 ID 为主，详情入口是 `m.toutiao.com/i<article_id>/info/`。

北京号样本的详情 JSON 没有数字报 URL、头条 URL、头条文章 ID或数字报 `data-newid`。尝试普通 GET 访问同日数字报索引时站点返回 `403`，按约束没有规避。因此，**未能确认同一篇稿件是否实际同时出现在三条通路，也未能建立样本级一一对应关系**。

### 9. 可用于跨通路对应的字段

没有观察到三条通路共享的强标识：

- 北京号 `id` 和 `originalId` 都是其内容系统内部 ID；
- 数字报使用另一套 `data-newid`/页面路径；
- 头条使用自己的数字文章 ID。

北京号 JSON 未提供外部“原文链接”指向数字报或头条。当前能跨页面观察到的只有标题和发布时间，可用于后续做候选匹配，但本次没有同稿样本证明“标题 + 发布时间”足以稳定对应，因此不能把它记录成已确认的 canonical 依据。

## 未能确认的事项

1. 北京时间无限滚动的下一页参数变化规则：首包的 `page=1`、`offset=0` 等参数已经确认，但响应没有明确 `next_cursor`，未能观察到下一页请求，且没有继续猜测参数组合。
2. 北京号同一篇稿件是否也出现在北京日报数字报和现有头条镜像通路：北京号没有外部原文 ID，同日数字报普通请求返回 `403`，且没有已确认的头条同稿样本。
3. “标题 + 发布时间”能否可靠承担三通路匹配：目前只有字段可用性观察，没有同稿验证，不能下结论。

这些未确认项不影响“不需要 Playwright”的结论：两个来源的首批数据和详情均已有可直接请求的入口。
