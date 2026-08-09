# 四分类正面提示词 v2 验收报告

## 技术结论

**验收结论：不通过。** D-v2 完全一致文章 14/30（目标 ≥24），整档跳文章 3/30（目标 ≤2）；指定市政府常务会议 5 次得分为 85/85/85/85/85，硬性回归通过。

- 生产配置直接结论：A-v2 整档跳 2/30，A-v1 为 1/30；没有下降。因此就整档跳主问题而言，v2 在开启 reasoning、自由路由的生产配置下没有提供改善证据。
- 调用完整性：计划 305 次，CSV 305 行；状态分布：{'ok': 305}
- 提示词版本：internal_positive=`v2`，external_positive=`v2`；external_negative=`v1`、internal_negative=`v1`（本次未调用负面提示词）
- 路由核对：{'Alibaba': 227, 'GMICloud': 2, 'Baidu': 8, 'CoreWeave': 28, 'StreamLake': 19, 'Parasail': 8, 'Morph': 2, 'DigitalOcean': 3, 'Venice': 1, 'Novita': 3, 'Cloudflare': 2, 'SiliconFlow': 2}；响应 model：{'deepseek/deepseek-v4-flash': 305}
- D 组仅作为固定供应商、关闭 reasoning 的低噪声诊断环境，不作为生产配置建议

## v2 是否消除了拒绝档与 Tier 1/2 之间的整档跳

主指标定义为同一篇 5 次中同时出现 ≤20 与 ≥50；它比普通极差更直接对应本次规则冲突问题。

| 指标 | v1 实测 | v2 本次 | 目标 | 结果 |
|---|---:|---:|---:|---|
| 5 次完全一致的文章数 | 21/30 | 14/30 | ≥24/30 | 不通过 |
| 整档跳文章数（同时出现 ≤20 和 ≥50） | 5/30 | 3/30 | ≤2/30 | 不通过 |
| 平均分 | 39.87 | 23.21 | 记录 | — |
| 分数取值个数 | 10 | 16 | 记录 | — |

在 D 组中，v2 把整档跳从 5 条降到 3 条，但仍高于上限 2；同时完全一致文章从 21 条降到 14 条，说明冲突范围缩小但一般重复性反而变差。

### v1 五条整档跳文章在 v2 下的结果

| article_id | 标题 | 类别 | v1 五次 | v2 五次 | v2 是否仍整档跳 |
|---|---|---|---|---|---|
| 7581807128730534452 | 中国技术为攻克艾滋病开新路，国产“抗艾”疫苗已在路上 | internal_positive | 15/15/0/20/75 | 0/0/0/0/0 | 否 |
| 7661199314286346815 | 甘肃人社：多举措助力就业创业梦想拔节生长 | external_positive | 75/75/75/15/15 | 0/19/0/19/15 | 否 |
| 7671161332351156799 | 新中国成立初期中国哲学社会科学的发展 | internal_positive | 15/75/15/15/15 | 0/0/19/0/0 | 否 |
| gmw:2026-06/24/content_38845066 | 美院毕业展别只看到“销冠” | internal_positive | 75/75/65/50/20 | 0/0/0/0/0 | 否 |
| tencent:20260618A04KRO00 | 2027QS世界大学排名发布：港大11名，北大13名，清华14名，浙大47名 | internal_positive | 75/75/20/75/20 | 20/20/20/20/20 | 否 |

### v2 仍然整档跳的文章

| article_id | 标题 | 类别 | 五次分数 | 摆动档位 | 对应条款判断 |
|---|---|---|---|---|---|
| 7659340936928887336 | 大理巍山国家级流动科普试点工作圆满收官 | external_positive | 22/42/15/15/72 | Tier 3 ↔ Tier 1 | 【第一步：教育主体判定】/Tier 3-C（科普公共服务仍是非教育主体） ↔ Tier 1-C（被“四位一体、可复制推广、种子教师培训”误判为教育治理创新） |
| chinanews:/gn/2026/05-25/10627892 | 首届中国-巴尔干文化交流论坛在京举行 | internal_positive | 15/75/19/19/65 | Tier 6 ↔ Tier 2 | Tier 6-C（论坛核心议题是巴尔干文化与区域研究，而非教育） ↔ Tier 3-F（北京单所高校主办的人文国际交流论坛）；75 分还说明 Tier 2-E 被规模表述误触发 |
| jyb:/rmtxwwyyq/jyxx1306/202607/t20260706_2111499339 | 四川老年学学会老年健康教育专委会第三次学术会议在达州召开 | external_positive | 18/18/75/20/18 | Tier 3 ↔ Tier 1 | 【第一步：教育主体判定】/Tier 3-C（核心对象被判为老年医疗健康） ↔ Tier 1-C（课程体系、实践基地、产教融合与人才培养被判为教育治理创新） |

## 指定市政府常务会议的 Tier 1-G 硬性回归

**结论：通过。** 5 次分数：`85/85/85/85/85`；期望全部落在 85-100。

