from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from p2p.domain.casefacts import SEVERITY_RANK, primary_exception
from p2p.domain.models import DecisionType, HumanReview
from p2p.ui.components.case_detail import render_case_detail
from p2p.ui.components.chips import action_pill, h, icon, render_html
from p2p.ui.components.shell import switch_to, top_bar
from p2p.ui.identity import current_reviewer, initials
from p2p.ui.runtime import service
from p2p.ui.viewmodels import (
    format_duration,
    format_money,
    label_action,
    label_exception,
    parse_timestamp,
    severity_chip,
)

DECISIONS = [DecisionType.APPROVE, DecisionType.REQUEST_INFO, DecisionType.REJECT]
DECISION_CAPTIONS = {
    DecisionType.APPROVE: "Accept the invoice as billed",
    DecisionType.REQUEST_INFO: "Ask for evidence; reservations are kept",
    DecisionType.REJECT: "Return the invoice; releases reserved quantities",
}
NOTE_REQUIRED = {DecisionType.APPROVE, DecisionType.REJECT}
MIN_NOTE = 3
DAY = 24 * 3600


def queue(cases: list[dict], now: datetime) -> list[dict]:
    """Pending cases by severity, then invoice value, then time waiting (all descending)."""

    def priority(case: dict) -> tuple:
        _, severity, _ = primary_exception(case["state"])
        waited = (now - parse_timestamp(case["updated_at"])).total_seconds()
        return (SEVERITY_RANK.get(severity or "", 0), float(case["invoice"]["total"]), waited)

    return sorted(cases, key=priority, reverse=True)


def select_case(case_id: str) -> None:
    st.query_params["case"] = case_id


def clear_confirmation() -> None:
    st.session_state.pop("review_confirm", None)


def queue_card(case: dict, now: datetime, selected: bool) -> None:
    invoice = case["invoice"]
    exception, severity, _ = primary_exception(case["state"])
    chip = severity_chip(severity)
    waited = (now - parse_timestamp(case["updated_at"])).total_seconds()
    late = " p2p-queue-late" if waited > DAY else ""
    with st.container(key=f"queue-card-{case['case_id']}"):
        render_html(
            f"""
            <div class="p2p-queue-card{" p2p-queue-selected" if selected else ""}">
              <div class="p2p-queue-top"><strong>{h(invoice["supplier_name"])}</strong><span>{h(format_money(invoice["total"], invoice["currency"]))}</span></div>
              <span class="p2p-muted">{h(label_exception(exception))}</span>
              <div class="p2p-queue-bottom">
                <span class="p2p-severity-chip p2p-severity-{h(chip.key)}">{h(chip.text)}</span>
                <span class="p2p-queue-wait{late}">{icon("schedule")}{h(format_duration(waited))}</span>
              </div>
            </div>
            """
        )
        st.button(
            "Open",
            key=f"queue_open_{case['case_id']}",
            width="stretch",
            type="primary" if selected else "secondary",
            on_click=select_case,
            args=(case["case_id"],),
        )


def recommendation_html(case: dict, auto_resolve: float) -> str:
    state = case["state"]
    resolution = state.get("resolution")
    if resolution:
        confidence = float(resolution["confidence"])
        chip = action_pill(resolution["action"])
        meter = f"""
        <div class="p2p-meter" title="{auto_resolve:.2f} auto-resolve gate">
          <div class="p2p-meter-fill" style="width:{confidence * 100:.0f}%"></div>
          <div class="p2p-meter-gate" style="left:{auto_resolve * 100:.0f}%"></div>
        </div>
        """
        confidence_text = f"confidence {confidence:.2f}"
    else:
        chip = '<span class="p2p-muted">No AI recommendation</span>'
        meter = ""
        confidence_text = ""
    revisions = int(state.get("reflection_count") or 0)
    if revisions:
        note = (
            f"Revised {revisions}× after the Critic rejected the first proposal. "
            "Policy requires review."
        )
    elif state.get("investigation"):
        note = "Policy requires review. The Policy tab lists the failed checks."
    else:
        note = "AI was not invoked. Policy paused this case; see the Policy tab."
    return f"""
    <div class="p2p-recommendation">
      <span class="p2p-caption">AI recommendation</span>
      <div class="p2p-recommendation-row">{chip}<span class="p2p-muted">{h(confidence_text)}</span></div>
      {meter}
      <span class="p2p-caption">{h(note)}</span>
    </div>
    """


def confirm_dialog(case: dict, app) -> None:
    request = st.session_state["review_confirm"]
    action = DecisionType(request["action"])
    label = label_action(action)

    def body() -> None:
        render_html(
            f"""
            <p class="p2p-dialog-copy">This records a human decision on {h(request["case_id"])} and resumes the workflow. The decision is added to the audit trail and can't be edited.</p>
            <div class="p2p-note-echo">{h(f"Note: {request['note']}" if request["note"] else "No note added.")}</div>
            """
        )
        with st.container(horizontal=True, horizontal_alignment="right"):
            if st.button("Cancel", key="review_cancel"):
                clear_confirmation()
                st.rerun()
            if st.button(f"Confirm {label.lower()}", key="review_confirm_button", type="primary"):
                apply_review(case, app, action, request)

    st.dialog(f"{label} {case['invoice_id']}?", on_dismiss=clear_confirmation)(body)()


