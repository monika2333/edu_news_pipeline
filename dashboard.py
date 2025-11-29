from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

import streamlit as st

from src.console.services import manual_filter

PAGE_SIZE = 30
DEFAULT_EXPORT_PATH = "outputs/manual_filter_export.txt"


def _init_state() -> None:
    st.session_state.setdefault("page_select", 1)
    st.session_state.setdefault("page_review_sel", 1)
    st.session_state.setdefault("page_review_backup", 1)
    st.session_state.setdefault("page_discard", 1)
    st.session_state.setdefault("actor", "")


def _render_sidebar() -> None:
    counts = manual_filter.status_counts()
    st.sidebar.header("状态汇总")
    cols = st.sidebar.columns(5)
    cols[0].metric("待处理", counts.get("pending", 0))
    cols[1].metric("采纳", counts.get("selected", 0))
    cols[2].metric("备选", counts.get("backup", 0))
    cols[3].metric("放弃", counts.get("discarded", 0))
    cols[4].metric("已导出", counts.get("exported", 0))
    st.sidebar.markdown("---")
    actor = st.sidebar.text_input("操作者（可选）", value=st.session_state.get("actor", ""))
    st.session_state["actor"] = actor.strip()
    if st.sidebar.button("刷新数据", key="btn-refresh"):
        st.experimental_rerun()


def _display_meta(item: Dict[str, Any]) -> None:
    st.markdown(
        f"来源：{item.get('source') or '-'} | 分数：{item.get('score') or '-'} | 情感：{item.get('sentiment_label') or '-'} | 京内：{item.get('is_beijing_related')}"
    )
    bonus = item.get("bonus_keywords") or []
    if bonus:
        st.caption("Bonus keywords: " + ", ".join(bonus))


