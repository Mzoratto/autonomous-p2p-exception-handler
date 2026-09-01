from __future__ import annotations

from html import escape
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from p2p.application.service import CaseService, create_runtime
from p2p.domain.models import DecisionType, HumanReview


def render_html(markup: str) -> None:
    """Render generated HTML without Markdown interpreting indented siblings."""
    st.html("".join(line.strip() for line in markup.splitlines()))


st.set_page_config(
    page_title="P2P Exception Control Center",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

UI_STYLES = """
<style>
  :root {
    --ink: #172033;
    --muted: #667085;
    --line: #e5e9f0;
    --surface: #ffffff;
    --surface-subtle: #f7f8fa;
    --navy: #0d1830;
    --blue: #2f6fed;
    --blue-soft: #edf3ff;
    --green: #12805c;
    --green-soft: #eaf8f2;
    --amber: #a65b00;
    --amber-soft: #fff5e5;
    --red: #c43d4b;
    --red-soft: #fff0f1;
    --radius: 14px;
    --shadow: 0 10px 26px rgba(16, 24, 40, .06);
  }

  html, body, [class*="css"] {
    color: var(--ink);
    font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }
  .stApp { background: #f4f6f9; }
  .block-container { max-width: 1400px; padding: 4.25rem 2.5rem 4rem; }

  /* Sidebar */
  [data-testid="stSidebar"] { background: var(--navy); border-right: 0; }
  [data-testid="stSidebar"] > div:first-child { padding: 1.35rem 1rem; }
  [data-testid="stSidebar"] * { color: #d9e2f2; }
  [data-testid="stSidebar"] hr { border-color: rgba(255,255,255,.11); }
  [data-testid="stSidebar"] [data-testid="stRadio"] label {
    padding: .58rem .72rem;
    border-radius: 9px;
    transition: background .15s ease, color .15s ease;
  }
  [data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
    background: rgba(255,255,255,.07);
  }
  [data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) {
    background: #2f6fed;
    color: white;
  }
  [data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) * {
    color: white !important;
  }
  [data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] > label > div:first-child {
    display: none;
  }
  .brand { display: flex; gap: .72rem; align-items: center; margin: .05rem .25rem 1.7rem; }
  .brand-mark {
    display: grid; place-items: center; width: 36px; height: 36px; flex: 0 0 36px;
    border-radius: 10px; background: #2f6fed; color: white !important;
    box-shadow: 0 7px 16px rgba(47, 111, 237, .35); font-size: 1.08rem;
  }
  .brand-name { color: white !important; font-weight: 740; letter-spacing: -.02em; }
  .brand-kicker { color: #8fa0bd !important; font-size: .71rem; text-transform: uppercase; letter-spacing: .12em; }
  .runtime-card {
    margin: .25rem; padding: .8rem; border-radius: 10px;
    background: rgba(255,255,255,.055); border: 1px solid rgba(255,255,255,.09);
  }
  .runtime-label { color: #8fa0bd !important; font-size: .67rem; text-transform: uppercase; letter-spacing: .1em; }
  .runtime-value { color: white !important; font-size: .84rem; margin-top: .28rem; }
  .live-dot { display:inline-block; width:7px; height:7px; margin-right:.42rem; border-radius:50%; background:#51d39a; box-shadow:0 0 0 3px rgba(81,211,154,.15); }

  /* Page structure */
  .page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 1.25rem; margin-bottom: 1.45rem; }
  .eyebrow { color: var(--blue); font-size: .7rem; font-weight: 760; letter-spacing: .12em; text-transform: uppercase; margin-bottom: .42rem; }
  .page-header h1 { color: var(--ink); font-size: clamp(1.72rem, 3vw, 2.35rem); letter-spacing: -.045em; line-height: 1.1; margin: 0; }
  .page-header p { color: var(--muted); font-size: .94rem; max-width: 720px; margin: .55rem 0 0; }
  .header-chip { white-space: nowrap; border: 1px solid #dbe2ec; border-radius: 999px; background: white; padding: .52rem .74rem; color: #475467; font-size: .76rem; box-shadow: 0 3px 10px rgba(16,24,40,.03); }
  .header-chip i { display:inline-block; width:7px; height:7px; border-radius:50%; background:var(--green); margin-right:.4rem; }
  .section-title { color: var(--ink); font-size: .99rem; font-weight: 720; letter-spacing: -.015em; margin: 0 0 .9rem; }
  .section-subtitle { color: var(--muted); font-size: .76rem; margin-top: -.65rem; margin-bottom: .8rem; }

  /* Cards and metrics */
  .metric-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: .85rem; margin-bottom: 1.25rem; }
  .metric-card { min-height: 116px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 1rem 1.05rem; box-shadow: var(--shadow); }
  .metric-top { display:flex; justify-content:space-between; align-items:center; gap:.5rem; }
  .metric-label { color: var(--muted); font-size: .76rem; font-weight: 580; }
  .metric-icon { display:grid; place-items:center; width:28px; height:28px; border-radius:8px; background:var(--blue-soft); color:var(--blue); font-size:.76rem; font-weight:800; }
  .metric-value { color: var(--ink); font-size: 1.72rem; font-weight: 760; letter-spacing: -.04em; line-height: 1; margin-top: .8rem; }
  .metric-note { color: #98a2b3; font-size: .68rem; margin-top: .45rem; }
  .panel { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 1.1rem 1.15rem; box-shadow: var(--shadow); margin-bottom: 1rem; }
  .distribution-row { display:grid; grid-template-columns: minmax(90px, 1fr) 2.5fr 34px; align-items:center; gap:.75rem; margin:.78rem 0; }
  .distribution-label { color:#344054; font-size:.78rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .distribution-track { height:7px; background:#edf0f4; border-radius:999px; overflow:hidden; }
  .distribution-fill { height:100%; min-width:3px; background:var(--blue); border-radius:999px; }
  .distribution-value { color:#475467; text-align:right; font-size:.76rem; font-variant-numeric:tabular-nums; }
  .empty-mini { color:#98a2b3; font-size:.8rem; padding:.75rem 0 .25rem; }
  .recent-row { display:grid; grid-template-columns: minmax(112px,1fr) minmax(60px,.75fr) auto; align-items:center; gap:.62rem; padding:.72rem 0; border-top:1px solid #edf0f4; }
  .recent-row:first-of-type { border-top:0; }
  .recent-id { color:#344054; font-size:.7rem; font-weight:700; white-space:nowrap; }
  .recent-meta { color:#98a2b3; font-size:.7rem; margin-top:.13rem; }
  .recent-supplier { color:#475467; font-size:.78rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

  /* Status */
  .status-pill { display:inline-flex; align-items:center; gap:.34rem; width:max-content; border-radius:999px; padding:.28rem .52rem; font-size:.65rem; font-weight:760; letter-spacing:.02em; text-transform:uppercase; }
  .status-pill:before { content:""; width:5px; height:5px; border-radius:50%; background:currentColor; }
  .status-completed, .status-approve { background:var(--green-soft); color:var(--green); }
  .status-pending, .status-pending-review, .status-escalate { background:var(--amber-soft); color:var(--amber); }
  .status-reject { background:var(--red-soft); color:var(--red); }
  .status-request-info { background:var(--blue-soft); color:var(--blue); }

  /* Case detail */
  .case-hero { position:relative; overflow:hidden; border-radius:16px; padding:1.25rem 1.35rem; margin:.25rem 0 1rem; background:linear-gradient(125deg,#111f3c 0%,#172b51 68%,#1e3d72 100%); box-shadow:0 12px 30px rgba(13,24,48,.17); }
  .case-hero:after { content:""; position:absolute; width:220px; height:220px; border-radius:50%; right:-80px; top:-120px; background:rgba(74,133,255,.15); }
  .case-kicker { color:#8fa9d1; font-size:.66rem; letter-spacing:.11em; text-transform:uppercase; }
  .case-title { color:white; font-size:1.3rem; font-weight:720; letter-spacing:-.025em; margin:.3rem 0 .72rem; }
  .case-meta { display:flex; align-items:center; gap:.7rem; flex-wrap:wrap; color:#b8c6db; font-size:.75rem; }
  .case-summary-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:.75rem; margin-bottom:1.1rem; }
  .summary-card { background:white; border:1px solid var(--line); border-radius:12px; padding:.82rem .9rem; }
  .summary-label { color:#98a2b3; font-size:.66rem; text-transform:uppercase; letter-spacing:.08em; }
  .summary-value { color:var(--ink); font-size:.89rem; font-weight:680; margin-top:.32rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .timeline { margin:.55rem 0 1rem; }
  .stage { position:relative; margin-left:.78rem; padding:0 0 1.12rem 1.65rem; border-left:1px solid #dce3ed; }
  .stage:last-child { border-left-color:transparent; padding-bottom:.2rem; }
  .stage-dot { position:absolute; left:-.55rem; top:0; display:grid; place-items:center; width:1.05rem; height:1.05rem; border-radius:50%; background:var(--green); color:white; border:3px solid white; box-shadow:0 0 0 1px #c8d7d0; font-size:.5rem; }
  .stage-dot.warn { background:var(--amber); box-shadow:0 0 0 1px #e5c99f; }
  .stage-title { color:#344054; font-size:.81rem; font-weight:680; line-height:1.25; }
  .stage-detail { color:#98a2b3; font-size:.72rem; margin-top:.2rem; }
  .guardrail { display:flex; align-items:flex-start; gap:.72rem; background:var(--amber-soft); border:1px solid #f0d39f; border-radius:12px; padding:.85rem .95rem; margin:.9rem 0; color:#6e4708; font-size:.78rem; }
  .guardrail-mark { display:grid; place-items:center; width:22px; height:22px; flex:0 0 22px; border-radius:7px; background:#f4d69d; color:#7c4a00; font-weight:800; }
  .discrepancy { border:1px solid var(--line); border-radius:11px; padding:.8rem .9rem; margin:.55rem 0; background:white; }
  .discrepancy-head { display:flex; justify-content:space-between; gap:.75rem; align-items:center; }
  .discrepancy-type { color:#344054; font-size:.79rem; font-weight:700; }
  .discrepancy-copy { color:#667085; font-size:.75rem; line-height:1.5; margin-top:.42rem; }
  .severity-high { color:var(--red); background:var(--red-soft); }
  .severity-medium { color:var(--amber); background:var(--amber-soft); }
  .severity-low { color:var(--blue); background:var(--blue-soft); }
  .severity-chip { border-radius:999px; padding:.22rem .45rem; font-size:.6rem; font-weight:760; text-transform:uppercase; }
  .decision-card { background:white; border:1px solid var(--line); border-radius:12px; padding:.95rem 1rem; margin:.7rem 0; }
  .decision-label { color:#98a2b3; font-size:.66rem; text-transform:uppercase; letter-spacing:.09em; }
  .decision-title { color:var(--ink); font-size:.92rem; font-weight:720; margin:.35rem 0; }
  .decision-copy { color:#667085; font-size:.79rem; line-height:1.55; }
  .request-copy { background:#f6f8fb; border:1px solid #e4e9f1; border-radius:9px; color:#475467; padding:.7rem .8rem; margin-top:.7rem; font: .73rem/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; white-space:pre-wrap; }
  .audit-row { display:grid; grid-template-columns:12px 1fr; gap:.75rem; padding:.1rem 0 .95rem; }
  .audit-node { width:8px; height:8px; border-radius:50%; background:var(--blue); margin-top:.31rem; box-shadow:0 0 0 4px var(--blue-soft); }
  .audit-title { color:#344054; font-size:.79rem; font-weight:680; }
  .audit-meta { color:#98a2b3; font-size:.68rem; margin-top:.15rem; }

  /* Empty states and workflow controls */
  .empty-state { text-align:center; border:1px dashed #cfd7e3; border-radius:14px; padding:2.2rem 1rem; background:rgba(255,255,255,.58); }
  .empty-icon { display:grid; place-items:center; width:42px; height:42px; margin:0 auto .8rem; border-radius:12px; color:var(--blue); background:var(--blue-soft); font-weight:800; }
  .empty-title { color:var(--ink); font-size:.95rem; font-weight:700; }
  .empty-copy { color:var(--muted); font-size:.78rem; margin-top:.35rem; }
  .stepper { display:flex; gap:.45rem; align-items:center; margin:-.2rem 0 1.15rem; }
  .step { display:flex; align-items:center; gap:.38rem; color:#98a2b3; font-size:.7rem; font-weight:650; }
  .step b { display:grid; place-items:center; width:20px; height:20px; border-radius:50%; color:white; background:#98a2b3; font-size:.62rem; }
  .step.active { color:var(--blue); }
  .step.active b { background:var(--blue); }
  .step-line { width:30px; height:1px; background:#d9dfe8; }
  div[data-testid="stVerticalBlockBorderWrapper"] { border-color:var(--line); border-radius:var(--radius); box-shadow:var(--shadow); background:white; }

  /* Streamlit components */
  .stTabs [data-baseweb="tab-list"] { gap:.25rem; border-bottom:1px solid var(--line); }
  .stTabs [data-baseweb="tab"] { color:#667085; height:2.7rem; padding:0 .8rem; font-size:.78rem; }
  .stTabs [aria-selected="true"] { color:var(--blue) !important; }
  .stTabs [data-baseweb="tab-highlight"] { background-color:var(--blue); }
  .stButton > button { border-radius:9px; min-height:2.55rem; font-weight:680; }
  .stButton > button[kind="primary"] { background:var(--blue); border-color:var(--blue); box-shadow:0 5px 14px rgba(47,111,237,.2); }
  .stButton > button[kind="primary"]:hover { background:#245fd3; border-color:#245fd3; }
  [data-baseweb="select"] > div, [data-baseweb="input"] > div, textarea { border-radius:9px !important; }
  [data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:10px; overflow:hidden; }
  [data-testid="stAlert"] { border-radius:10px; }
  summary:focus-visible, button:focus-visible, input:focus-visible, textarea:focus-visible, [role="tab"]:focus-visible { outline:3px solid rgba(47,111,237,.3) !important; outline-offset:2px; }

  @media (max-width: 1050px) {
    .metric-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }
    .case-summary-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  }
  @media (max-width: 700px) {
    .block-container { padding:4.25rem 1rem 3rem; }
    .page-header { display:block; }
    .header-chip { display:inline-flex; margin-top:.85rem; }
    .metric-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
    .metric-card { min-height:105px; }
    .case-summary-grid { grid-template-columns:1fr 1fr; }
    .recent-row { grid-template-columns:1fr auto; }
    .recent-supplier { display:none; }
    .distribution-row { grid-template-columns:90px 1fr 28px; gap:.45rem; }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *:before, *:after { scroll-behavior:auto !important; transition:none !important; animation:none !important; }
  }
</style>
<span class="ui-style-anchor" aria-hidden="true"></span>
"""


@st.cache_resource
def service() -> CaseService:
    return CaseService(create_runtime())


def h(value: object) -> str:
    return escape(str(value), quote=True)


def DecimalDisplay(value: object) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def label_action(action: str) -> str:
    return {
        "APPROVE": "Approve",
        "REJECT": "Reject",
        "REQUEST_INFO": "Request info",
        "ESCALATE": "Escalate",
    }.get(action, action.replace("_", " ").title())


def status_class(value: str) -> str:
    return value.lower().replace("_", "-")


def status_pill(value: str, label: str | None = None) -> str:
    return (
        f'<span class="status-pill status-{h(status_class(value))}">'
        f"{h(label or label_action(value))}</span>"
    )


def page_header(eyebrow: str, title: str, description: str, chip: str) -> None:
    render_html(
        f"""
        <div class="page-header">
          <div>
            <div class="eyebrow">{h(eyebrow)}</div>
            <h1>{h(title)}</h1>
            <p>{h(description)}</p>
          </div>
          <div class="header-chip"><i></i>{h(chip)}</div>
        </div>
        """,
    )


def metric_card(label: str, value: object, note: str, icon: str) -> str:
    return f"""
      <div class="metric-card">
        <div class="metric-top"><span class="metric-label">{h(label)}</span><span class="metric-icon">{h(icon)}</span></div>
        <div class="metric-value">{h(value)}</div>
        <div class="metric-note">{h(note)}</div>
      </div>
    """


def render_distribution(title: str, subtitle: str, values: dict[str, int]) -> None:
    rows = []
    maximum = max(values.values(), default=0)
    for label, value in sorted(values.items(), key=lambda item: item[1], reverse=True):
        width = (value / maximum * 100) if maximum else 0
        rows.append(
            f"""
            <div class="distribution-row">
              <div class="distribution-label" title="{h(label)}">{h(label.replace("_", " ").title())}</div>
              <div class="distribution-track"><div class="distribution-fill" style="width:{width:.1f}%"></div></div>
              <div class="distribution-value">{h(value)}</div>
            </div>
            """
        )
    content = "".join(rows) or '<div class="empty-mini">No data available yet.</div>'
    render_html(
        f'<div class="panel"><div class="section-title">{h(title)}</div>'
        f'<div class="section-subtitle">{h(subtitle)}</div>{content}</div>',
    )


def render_recent_cases(cases: list[dict]) -> None:
    rows = []
    for case in cases[:5]:
        invoice = case.get("invoice", {})
        rows.append(
            f"""
            <div class="recent-row">
              <div>
                <div class="recent-id">{h(case.get("case_id", "Unknown case"))}</div>
                <div class="recent-meta">{h(invoice.get("invoice_id", "No invoice ID"))}</div>
              </div>
              <div class="recent-supplier">{h(invoice.get("supplier_name", "Unknown supplier"))}</div>
              {status_pill(case.get("status", "UNKNOWN"), case.get("status", "Unknown").replace("_", " ").title())}
            </div>
            """
        )
    content = "".join(rows) or '<div class="empty-mini">Processed cases will appear here.</div>'
    render_html(
        '<div class="panel"><div class="section-title">Recent cases</div>'
        '<div class="section-subtitle">Latest workflow activity</div>'
        f"{content}</div>",
    )


def render_discrepancies(state: dict) -> None:
    result = state.get("match_result") or {}
    summary = result.get("summary", "Not yet available")
    render_html(f'<div class="section-subtitle">{h(summary)}</div>')
    discrepancies = result.get("discrepancies", [])
    if not discrepancies:
        st.success("No discrepancies — exact three-way match")
        return
    for item in discrepancies:
        severity = str(item.get("severity", "medium")).lower()
        render_html(
            f"""
            <div class="discrepancy">
              <div class="discrepancy-head">
                <div class="discrepancy-type">{h(str(item.get("type", "exception")).replace("_", " ").title())}</div>
                <span class="severity-chip severity-{h(severity)}">{h(severity)}</span>
              </div>
              <div class="discrepancy-copy">{h(item.get("explanation", "No explanation provided."))}</div>
            </div>
            """,
        )


def stage(title: str, detail: str, warning: bool = False) -> str:
    warning_class = " warn" if warning else ""
    marker = "!" if warning else "✓"
    return f"""
      <div class="stage">
        <span class="stage-dot{warning_class}">{marker}</span>
        <div class="stage-title">{h(title)}</div>
        <div class="stage-detail">{h(detail)}</div>
      </div>
    """


def render_case(case: dict) -> None:
    if not case:
        st.error("The selected case could not be loaded.")
        return

    state = case["state"]
    invoice = case["invoice"]
    case_status = case["status"]
    render_html(
        f"""
        <div class="case-hero">
          <div class="case-kicker">Exception case</div>
          <div class="case-title">{h(case["case_id"])}</div>
          <div class="case-meta">
            {status_pill(case_status, case_status.replace("_", " ").title())}
            <span>{h(invoice["supplier_name"])}</span><span>·</span><span>{h(invoice["invoice_id"])}</span>
          </div>
        </div>
        <div class="case-summary-grid">
          <div class="summary-card"><div class="summary-label">Invoice</div><div class="summary-value">{h(invoice["invoice_id"])}</div></div>
          <div class="summary-card"><div class="summary-label">Supplier</div><div class="summary-value" title="{h(invoice["supplier_name"])}">{h(invoice["supplier_name"])}</div></div>
          <div class="summary-card"><div class="summary-label">Purchase order</div><div class="summary-value">{h(invoice.get("po_number") or "Missing")}</div></div>
          <div class="summary-card"><div class="summary-label">Invoice total</div><div class="summary-value">{h(invoice["currency"])} {h(DecimalDisplay(invoice["total"]))}</div></div>
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
        render_html(f'<div class="timeline">{"".join(timeline)}</div>')
        render_html('<div class="section-title">Match findings</div>')
        render_discrepancies(state)
        policy = state.get("policy_result")
        if policy:
            reasons = "; ".join(policy["reasons"])
            verdict = (
                "Autonomous action permitted" if policy["permitted"] else "Human review required"
            )
            render_html(
                f"""
                <div class="guardrail">
                  <div class="guardrail-mark">◇</div>
                  <div><strong>Deterministic policy · {h(verdict)}</strong><br>{h(reasons)}</div>
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
                <div class="decision-card">
                  <div class="decision-label">Investigation</div>
                  <div class="decision-title">{h(investigation["root_cause"])}</div>
                  <div class="decision-copy"><strong>Evidence</strong> · {h(evidence or "None cited")}</div>
                  {f'<div class="decision-copy"><strong>Missing</strong> · {h("; ".join(missing))}</div>' if missing else ""}
                </div>
                """,
            )
        if state.get("resolution"):
            resolution = state["resolution"]
            request = resolution.get("information_request")
            render_html(
                f"""
                <div class="decision-card">
                  <div class="decision-label">Proposed resolution</div>
                  <div class="decision-title">{status_pill(resolution["action"])}</div>
                  <div class="decision-copy">{h(resolution["rationale"])}</div>
                  {f'<div class="request-copy">{h(request)}</div>' if request else ""}
                </div>
                """,
            )
        if state.get("critique"):
            critique = state["critique"]
            verdict = "Valid" if critique["valid"] else "Revision required"
            issues = "".join(
                f'<div class="decision-copy">• {h(issue)}</div>' for issue in critique["issues"]
            )
            render_html(
                f"""
                <div class="decision-card">
                  <div class="decision-label">Reflexion</div>
                  <div class="decision-title">{h(verdict)}</div>{issues}
                </div>
                """,
            )
        if state.get("outcome"):
            outcome = state["outcome"]
            render_html(
                f"""
                <div class="decision-card">
                  <div class="decision-label">Final outcome · {h(outcome["source"].replace("_", " ").title())}</div>
                  <div class="decision-title">{status_pill(outcome["action"])}</div>
                  <div class="decision-copy">{h(outcome["rationale"])}</div>
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
                <div class="audit-row">
                  <div class="audit-node"></div>
                  <div>
                    <div class="audit-title">{h(event["event_type"].replace("_", " ").title())}</div>
                    <div class="audit-meta">{h(event["timestamp"])} · {h(event["actor"])}</div>
                  </div>
                </div>
                """,
            )
            if event.get("data"):
                with st.expander("Event data"):
                    st.json(event["data"], expanded=False)


def dashboard(app: CaseService) -> None:
    page_header(
        "Operations overview",
        "Exception control center",
        "Monitor throughput, risk routing, and every decision made across the P2P workflow.",
        "Control plane healthy",
    )
    metrics = app.metrics()
    render_html(
        '<div class="metric-grid">'
        + metric_card("Processed", metrics["total"], "All workflow runs", "01")
        + metric_card("Autonomous", f"{metrics['autonomous_rate']:.0%}", "Policy-authorized", "A")
        + metric_card("Human review", f"{metrics['human_review_rate']:.0%}", "Risk-routed", "H")
        + metric_card("Pending", metrics["pending"], "Awaiting decision", "P")
        + metric_card(
            "Avg confidence", f"{metrics['average_confidence']:.0%}", "Across AI proposals", "C"
        )
        + "</div>",
    )
    left, middle, right = st.columns([1, 1, 1.12])
    with left:
        render_distribution("Outcomes", "Final decision mix", metrics.get("actions", {}))
    with middle:
        render_distribution(
            "Exception types",
            "Detected discrepancy mix",
            metrics.get("exception_types", {}),
        )
    with right:
        render_recent_cases(app.list_cases())

    render_html('<div class="section-title">Control architecture</div>')
    architecture = st.columns(3)
    layers = [
        (
            "01 · Evidence",
            "Deterministic checks",
            "Validation, ERP retrieval, and three-way matching create the factual record.",
        ),
        (
            "02 · Reasoning",
            "Bounded AI analysis",
            "Investigation, resolution, and critique run only when an exception exists.",
        ),
        (
            "03 · Authority",
            "Policy-controlled action",
            "Deterministic guardrails either finalize the case or pause it for a reviewer.",
        ),
    ]
    for column, (label, title, copy) in zip(architecture, layers, strict=True):
        with column:
            render_html(
                f"""
                <div class="panel">
                  <div class="decision-label">{h(label)}</div>
                  <div class="decision-title">{h(title)}</div>
                  <div class="decision-copy">{h(copy)}</div>
                </div>
                """,
            )


def process_invoice_page(app: CaseService) -> None:
    page_header(
        "New workflow",
        "Process an invoice",
        "Load a demo or provide invoice JSON, then run the auditable exception workflow.",
        "No external actions",
    )
    render_html(
        """
        <div class="stepper" aria-label="Workflow steps">
          <div class="step active"><b>1</b>Input</div><div class="step-line"></div>
          <div class="step"><b>2</b>Analyze</div><div class="step-line"></div>
          <div class="step"><b>3</b>Resolve</div>
        </div>
        """,
    )
    with st.container(border=True):
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
            "Run exception workflow",
            type="primary",
            disabled=not payload,
            width="stretch",
        ):
            try:
                invoice = app.parse_invoice(payload or "")
                with st.status("Executing auditable workflow…", expanded=True) as status:
                    st.write("Validating invoice and retrieving ERP context")
                    result = app.process_invoice(invoice)
                    status.update(
                        label="Workflow paused for review"
                        if result["status"] == "PENDING_REVIEW"
                        else "Workflow completed",
                        state="complete",
                    )
                st.session_state["selected_case"] = result["case_id"]
                if result["status"] == "PENDING_REVIEW":
                    st.warning(f"{result['case_id']} is persisted and waiting for a reviewer.")
                else:
                    st.success(f"{result['case_id']} completed autonomously.")
                render_case(app.get_case(result["case_id"]) or {})
            except ValidationError as exc:
                st.error("Invoice validation failed. Correct the highlighted structure or totals.")
                st.code(str(exc), language=None)
            except Exception as exc:
                st.error(f"Workflow could not be completed: {type(exc).__name__}: {exc}")


def cases_page(app: CaseService) -> None:
    page_header(
        "Case archive",
        "Cases",
        "Inspect the evidence, reasoning, policy outcome, and immutable audit history for every run.",
        "Auditable by design",
    )
    cases = app.list_cases()
    if not cases:
        render_html(
            """
            <div class="empty-state"><div class="empty-icon">◇</div>
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
            f"{item['case_id']} · {item['invoice_id']} · {item['status'].replace('_', ' ').title()}"
        ),
    )
    render_case(app.get_case(selected["case_id"]) or selected)


