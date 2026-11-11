from __future__ import annotations

import streamlit as st

from p2p.domain.models import DecisionType, HumanReview
from p2p.ui.components.case_detail import render_case
from p2p.ui.components.chips import icon, render_html
from p2p.ui.components.shell import page_header, show_failure, top_bar
from p2p.ui.runtime import service
from p2p.ui.viewmodels import format_money, label_action


def remember_case() -> None:
    st.query_params["case"] = st.session_state["review_case"]


def render() -> None:
    app = service()
    pending = {case["case_id"]: case for case in app.list_cases("PENDING_REVIEW")}
    if not pending:
        top_bar(["Review queue"], "/review")
        page_header(
            "Human-in-the-loop",
            "Review queue",
            "Resolve material or uncertain exceptions that deterministic policy will not authorize.",
        )
        render_html(
            f"""
            <div class="p2p-empty-state"><div class="p2p-empty-icon">{icon("task_alt")}</div>
              <div class="p2p-empty-title">Queue clear</div>
              <div class="p2p-empty-copy">No cases are waiting for a human decision.</div>
            </div>
            """,
        )
        return

    ids = list(pending)
    selected_id = next(
        (
            candidate
            for candidate in (st.session_state.get("review_case"), st.query_params.get("case"))
            if candidate in pending
        ),
        ids[0],
    )
    top_bar(["Review queue", pending[selected_id]["invoice_id"]], "/review")
    page_header(
        "Human-in-the-loop",
        "Review queue",
        "Resolve material or uncertain exceptions that deterministic policy will not authorize.",
    )
    selected_id = st.selectbox(
        "Pending case",
        ids,
        index=ids.index(selected_id),
        format_func=lambda case_id: (
            f"{case_id} · {pending[case_id]['invoice_id']} · "
            f"{format_money(pending[case_id]['invoice']['total'], pending[case_id]['invoice']['currency'])}"
        ),
        key="review_case",
        on_change=remember_case,
    )
    case = app.get_case(selected_id) or pending[selected_id]
    render_case(case)
    st.divider()
    render_html('<div class="p2p-section-title">Reviewer decision</div>')
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
            key="review_action",
        )
        reviewer = st.text_input("Reviewer", value="demo-reviewer")
        comment = st.text_area(
            "Review note",
            placeholder="Record the evidence or reason for this decision.",
        )
        if st.button(
            "Submit and resume workflow", type="primary", width="stretch", key="review_submit"
        ):
            try:
                app.review_case(
                    selected_id,
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
                if "case" in st.query_params:
                    del st.query_params["case"]
                st.rerun()


render()
