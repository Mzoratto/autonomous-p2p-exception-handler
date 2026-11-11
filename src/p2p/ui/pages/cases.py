from __future__ import annotations

import streamlit as st

from p2p.application.service import CaseService
from p2p.ui.components.case_detail import render_case
from p2p.ui.components.chips import icon, render_html
from p2p.ui.components.shell import page_header
from p2p.ui.viewmodels import display_status


def render(app: CaseService) -> None:
    page_header(
        "Case archive",
        "Cases",
        "Inspect the evidence, reasoning, policy outcome, and immutable audit history for every run.",
    )
    cases = app.list_cases()
    if not cases:
        render_html(
            f"""
            <div class="empty-state"><div class="empty-icon">{icon("inbox")}</div>
              <div class="empty-title">No cases yet</div>
              <div class="empty-copy">Run an invoice workflow to create the first auditable case.</div>
            </div>
            """,
        )
        return
    selected = st.selectbox(
        "Select case",
        cases,
        format_func=lambda item: (
            f"{item['case_id']} · {item['invoice_id']} · {display_status(item).label}"
        ),
    )
    render_case(app.get_case(selected["case_id"]) or selected)
