from __future__ import annotations

import pandas as pd
import streamlit as st
from pandas.io.formats.style import Styler

from p2p.ui.components.case_detail import render_case_detail
from p2p.ui.components.chips import h, render_html, status_pill
from p2p.ui.components.shell import page_header, switch_to, top_bar
from p2p.ui.runtime import service
from p2p.ui.viewmodels import display_status, label_exception, outcome_view, parse_timestamp

VIEWS = {
    "all": "All",
    "review": "Awaiting review",
    "approved": "Approved",
    "rejected": "Rejected",
    "request_info": "Request info",
}
PAGE_SIZE = 50
SEVERITY_FG = {
    "CRITICAL": "#a1122b",
    "MATERIAL": "#a6380f",
    "WARNING": "#7f4d00",
    "INFO": "#33507a",
}
STATUS_FG = {
    "review": "#7f4d00",
    "escalated": "#7f4d00",
    "recovery": "#7f4d00",
    "approved": "#14653f",
    "rejected": "#a1122b",
    "request_info": "#1d4fc4",
    "processing": "#475264",
}


def remember_view() -> None:
    view = st.session_state.get("cases_view") or "all"
    if view == "all":
        st.query_params.pop("status", None)
    else:
        st.query_params["status"] = view
    st.session_state.pop("cases_limit", None)


def show_all_cases() -> None:
    st.query_params.pop("case", None)


def load_more() -> None:
    st.session_state["cases_limit"] = st.session_state.get("cases_limit", PAGE_SIZE) + PAGE_SIZE


def header() -> None:
    page_header(
        "Case archive",
        "Cases",
        "Inspect the evidence, reasoning, policy outcome, and immutable audit history for every run.",
    )


def table(rows: list[dict]) -> Styler:
    frame = pd.DataFrame(
        [
            {
                "Case": row["case_id"],
                "Supplier · invoice": f"{row['supplier_name']} · {row['invoice_id']} · {row['po_number'] or 'No PO'}",
                "Amount": float(row["total"]) if row["total"] is not None else None,
                "Currency": row["currency"],
                "Exception": (
                    f"{row['max_severity']} · {label_exception(row['primary_exception'])}"
                    if row["max_severity"]
                    else label_exception(row["primary_exception"])
                ),
                "AI confidence": row["ai_confidence"],
                "Status": display_status(row).label,
                "Updated": parse_timestamp(row["updated_at"]),
            }
            for row in rows
        ]
    )
    severities = [row["max_severity"] for row in rows]
    statuses = [display_status(row).key for row in rows]

    def colour_exception(column: pd.Series) -> list[str]:
        return [f"color: {SEVERITY_FG.get(severity, '#475264')}" for severity in severities]

    def colour_status(column: pd.Series) -> list[str]:
        return [f"color: {STATUS_FG.get(key, '#475264')}; font-weight: 600" for key in statuses]

    return frame.style.apply(colour_exception, subset=["Exception"]).apply(
        colour_status, subset=["Status"]
    )


def case_list() -> None:
    app = service()
    top_bar(["Cases"], "/cases")
    header()
    counts = app.view_counts()
    requested = st.query_params.get("status")
    with st.container(horizontal=True, wrap=True, vertical_alignment="center", key="cases-filters"):
        view = st.pills(
            "Status",
            list(VIEWS),
            default=requested if requested in VIEWS else "all",
            format_func=lambda key: f"{VIEWS[key]} {counts[key]}",
            key="cases_view",
            on_change=remember_view,
            label_visibility="collapsed",
        )
        search = st.text_input(
            "Search",
            placeholder="Search case, invoice, supplier, PO",
            key="cases_search",
            label_visibility="collapsed",
            width=320,
        )
    limit = st.session_state.get("cases_limit", PAGE_SIZE)
    rows, total = app.query_cases(view=view or "all", search=search, sort="updated", limit=limit)
    if not rows:
        render_html(
            '<div class="p2p-empty-state"><div class="p2p-empty-copy">No cases match this filter.</div></div>'
        )
        return
    event = st.dataframe(
        table(rows),
        on_select="rerun",
        selection_mode="single-row",
        hide_index=True,
        width="stretch",
        key="cases_table",
        column_config={
            "Amount": st.column_config.NumberColumn(format="%.2f"),
            "AI confidence": st.column_config.ProgressColumn(
                min_value=0.0, max_value=1.0, format="%.2f"
            ),
            "Updated": st.column_config.DatetimeColumn(format="D MMM YYYY, HH:mm"),
        },
    )
    if event.selection.rows:
        st.query_params["case"] = rows[event.selection.rows[0]]["case_id"]
        st.rerun()
    with st.container(horizontal=True, vertical_alignment="center", key="cases-paging"):
        render_html(f'<span class="p2p-caption">Showing {len(rows)} of {total}</span>')
        if len(rows) < total:
            st.button("Load more", key="cases_load_more", on_click=load_more)


def outcome_panel(case: dict) -> None:
    status = display_status(case)
    rows = "".join(
        f'<div class="p2p-kv"><code>{h(key)}</code><span>{h(value)}</span></div>'
        for key, value in outcome_view(case)
    )
    render_html(
        f"""
        <section class="p2p-card p2p-outcome-panel">
          <h3 class="p2p-panel-title">Outcome</h3>
          {status_pill(status.key, status.label)}
          {rows}
        </section>
        """
    )


def case_detail(case_id: str) -> None:
    app = service()
    top_bar(["Cases", case_id], "/cases")
    st.button("All cases", icon=":material/arrow_back:", key="cases_back", on_click=show_all_cases)
    case = app.get_case(case_id)
    if not case:
        st.error(f"Case {case_id} was not found.")
        return
    with st.container(horizontal=True, wrap=True, gap="medium", key="cases-detail"):
        with st.container(width="stretch", key="cases-detail-case"):
            render_case_detail(case, app)
        with st.container(width=300, key="cases-detail-side"):
            if case["status"] == "PENDING_REVIEW":
                render_html(
                    '<section class="p2p-card"><h3 class="p2p-panel-title">Awaiting review</h3>'
                    '<p class="p2p-caption">Policy paused this case for a human decision.</p></section>'
                )
                if st.button(
                    "Review this case", type="primary", width="stretch", key="cases_review"
                ):
                    switch_to("review", case=case_id)
            elif case.get("state", {}).get("outcome"):
                outcome_panel(case)


def render() -> None:
    case_id = st.query_params.get("case")
    if case_id:
        case_detail(case_id)
    else:
        case_list()


render()
