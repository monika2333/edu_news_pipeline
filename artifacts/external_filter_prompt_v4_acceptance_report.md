# 四分类正面提示词 v4 验收报告

## 技术结论

**主验收结论：不通过。** v4 的 A 组决策稳定性为 82/100；低于 84/100，v4 结构性改动未奏效。D 组为 84/100，仅作低噪诊断。11 条定点回归通过 3/11，作为逐题材风险记录，不擅自改变题目规定的 A 组主判定规则。

- 调用完整性：计划 655 次，CSV 655 行；状态分布 {'ok': 655}；响应 model {'deepseek/deepseek-v4-flash': 655}
- 提示词版本：internal_positive=`v4`、external_positive=`v4`；internal_negative=`v1`、external_negative=`v1`（负面提示词未调用）
- 样本来源：直接读取 `artifacts/external_filter_prompt_v3_acceptance_calls.csv` 中 A 组的 100 个唯一 article_id；internal/external 各 50 条，原分数五个 20 分档各 10 条/类别（实际分层 {('external', 0): 10, ('external', 1): 10, ('external', 2): 10, ('external', 3): 10, ('external', 4): 10, ('internal', 0): 10, ('internal', 1): 10, ('internal', 2): 10, ('internal', 3): 10, ('internal', 4): 10}）；记录原抽样种子 `external-filter-prompt-v3-acceptance-20260809`，没有重新抽样
- 本报告只以过/不过决策稳定性验收；不使用“分数完全一致”或“整档跳条数”作为通过条件

## 第一部分：决策稳定性

同一文章同一配置的 3 次分数若在阈值下给出同一个过/不过结论，即为决策稳定。internal_positive 阈值为 30、external_positive 为 35；两者只用于本报告计算，未修改生产配置。

| 版本 | A 组决策稳定 | D 组决策稳定 |
|---|---:|---:|
| v3（从上轮 CSV 复算） | 84/100 | 90/100 |
| v4 | **82/100** | 84/100 |

按指定三档规则：低于 84/100，v4 结构性改动未奏效。

### 决策不稳定文章

#### A 组：18 条