def review_page(app: CaseService) -> None:
    page_header(
        "Human-in-the-loop",
        "Review queue",
        "Resolve material or uncertain exceptions that deterministic policy will not authorize.",
        "Reviewer authority",
    )
    pending = app.list_cases("PENDING_REVIEW")
    if not pending:
        render_html(
            """
            <div class="empty-state"><div class="empty-icon">✓</div>
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
            f"{item['invoice']['currency']} {DecimalDisplay(item['invoice']['total'])}"
        ),
    )
    case = app.get_case(selected["case_id"]) or selected
    render_case(case)
    st.divider()
    render_html('<div class="section-title">Reviewer decision</div>')
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
                result = app.review_case(
                    selected["case_id"],
                    HumanReview(reviewer=reviewer, action=action, comment=comment or None),
                )
                st.success(f"Workflow resumed and completed: {result['state']['outcome'].action}")
                st.rerun()
            except Exception as exc:
                st.error(f"Review could not be applied: {type(exc).__name__}: {exc}")


def main() -> None:
    app = service()
    render_html(UI_STYLES)
    with st.sidebar:
        render_html(
            """
            <div class="brand">
              <div class="brand-mark">◇</div>
              <div><div class="brand-name">P2P Control</div><div class="brand-kicker">Exception operations</div></div>
            </div>
            """,
        )
        page = st.radio(
            "Navigate",
            ["Overview", "Process invoice", "Cases", "Review queue"],
            label_visibility="collapsed",
        )
        st.divider()
        mode = "Deterministic demo" if app.runtime.demo_mode else "OpenAI live"
        render_html(
            f"""
            <div class="runtime-card">
              <div class="runtime-label">Runtime</div>
              <div class="runtime-value"><span class="live-dot"></span>{h(mode)}</div>
            </div>
            """,
        )
        st.caption("Decision-support simulation. No payments or external messages are executed.")
    {
        "Overview": dashboard,
        "Process invoice": process_invoice_page,
        "Cases": cases_page,
        "Review queue": review_page,
    }[page](app)


if __name__ == "__main__":
    main()
