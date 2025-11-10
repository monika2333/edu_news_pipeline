# dup_news1108 功能集成计划

## 目标
- 在 `export` worker 中实现新闻标题聚类，替换 `dup_news1108` 手工脚本。
- 聚类输出满足四个分类桶（【京内正面】【京内负面】【京外正面】【京外负面】）分别聚类、按 `external_importance_score` 进行组内/组间排序。
- 最终删除 `dup_news1108/` 目录及其示例产物。

## 执行步骤
1. **抽取聚类能力**
   - 新建模块（建议 `src/services/title_cluster.py`），封装加载 `BAAI/bge-large-zh` 模型、编码标题、计算相似度矩阵、贪心聚类等逻辑，直接复用 `dup_news1108/dup_news_bge.py` 的核心实现。
   - 实现单一入口函数（如 `cluster_titles(titles: List[str], threshold: float = 0.9)`），返回聚类结果（簇 -> 索引列表）。
   - 采用懒加载模型 + 全局缓存，避免多次运行 export worker 时重复初始化。

2. **引入依赖**
   - 在 `requirements.txt` 补充 `sentence-transformers`（随带 `torch` 依赖），确保环境能加载 BGE 模型。
   - 可在 README/docs 简要说明该依赖及需要联网下载模型。

3. **接入 export worker**
   - 在 `src/workers/export_brief.py` 中、对四个桶进行排序前，调用聚类模块：
     - 对每个桶单独聚类。
     - 组内：按 `external_importance_score`（缺失时视为 -inf）降序，再按 `score` 兜底。
     - 组间：取每簇最高 `external_importance_score` 作为排序键，降序排列（缺值簇排后）。
   - 输出格式保持原有类别标题，簇与簇之间插入一行 `---` 分隔，不再额外添加其他说明。

4. **记录与历史**
   - `export_payload` 顺序应与最终文本一致，便于追溯。
   - 如聚类模块或模型加载失败，提供清晰错误日志，或回退到原始顺序（必要时再讨论）。

5. **删除旧脚本**
   - 在功能验证完成后，删除整个 `dup_news1108/` 目录（脚本与示例输出）。

6. **验证**
   - 运行 `export` worker（可在测试数据库或本地样本上）确认：
     - 四类输出文本按新排序展示。
     - `external_importance_score` 缺失、单元素簇、空桶等边界情况处理正常。
   - 视情况添加简单单元测试，覆盖聚类排序逻辑。

## 备注
- 按需求固定模型与阈值，无需额外配置开关；若后续性能受限再考虑抽象为可配置项。
- 保持代理设置由外部环境控制，不在代码中硬编码。