| article_id | 标题 | 类别 | 3 次分数 | 决策 | 档位与条款判断 |
|---|---|---|---|---|---|
| 7575087565024379392 | 深耕古诗文教学 赋能情感育人 | external_positive | 45/20/45 | 过/不过/过 | external Tier 2-A（局部课程教学实践） ↔ Tier 3-C（把单校教研视为低借鉴教育资讯） |
| 7576077820816884262 | 科技日报：从院士增选看中国科技创新新风向 | internal_positive | 35/25/65 | 过/不过/过 | internal Tier 3-B（误把院士增选及北大教授当成北京高校科研成果） ↔ Tier 5-C（科技人才报道仅顺带涉及高校） |
| 7584376512065929737 | 宜宾江安汉安中学扎实开展教学常规检查工作 | external_positive | 45/20/20 | 过/不过/不过 | external Tier 2-A/2-C（局部教学管理实践或一般权威信息） ↔ Tier 3-A/3-C（单校常规检查、借鉴价值低） |
| 7605519670547857954 | “京字号”年宵花新品种扮靓京城 | internal_positive | 30/22/25 | 过/不过/不过 | internal Tier 5-C 同一条款内部跨阈值（北林大花卉成果未达到 Tier 3-B 的重大科研标准） |
| 7628162448122331686 | 2026清华大学校园马拉松举行 3000余名师生校友共同奔跑 | internal_positive | 40/40/25 | 过/过/不过 | internal Tier 4-A（单校校园体育活动） ↔ Tier 5-C（无政策或推广价值的普通校内赛事） |
| 7632134103197811254 | 山西省蒲剧艺术院：蒲韵传京畿 梨园绽芳华 | internal_positive | 30/25/38 | 过/不过/过 | internal Tier 4-C（在京文化交流边界活动） ↔ Tier 5-C（京外艺术院团的普通展演交流） |
| 7634356249621987867 | 不写论文也能获学位：又一批高校首位工程博士亮相 | internal_positive | 55/27/65 | 过/不过/过 | internal Tier 3-G（专业学位培养改革观察） ↔ Tier 5-C（京外高校个案、报送价值有限） |
| 7636001703149486619 | 视频丨把课堂搬进火箭工厂！这个假期近距离追“星”大国重器 | external_positive | 15/47/25 | 不过/过/不过 | external Tier 2-A/2-B（把工业研学游当作教育实践或交流活动） ↔ Tier 3-E/3-C（京外科普文旅、教育并非主体） |
| 7648848244943962659 | 外媒关注：AI时代，中国迎来2026年全国高考 | internal_positive | 27/27/30 | 不过/不过/过 | internal Tier 5-C 同一条款内部跨阈值（全国高考外媒综述虽含北京考点，但无明确北京教育工作价值） |
| 7665280166154781238 | 体育强国建设“十五五”规划发布，设定五项主要指标 | internal_positive | 62/10/88 | 过/不过/过 | internal Tier 1-C/1-D（误把国家体育规划当成教育顶层设计或北京市级体教融合） ↔ Tier 6-C（体育政策主体并非教育） |
| 7671187122530812457 | 北京一所小学门口的“一根棍儿”，怎么就火了？ | internal_positive | 30/25/25 | 过/不过/不过 | internal Tier 5-C 同一条款内部跨阈值（学校门口城市微改造）并与 Tier 4-A（小范围校园相关活动）相邻 |
| chinadaily:/a/202512/03/WS69301868a310942cc4994cc5 | 2025研究前沿发布暨研讨会在京举行 | internal_positive | 30/30/27 | 过/过/不过 | internal Tier 5-C 同一条款内部跨阈值（科研趋势论坛无北京高校成果主体） |
| jyb:/rmtsy1240/zt/jyyzpzt/202603/t20260310_2111452618 | AI时代，重申教育的核心价值 | internal_positive | 60/55/27 | 过/过/不过 | internal Tier 3-G（AI 时代教育评论与观察） ↔ Tier 5-C（正文近乎只有嘉宾名单、实质内容不足） |
| jyb:/rmtzgjyb/202606/t20260611_2111490047 | 多地增加高中学位供给——多元布局 特色赋能启新局 | internal_positive | 12/27/60 | 不过/不过/过 | internal Tier 3-G（全国高中学位供给的教育观察） ↔ Tier 6-B/5-C（京外为主或仅属低价值教育议题） |
| jyb:/talents/202604/t20260421_2111469764 | 北京：怀柔区释放“雁栖十条”政策效能，打造青年人才创新创业生态区 | internal_positive | 30/30/28 | 过/过/不过 | internal Tier 5-C 同一条款内部跨阈值（区域人才就业创业政策仅顺带出现高校） |
| jyb:/talents/202605/t20260520_2111480859 | 北京：怀柔区“十五五”时期人才发展规划发布 | internal_positive | 25/40/25 | 不过/过/不过 | internal Tier 4-C（误把人才规划中的培训/教育资源视作边界教育题材） ↔ Tier 5-C（人才政策仅顺带涉及教育） |
| tencent:20260604A09MJP00 | 甘肃省第二届职业技能大赛在兰州开幕 75个竞赛项目点亮“技能照亮前程” | external_positive | 5/50/20 | 不过/过/不过 | external Tier 1-C/1-D（误把技能人才制度或趋势当成教育治理创新/教育报告） ↔ Tier 3-E（京外职业技能竞赛） |
| tencent:20260607A02IBY00 | 海南省7.8万余名学生逐梦高考 | external_positive | 20/20/45 | 不过/不过/过 | external Tier 2-C（一般权威教育信息） ↔ Tier 3-C/3-D（高考当天事实或实用资讯） |

#### D 组：16 条

