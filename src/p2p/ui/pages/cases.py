from __future__ import annotations

import streamlit as st

from p2p.ui.components.case_detail import render_case_detail
from p2p.ui.components.chips import icon, render_html
from p2p.ui.components.shell import page_header, top_bar
from p2p.ui.runtime import service
from p2p.ui.viewmodels import display_status


def remember_case() -> None:
    st.query_params["case"] = st.session_state["cases_case"]


def header() -> None:
    page_header(
        "Case archive",
        "Cases",
        "Inspect the evidence, reasoning, policy outcome, and immutable audit history for every run.",
    )


def render() -> None:
    app = service()
    cases = {case["case_id"]: case for case in app.list_cases()}
    if not cases:
        top_bar(["Cases"], "/cases")
        header()
        render_html(
            f"""
            <div class="p2p-empty-state"><div class="p2p-empty-icon">{icon("inbox")}</div>
              <div class="p2p-empty-title">No cases yet</div>
              <div class="p2p-empty-copy">Run an invoice workflow to create the first auditable case.</div>
            </div>
            """,
        )
        return

    ids = list(cases)
    selected_id = next(
        (
            candidate
            for candidate in (st.session_state.get("cases_case"), st.query_params.get("case"))
            if candidate in cases
        ),
        ids[0],
    )
    top_bar(["Cases", selected_id], "/cases")
    header()
    selected_id = st.selectbox(
        "Select case",
        ids,
        index=ids.index(selected_id),
        format_func=lambda case_id: (
            f"{case_id} · {cases[case_id]['invoice_id']} · {display_status(cases[case_id]).label}"
        ),
        key="cases_case",
        on_change=remember_case,
    )
    render_case_detail(app.get_case(selected_id) or cases[selected_id], app)


render()
