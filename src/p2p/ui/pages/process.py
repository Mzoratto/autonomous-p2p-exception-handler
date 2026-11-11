from __future__ import annotations

from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from p2p.application.service import CaseService
from p2p.domain.models import Invoice
from p2p.ui.components.case_detail import render_case
from p2p.ui.components.chips import h, render_html
from p2p.ui.components.shell import navigate_to_page, page_header, render_field_errors, show_failure
from p2p.ui.viewmodels import format_money, validation_rows


def queue_invoice_workflow_stage(stage_name: str) -> None:
    st.session_state["invoice_workflow_pending_stage"] = stage_name


def reset_invoice_workflow() -> None:
    for key in (
        "invoice_workflow_invoice",
        "invoice_workflow_case_id",
        "invoice_workflow_status",
    ):
        st.session_state.pop(key, None)
    queue_invoice_workflow_stage("Input")


def render_validated_invoice(invoice: Invoice) -> None:
    render_html(
        f"""
        <div class="case-summary-grid">
          <div class="summary-card"><div class="summary-label">Invoice</div><div class="summary-value">{h(invoice.invoice_id)}</div></div>
          <div class="summary-card"><div class="summary-label">Supplier</div><div class="summary-value" title="{h(invoice.supplier_name)}">{h(invoice.supplier_name)}</div></div>
          <div class="summary-card"><div class="summary-label">Purchase order</div><div class="summary-value">{h(invoice.po_number or "Missing")}</div></div>
          <div class="summary-card"><div class="summary-label">Invoice total</div><div class="summary-value">{h(format_money(invoice.total, invoice.currency))}</div></div>
        </div>
        """,
    )


def render(app: CaseService) -> None:
    pending_stage = st.session_state.pop("invoice_workflow_pending_stage", None)
    if pending_stage:
        st.session_state["invoice_workflow_stage"] = pending_stage
    st.session_state.setdefault("invoice_workflow_stage", "Input")

    page_header(
        "New workflow",
        "Process an invoice",
        "Validate the input, run the controlled analysis, and inspect the resulting decision.",
    )
    with st.container(key="invoice-workflow-stepper"):
        workflow_stage = st.radio(
            "Workflow stage",
            ["Input", "Analyze", "Resolve"],
            key="invoice_workflow_stage",
            horizontal=True,
            label_visibility="collapsed",
        )

    if workflow_stage == "Input":
        with st.container(border=True):
            render_html('<div class="section-title">1 · Select invoice input</div>')
            source = st.radio(
                "Input source",
                ["Demo scenario", "Upload JSON", "Paste JSON"],
                horizontal=True,
            )
            payload: str | bytes | None = None
            if source == "Demo scenario":
                files = sorted(Path("data/demo/invoices").glob("*.json"))
                if not files:
                    st.warning("Run `make seed` to generate demo invoices.")
                    return
                chosen = st.selectbox(
                    "Scenario",
                    files,
                    format_func=lambda item: item.stem.replace("_", " ").title(),
                )
                payload = chosen.read_text(encoding="utf-8")
                with st.expander("Preview invoice JSON"):
                    st.code(payload, language="json")
            elif source == "Upload JSON":
                upload = st.file_uploader("Invoice JSON", type=["json"])
                payload = upload.getvalue() if upload else None
            else:
                payload = st.text_area("Invoice JSON", height=320)

            if st.button(
                "Validate and continue",
                type="primary",
                disabled=not payload,
                width="stretch",
            ):
                try:
                    st.session_state["invoice_workflow_invoice"] = app.parse_invoice(payload or "")
                    st.session_state.pop("invoice_workflow_case_id", None)
                    st.session_state.pop("invoice_workflow_status", None)
                    queue_invoice_workflow_stage("Analyze")
                    st.rerun()
                except ValidationError as exc:
                    render_field_errors(validation_rows(exc))
                except UnicodeDecodeError:
                    render_field_errors([("file", "The file is not valid UTF-8 text.")])
        return

    invoice = st.session_state.get("invoice_workflow_invoice")
    if invoice is None:
        st.info("Validate an invoice in the Input stage before continuing.")
        st.button(
            "Go to input",
            type="primary",
            on_click=queue_invoice_workflow_stage,
            args=("Input",),
        )
        return

    if workflow_stage == "Analyze":
        with st.container(border=True):
            render_html('<div class="section-title">2 · Analyze the exception</div>')
            st.success("Invoice structure and arithmetic are valid.")
            render_validated_invoice(invoice)
            st.caption(
                "The workflow will retrieve ERP evidence, run deterministic three-way matching, "
                "invoke bounded AI reasoning only for material exceptions, and apply policy controls."
            )
            back, run = st.columns([1, 2])
            with back:
                st.button(
                    "Back to input",
                    width="stretch",
                    on_click=queue_invoice_workflow_stage,
                    args=("Input",),
                )
            with run:
                if st.button("Run analysis", type="primary", width="stretch"):
                    try:
                        with st.status("Analyzing invoice exception…", expanded=True) as status:
                            st.write("Retrieving purchase order, receipt, and supplier evidence")
                            st.write("Running deterministic three-way matching")
                            st.write("Applying bounded reasoning and decision policy when required")
                            result = app.process_invoice(invoice)
                            status.update(
                                label="Analysis complete — human review required"
                                if result["status"] == "PENDING_REVIEW"
                                else "Analysis and resolution complete",
                                state="complete",
                            )
                        st.session_state["invoice_workflow_case_id"] = result["case_id"]
                        st.session_state["invoice_workflow_status"] = result["status"]
                        st.session_state["selected_case"] = result["case_id"]
                        queue_invoice_workflow_stage("Resolve")
                        st.rerun()
                    except Exception as exc:
                        show_failure(
                            "The workflow could not finish. The case is saved; "
                            "run the same invoice again to resume.",
                            exc,
                        )
        return

    case_id = st.session_state.get("invoice_workflow_case_id")
    if not case_id:
        st.info("Run the Analyze stage before opening the resolution.")
        st.button(
            "Go to analysis",
            type="primary",
            on_click=queue_invoice_workflow_stage,
            args=("Analyze",),
        )
        return

    case = app.get_case(case_id) or {}
    if case.get("status") == "PENDING_REVIEW":
        st.warning(f"{case_id} is persisted and waiting for a reviewer.")
    else:
        source = case.get("state", {}).get("outcome", {}).get("source")
        if case.get("status") == "COMPLETED":
            st.success(
                f"{case_id} completed autonomously."
                if source == "AUTONOMOUS"
                else f"{case_id} completed after human review."
            )
        else:
            st.warning(
                f"{case_id} requires recovery. Resubmit the same invoice to resume processing."
            )
    actions = st.columns(2)
    with actions[0]:
        st.button(
            "Process another invoice",
            width="stretch",
            on_click=reset_invoice_workflow,
        )
    with actions[1]:
        destination = "Review Queue" if case.get("status") == "PENDING_REVIEW" else "Cases"
        st.button(
            f"Open {destination.lower()}",
            type="primary",
            width="stretch",
            on_click=navigate_to_page,
            args=(destination,),
        )
    render_case(case)
