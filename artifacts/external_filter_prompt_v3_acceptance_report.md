# 四分类正面提示词 v3 验收报告

## 技术结论

**验收结论：不通过。** A 组决策稳定 84/100（上线目标 ≥85），因此生产配置主指标不通过；8 条定点回归通过 5/8。D 组决策稳定 90/100，仅作为低噪诊断证据。

- 调用完整性：计划 640 次，CSV 640 行；状态分布 {'ok': 640}，响应 model {'deepseek/deepseek-v4-flash': 640}
- 提示词版本：internal_positive=`v3`、external_positive=`v3`；internal_negative=`v1`、external_negative=`v1`（负面提示词未调用）
- 静态交叉检查：12 个“同一提示词内跨 ≥3 个 Tier”的关键词；其中 2 个为部分裁定或与验收目标冲突，另发现 1 处前置判定结构矛盾
- 本报告不使用“逐次分数完全一致”作为验收指标；v3 允许同一档位内按理由取不同整数

## A 组达到上线所需的决策稳定性

决策稳定定义：同一文章同一配置的 3 次分数，在该类别阈值下全部给出相同的通过/不通过结论。internal_positive 使用本次验收阈值 30，external_positive 使用 35；30 只用于本报告计算，没有修改生产配置。

| 配置 | 决策稳定 | 备注 |
|---|---:|---|
| A 组 | 84/100 | 生产配置；上线目标 ≥85，不通过 |
| D 组 | 90/100 | 固定 Alibaba FP8、关闭 reasoning 的低噪环境 |

### 决策不稳定文章

#### A 组：16 条

| article_id | 标题 | 类别 | 3 次分数 | 决策 | 档位与条款判断 |
|---|---|---|---|---|---|
| 7575087565024379392 | 深耕古诗文教学 赋能情感育人 | external_positive | 45/45/10 | 过/过/不过 | external Tier 3-A（单校一般活动） ↔ Tier 2-A（局部课程与教学实践）；同一校本教研被交替理解为例行活动或有启发性的教学实践 |
| 7576077820816884262 | 科技日报：从院士增选看中国科技创新新风向 | internal_positive | 5/55/0 | 不过/过/不过 | internal Tier 6-C（院士增选属于科技人才而非教育） ↔ Tier 3-B（因北京大学教授和科研表述误套高校科研成果） |
| 7584376512065929737 | 宜宾江安汉安中学扎实开展教学常规检查工作 | external_positive | 38/15/15 | 过/不过/不过 | external Tier 3-A（单校常规检查） ↔ Tier 2-A/2-C（局部教育实践或一般权威信息）；学校自发稿中的教学管理细节触发了不同判断 |
| 7585425820647277066 | 驼铃渡远，玉帛通衢：丝路胡商与汉唐社会的文化交流 | internal_positive | 10/40/40 | 不过/过/过 | internal Tier 6-C（纯文化与考古论坛） ↔ Tier 4-C（在京文化交流论坛边界题材） |
| 7632134103197811254 | 山西省蒲剧艺术院：蒲韵传京畿 梨园绽芳华 | internal_positive | 20/5/35 | 不过/不过/过 | internal Tier 6-C（戏曲展演与文化传播） ↔ Tier 4-C（在京文化交流边界题材）；20 分是中间的 Tier 5-A |
| 7636001703149486619 | 视频丨把课堂搬进火箭工厂！这个假期近距离追“星”大国重器 | external_positive | 8/50/0 | 不过/过/不过 | external Tier 3-E（科普活动明确排除） ↔ Tier 2-A/2-B（把工业研学游误作教育实践或教育活动） |
| 7648848244943962659 | 外媒关注：AI时代，中国迎来2026年全国高考 | internal_positive | 30/30/25 | 过/过/不过 | internal Tier 5-C 同一条款内部：25-29 不过 ↔ 30-34 通过；验收阈值 30 切穿 Tier 5 的 20-34 区间，并非两个 Tier 条款冲突 |
| 7664113108658487860 | 舞剧《李清照》登陆国家大剧院，研学团队重温宋代风华 | internal_positive | 35/35/25 | 过/过/不过 | internal Tier 5-C（教育相关但无明确北京主体） ↔ Tier 4-A（在京小范围美育研学） |
| 7665280166154781238 | 体育强国建设“十五五”规划发布，设定五项主要指标 | internal_positive | 88/10/12 | 过/不过/不过 | internal Tier 6-C（国家体育规划主体非教育） ↔ Tier 1-C/1-D（因青少年体育、体育教育和人才培养表述误套国家教育政策或市级体教融合） |
| 7671187122530812457 | 北京一所小学门口的“一根棍儿”，怎么就火了？ | internal_positive | 5/45/27 | 不过/过/不过 | internal Tier 6-C/Tier 5-A（城市便民设施） ↔ Tier 4-A（因位于小学校门口而被当作小范围校园安全实践） |
| chinanews:/gn/shipin/2026/03-07/news1048284 | 长安街上看两会丨政府工作报告首提“中小学春秋假”，你有哪些期待？ | external_positive | 10/80/45 | 不过/过/过 | external Tier 3-C（正文只有标题、缺乏实质分析） ↔ Tier 1-B（政府工作报告中的重大教育政策）；45 分是中间的 Tier 2-C |
| jyb:/rmtzgjyb/202606/t20260611_2111490047 | 多地增加高中学位供给——多元布局 特色赋能启新局 | internal_positive | 60/10/62 | 过/不过/过 | internal Tier 6-B（多地/京外报道） ↔ Tier 3-G（全国高中学位供给的教育观察）；正文包含北京案例但主体是全国性教育分析 |
| qianlong:2026/0627/8689051 | 北京公园树下“千足虫”横行，市民该如何防护？专家科普 | internal_positive | 5/35/0 | 不过/过/不过 | internal Tier 6-C（市民生态健康科普） ↔ Tier 4-C（把标题中的“专家科普”误当在京科普活动） |
| tencent:20260310A02U9700 | 姜耀东委员：建立企业“人工智能+”激励机制，为高质量就业破局赋能 | internal_positive | 40/10/10 | 过/不过/不过 | internal Tier 6-C（企业 AI 与就业政策） ↔ Tier 4-C（把企业职业技能培训表述误作边界教育活动） |
| tencent:20260607A02IBY00 | 海南省7.8万余名学生逐梦高考 | external_positive | 45/20/15 | 过/不过/不过 | external Tier 3-D/3-C（高考当天实用事实与低分析信息） ↔ Tier 2-C（一般权威教育信息） |
| tencent:20260710A08E3D00 | 2026年“中国科学院—香港青年实习计划”在京启动 | internal_positive | 42/40/25 | 过/过/不过 | internal Tier 5-C（非学校主体的青年科研实习、无明确对应项） ↔ Tier 4-A（在京小范围活动）；阈值 30 把相邻判断转成不同决策 |

