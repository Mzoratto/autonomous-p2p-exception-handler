from __future__ import annotations

import streamlit as st

from p2p.ui.components.chips import action_pill, h, icon, render_html, status_pill
from p2p.ui.viewmodels import (
    display_status,
    format_money,
    label_action,
    label_event,
    label_exception,
)


def render_discrepancies(state: dict) -> None:
    result = state.get("match_result") or {}
    summary = result.get("summary", "Not yet available")
    render_html(f'<div class="p2p-section-subtitle">{h(summary)}</div>')
    discrepancies = result.get("discrepancies", [])
    if not discrepancies:
        st.success("No discrepancies — exact three-way match")
        return
    for item in discrepancies:
        severity = str(item.get("severity", "info")).lower()
        render_html(
            f"""
            <div class="p2p-discrepancy">
              <div class="p2p-discrepancy-head">
                <div class="p2p-discrepancy-type">{h(label_exception(item.get("type")))}</div>
                <span class="p2p-severity-chip p2p-severity-{h(severity)}">{h(severity)}</span>
              </div>
              <div class="p2p-discrepancy-copy">{h(item.get("explanation", "No explanation provided."))}</div>
            </div>
            """,
        )


def stage(title: str, detail: str, warning: bool = False) -> str:
    warning_class = " p2p-warn" if warning else ""
    marker = icon("priority_high" if warning else "check")
    return f"""
      <div class="p2p-stage">
        <span class="p2p-stage-dot{warning_class}">{marker}</span>
        <div class="p2p-stage-title">{h(title)}</div>
        <div class="p2p-stage-detail">{h(detail)}</div>
      </div>
    """