- 数据来源：[北京日报客户端原文](https://xinwen.bjd.com.cn/content/s6a733642e4b0e45f3fd5aa33.html) 在 news_summaries、primary_articles、raw_articles 均未命中；脚本对该页面执行 HTTP GET，从 `CSS .storyContent 内全部非空 p 段落` 抽取正文
- 正文字数：1086 字；1500 字截断：否
- 候选构造：category=`internal_positive`，summary 为空字符串，source=`北京日报客户端`，is_beijing_related=True；随后调用生产 `build_prompt`

## v2 的阈值通过率位移

仅记录位移，不据此调整阈值。分母为对应类别 15 篇 × 5 次 = 75 次调用。

| 类别 | 当前阈值 | v1 通过 | v1 通过率 | v2 通过 | v2 通过率 | 变化 |
|---|---:|---:|---:|---:|---:|---:|
| internal_positive | 20 | 43/75 | 57.3% | 23/75 | 30.7% | -26.7 pp |
| external_positive | 35 | 33/75 | 44.0% | 17/75 | 22.7% | -21.3 pp |

## A 组生产配置下的 v2 回归

A 组保持生产现状：不设置 provider、reasoning 按生产环境保持开启、temperature=0。D 组仅用于低噪声诊断，不作为生产配置建议。

| 配置 | 提示词 | 5 次完全一致 | 整档跳条数 |
|---|---|---:|---:|
| A | v1 | 3/30 | 1/30 |
| A | v2 | 6/30 | 2/30 |
| D | v1 | 21/30 | 5/30 |
| D | v2 | 14/30 | 3/30 |

**直接回答：否。** A 组整档跳从 1/30 变为 2/30，v2 在生产实际配置下没有降低整档跳。

A-v2 实际 provider 分布：{'Alibaba': 72, 'GMICloud': 2, 'Baidu': 8, 'CoreWeave': 28, 'StreamLake': 19, 'Parasail': 8, 'Morph': 2, 'DigitalOcean': 3, 'Venice': 1, 'Novita': 3, 'Cloudflare': 2, 'SiliconFlow': 2}；payload reasoning={'enabled': True, 'exclude': True}。

### A-v2 整档跳明细

| article_id | 标题 | 类别 | A-v1 五次 | A-v2 五次 |
|---|---|---|---|---|
| 7659340936928887336 | 大理巍山国家级流动科普试点工作圆满收官 | external_positive | 75/70/75/50/55 | 55/12/10/75/45 |
| chinanews:/gn/2026/05-10/10618741 | 以赛兴科普 第十三届重庆科普讲解大赛收官 | external_positive | 45/30/45/45/35 | 15/55/45/45/5 |

## 样本与唯一变量验证

- 同批样本：直接从上次 CSV 的 D 组提取 30 个 article_id，再以 `WHERE article_id = ANY(...)` 只读回取；随机种子记录为 `external-filter-determinism-v1-20260809`。没有重新按当前全库抽样，避免新增数据改变样本。
- 样本一致性：旧 A、D 两组各 150 个唯一调用槽位；本次 A、D 主样本也各为相同 30 个 article_id × 5；D 组 30/30 的 prompt SHA 与 v1 不同（实测 30/30），符合提示词版本变更。
- Payload：复用 `scripts/one_time_external_filter_determinism.py` 的 `_payload_for_config` 与 `_invoke`；payload 断言结果=True，去掉实际 prompt 后各配置 skeleton hash 个数={'A': 1, 'D': 1}。
- D 固定参数：model=`deepseek/deepseek-v4-flash`（v1 响应 model=['deepseek/deepseek-v4-flash']），temperature=0，provider.only=[`alibaba/fp8`]，allow_fallbacks=false，reasoning.effort=none。A 与 D 的预定差异仅为 A 不固定 provider 且 reasoning 使用生产值 `{'enabled': True, 'exclude': True}`；两组共同 timeout=90s、retries=3、concurrency=8。与上次报告运行参数匹配=True。
- v1→v2 比较的唯一变化是从当前 VERSIONS 加载的正面提示词内容；A-v1/A-v2 使用同一 A payload 构造函数，D-v1/D-v2 使用同一 D payload 构造函数。A 与 D 之间的 provider/reasoning 差异是实验定义，不被当作提示词效果。
- 解析与记录：复用上一脚本 `_invoke`，内部仍调用生产 `parse_external_filter_score`；CSV 字段顺序复用上一脚本 `CSV_FIELDS`。
- CSV：`C:/Monica_program/edu_news_pipeline/artifacts/external_filter_prompt_v2_acceptance_calls.csv`；所有计划槽位均保留 status/error，不静默丢弃。

## 限制与后续动作

- A 组给出自由路由、开启 reasoning 的生产现状证据；provider 分布只代表本次 150 次调用时的实际路由结果。
- D 组是固定 Alibaba FP8、关闭 reasoning 的诊断环境，不是生产配置建议。
- 通过率位移只是 30 条分层样本上的调用级描述，不用于重校阈值。
- 若主指标或硬性回归未通过，应回到仍冲突的具体条款修订提示词；本任务未修改任何提示词或阈值。

生成时间：2026-08-09T19:16:45.110300+08:00