#### D 组：10 条

| article_id | 标题 | 类别 | 3 次分数 | 决策 | 档位与条款判断 |
|---|---|---|---|---|---|
| 7571345639725662763 | 校长把稻田“搬进”教室，让孩子读懂“粒粒皆辛苦” | external_positive | 35/35/25 | 过/过/不过 | external Tier 3-A（单校课堂展示） ↔ Tier 2-A（具有方法细节的局部教学实践） |
| 7576077820816884262 | 科技日报：从院士增选看中国科技创新新风向 | internal_positive | 15/15/75 | 不过/不过/过 | internal Tier 6-C（科技人才报道） ↔ Tier 2-F/3-B（把权威媒体科技分析或北大教授当作北京教育系统总结/高校科研成果） |
| 7599799731375620662 | 努力寻找药物合成的最优解（弘扬科学家精神） | external_positive | 0/75/15 | 不过/过/不过 | external Tier 3-D（科学家个人事迹） ↔ Tier 1-A/1-D（因导师、学生和奖学金内容误套教育评论或系统性教育成果） |
| 7614650800782017060 | “谁拥有年轻人，谁就拥有未来” | external_positive | 0/75/75 | 不过/过/过 | external 前置非教育主体/Tier 3-C（区域人才与就业发展） ↔ Tier 1-B/1-C（把省级青年政策误作重大教育政策或教育治理创新） |
| 7648848244943962659 | 外媒关注：AI时代，中国迎来2026年全国高考 | internal_positive | 75/15/75 | 过/不过/过 | internal Tier 6-B（非北京主体的全国高考外媒综述） ↔ Tier 2-F（因北京考点和权威教育内容误作北京教育系统性报道） |
| 7664113108658487860 | 舞剧《李清照》登陆国家大剧院，研学团队重温宋代风华 | internal_positive | 15/65/15 | 不过/过/不过 | internal Tier 6-B/6-C（京外学校参加商业舞剧研学） ↔ Tier 3-C（误作北京单校美育活动） |
| 7665280166154781238 | 体育强国建设“十五五”规划发布，设定五项主要指标 | internal_positive | 85/15/85 | 过/不过/过 | internal Tier 6-C（体育主管部门的全国体育规划） ↔ Tier 1-C/1-D（因青少年体育和体育教育表述误作重大教育政策或市级体教融合） |
| 7671187122530812457 | 北京一所小学门口的“一根棍儿”，怎么就火了？ | internal_positive | 15/75/15 | 不过/过/不过 | internal Tier 6-C（城市公共空间微改造） ↔ Tier 2-B（因学校门口安全隔离表述误作市级教育治理权威信息） |
| chinadaily:/a/202512/03/WS69301868a310942cc4994cc5 | 2025研究前沿发布暨研讨会在京举行 | internal_positive | 15/65/15 | 不过/过/不过 | internal Tier 6-C（科研趋势报告与科技论坛） ↔ Tier 3-B/3-F（因北京高校参会和科研报告误作高校科研成果或单校论坛） |
| chinanews:/txy/2026/06-12/10638944 | “地域+领域”组团式帮扶 助力毕节打造人才“金钥匙” | internal_positive | 15/75/15 | 不过/过/不过 | internal Tier 6-B（京外毕节职业院校） ↔ Tier 1-F（北京参与的跨区域教育对口帮扶）；地域排除与对口支援例外未稳定裁定 |

