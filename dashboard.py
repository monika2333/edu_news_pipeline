from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List

import streamlit as st

from src.console.services import manual_filter

PAGE_SIZE = 30
DEFAULT_EXPORT_PATH = "outputs/manual_filter_export.txt"


def _init_state() -> None:
    st.session_state.setdefault("page", 1)
    st.session_state.setdefault("edits", {})  # article_id -> {summary, score, notes}
    st.session_state.setdefault("selected", set())  # approved IDs
    st.session_state.setdefault("actor", "")


def _render_sidebar() -> None:
    counts = manual_filter.status_counts()
    st.sidebar.header("状态汇总")
    cols = st.sidebar.columns(4)
    cols[0].metric("待处理", counts.get("pending", 0))
    cols[1].metric("已通过", counts.get("approved", 0))
    cols[2].metric("已丢弃", counts.get("discarded", 0))
    cols[3].metric("已导出", counts.get("exported", 0))
    st.sidebar.markdown("---")
    actor = st.sidebar.text_input("操作者（可选）", value=st.session_state.get("actor", ""))
    st.session_state["actor"] = actor.strip()
    if st.sidebar.button("刷新数据"):
        st.experimental_rerun()


def _persist_edit(article_id: str, summary: str, score: float | None, notes: str) -> None:
    edits: Dict[str, Dict[str, str]] = st.session_state.get("edits", {})
    edits[article_id] = {"summary": summary, "score": score, "notes": notes}
    st.session_state["edits"] = edits


def _render_candidates(page_data: Dict[str, any]) -> None:
    items: List[Dict[str, any]] = page_data["items"]
    total = page_data["total"]
    page = st.session_state["page"]
    st.caption(f"共 {total} 条，当前第 {page} 页")

    form = st.form("manual_filter_form")
    approved_ids: List[str] = []
    discarded_ids: List[str] = []

    for item in items:
        aid = str(item.get("article_id") or "")
        default_edit = st.session_state["edits"].get(aid, {})
        keep_default = aid in st.session_state["selected"]
        col1, col2 = form.columns([3, 2])
        with col1:
            keep = st.checkbox("保留", value=keep_default, key=f"keep-{aid}")
            title = item.get("title") or "(无标题)"
            st.markdown(f"**{title}**")
            st.markdown(
                f"来源：{item.get('source') or '-'} | 分数：{item.get('score') or '-'} | 情感：{item.get('sentiment_label') or '-'} | 京内：{item.get('is_beijing_related')}"
            )
            summary_key = f"summary-{aid}"
            summary_val = default_edit.get("summary") or item.get("summary") or ""
            summary = st.text_area("摘要", summary_val, key=summary_key, height=160)
        with col2:
            score_key = f"score-{aid}"
            base_score = default_edit.get("score") or item.get("manual_score") or item.get("score")
            score = st.number_input("手工分数（可选）", value=float(base_score) if base_score is not None else 0.0, key=score_key)
            notes_key = f"notes-{aid}"
            notes_val = default_edit.get("notes") or item.get("manual_notes") or ""
            notes = st.text_area("备注", notes_val, key=notes_key, height=80)

        _persist_edit(aid, summary, score, notes)
        if keep:
            approved_ids.append(aid)
        else:
            discarded_ids.append(aid)

    submitted = form.form_submit_button("提交当前页决策")
    if submitted:
        edits = st.session_state["edits"]
        actor = st.session_state.get("actor") or None
        result = manual_filter.bulk_decide(
            approved_ids=approved_ids, discarded_ids=discarded_ids, edits=edits, actor=actor
        )
        st.session_state["selected"] = set(approved_ids)
        st.success(f"已更新：通过 {result.get('approved', 0)}，丢弃 {result.get('discarded', 0)}")
        st.experimental_rerun()

    col_prev, col_next = st.columns(2)
    if col_prev.button("上一页", disabled=page <= 1):
        st.session_state["page"] = max(1, page - 1)
        st.experimental_rerun()
    if col_next.button("下一页", disabled=(page * PAGE_SIZE) >= total):
        st.session_state["page"] = page + 1
        st.experimental_rerun()


def _render_export() -> None:
    st.markdown("---")
    st.header("导出")
    default_tag = datetime.now().strftime("%Y-%m-%d")
    report_tag = st.text_input("Report Tag", value=default_tag)
    output_path = st.text_input("输出路径", value=DEFAULT_EXPORT_PATH)
    if st.button("生成并导出"):
        result = manual_filter.export_batch(report_tag=report_tag.strip(), output_path=output_path)
        st.success(f"生成 {result.get('count', 0)} 条，输出：{result.get('output_path')}")
        st.json(result.get("category_counts", {}))


def main() -> None:
    st.set_page_config(page_title="筛选控制台", layout="wide")
    _init_state()
    _render_sidebar()
    st.title("人工筛选控制台")

    page = st.session_state["page"]
    page_data = manual_filter.list_candidates(limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    if not page_data.get("items"):
        st.info("暂无待处理记录。")
    else:
        _render_candidates(page_data)

    _render_export()


if __name__ == "__main__":
    main()