def render_case(case: dict) -> None:
    if not case:
        st.error("The selected case could not be loaded.")
        return

    state = case["state"]
    invoice = case["invoice"]
    status = display_status(case)
    render_html(
        f"""
        <div class="p2p-case-hero">
          <div class="p2p-case-kicker">Exception case</div>
          <div class="p2p-case-title">{h(case["case_id"])}</div>
          <div class="p2p-case-meta">
            {status_pill(status.key, status.label)}
            <span>{h(invoice["supplier_name"])}</span><span>·</span><span>{h(invoice["invoice_id"])}</span>
          </div>
        </div>
        <div class="p2p-case-summary-grid">
          <div class="p2p-summary-card"><div class="p2p-summary-label">Invoice</div><div class="p2p-summary-value">{h(invoice["invoice_id"])}</div></div>
          <div class="p2p-summary-card"><div class="p2p-summary-label">Supplier</div><div class="p2p-summary-value" title="{h(invoice["supplier_name"])}">{h(invoice["supplier_name"])}</div></div>
          <div class="p2p-summary-card"><div class="p2p-summary-label">Purchase order</div><div class="p2p-summary-value">{h(invoice.get("po_number") or "Missing")}</div></div>
          <div class="p2p-summary-card"><div class="p2p-summary-label">Invoice total</div><div class="p2p-summary-value">{h(format_money(invoice["total"], invoice["currency"]))}</div></div>
        </div>
        """,
    )

    tabs = st.tabs(["Workflow", "Invoice & ERP", "Decision", "Audit timeline"])
    with tabs[0]:
        erp = state.get("erp_context", {})
        timeline = [
            stage("Invoice validated", "Schema, line totals, and required fields passed"),
            stage(
                "ERP context loaded",
                f"PO {'found' if erp.get('purchase_order') else 'missing'} · "
                f"{len(erp.get('goods_receipts', []))} goods receipt(s)",
                not erp.get("purchase_order"),
            ),
            stage(
                "Three-way match completed",
                "Invoice, purchase order, and goods receipt compared deterministically",
            ),
        ]
        if state.get("investigation"):
            investigation = state["investigation"]
            timeline.append(
                stage(
                    "AI investigation completed",
                    f"{investigation['root_cause']} · {investigation['confidence']:.0%} confidence",
                )
            )
        if state.get("resolution"):
            resolution = state["resolution"]
            timeline.append(
                stage(
                    "Resolution proposed",
                    f"{label_action(resolution['action'])} · {resolution['confidence']:.0%} confidence",
                )
            )
        if state.get("critique"):
            critique = state["critique"]
            timeline.append(
                stage(
                    "Critic passed" if critique["valid"] else "Critic requested revision",
                    f"{state.get('reflection_count', 0)} reflection cycle(s)",
                    not critique["valid"],
                )
            )
        render_html(f'<div class="p2p-timeline">{"".join(timeline)}</div>')
        render_html('<div class="p2p-section-title">Match findings</div>')
        render_discrepancies(state)
        policy = state.get("policy_result")
        if policy:
            reasons = "".join(f"<li>{h(reason)}</li>" for reason in policy["reasons"])
            verdict = (
                "Autonomous action permitted" if policy["permitted"] else "Human review required"
            )
            render_html(
                f"""
                <div class="p2p-guardrail">
                  <div class="p2p-guardrail-mark">{icon("policy")}</div>
                  <div><strong>Deterministic policy · {h(verdict)}</strong><ul>{reasons}</ul></div>
                </div>
                """,
            )
    with tabs[1]:
        st.markdown("#### Invoice lines")
        st.dataframe(invoice["line_items"], width="stretch", hide_index=True)
        st.markdown("#### ERP context")
        st.json(state.get("erp_context", {}), expanded=False)
    with tabs[2]:
        if state.get("investigation"):
            investigation = state["investigation"]
            evidence = ", ".join(item["evidence_id"] for item in investigation["evidence"])
            missing = investigation["missing_information"]
            render_html(
                f"""
                <div class="p2p-decision-card">
                  <div class="p2p-decision-label">AI interpretation · verify against evidence</div>
                  <div class="p2p-decision-title">{h(investigation["root_cause"])}</div>
                  <div class="p2p-decision-copy"><strong>Evidence</strong> · {h(evidence or "None cited")}</div>
                  {f'<div class="p2p-decision-copy"><strong>Missing</strong> · {h("; ".join(missing))}</div>' if missing else ""}
                </div>
                """,
            )
        if state.get("resolution"):
            resolution = state["resolution"]
            request = resolution.get("information_request")
            render_html(
                f"""
                <div class="p2p-decision-card">
                  <div class="p2p-decision-label">Proposed resolution</div>
                  <div class="p2p-decision-title">{action_pill(resolution["action"])}</div>
                  <div class="p2p-decision-copy">{h(resolution["rationale"])}</div>
                  {f'<div class="p2p-request-copy">{h(request)}</div>' if request else ""}
                </div>
                """,
            )
        proposals = [
            event
            for event in case.get("audit", [])
            if event["event_type"] in {"RESOLUTION_PROPOSED", "RESOLUTION_REVISED"}
        ]
        if proposals:
            with st.expander("Recommendation history"):
                for proposal in proposals:
                    st.json(proposal)
        if state.get("critique"):
            critique = state["critique"]
            verdict = "Valid" if critique["valid"] else "Revision required"
            issues = "".join(
                f'<div class="p2p-decision-copy">• {h(issue)}</div>' for issue in critique["issues"]
            )
            render_html(
                f"""
                <div class="p2p-decision-card">
                  <div class="p2p-decision-label">Reflexion</div>
                  <div class="p2p-decision-title">{h(verdict)}</div>{issues}
                </div>
                """,
            )
        if state.get("outcome"):
            outcome = state["outcome"]
            render_html(
                f"""
                <div class="p2p-decision-card">
                  <div class="p2p-decision-label">Final outcome · {h(outcome["source"].replace("_", " ").title())}</div>
                  <div class="p2p-decision-title">{action_pill(outcome["action"])}</div>
                  <div class="p2p-decision-copy">{h(outcome["rationale"])}</div>
                </div>
                """,
            )
    with tabs[3]:
        audit = case.get("audit", [])
        if not audit:
            st.info("No audit events recorded.")
        for event in audit:
            render_html(
                f"""
                <div class="p2p-audit-row">
                  <div class="p2p-audit-node"></div>
                  <div>
                    <div class="p2p-audit-title">{h(label_event(event["event_type"]))}</div>
                    <div class="p2p-audit-meta">{h(event["timestamp"])} · {h(event["actor"])}</div>
                  </div>
                </div>
                """,
            )
            if event.get("data"):
                with st.expander("Event data"):
                    st.json(event["data"], expanded=False)
