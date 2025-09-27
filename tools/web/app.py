import sys
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "tools"
DEFAULT_DB_PATH = REPO_ROOT / "articles.sqlite3"
OUTPUT_DIR = REPO_ROOT / "outputs"
DEFAULT_OUTPUT_BASENAME = "high_correlation_summaries.txt"
PYTHON_EXEC = Path(sys.executable)


def run_command(args: List[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            args,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
    except Exception as exc:
        return -1, f"Failed to execute {' '.join(args)}: {exc}"
    output = "".join([result.stdout or "", result.stderr or ""])
    return result.returncode, output


def fetch_metrics(db_path: Path) -> dict:
    metrics = {
        "articles_total": None,
        "articles_with_content": None,
        "summaries_total": None,
        "summaries_missing_content": None,
        "summaries_missing_source_llm": None,
        "summaries_missing_summary": None,
    }
    if not db_path.exists():
        return metrics
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(1), SUM(CASE WHEN content IS NOT NULL AND LENGTH(TRIM(content))>0 THEN 1 ELSE 0 END) FROM articles")
        row = cur.fetchone()
        if row:
            metrics["articles_total"] = row[0]
            metrics["articles_with_content"] = row[1] or 0
        cur.execute("SELECT COUNT(1) FROM news_summaries")
        metrics["summaries_total"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(1) FROM news_summaries WHERE content IS NULL OR LENGTH(TRIM(content))=0")
        metrics["summaries_missing_content"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(1) FROM news_summaries WHERE source_LLM IS NULL OR LENGTH(TRIM(source_LLM))=0")
        metrics["summaries_missing_source_llm"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(1) FROM news_summaries WHERE summary IS NULL OR LENGTH(TRIM(summary))=0")
        metrics["summaries_missing_summary"] = cur.fetchone()[0]
    finally:
        conn.close()
    return metrics


def fetch_export_history(db_path: Path) -> List[tuple]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT report_tag,
                   COUNT(*) AS total,
                   MIN(exported_at) AS first_time,
                   MAX(exported_at) AS last_time
            FROM export_history
            GROUP BY report_tag
            ORDER BY last_time DESC
            """
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return rows


def list_outputs(output_dir: Path) -> List[Path]:
    if not output_dir.exists():
        return []
    return sorted(output_dir.glob("high_correlation_summaries*.txt"))


def render_metrics_section(db_path: Path) -> None:
    st.subheader("数据库指标")
    if not db_path.exists():
        st.warning(f"数据库不存在：{db_path}")
        return
    metrics = fetch_metrics(db_path)
    cols = st.columns(3)
    cols[0].metric("文章总数", metrics["articles_total"])
    cols[1].metric("有正文文章", metrics["articles_with_content"])
    if metrics["summaries_total"] is not None:
        cols[2].metric("摘要总数", metrics["summaries_total"])
    cols = st.columns(3)
    cols[0].metric("正文缺失摘要", metrics["summaries_missing_content"])
    cols[1].metric("缺少source_LLM", metrics["summaries_missing_source_llm"])
    cols[2].metric("缺少summary", metrics["summaries_missing_summary"])


def render_history_section(db_path: Path) -> None:
    st.subheader("导出历史")
    rows = fetch_export_history(db_path)
    if not rows:
        st.info("暂无 export_history 记录。")
        return
    st.table(
        {
            "report_tag": [r[0] for r in rows],
            "导出篇数": [r[1] for r in rows],
            "首次导出": [r[2] for r in rows],
            "最近导出": [r[3] for r in rows],
        }
    )


def render_outputs_section(output_dir: Path) -> None:
    st.subheader("输出文件")
    files = list_outputs(output_dir)
    if not files:
        st.info("当前 outputs 目录暂无导出文件。")
        return
    for file_path in files:
        try:
            file_data = file_path.read_text(encoding="utf-8")
        except Exception:
            file_data = file_path.read_text(encoding="utf-8", errors="ignore")
        preview = file_data[:500]
        with st.expander(f"{file_path.name} ({file_path.stat().st_size} bytes)"):
            st.code(preview or "<空文件>")
            st.download_button(
                label="下载",
                data=file_data,
                file_name=file_path.name,
                mime="text/plain",
            )


def render_pipeline_controls(db_path: Path) -> None:
    st.subheader("流水线执行")
    default_tag_suffix = st.selectbox("标签后缀", ["ZM", "ZB", "自定义"], index=0)
    today_str = datetime.now().strftime("%Y-%m-%d")
    if default_tag_suffix == "自定义":
        custom_suffix = st.text_input("自定义标签", value=f"{today_str}-TAG")
        report_tag = custom_suffix.strip()
    else:
        report_tag = f"{today_str}-{default_tag_suffix}".strip()
    col1, col2 = st.columns(2)
    with col1:
        cleanup_apply = st.checkbox("清理阶段执行删除", value=True)
        skip_exported = st.checkbox("导出时跳过历史记录", value=True)
    with col2:
        record_history = st.checkbox("导出后记录历史", value=True)
        dry_run_cleanup = not cleanup_apply
    st.caption("若需重跑历史内容，可取消“导出时跳过历史记录”。")

    if st.button("运行完整流水线", use_container_width=True):
        cmd = [
            str(PYTHON_EXEC),
            str(REPO_ROOT / "run_pipeline.py"),
            "--db",
            str(db_path),
            "--keywords",
            str(REPO_ROOT / "education_keywords.txt"),
            "--export-report-tag",
            report_tag,
        ]
        if dry_run_cleanup:
            cmd.append("--no-cleanup-apply")
        if not skip_exported:
            cmd.append("--no-export-skip-exported")
        if not record_history:
            cmd.append("--no-export-record-history")
        with st.spinner("流水线执行中..."):
            code, output = run_command(cmd)
        if code == 0:
            st.success("流水线执行完成")
        else:
            st.error(f"执行失败，退出码 {code}")

    st.markdown("---")
    st.caption("按需执行分阶段：")

    if st.button("1 导入 AuthorFetch", use_container_width=True):
        cmd = [
            str(PYTHON_EXEC),
            str(TOOLS_DIR / "import_authorfetch_to_sqlite.py"),
            "--src",
            str(REPO_ROOT / "AuthorFetch"),
            "--db",
            str(db_path),
        ]
        with st.spinner("导入中..."):
            code, output = run_command(cmd)
        with st.expander("查看输出：1 导入 AuthorFetch", expanded=False):
            st.code(output or "无输出")
        st.success("导入完成") if code == 0 else st.error(f"导入失败 ({code})")

    if st.button("2 补全文本", use_container_width=True):
        cmd = [
            str(PYTHON_EXEC),
            str(TOOLS_DIR / "fill_missing_content.py"),
            "--db",
            str(db_path),
        ]
        with st.spinner("补全文本中..."):
            code, output = run_command(cmd)
        with st.expander("查看输出：2 补全文本", expanded=False):
            st.code(output or "无输出")
        st.success("补全完成") if code == 0 else st.error(f"补全失败 ({code})")

    if st.button("3 生成摘要", use_container_width=True):
        cmd = [
            str(PYTHON_EXEC),
            str(TOOLS_DIR / "summarize_news.py"),
            "--db",
            str(db_path),
            "--keywords",
            str(REPO_ROOT / "education_keywords.txt"),
        ]
        with st.spinner("生成摘要中..."):
            code, output = run_command(cmd)
        with st.expander("查看输出：3 生成摘要", expanded=False):
            st.code(output or "无输出")
        st.success("摘要完成") if code == 0 else st.error(f"摘要失败 ({code})")

    if st.button("4 计算高相关度", use_container_width=True):
        cmd = [
            str(PYTHON_EXEC),
            str(TOOLS_DIR / "score_correlation_fulltext.py"),
            "--db",
            str(db_path),
        ]
        with st.spinner("计算中..."):
            code, output = run_command(cmd)
        with st.expander("查看输出：4 计算高相关度", expanded=False):
            st.code(output or "无输出")
        st.success("计算完成") if code == 0 else st.error(f"计算失败 ({code})")

    if st.button("5 导出摘要", use_container_width=True):
        output_base = REPO_ROOT / "outputs" / DEFAULT_OUTPUT_BASENAME
        cmd = [
            str(PYTHON_EXEC),
            str(TOOLS_DIR / "export_high_correlation.py"),
            "--db",
            str(db_path),
            "--output",
            str(output_base),
            "--report-tag",
            report_tag,
        ]
        if not skip_exported:
            cmd.append("--no-skip-exported")
        if not record_history:
            cmd.append("--no-record-history")
        with st.spinner("导出中..."):
            code, output = run_command(cmd)
        with st.expander("查看输出：5 导出摘要", expanded=False):
            st.code(output or "无输出")
        st.success("导出完成") if code == 0 else st.error(f"导出失败 ({code})")


def main() -> None:
    st.set_page_config(page_title="新闻流水线控制台", layout="wide")
    st.title("每日新闻数据处理控制台")

    db_path = Path(st.sidebar.text_input("数据库路径", value=str(DEFAULT_DB_PATH)))
    st.sidebar.write(f"工作目录: {REPO_ROOT}")

    render_metrics_section(db_path)
    render_history_section(db_path)
    render_outputs_section(OUTPUT_DIR)
    render_pipeline_controls(db_path)


if __name__ == "__main__":
    main()