def apply_review(case: dict, app, action: DecisionType, request: dict) -> None:
    clear_confirmation()
    try:
        app.review_case(
            request["case_id"],
            HumanReview(
                reviewer=current_reviewer(), action=action, comment=request["note"] or None
            ),
            checkpoint_id=request["checkpoint_id"],
        )
    except Exception:
        st.session_state["flash"] = {
            "kind": "error",
            "text": "The review could not be applied. Reload the case and try again.",
        }
        st.rerun()
    remaining = queue(app.list_cases("PENDING_REVIEW"), datetime.now(UTC))
    st.session_state["flash"] = {
        "kind": "toast",
        "text": (
            f"{label_action(action)} recorded for {case['invoice_id']} · "
            f"{len(remaining)} left in queue"
        ),
    }
    if remaining:
        st.query_params["case"] = remaining[0]["case_id"]
    elif "case" in st.query_params:
        del st.query_params["case"]
    st.rerun()


def decision_panel(case: dict, app) -> None:
    case_id = case["case_id"]
    decision_key, note_key = f"review_decision_{case_id}", f"review_note_{case_id}"
    submitted = case.get("submitted_review")
    if submitted:
        st.session_state.setdefault(decision_key, DecisionType(submitted["action"]))
        st.session_state.setdefault(note_key, submitted.get("comment") or "")

    render_html('<h3 class="p2p-panel-title">Your decision</h3>')
    render_html(recommendation_html(case, app.runtime.rules.confidence.auto_resolve))
    if submitted:
        st.info(
            "A review is already durably submitted. Submit the same decision and note to "
            "finish recovery."
        )
    ai_action = (case["state"].get("resolution") or {}).get("action")
    decision = st.radio(
        "Decision",
        DECISIONS,
        index=None,
        format_func=lambda item: label_action(item.value),
        captions=[
            DECISION_CAPTIONS[item] + (" · matches AI" if item.value == ai_action else "")
            for item in DECISIONS
        ],
        key=decision_key,
        label_visibility="collapsed",
    )
    if decision is None:
        hint = "Choose a decision first"
    elif decision in NOTE_REQUIRED:
        hint = f"Required for {label_action(decision.value)}"
    else:
        hint = "Optional"
    render_html(f'<div class="p2p-note-label"><span>Note</span><span>{h(hint)}</span></div>')
    note = st.text_area(
        "Note",
        placeholder="What evidence supports this decision?",
        key=note_key,
        label_visibility="collapsed",
    )
    reviewer = current_reviewer()
    render_html(
        f'<div class="p2p-signing"><span class="p2p-avatar">{h(initials(reviewer))}</span>'
        f"Signing as {h(reviewer)}</div>"
    )
    label = "Select a decision" if decision is None else f"Submit: {label_action(decision.value)}"
    if st.button(
        label,
        type="primary",
        width="stretch",
        disabled=decision is None,
        key=f"review_submit_{case_id}",
    ):
        cleaned = (note or "").strip()
        if decision in NOTE_REQUIRED and len(cleaned) < MIN_NOTE:
            st.error(
                f"Add a note of at least {MIN_NOTE} characters to "
                f"{label_action(decision.value).lower()}."
            )
        else:
            st.session_state["review_confirm"] = {
                "case_id": case_id,
                "action": decision.value,
                "note": cleaned,
                "checkpoint_id": case.get("checkpoint_id"),
            }
            st.rerun()


def empty_queue() -> None:
    top_bar(["Review queue"], "/review")
    render_html(
        f"""
        <div class="p2p-empty-state"><div class="p2p-empty-icon p2p-empty-icon-done">{icon("task_alt")}</div>
          <div class="p2p-empty-title">Queue clear</div>
          <div class="p2p-empty-copy">No cases are waiting for a human decision.</div>
        </div>
        """
    )
    with st.container(horizontal=True, horizontal_alignment="center", key="review-empty-actions"):
        if st.button("See decided cases", key="review_empty_cases"):
            switch_to("cases")
        if st.button("Process invoice", type="primary", key="review_empty_process"):
            switch_to("process")


def render() -> None:
    app = service()
    now = datetime.now(UTC)
    pending = queue(app.list_cases("PENDING_REVIEW"), now)
    if not pending:
        empty_queue()
        return

    by_id = {case["case_id"]: case for case in pending}
    selected_id = st.query_params.get("case")
    if selected_id not in by_id:
        selected_id = pending[0]["case_id"]
    st.query_params["case"] = selected_id
    case = app.get_case(selected_id) or by_id[selected_id]

    top_bar(["Review queue", case["invoice_id"]], "/review")
    if st.session_state.get("review_confirm", {}).get("case_id") == selected_id:
        confirm_dialog(case, app)

    with st.container(horizontal=True, wrap=True, gap="medium", key="review-workbench"):
        with st.container(width=260, key="review-queue"):
            render_html(
                f"""
                <div class="p2p-queue-head"><h1>Review queue</h1><span>{len(pending)} open</span></div>
                <span class="p2p-caption">Priority: severity, then value and wait</span>
                """
            )
            for item in pending:
                queue_card(item, now, item["case_id"] == selected_id)
        with st.container(width="stretch", key="review-case"):
            render_case_detail(case, app)
        with st.container(width=300, border=True, key="review-decision"):
            decision_panel(case, app)


render()