| article_id | 标题 | 类别 | 3 次分数 | 决策 | 档位与条款判断 |
|---|---|---|---|---|---|
| 7571345639725662763 | 校长把稻田“搬进”教室，让孩子读懂“粒粒皆辛苦” | external_positive | 45/45/25 | 过/过/不过 | external Tier 2-A（有方法细节的劳动教育课堂） ↔ Tier 3-A/3-C（单校课堂展示、借鉴价值低） |
| 7578798004317078056 | 大国五年丨立德树人，夯实民族复兴之基 | external_positive | 15/75/75 | 不过/过/过 | external Tier 1-A/1-D（全国教育深度总结或系统趋势） ↔ Tier 3-C（被当成空泛宣传） |
| 7584732182552379919 | 中国首例二维金属入选《物理世界》“2025年十大突破” | internal_positive | 75/20/15 | 过/不过/不过 | internal Tier 2-F/3-B（误把中科院成果榜单当北京教育系统报道或高校科研成果） ↔ Tier 6-C（无北京高校教育主体） |
| 7614650800782017060 | “谁拥有年轻人，谁就拥有未来” | external_positive | 72/75/0 | 过/过/不过 | external Tier 1-B/1-C（误把区域青年人才政策当重大教育政策或教育治理创新） ↔ Tier 3-C（就业人才领域、教育非主体） |
| 7636001703149486619 | 视频丨把课堂搬进火箭工厂！这个假期近距离追“星”大国重器 | external_positive | 34/34/65 | 不过/不过/过 | external Tier 2-A/2-B（工业研学游被当作教育实践或活动） ↔ Tier 3-E/3-C（科普文旅、教育非主体） |
| 7648848244943962659 | 外媒关注：AI时代，中国迎来2026年全国高考 | internal_positive | 15/65/15 | 不过/过/不过 | internal Tier 3-G（全国高考教育观察） ↔ Tier 6-B/6-C（外媒综述、北京教育并非主体） |
| 7658936010105815567 | 打造中国国际青少年数字体育赛事新范式！WIRC智能机器人大赛启动 | internal_positive | 75/75/15 | 过/过/不过 | internal Tier 2-E（全国性青少年科技人才赛事） ↔ Tier 6-C（社会机构数字体育赛事、无学校教育主体） |
| 7670053228096061971 | 暑期活动丨重温2026暑期安全训练营的精彩瞬间，致谢同行伙伴 | internal_positive | 15/45/45 | 不过/过/过 | internal Tier 4-C（在京科普与青少年安全边界活动） ↔ Tier 6-C（科普活动不被视为具体教育对象） |
| 7671187122530812457 | 北京一所小学门口的“一根棍儿”，怎么就火了？ | internal_positive | 20/15/75 | 不过/不过/过 | internal Tier 2-B（误作教育治理或校园安全权威措施） ↔ Tier 6-C（城市便民设施、学校只是地点） |
| chinadaily:/a/202512/03/WS69301868a310942cc4994cc5 | 2025研究前沿发布暨研讨会在京举行 | internal_positive | 19/65/65 | 不过/过/过 | internal Tier 3-B/3-F（误作高校科研成果或教育论坛） ↔ Tier 6-C（中科院科技趋势报告、教育非主体） |
| chinanews:/sh/2026/01-26/10559256 | 走边关学非遗探自然 云南爱心托管班助力少年儿童多彩成长 | external_positive | 65/34/75 | 过/不过/过 | external Tier 1-C/2-A（全省托管机制或局部教育实践） ↔ Tier 3-A/3-C（一般青少年活动、教育借鉴不足） |
| chinanews:/sh/2026/04-09/10600861 | 甘肃高台：百年校园“搭台”让古戏唱“新声” | external_positive | 75/45/34 | 过/过/不过 | external Tier 1-C/2-A（县域传统文化进校园机制或局部育人实践） ↔ Tier 3-A（单校戏曲社团活动） |
| jyb:/rmtzgjyb/202511/t20251124_2111416917 | “孩子们，笑一笑，比个‘耶’” | external_positive | 72/34/72 | 过/不过/过 | external Tier 1-C（区域全员关爱导师机制） ↔ Tier 3-D（教师个人事迹硬性排除） |
| jyb:/rmtzgjyb/202606/t20260611_2111490047 | 多地增加高中学位供给——多元布局 特色赋能启新局 | internal_positive | 65/20/65 | 过/不过/过 | internal Tier 3-G（全国高中学位供给教育观察） ↔ Tier 5-C（京外为主、北京案例有限） |
| qianlong:2026/0424/8659072 | 科创扩围 聚链兴产 | internal_positive | 75/15/75 | 过/不过/过 | internal Tier 2-A（误把京津冀科创产业协同当教育资源协同） ↔ Tier 6-C（产业科技报道无教育对象） |
| tencent:20260604A09MJP00 | 甘肃省第二届职业技能大赛在兰州开幕 75个竞赛项目点亮“技能照亮前程” | external_positive | 34/72/72 | 不过/过/过 | external Tier 1-C/1-D（技能人才机制被误作教育治理或教育趋势） ↔ Tier 3-E（京外职业技能竞赛） |