### 分数分布与辅助指标

直方图使用 10 分等宽分档；条形只帮助观察形状，精确条数以括号数字为准。每组分母均为 100 篇 × 3 次 = 300 次。

| 分数段 | A 组 | D 组 |
|---|---|---|
| 0-9 | `████████████████████████` 122 | `███████████████████████` 115 |
| 10-19 | `███████████` 56 | `███████████████` 74 |
| 20-29 | `███` 15 | `███` 13 |
| 30-39 | `██` 9 | `█` 5 |
| 40-49 | `█████████` 44 | `` 1 |
| 50-59 | `████` 19 | `` 0 |
| 60-69 | `██` 8 | `█` 6 |
| 70-79 | `█` 5 | `███████████` 58 |
| 80-89 | `████` 18 | `█████` 27 |
| 90-100 | `█` 4 | `` 1 |

| 配置 | 类别 | 平均分 | 逐篇极差中位数 | 0-19 取值数 | 20-39 取值数 | 40-59 取值数 | 60-79 取值数 | 80-100 取值数 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | internal_positive | 22.43 | 5.0 | 5 | 5 | 8 | 5 | 3 |
| A | external_positive | 25.89 | 5.0 | 9 | 3 | 5 | 3 | 5 |
| D | internal_positive | 27.20 | 0.0 | 2 | 3 | 0 | 2 | 2 |
| D | external_positive | 31.17 | 0.0 | 4 | 2 | 1 | 1 | 1 |

## 8 条定点回归

**结果：5/8 通过。** 任一不通过项的 5 次原始输出附在本节表格之后。

| 文章 | 类别 | 期望 | D 组 5 次 | 结果 | 对应规则 |
|---|---|---|---|---|---|
| 市政府常务会议研究国际科技创新中心建设 | internal_positive | 稳定 ≥85 | 85/85/85/85/85 | 通过 | internal Tier 1-G（市级决策部署中的具体教育内容） |
| 河南两部门发文规范学校餐饮具清洗消毒 | external_positive | 稳定 ≥50 | 75/75/75/75/75 | 通过 | external 前置判定第三项（校园专项治理）及 Tier 1-B / Tier 2-A |
| 大理巍山流动科普试点 | external_positive | 稳定 0-19 | 0/0/0/0/0 | 通过 | external 前置非教育主体及 Tier 3-E（科普活动） |
| 重庆科普讲解大赛 | external_positive | 稳定 0-19 | 18/18/18/18/15 | 通过 | external 前置非教育主体及 Tier 3-E（科普赛事） |
| 四川老年健康教育专委会学术会议 | external_positive | 稳定 0-19 | 0/0/0/0/0 | 通过 | external 前置非教育主体及 Tier 3-E（老年健康教育） |
| 首届中国—巴尔干文化交流论坛 | internal_positive | 稳定 35-49 | 75/75/75/85/85 | **不通过** | internal 前置边界题材及 Tier 4-C（文化交流论坛） |
| 300余家国企招聘岗位上线 | internal_positive | 稳定 0-19 | 75/75/20/75/20 | **不通过** | internal Tier 6-D（招聘与招考公告） |
| 2027QS世界大学排名发布 | internal_positive | 稳定 50-69 | 85/75/19/0/15 | **不通过** | internal Tier 3-H（含北京高校的排名、榜单与评估） |

