import os
from datetime import datetime
from typing import List

import pandas as pd
from sentence_transformers import SentenceTransformer, util


# （可选）代理设置，如无需要可注释
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"


def prompt_file() -> str:
    """获取待处理的 CSV 文件路径。"""
    path = input("请输入要处理的 CSV 文件路径：").strip()
    if not path:
        raise ValueError("❌ 未提供文件路径。")
    if not os.path.exists(path):
        raise FileNotFoundError(f"❌ 文件不存在：{path}")
    return path


def prompt_model() -> str:
    """供用户选择 BGE 模型。"""
    print("\n可选模型：")
    print("1. BAAI/bge-small-zh-v1.5（轻量、速度快）")
    print("2. BAAI/bge-base-zh（均衡）")
    print("3. BAAI/bge-large-zh（最准确）")
    choice = input("请选择模型（输入编号 1-3，默认 3）：").strip()
    mapping = {
        "1": "BAAI/bge-small-zh-v1.5",
        "2": "BAAI/bge-base-zh",
        "3": "BAAI/bge-large-zh",
    }
    return mapping.get(choice, mapping["3"])


def prompt_threshold(default: float = 0.9) -> float:
    """输入相似度阈值。"""
    value = input(f"请输入相似度阈值（0-1，默认 {default}）：").strip()
    if not value:
        return default
    try:
        threshold = float(value)
    except ValueError as exc:
        raise ValueError("❌ 阈值必须是数字。") from exc
    if not 0 <= threshold <= 1:
        raise ValueError("❌ 阈值需在 0 与 1 之间。")
    return threshold


def greedy_grouping(sim_matrix, threshold: float) -> List[List[int]]:
    """基于相似度矩阵的简单贪心聚类。"""
    visited = set()
    groups: List[List[int]] = []
    for i in range(len(sim_matrix)):
        if i in visited:
            continue
        group = [i]
        visited.add(i)
        for j in range(i + 1, len(sim_matrix)):
            if j not in visited and sim_matrix[i][j] >= threshold:
                group.append(j)
                visited.add(j)
        groups.append(group)
    return groups


def main():
    input_file = prompt_file()
    model_name = prompt_model()
    threshold = prompt_threshold()

    print(f"\n🧠 正在加载模型：{model_name} …")
    model = SentenceTransformer(model_name)

    df = pd.read_csv(input_file)
    if "title" not in df.columns:
        raise ValueError("❌ CSV 文件必须包含 'title' 列。")

    df = df.dropna(subset=["title"]).reset_index(drop=True)
    if df.empty:
        raise ValueError("❌ 没有可用的标题数据。")

    titles = df["title"].astype(str).tolist()
    print(f"✅ 共加载 {len(titles)} 条新闻标题。")

    base_name = os.path.splitext(os.path.basename(input_file))[0]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = f"{base_name}_results_{timestamp}"
    os.makedirs(output_dir, exist_ok=True)
    print(f"📁 输出文件将保存到：{output_dir}\n")

    print("🧮 正在计算标题向量相似度矩阵……")
    embeddings = model.encode(titles, convert_to_tensor=True, normalize_embeddings=True)
    sim_matrix = util.cos_sim(embeddings, embeddings).cpu().numpy()

    print(f"🤝 使用阈值 {threshold:.2f} 聚类……")
    groups = greedy_grouping(sim_matrix, threshold)
    group_ids = [-1] * len(df)
    for gid, group in enumerate(groups):
        for idx in group:
            group_ids[idx] = gid
    df["group_id"] = group_ids

    grouped_path = os.path.join(output_dir, "news_grouped.csv")
    md_path = os.path.join(output_dir, "news_groups_report.md")

    df.to_csv(grouped_path, index=False, encoding="utf-8-sig")

    print("📝 正在生成 Markdown 报告……")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# 📰 新闻标题聚类报告\n\n")
        f.write(f"- 输入文件：**{input_file}**\n")
        f.write(f"- 模型：**{model_name}**\n")
        f.write(f"- 相似度阈值：**{threshold:.2f}**\n")
        f.write(f"- 聚类总数：**{len(groups)}**\n")
        f.write(f"- 生成时间：{timestamp}\n\n---\n\n")

        for gid, group in enumerate(groups):
            f.write(f"## 🟩 第 {gid} 组（{len(group)} 条）\n\n")
            for idx in group:
                title = df.loc[idx, "title"]
                f.write(f"- **{title}**\n")
            f.write("\n---\n\n")

    print(f"✅ Markdown 报告已生成：{md_path}")
    print(f"📁 CSV 文件输出路径：\n  - 分组：{grouped_path}\n")


if __name__ == "__main__":
    main()