def _render_select_tab() -> None:
    page = st.session_state["page_select"]
    page_data = manual_filter.list_candidates(limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    st.caption(f"待处理 {page_data['total']} 条，第 {page} 页")
    form = st.form("select_form")
    selected_ids: List[str] = []
    backup_ids: List[str] = []
    discarded_ids: List[str] = []
    for item in page_data["items"]:
        aid = str(item.get("article_id") or "")
        url = item.get("url")
        link = f" · [原文]({url})" if url else ""
        form.markdown(f"### {item.get('title') or '(无标题)'}{link}")
        _display_meta(item)
        choice = form.radio(
            "选择",
            options=("selected", "backup", "discarded"),
            format_func=lambda x: {"selected": "采纳", "backup": "备选", "discarded": "放弃"}[x],
            key=f"choice-{aid}",
        )
        if choice == "selected":
            selected_ids.append(aid)
        elif choice == "backup":
            backup_ids.append(aid)
        else:
            discarded_ids.append(aid)
        form.text_area("摘要（只读）", item.get("summary") or "", key=f"summary-ro-{aid}", height=140, disabled=True)
        form.markdown("---")
    submitted = form.form_submit_button("提交当前页选择", key="submit-select")
    if submitted:
        actor = st.session_state.get("actor") or None
        result = manual_filter.bulk_decide(
            selected_ids=selected_ids, backup_ids=backup_ids, discarded_ids=discarded_ids, actor=actor
        )
        st.success(
            f"已更新：采纳 {result.get('selected', 0)}，备选 {result.get('backup', 0)}，放弃 {result.get('discarded', 0)}"
        )
        st.experimental_rerun()
    col_prev, col_next = st.columns(2)
    if col_prev.button("上一页", key="select-prev", disabled=page <= 1):
        st.session_state["page_select"] = max(1, page - 1)
        st.experimental_rerun()
    if col_next.button("下一页", key="select-next", disabled=(page * PAGE_SIZE) >= page_data["total"]):
        st.session_state["page_select"] = page + 1
        st.experimental_rerun()


def _render_review_list(title: str, items: List[Dict[str, Any]], bucket: str) -> Dict[str, Dict[str, Any]]:
    st.subheader(title)
    edits: Dict[str, Dict[str, Any]] = {}
    selected_ids: List[str] = []
    backup_ids: List[str] = []
    discarded_ids: List[str] = []
    pending_ids: List[str] = []
    for item in items:
        aid = str(item.get("article_id") or "")
        url = item.get("url")
        link = f" · [原文]({url})" if url else ""
        st.markdown(f"**{item.get('title') or '(无标题)'}{link}**")
        _display_meta(item)
        summary_key = f"edit-{bucket}-{aid}"
        new_summary = st.text_area("摘要（可编辑）", item.get("summary") or "", key=summary_key, height=160)
        status_key = f"status-{bucket}-{aid}"
        choice = st.selectbox(
            "状态",
            options=("selected", "backup", "discarded", "pending"),
            index=("selected", "backup", "discarded", "pending").index(item.get("manual_status", "selected")),
            format_func=lambda x: {"selected": "采纳", "backup": "备选", "discarded": "放弃", "pending": "待处理"}[x],
            key=status_key,
        )
        edits[aid] = {"summary": new_summary, "status": choice}
        if choice == "selected":
            selected_ids.append(aid)
        elif choice == "backup":
            backup_ids.append(aid)
        elif choice == "discarded":
            discarded_ids.append(aid)
        else:
            pending_ids.append(aid)
        st.markdown("---")
    return {
        "edits": edits,
        "selected": selected_ids,
        "backup": backup_ids,
        "discarded": discarded_ids,
        "pending": pending_ids,
    }


def _render_review_tab() -> None:
    st.markdown("### 审阅（可编辑摘要）")
    page_sel = st.session_state["page_review_sel"]
    page_bak = st.session_state["page_review_backup"]
    data_sel = manual_filter.list_review("selected", limit=PAGE_SIZE, offset=(page_sel - 1) * PAGE_SIZE)
    data_bak = manual_filter.list_review("backup", limit=PAGE_SIZE, offset=(page_bak - 1) * PAGE_SIZE)

    export_col1, export_col2 = st.columns(2)
    default_tag = datetime.now().strftime("%Y-%m-%d")
    report_tag = export_col1.text_input("Report Tag", value=default_tag)
    output_path = export_col2.text_input("输出路径", value=DEFAULT_EXPORT_PATH)
    if st.button("导出（仅采纳）", key="export-selected"):
        result = manual_filter.export_batch(report_tag=report_tag.strip(), output_path=output_path)
        st.success(f"生成 {result.get('count', 0)} 条，输出：{result.get('output_path')}")
        st.json(result.get("category_counts", {}))

    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        st.caption(f"采纳 {data_sel['total']} 条，第 {page_sel} 页")
        result_sel = _render_review_list("采纳列表", data_sel["items"], bucket="selected")
    with col2:
        st.caption(f"备选 {data_bak['total']} 条，第 {page_bak} 页")
        result_bak = _render_review_list("备选列表", data_bak["items"], bucket="backup")

    if st.button("保存编辑与状态", key="save-review"):
        actor = st.session_state.get("actor") or None
        # 合并编辑
        merged_edits: Dict[str, Dict[str, Any]] = {}
        for container in (result_sel["edits"], result_bak["edits"]):
            merged_edits.update({k: {"summary": v["summary"]} for k, v in container.items()})
        manual_filter.save_edits(merged_edits, actor=actor)
        # 状态更新
        selected_ids = result_sel["selected"] + result_bak["selected"]
        backup_ids = result_sel["backup"] + result_bak["backup"]
        discarded_ids = result_sel["discarded"] + result_bak["discarded"]
        pending_ids = result_sel["pending"] + result_bak["pending"]
        manual_filter.bulk_decide(
            selected_ids=selected_ids,
            backup_ids=backup_ids,
            discarded_ids=discarded_ids,
            actor=actor,
        )
        if pending_ids:
            manual_filter.reset_to_pending(pending_ids, actor=actor)
        st.success("已保存编辑与状态")
        st.experimental_rerun()

    col_prev1, col_next1 = st.columns(2)
    if col_prev1.button("采纳上一页", key="sel-prev", disabled=page_sel <= 1):
        st.session_state["page_review_sel"] = max(1, page_sel - 1)
        st.experimental_rerun()
    if col_next1.button("采纳下一页", key="sel-next", disabled=(page_sel * PAGE_SIZE) >= data_sel["total"]):
        st.session_state["page_review_sel"] = page_sel + 1
        st.experimental_rerun()

    col_prev2, col_next2 = st.columns(2)
    if col_prev2.button("备选上一页", key="bak-prev", disabled=page_bak <= 1):
        st.session_state["page_review_backup"] = max(1, page_bak - 1)
        st.experimental_rerun()
    if col_next2.button("备选下一页", key="bak-next", disabled=(page_bak * PAGE_SIZE) >= data_bak["total"]):
        st.session_state["page_review_backup"] = page_bak + 1
        st.experimental_rerun()


def _render_discard_tab() -> None:
    page = st.session_state["page_discard"]
    data = manual_filter.list_discarded(limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    st.caption(f"放弃 {data['total']} 条，第 {page} 页")
    form = st.form("discard_form")
    backup_ids: List[str] = []
    for item in data["items"]:
        aid = str(item.get("article_id") or "")
        url = item.get("url")
        link = f" · [原文]({url})" if url else ""
        form.markdown(f"**{item.get('title') or '(无标题)'}{link}**")
        _display_meta(item)
        pick = form.checkbox("加入备选", key=f"discard-backup-{aid}")
        if pick:
            backup_ids.append(aid)
        form.markdown("---")
    if form.form_submit_button("批量加入备选", key="discard-to-backup"):
        actor = st.session_state.get("actor") or None
        manual_filter.bulk_decide(selected_ids=[], backup_ids=backup_ids, discarded_ids=[], actor=actor)
        st.success(f"已加入备选 {len(backup_ids)} 条")
        st.experimental_rerun()
    col_prev, col_next = st.columns(2)
    if col_prev.button("上一页", key="discard-prev", disabled=page <= 1):
        st.session_state["page_discard"] = max(1, page - 1)
        st.experimental_rerun()
    if col_next.button("下一页", key="discard-next", disabled=(page * PAGE_SIZE) >= data["total"]):
        st.session_state["page_discard"] = page + 1
        st.experimental_rerun()


def main() -> None:
    st.set_page_config(page_title="筛选控制台（二期）", layout="wide")
    _init_state()
    _render_sidebar()
    st.title("人工筛选控制台")
    tab_select, tab_review, tab_discard = st.tabs(["筛选", "审阅", "放弃"])
    with tab_select:
        _render_select_tab()
    with tab_review:
        _render_review_tab()
    with tab_discard:
        _render_discard_tab()


if __name__ == "__main__":
    main()