市政府常务会议原文仍未在库中命中；脚本沿用上次方式对[北京日报客户端原文](https://xinwen.bjd.com.cn/content/s6a733642e4b0e45f3fd5aa33.html)执行 HTTP GET，从 `CSS .storyContent 内全部非空 p 段落` 抽取正文。正文字数 1086，1500 字截断：否。

### 未通过项原始输出与规则判断

#### 首届中国—巴尔干文化交流论坛：75/75/75/85/85

判断：未稳定落入 `稳定 35-49`。前置判定要求文化交流论坛直接落入 Tier 4-C，但 Tier 4-C 又规定北京高校实质主办时改按 Tier 3-F；本文由首都师范大学主办，当前文字本身就把它从期望的 35-49 推向 50-69。75/85 进一步表明模型还把“11 国、40 多所高校、教育部和市教委领导致辞”误套到 Tier 2-E，尽管核心议题是文化与区域国别研究。

第 1 次（parsed=75）：

```text
75
```

第 2 次（parsed=75）：

```text
75
```

第 3 次（parsed=75）：

```text
75
```

第 4 次（parsed=85）：

```text
85
```

第 5 次（parsed=85）：

```text
85
```

#### 300余家国企招聘岗位上线：75/75/20/75/20

判断：未稳定落入 `稳定 0-19`。Tier 6-D 对具体岗位招聘公告有明确排除且冲突规则要求先判 Tier 6；75 分说明“教育部联合发布、国家大学生就业服务平台”仍被误套为 Tier 2-B 权威教育治理信息或 Tier 1-C 国家部委政策，排除优先级没有被稳定执行。

第 1 次（parsed=75）：

```text
75
```

第 2 次（parsed=75）：

```text
75
```

第 3 次（parsed=20）：

```text
20
```

第 4 次（parsed=75）：

```text
75
```

第 5 次（parsed=20）：

```text
20
```

#### 2027QS世界大学排名发布：85/75/19/0/15

判断：未稳定落入 `稳定 50-69`。Tier 3-H 和 Tier 6-B 的例外都明确把含北京高校的第三方排名固定在 50-69；0/15/19 表明模型仍误走 Tier 6-B，75/85 则误走 Tier 2-F 或更高档，专门条款没有形成稳定裁定。

第 1 次（parsed=85）：

```text
85
```

第 2 次（parsed=75）：

```text
75
```

第 3 次（parsed=19）：

```text
19
```

第 4 次（parsed=0）：

```text
0
```

第 5 次（parsed=15）：

```text
15
```

## 题材关键词交叉检查

**额外发现一处不依赖关键词计数的结构矛盾：** internal_positive 的【第一步】开头仍写“先做一个二值判断”，紧接着又要求“判定结果分三种，不要只做是/否二选一”。这与 v3 的三分判定目标直接冲突，应删除“二值判断”残留措辞。

扫描对象仅为当前 internal_positive 与 external_positive v3 文件。位置按所在 Tier 条目去重；“跨越档位数”只统计同一提示词内不同 Tier，不把前置判定和冲突裁定算作 Tier。

| 提示词 | 关键词 | 出现位置列表 | 跨越档位数 |
|---|---|---|---:|
| internal_positive | 体育 | Tier 1-D；Tier 3-D；Tier 6-C；第一步：教育实质判定 | 3 |
| internal_positive | 体教融合 | Tier 1-D；Tier 3-D；第一步：教育实质判定 | 2 |
| internal_positive | 老年 | Tier 1-B；Tier 4-C；第一步：教育实质判定 | 2 |
| internal_positive | 职业 | Tier 1-B；Tier 4-C；第一步：教育实质判定 | 2 |
| internal_positive | 科普 | Tier 4-C；Tier 5-C；第一步：教育实质判定 | 2 |
| internal_positive | 论坛 | Tier 2-E；Tier 3-F；Tier 4-C；第一步：教育实质判定 | 3 |
| internal_positive | 文化 | Tier 2-C；Tier 3-C；Tier 4-C；Tier 6-C；第一步：教育实质判定 | 4 |
| internal_positive | 科研 | Tier 1-G；Tier 3-B；Tier 4-C；Tier 5-A | 4 |
| internal_positive | 科技 | Tier 1-E；Tier 1-G；Tier 2-E；Tier 3-B；Tier 3-F；Tier 6-C；第一步：教育实质判定 | 4 |
| internal_positive | 评论 | Tier 3-G | 1 |
| internal_positive | 排名 | Tier 3-H；Tier 6-B | 2 |
| internal_positive | 榜单 | Tier 3-H；Tier 6-B | 2 |
| internal_positive | 培训 | Tier 4-C；第一步：教育实质判定 | 1 |
| internal_positive | 招聘 | 第一步 | 0 |
| internal_positive | 招考 | 第一步 | 0 |
| internal_positive | 人才 | Tier 1-A；Tier 1-D；Tier 1-E；Tier 1-F；Tier 1-G；Tier 2-D；Tier 2-E；Tier 3-A；Tier 3-D；Tier 3-F；Tier 6-C；第一步；第一步：教育实质判定 | 4 |
| internal_positive | 思政 | Tier 2-C；Tier 3-C；Tier 4-C；Tier 5-A；第一步：教育实质判定 | 4 |
| internal_positive | 青少年 | Tier 1-D；Tier 2-C；Tier 3-D；第一步：教育实质判定 | 3 |
| internal_positive | 社区教育 | Tier 4-C；第一步：教育实质判定 | 1 |
| internal_positive | 竞赛 | Tier 3-D | 1 |
| internal_positive | 校园安全 | Tier 1-A；Tier 1-G；Tier 3-A；第一步：教育实质判定 | 2 |
| internal_positive | 招生 | Tier 1-G；Tier 2-D；Tier 2-G；第一步：教育实质判定 | 2 |
| internal_positive | 教师 | Tier 1-F；Tier 5-B；第一步：教育实质判定 | 2 |
| internal_positive | 个人 | Tier 5-B；第一步 | 1 |
| internal_positive | 广告 | Tier 6-A；核心原则 | 1 |
| internal_positive | 艺术 | Tier 2-C；Tier 3-C | 2 |
| internal_positive | 党建 | Tier 2-C；Tier 3-C | 2 |
| internal_positive | 健康 | Tier 1-D；Tier 3-D；Tier 4-C；Tier 5-A；第一步：教育实质判定 | 4 |
| internal_positive | 食品卫生 | — | 0 |
| internal_positive | 治理 | Tier 2-B；Tier 3-G；Tier 5-C；主体优先规则；第一步：教育实质判定 | 3 |
| internal_positive | 创新 | Tier 1-G；Tier 6-C；第一步：教育实质判定 | 2 |
| external_positive | 体育 | Tier 3-E；第一步：教育主体判定 | 1 |
| external_positive | 体教融合 | — | 0 |
| external_positive | 老年 | Tier 3-E；第一步：教育主体判定 | 1 |
| external_positive | 职业 | Tier 1-D；Tier 3-E；第一步：教育主体判定 | 2 |
| external_positive | 科普 | Tier 3-E；第一步：教育主体判定 | 1 |
| external_positive | 论坛 | Tier 2-B；Tier 3-E；第一步：教育主体判定 | 2 |
| external_positive | 文化 | Tier 3-E；第一步：教育主体判定 | 1 |
| external_positive | 科研 | — | 0 |
| external_positive | 科技 | Tier 1-D；第一步：教育主体判定 | 1 |
| external_positive | 评论 | Tier 1-A | 1 |
| external_positive | 排名 | Tier 2-D | 1 |
| external_positive | 榜单 | Tier 2-D | 1 |
| external_positive | 培训 | Tier 1-D；Tier 2-B；Tier 3-E；第一步：教育主体判定 | 3 |
| external_positive | 招聘 | Tier 3-D | 1 |
| external_positive | 招考 | — | 0 |
| external_positive | 人才 | Tier 1-B；Tier 1-D；Tier 2-A；第一步：教育主体判定 | 2 |
| external_positive | 思政 | — | 0 |
| external_positive | 青少年 | — | 0 |
| external_positive | 社区教育 | Tier 3-E；第一步：教育主体判定 | 1 |
| external_positive | 竞赛 | Tier 2-B | 1 |
| external_positive | 校园安全 | Tier 1-B | 1 |
| external_positive | 招生 | Tier 1-B；Tier 3-D；第一步：教育主体判定 | 2 |
| external_positive | 教师 | Tier 1-B；第一步：教育主体判定 | 1 |
| external_positive | 个人 | Tier 3-D | 1 |
| external_positive | 广告 | — | 0 |
| external_positive | 艺术 | — | 0 |
| external_positive | 党建 | — | 0 |
| external_positive | 健康 | Tier 2-A；Tier 3-E；第一步：教育主体判定 | 2 |
| external_positive | 食品卫生 | 第一步：教育主体判定 | 0 |
| external_positive | 治理 | Tier 1-A；Tier 1-B；Tier 1-C；Tier 2-C；Tier 3-A；Tier 3-C；任务；核心原则；第一步：教育主体判定；角色 | 3 |
| external_positive | 创新 | Tier 1-C；核心原则；第一步：教育主体判定 | 1 |

### 跨越 3 个及以上档位的候选冲突点

- **internal_positive / 体育：裁定状态为有。** 前置判定明确将青少年体育和体教融合排除出边界题材；Tier 1-D 与 Tier 3-D 再按市级覆盖和项目规模分档，Tier 6-C 只处理无教育实质的普通体育。（位置：Tier 1-D、Tier 3-D、Tier 6-C、第一步：教育实质判定）
- **internal_positive / 论坛：裁定状态为冲突。** 前置判定要求文化交流论坛直接归 Tier 4-C，但 Tier 4-C 又规定北京高校主办时改按 Tier 3-F。首都师范大学主办的定点文章期望 35-49，与这条例外的 50-69 落点直接冲突；Tier 2-E 还会被国际规模表述误触发。（位置：Tier 2-E、Tier 3-F、Tier 4-C、第一步：教育实质判定）
- **internal_positive / 文化：裁定状态为有。** Tier 4-C 的主体例外把北京学校、高校或教育主管部门实质主办的活动送回 Tier 1/3；冲突裁定再规定排除优先、其余取最高档。（位置：Tier 2-C、Tier 3-C、Tier 4-C、Tier 6-C、第一步：教育实质判定）
- **internal_positive / 科研：裁定状态为有。** Tier 1-G 处理市级决策部署中的高校科研要求，Tier 3-B 处理北京高校作为成果主体的科研突破；Tier 4-C/Tier 5-A 中的“科研”只是说明市教委核心业务边界，不是独立提档条件。（位置：Tier 1-G、Tier 3-B、Tier 4-C、Tier 5-A）
- **internal_positive / 科技：裁定状态为有。** 前置判定和 Tier 6-C 明确规定仅出现科技不构成教育实质；Tier 2-E 要求全国性重大活动，Tier 3-B 要求北京高校是科研成果主体。（位置：Tier 1-E、Tier 1-G、Tier 2-E、Tier 3-B、Tier 3-F、Tier 6-C、第一步：教育实质判定）
- **internal_positive / 人才：裁定状态为部分。** 已明确单独出现人才不触发教育实质，但 Tier 1-E、Tier 2-D/E、Tier 3-D/F 等仍依赖主体与规模判断；取最高档规则存在，跨题材命中时仍需模型先正确识别主体。（位置：Tier 1-A、Tier 1-D、Tier 1-E、Tier 1-F、Tier 1-G、Tier 2-D、Tier 2-E、Tier 3-A、Tier 3-D、Tier 3-F、Tier 6-C、第一步、第一步：教育实质判定）
- **internal_positive / 思政：裁定状态为有。** Tier 2-C 与 Tier 3-C 以区级覆盖和单校范围分档，前置判定将思政教育明确视为教育实质。（位置：Tier 2-C、Tier 3-C、Tier 4-C、Tier 5-A、第一步：教育实质判定）
- **internal_positive / 青少年：裁定状态为有。** 前置判定排除青少年体育的边界归类，Tier 1-D/Tier 3-D 按市级覆盖规模分档，Tier 2-C 另处理区级思政艺术育人。（位置：Tier 1-D、Tier 2-C、Tier 3-D、第一步：教育实质判定）
- **internal_positive / 健康：裁定状态为有。** Tier 1-D/Tier 3-D 的健康指学生和青少年身心健康并按市级覆盖分档；老年健康教育固定 Tier 4-C，普通市民健康固定 Tier 5-A。（位置：Tier 1-D、Tier 3-D、Tier 4-C、Tier 5-A、第一步：教育实质判定）
- **internal_positive / 治理：裁定状态为有。** 主体优先规则和冲突裁定明确市教委权威治理信息优先 Tier 2，普通治理借鉴按具体主体、规模和对应条目取最高档。（位置：Tier 2-B、Tier 3-G、Tier 5-C、主体优先规则、第一步：教育实质判定）
- **external_positive / 培训：裁定状态为有。** 前置判定与 Tier 3-E 明确把职业技能培训排除；Tier 2-B 仅适用于具有明确教育主题和行业价值的专业培训。（位置：Tier 1-D、Tier 2-B、Tier 3-E、第一步：教育主体判定）
- **external_positive / 治理：裁定状态为有。** 前置判定要求治理创新的实施主体或对象必须是教育系统、学校或师生，并明确非教育治理创新不得进入 Tier 1-C。（位置：Tier 1-A、Tier 1-B、Tier 1-C、Tier 2-C、Tier 3-A、Tier 3-C、任务、核心原则、第一步：教育主体判定、角色）

## 样本、参数与可复现性

- 抽样种子：`external-filter-prompt-v3-acceptance-20260809`。从已完成 external-filter 的正面文章中抽样，internal_positive 与 external_positive 各 50 条；原分数 0-19、20-39、40-59、60-79、80-100 每个“类别 × 分档”精确 10 条。实际分层：{('internal', 0): 10, ('internal', 1): 10, ('internal', 2): 10, ('internal', 3): 10, ('internal', 4): 10, ('external', 0): 10, ('external', 1): 10, ('external', 2): 10, ('external', 3): 10, ('external', 4): 10}。
- 样本不沿用此前 30 条：从上一轮 determinism CSV 读取其 article_id 并在 SQL 中全部排除；7 条库内定点回归也包含在该排除集合中，因此与 100 条主样本无重叠。排序使用 `md5(article_id || seed)`，同库快照下可复现。
- A/D 共同参数：生产 `build_prompt`（默认正文 1500 字）、model=`deepseek/deepseek-v4-flash`、temperature=0、timeout=90s、retries=3、concurrency=8，并复用上一轮 `_invoke` 与生产 `parse_external_filter_score`。
- 唯一指定差异：A 不设置 provider，reasoning 使用生产值 `{'enabled': True, 'exclude': True}`；D 设置 provider.only=[`alibaba/fp8`]、allow_fallbacks=false、reasoning.effort=none。去除实际 prompt 后 payload skeleton 数量={'A': 1, 'D': 1}；每篇 A/D 的 prompt SHA 与 common payload SHA 均相同。
- 实际路由：A={'Alibaba': 278, 'Baidu': 15, 'Cloudflare': 2, 'SiliconFlow': 1, 'Mancer 2': 1, 'StreamLake': 1, 'Venice': 1, 'GMICloud': 1}；D={'Alibaba': 300}。
- CSV：`C:/Monica_program/edu_news_pipeline/artifacts/external_filter_prompt_v3_acceptance_calls.csv`，字段顺序与前两轮 `CSV_FIELDS` 完全一致；所有逻辑槽位保留 status/error，不静默丢弃。

## 限制与下一步

- 这是一次分层随机样本上的重复调用实验，描述当前模型与当前路由的稳定性，不证明未来供应商路由或模型版本变化后仍保持同一水平。
- 原分数分层只用于扩大覆盖面，不代表生产流量的自然分布，因此直方图和平均分不应解释为线上总体分布。
- 若 A 组未达 85/100，或定点回归仍失败，应优先修订报告列出的具体冲突条款，再用相同种子复测；本任务未修改提示词、阈值或生产配置。

生成时间：2026-08-09T20:10:26.882477+08:00
