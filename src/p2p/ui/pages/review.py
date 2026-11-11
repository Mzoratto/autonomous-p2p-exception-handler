from __future__ import annotations

import streamlit as st

from p2p.application.service import CaseService
from p2p.domain.models import DecisionType, HumanReview
from p2p.ui.components.case_detail import render_case
from p2p.ui.components.chips import icon, render_html
from p2p.ui.components.shell import page_header, show_failure
from p2p.ui.viewmodels import format_money, label_action


def render(app: CaseService) -> None:
    page_header(
        "Human-in-the-loop",
        "Review queue",
        "Resolve material or uncertain exceptions that deterministic policy will not authorize.",
    )
    pending = app.list_cases("PENDING_REVIEW")
    if not pending:
        render_html(
            f"""
            <div class="empty-state"><div class="empty-icon">{icon("task_alt")}</div>
              <div class="empty-title">Queue clear</div>
              <div class="empty-copy">No cases are waiting for a human decision.</div>
            </div>
            """,
        )
        return
    selected = st.selectbox(
        "Pending case",
        pending,
        format_func=lambda item: (
            f"{item['case_id']} · {item['invoice_id']} · "
            f"{format_money(item['invoice']['total'], item['invoice']['currency'])}"
        ),
    )
    case = app.get_case(selected["case_id"]) or selected
    render_case(case)
    st.divider()
    render_html('<div class="section-title">Reviewer decision</div>')
    if case.get("submitted_review"):
        st.info(
            "A review is already durably submitted. Retry the same reviewer, action and note to finish recovery."
        )
        st.json(case["submitted_review"])
    with st.container(border=True):
        action = st.radio(
            "Action",
            [DecisionType.APPROVE, DecisionType.REJECT, DecisionType.REQUEST_INFO],
            format_func=lambda item: label_action(item.value),
            horizontal=True,
        )
        reviewer = st.text_input("Reviewer", value="demo-reviewer")
        comment = st.text_area(
            "Review note",
            placeholder="Record the evidence or reason for this decision.",
        )
        if st.button("Submit and resume workflow", type="primary", width="stretch"):
            try:
                app.review_case(
                    selected["case_id"],
                    HumanReview(reviewer=reviewer, action=action, comment=comment or None),
                    checkpoint_id=case["checkpoint_id"],
                )
            except Exception as exc:
                show_failure("The review could not be applied. Reload the case and try again.", exc)
            else:
                remaining = len(app.list_cases("PENDING_REVIEW"))
                st.session_state["flash"] = {
                    "kind": "toast",
                    "text": (
                        f"{label_action(action)} recorded for {case['invoice_id']} · "
                        f"{remaining} left in queue"
                    ),
                }
                st.rerun()