### 辅助分布（不作为通过条件）

| 配置 | 类别 | 平均分 | 逐篇极差中位数 | 各档位分数取值个数（0-19 / 20-39 / 40-59 / 60-79 / 80-100） |
|---|---|---:|---:|---|
| A | internal_positive | 23.59 | 6.5 | 4 / 8 / 5 / 4 / 3 |
| A | external_positive | 29.80 | 10.0 | 5 / 4 / 5 / 7 / 4 |
| D | internal_positive | 30.58 | 0.0 | 4 / 2 / 1 / 2 / 1 |
| D | external_positive | 32.23 | 0.0 | 3 / 3 / 1 / 3 / 1 |

| 分数段 | A 组 | D 组 |
|---|---:|---:|
| 0-9 | 78 | 96 |
| 10-19 | 66 | 71 |
| 20-29 | 56 | 10 |
| 30-39 | 15 | 24 |
| 40-49 | 23 | 5 |
| 50-59 | 20 | 0 |
| 60-69 | 12 | 14 |
| 70-79 | 10 | 64 |
| 80-89 | 17 | 16 |
| 90-100 | 3 | 0 |

## 第二部分：11 条定点回归

**结果：3/11 通过。** 三条与主样本重叠的文章仍额外执行了 5 次全新 D 调用，在 CSV 中用 repetition=4-8 标识；没有复用主实验的 3 次结果。

| 文章 | 类别 | 期望 | D 组 5 次 | 结果 | 对应规则 |
|---|---|---|---|---|---|
| 市政府常务会议研究国际科技创新中心建设 | internal_positive | 稳定 ≥85 | 85/85/95/85/95 | 通过 | internal Tier 1-G（市级决策部署中的具体教育内容） |
| 河南两部门发文规范学校餐饮具清洗消毒 | external_positive | 稳定 ≥50 | 75/75/75/75/75 | 通过 | external Tier 1-B（省级学校专项治理） |
| 大理巍山流动科普试点 | external_positive | 稳定 0-19 | 34/0/0/34/34 | **不通过** | external Tier 3-E（京外科普活动） |
| 重庆科普讲解大赛 | external_positive | 稳定 0-19 | 34/34/25/34/25 | **不通过** | external Tier 3-E（京外科普赛事） |
| 四川老年健康教育专委会学术会议 | external_positive | 稳定 0-19 | 34/25/34/0/34 | **不通过** | external Tier 3-E（京外老年健康教育） |
| 首届中国—巴尔干文化交流论坛（在京举行） | internal_positive | 稳定 35-49 | 75/75/85/75/85 | **不通过** | internal Tier 4-C（在京文化交流论坛） |
| 300余家国企招聘岗位上线 | internal_positive | 稳定 0-19 | 75/15/75/15/15 | **不通过** | internal Tier 6-D（招聘公告硬性排除） |
| 2027QS世界大学排名发布 | internal_positive | 稳定 50-69 | 15/15/15/15/15 | **不通过** | internal Tier 3-H（含北京高校的第三方排名） |
| 政府工作报告首提“中小学春秋假” | external_positive | 稳定 ≥35 | 0/34/34/0/0 | **不通过** | external Tier 1-B / Tier 2-C（政府教育政策或权威教育信息） |
| 多地增加高中学位供给 | internal_positive | 稳定 ≥35 | 65/65/65/65/65 | 通过 | internal Tier 3-G / Tier 5-C（教育观察或教育兜底） |
| 深耕古诗文教学 赋能情感育人 | external_positive | 稳定 ≥35 | 45/34/34/25/0 | **不通过** | external Tier 2-A（局部课程与教学实践） |

市政府常务会议原文未从库中取用；脚本沿用前轮方式对[北京日报客户端原文](https://xinwen.bjd.com.cn/content/s6a733642e4b0e45f3fd5aa33.html)执行 HTTP GET，从 `CSS .storyContent 内全部非空 p 段落` 抽取正文。正文字数 1086，1500 字截断：否。

### v4 重点修复对象未通过项：原始输出与规则判断

#### 首届中国—巴尔干文化交流论坛（在京举行）：75/75/85/75/85

判断：未稳定落入 `稳定 35-49`。Tier 2-E 明确排除文化交流议题，Tier 4-C 是专门落点；但高校主办例外仍可能触发 Tier 3-F。

第 1 次（CSV repetition=1，parsed=75）：

```text
75
```

第 2 次（CSV repetition=2，parsed=75）：

```text
75
```

第 3 次（CSV repetition=3，parsed=85）：

```text
85
```

第 4 次（CSV repetition=4，parsed=75）：

```text
75
```

第 5 次（CSV repetition=5，parsed=85）：

```text
85
```

#### 300余家国企招聘岗位上线：75/15/75/15/15

判断：未稳定落入 `稳定 0-19`。Tier 6-D 是体裁型硬排除，命中后不再与教育部或就业平台主体竞争。

第 1 次（CSV repetition=1，parsed=75）：

```text
75
```

第 2 次（CSV repetition=2，parsed=15）：

```text
15
```

第 3 次（CSV repetition=3，parsed=75）：

```text
75
```

第 4 次（CSV repetition=4，parsed=15）：

```text
15
```

第 5 次（CSV repetition=5，parsed=15）：

```text
15
```

#### 2027QS世界大学排名发布：15/15/15/15/15

判断：未稳定落入 `稳定 50-69`。Tier 3-H 是第三方高校排名的专门条款，且文中含北京高校时 Tier 6-B 不成立。

第 1 次（CSV repetition=1，parsed=15）：

```text
15
```

第 2 次（CSV repetition=2，parsed=15）：

```text
15
```

第 3 次（CSV repetition=3，parsed=15）：

```text
15
```

第 4 次（CSV repetition=4，parsed=15）：

```text
15
```

第 5 次（CSV repetition=5，parsed=15）：

```text
15
```

#### 政府工作报告首提“中小学春秋假”：0/34/34/0/0

判断：未稳定落入 `稳定 ≥35`。明确教育政策标题不应被 Tier 3-C/F 随机归零；正文信息较短时至少应稳定越过 35。

第 1 次（CSV repetition=4，parsed=0）：

```text
0
```

第 2 次（CSV repetition=5，parsed=34）：

```text
34
```

第 3 次（CSV repetition=6，parsed=34）：

```text
34
```

第 4 次（CSV repetition=7，parsed=0）：

```text
0
```

第 5 次（CSV repetition=8，parsed=0）：

```text
0
```

#### 深耕古诗文教学 赋能情感育人：45/34/34/25/0

判断：未稳定落入 `稳定 ≥35`。正文明确以学校教学为对象，Tier 3-F 不成立；Tier 2-A 是局部教学实践的直接落点。

第 1 次（CSV repetition=4，parsed=45）：

```text
45
```

第 2 次（CSV repetition=5，parsed=34）：

```text
34
```

第 3 次（CSV repetition=6，parsed=34）：

```text
34
```

第 4 次（CSV repetition=7，parsed=25）：

```text
25
```

第 5 次（CSV repetition=8，parsed=0）：

```text
0
```

## 第三部分：关键词跨档位静态检查

扫描对象为当前两份 v4 正面提示词。位置按 Tier 条目去重；跨越档位数统计不同 Tier 数，不把裁定规则或小节标题计入 Tier。

| 提示词 | 关键词 | 出现位置列表 | 跨越档位数 |
|---|---|---|---:|
| internal_positive | 体育 | Tier 1-D；Tier 3-D；Tier 4-C | 3 |
| internal_positive | 体教融合 | Tier 1-D；Tier 3-D；Tier 4-C | 3 |
| internal_positive | 老年 | Tier 1-B；Tier 4-C | 2 |
| internal_positive | 职业 | Tier 1-B；Tier 4-C | 2 |
| internal_positive | 科普 | Tier 4-C；Tier 5-C | 2 |
| internal_positive | 论坛 | Tier 2-E；Tier 3-F；Tier 4-C | 3 |
| internal_positive | 文化 | Tier 2-C；Tier 2-E；Tier 3-C；Tier 4-C | 3 |
| internal_positive | 科研 | Tier 1-G；Tier 3-B；Tier 4-C；Tier 5-A | 4 |
| internal_positive | 科技 | Tier 1-E；Tier 1-G；Tier 2-E；Tier 3-B；Tier 3-F | 3 |
| internal_positive | 评论 | Tier 3-G | 1 |
| internal_positive | 排名 | Tier 3-H | 1 |
| internal_positive | 榜单 | Tier 3-H | 1 |
| internal_positive | 培训 | Tier 4-C | 1 |
| internal_positive | 招聘 | 档位裁定规则；硬性排除项 | 0 |
| internal_positive | 招考 | 档位裁定规则；硬性排除项 | 0 |
| internal_positive | 人才 | Tier 1-A；Tier 1-D；Tier 1-E；Tier 1-F；Tier 1-G；Tier 2-D；Tier 2-E；Tier 3-A；Tier 3-D；Tier 3-F；硬性排除项 | 3 |
| internal_positive | 思政 | Tier 2-C；Tier 3-C；Tier 4-C；Tier 5-A | 4 |
| internal_positive | 青少年 | Tier 1-D；Tier 2-C；Tier 3-D；Tier 4-C | 4 |
| internal_positive | 社区教育 | Tier 4-C | 1 |
| internal_positive | 竞赛 | Tier 3-D | 1 |
| internal_positive | 校园安全 | Tier 1-A；Tier 1-G；Tier 3-A | 2 |
| internal_positive | 招生 | Tier 1-G；Tier 2-D；Tier 2-G；普通排除项 | 2 |
| internal_positive | 教师 | Tier 1-F；Tier 5-B；普通排除项 | 2 |
| internal_positive | 个人 | Tier 5-B；硬性排除项 | 1 |
| internal_positive | 广告 | 核心原则；档位裁定规则；硬性排除项 | 0 |
| internal_positive | 艺术 | Tier 2-C；Tier 2-E；Tier 3-C | 2 |
| internal_positive | 党建 | Tier 2-C；Tier 3-C | 2 |
| internal_positive | 健康 | Tier 1-D；Tier 3-D；Tier 4-C；Tier 5-A | 4 |
| internal_positive | 食品卫生 | — | 0 |
| internal_positive | 治理 | Tier 2-B；Tier 3-G；Tier 5-C；主体优先规则 | 3 |
| internal_positive | 创新 | Tier 1-G | 1 |
| external_positive | 体育 | 普通排除项 | 0 |
| external_positive | 体教融合 | — | 0 |
| external_positive | 老年 | 普通排除项 | 0 |
| external_positive | 职业 | Tier 1-D；普通排除项 | 1 |
| external_positive | 科普 | 普通排除项 | 0 |
| external_positive | 论坛 | Tier 2-B；普通排除项 | 1 |
| external_positive | 文化 | 普通排除项 | 0 |
| external_positive | 科研 | — | 0 |
| external_positive | 科技 | Tier 1-D；普通排除项 | 1 |
| external_positive | 评论 | Tier 1-A | 1 |
| external_positive | 排名 | Tier 2-D | 1 |
| external_positive | 榜单 | Tier 2-D | 1 |
| external_positive | 培训 | Tier 1-D；Tier 2-B；普通排除项 | 2 |
| external_positive | 招聘 | 档位裁定规则；硬性排除项 | 0 |
| external_positive | 招考 | — | 0 |
| external_positive | 人才 | Tier 1-B；Tier 1-D；Tier 2-A | 2 |
| external_positive | 思政 | — | 0 |
| external_positive | 青少年 | — | 0 |
| external_positive | 社区教育 | 普通排除项 | 0 |
| external_positive | 竞赛 | Tier 2-B | 1 |
| external_positive | 校园安全 | Tier 1-B | 1 |
| external_positive | 招生 | Tier 1-B；档位裁定规则；硬性排除项 | 1 |
| external_positive | 教师 | Tier 1-B；硬性排除项 | 1 |
| external_positive | 个人 | 档位裁定规则；硬性排除项 | 0 |
| external_positive | 广告 | — | 0 |
| external_positive | 艺术 | — | 0 |
| external_positive | 党建 | — | 0 |
| external_positive | 健康 | Tier 2-A；普通排除项 | 1 |
| external_positive | 食品卫生 | Tier 1-B | 1 |
| external_positive | 治理 | Tier 1-A；Tier 1-B；Tier 1-C；Tier 2-C；任务；普通排除项；核心原则；角色 | 2 |
| external_positive | 创新 | Tier 1-C；核心原则 | 1 |

### “文化”四处条目的互斥性判断

- **Tier 2-C vs Tier 3-C：主体和覆盖面大体可分，但并非逻辑互斥。** 前者要求北京区级党委、政府或相关部门组织且面向全区，后者要求北京单所学校或单个单位组织；区级部门与学校共同主办时可同时命中，但“所有命中取最高档”会裁到 Tier 2-C。
- **Tier 2-E vs Tier 4-C：议题条件明确互斥。** Tier 2-E 明写文化交流、艺术等议题即使规模大也不适用，并指向 Tier 4-C。
- **Tier 3-C vs Tier 4-C：仍有文字重叠。** Tier 4-C 规定由北京高校或中小学实质主办时转到 Tier 3-C/3-F，能处理学校主体；但 Tier 3-C 的“单个单位”可包含社会机构，而 Tier 4-C 也覆盖社会机构，非学校的单一社会机构文化活动可能同时命中。
- **落点缺口：有兜底但不够精确。** 非区级主办、非单校主办、也非论坛或明确社会机构主办的多校文化活动，四个专门条目均可能不完全贴合；可由 Tier 4-A 或最终 Tier 5-C 接住，因此不会无档可归，但落点取决于模型如何理解活动规模。

结论：四处条件不是严格互斥；现有最高档规则、Tier 4-C 学校主体例外和 Tier 5-C 兜底可完成裁定，但“单个单位/社会机构”是残余候选冲突点。

### 跨越 3 个及以上档位的候选冲突点

- **internal_positive / 体育：裁定状态为有。** Tier 1-D 与 Tier 3-D 以市级主办、跨区覆盖和系统机制区分；Tier 4-C 明确将青少年体育送回这两项。（位置：Tier 1-D、Tier 3-D、Tier 4-C）
- **internal_positive / 体教融合：裁定状态为有。** Tier 1-D/Tier 3-D 按市级覆盖与具体实践分层，Tier 4-C 明确不适用于青少年体教融合。（位置：Tier 1-D、Tier 3-D、Tier 4-C）
- **internal_positive / 论坛：裁定状态为有。** Tier 2-E 要求全国性且议题为教育、科技或人才；Tier 3-F 是单校论坛；文化交流论坛由 Tier 2-E 排除并落 Tier 4-C。（位置：Tier 2-E、Tier 3-F、Tier 4-C）
- **internal_positive / 文化：裁定状态为部分。** Tier 2-E 与文化论坛主题互斥，Tier 2-C/3-C 主要按区级主体与单校主体区分，Tier 4-C 有学校主体例外；但 Tier 3-C 的“单个单位”与 Tier 4-C 的“社会机构”存在重叠。（位置：Tier 2-C、Tier 2-E、Tier 3-C、Tier 4-C）
- **internal_positive / 科研：裁定状态为有。** Tier 1-G 是市级决策要求，Tier 3-B 要求北京高校为成果主体；Tier 4-C/5-A 中只是核心业务边界说明，不是独立提档条件。（位置：Tier 1-G、Tier 3-B、Tier 4-C、Tier 5-A）
- **internal_positive / 科技：裁定状态为部分。** Tier 1-E/G、Tier 2-E、Tier 3-B/F 均有主体或活动类型约束，但全国科技论坛与单校科研论坛仍依赖模型正确识别主办层级。（位置：Tier 1-E、Tier 1-G、Tier 2-E、Tier 3-B、Tier 3-F）
- **internal_positive / 人才：裁定状态为部分。** 各条分别要求领导教育活动、市级品牌、教育支援、专业布局或论坛主体；Tier 1-G 明确仅有宽泛人才表述不适用，但跨条款仍依赖主体识别。（位置：Tier 1-A、Tier 1-D、Tier 1-E、Tier 1-F、Tier 1-G、Tier 2-D、Tier 2-E、Tier 3-A、Tier 3-D、Tier 3-F、硬性排除项）
- **internal_positive / 思政：裁定状态为有。** Tier 2-C 与 Tier 3-C 以区级主体/覆盖和单校主体区分；Tier 4-C 中只作为市教委核心业务边界举例。（位置：Tier 2-C、Tier 3-C、Tier 4-C、Tier 5-A）
- **internal_positive / 青少年：裁定状态为有。** Tier 1-D/Tier 3-D 按市级覆盖与具体机制区分，Tier 2-C 另要求区级思政艺术活动，Tier 4-C 明确排除体教融合。（位置：Tier 1-D、Tier 2-C、Tier 3-D、Tier 4-C）
- **internal_positive / 健康：裁定状态为有。** 学生身心健康按 Tier 1-D/3-D 的主体规模裁定，老年健康教育落 Tier 4-C，普通市民健康落 Tier 5-A。（位置：Tier 1-D、Tier 3-D、Tier 4-C、Tier 5-A）
- **internal_positive / 治理：裁定状态为有。** 市教委权威治理发布由主体优先规则固定 Tier 2-B，评论观察和低价值教育议题分别落 Tier 3-G/5-C。（位置：Tier 2-B、Tier 3-G、Tier 5-C、主体优先规则）

## 样本、参数与可复现性

- 样本：从 v3 明细 CSV 恢复同一批 100 个 article_id，并只读回取当前库中候选字段；v3 原抽样种子 `external-filter-prompt-v3-acceptance-20260809`。脚本校验 v3 A/D 各恰有 3 次并复算为 84/100、90/100，否则拒绝运行。
- A/D 共同参数：生产 `build_prompt`（默认正文 1500 字）、model=`deepseek/deepseek-v4-flash`、temperature=0、timeout=90s、retries=3、concurrency=8；复用既有 `_invoke`、响应记录与生产 `parse_external_filter_score`。
- 唯一指定差异：A 不固定 provider，reasoning 维持生产值 `{'enabled': True, 'exclude': True}`；D 设置 `provider.only=["alibaba/fp8"]`、`allow_fallbacks=false`、`reasoning.effort=none`。去除 prompt 后每组 payload skeleton 均只有一个：{'A': 1, 'D': 1}；脚本逐篇断言 A/D 的 prompt SHA 和 common payload SHA 相同。
- 实际路由：A={'Alibaba': 271, 'Parasail': 11, 'SiliconFlow': 2, 'Baidu': 7, 'GMICloud': 1, 'Cloudflare': 2, 'CoreWeave': 2, 'Novita': 1, 'StreamLake': 3}；D={'Alibaba': 300}。
- CSV：`C:/Monica_program/edu_news_pipeline/artifacts/external_filter_prompt_v4_acceptance_calls.csv`，字段与前三轮 `CSV_FIELDS` 完全一致；655 个计划槽位全部保留 status/error，不静默丢弃。
- 本任务未写数据库、未修改提示词/VERSIONS/adapter/worker/config/阈值，也未注册计划任务。

## 限制

- 这是固定分层样本上的重复调用实验，衡量当前模型与当前路由，不保证供应商或模型版本变化后的表现。
- 样本按 v3 的原分数分层，不代表自然生产流量；平均分和直方图只用于版本间辅助观察。
- 对不稳定文章的 Tier 判断是基于标题、正文与 v4 条款的人工归因，不是模型返回的解释（模型按要求只输出整数）。

生成时间：2026-08-09T23:40:35.936118+08:00
