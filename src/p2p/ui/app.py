from __future__ import annotations

import os
from datetime import UTC, datetime
from html import escape
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from p2p.application.service import CaseService, create_runtime
from p2p.domain.models import DecisionType, HumanReview, Invoice

NAV_ITEMS = ["Overview", "Process Invoice", "Cases", "Review Queue"]


def render_html(markup: str) -> None:
    """Render generated HTML without Markdown interpreting indented siblings."""
    st.html("".join(line.strip() for line in markup.splitlines()))


st.set_page_config(
    page_title="P2P Exception Control Center",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="auto",
)

UI_STYLES = """
<style>
  :root {
    --ink: #0f172a;
    --muted: #64748b;
    --line: #e2e8f0;
    --surface: #ffffff;
    --surface-subtle: #f8fafc;
    --navy: #0e1525;
    --blue: #315bea;
    --blue-soft: #eef2ff;
    --green: #16854f;
    --green-soft: #edf9f1;
    --amber: #b46a08;
    --amber-soft: #fff7e8;
    --red: #c23b4a;
    --red-soft: #fff1f2;
    --radius: 14px;
    --shadow: 0 1px 4px rgba(15, 23, 42, .07);
  }

  html, body, [class*="css"] {
    color: var(--ink);
    font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }
  .stApp { background: #f0f2f6; }
  [data-testid="stHeader"] { background: transparent; }
  .block-container { max-width: 1480px; padding: 3.75rem 2rem 4rem; }

  /* Sidebar */
  [data-testid="stSidebar"] {
    width: 240px !important; min-width: 240px !important; max-width: 240px !important;
    background: var(--navy); border-right: 0;
  }
  [data-testid="stSidebar"] > div:first-child { padding: 1.5rem 1rem; }
  [data-testid="stSidebar"] * { color: #dce4f2; }
  [data-testid="stSidebar"] hr { border-color: rgba(255,255,255,.11); }
  [data-testid="stSidebar"] [data-testid="stRadio"] label {
    min-height: 2.5rem; padding: .55rem .72rem;
    border-radius: 8px; font-size: .78rem; font-weight: 590;
    transition: background .15s ease, color .15s ease;
  }
  [data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
    background: rgba(255,255,255,.07);
  }
  [data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) {
    background: #315bea;
    color: white;
  }
  [data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) * {
    color: white !important;
  }
  [data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] > label > div:first-child {
    display: none;
  }
  .brand { display:flex; gap:.62rem; align-items:flex-start; margin:0 .28rem 2.25rem; }
  .brand-mark {
    display:grid; place-items:center; width:22px; height:22px; flex:0 0 22px;
    border:1px solid #7698ff; border-radius:7px; color:#8fa9ff !important;
    font-size:.72rem; margin-top:.08rem;
  }
  .brand-name { color:white !important; font-size:.92rem; font-weight:740; letter-spacing:-.025em; line-height:1.2; }
  .brand-kicker { color:#8c9ab4 !important; font-size:.61rem; margin-top:.15rem; }
  .runtime-card {
    position:fixed; left:16px; bottom:18px; width:208px;
    padding:.85rem; border-radius:10px;
    background:#151e32; border:1px solid rgba(255,255,255,.08);
  }
  .runtime-label { color:#8c9ab4 !important; font-size:.58rem; font-weight:700; text-transform:uppercase; letter-spacing:.13em; }
  .runtime-value { color:white !important; font-size:.72rem; font-weight:650; margin:.4rem 0 .65rem; }
  .runtime-grid { display:grid; grid-template-columns:auto 1fr; gap:.32rem .55rem; font-size:.61rem; }
  .runtime-grid span:nth-child(odd) { color:#8290aa !important; }
  .runtime-grid span:nth-child(even) { color:#dbe4f3 !important; text-align:right; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .live-dot { display:inline-block; width:6px; height:6px; margin-right:.4rem; border-radius:50%; background:#51d39a; box-shadow:0 0 0 3px rgba(81,211,154,.12); }
  .st-key-mobile-navigation { display:none; }

  /* Page structure */
  .topbar { display:flex; align-items:center; justify-content:space-between; gap:1rem; min-height:2.1rem; margin:-.65rem 0 1.35rem; }
  .breadcrumb { color:#94a3b8; font-size:.67rem; font-weight:600; }
  .breadcrumb strong { color:#475569; font-weight:700; }
  .topbar-right { display:flex; align-items:center; gap:1rem; }
  .system-health { display:inline-flex; align-items:center; gap:.42rem; color:var(--green); font-size:.62rem; font-weight:760; letter-spacing:.08em; text-transform:uppercase; }
  .system-health i { width:6px; height:6px; border-radius:50%; background:#2fb36b; box-shadow:0 0 0 4px #e7f7ed; }
  .topbar-update { color:#94a3b8; font-size:.62rem; }
  .operator-avatar { display:grid; place-items:center; width:27px; height:27px; border-radius:50%; background:#111827; color:white; font-size:.61rem; font-weight:760; }
  .page-header { display:flex; align-items:flex-end; justify-content:space-between; gap:1.25rem; margin-bottom:1.5rem; }
  .eyebrow { color:var(--blue); font-size:.61rem; font-weight:760; letter-spacing:.14em; text-transform:uppercase; margin-bottom:.38rem; }
  .page-header h1 { color:var(--ink); font-size:clamp(1.72rem,3vw,2rem); letter-spacing:-.04em; line-height:1.14; margin:0; }
  .page-header p { color:var(--muted); font-size:.78rem; max-width:720px; margin:.38rem 0 0; }
  .section-title { color:var(--ink); font-size:.93rem; font-weight:720; letter-spacing:-.015em; margin:0 0 .9rem; }
  .section-subtitle { color: var(--muted); font-size: .76rem; margin-top: -.65rem; margin-bottom: .8rem; }

  /* Cards and metrics */
  .metric-grid { display:flex; gap:1rem; margin-bottom:1.5rem; overflow-x:auto; padding-bottom:.2rem; scrollbar-width:thin; scroll-snap-type:x proximity; }
  .metric-card { min-width:176px; min-height:116px; flex:1 1 0; scroll-snap-align:start; background:var(--surface); border:1px solid var(--line); border-radius:var(--radius); padding:1rem; box-shadow:var(--shadow); }
  .metric-top { display:flex; justify-content:space-between; align-items:center; gap:.5rem; }
  .metric-label { color:#94a3b8; font-size:.62rem; font-weight:720; letter-spacing:.08em; text-transform:uppercase; }
  .metric-icon { color:var(--blue); font-size:.82rem; font-weight:800; }
  .metric-value { color:var(--ink); font-size:1.7rem; font-weight:760; letter-spacing:-.045em; line-height:1; margin-top:.72rem; }
  .metric-note { color:#94a3b8; font-size:.64rem; margin-top:.44rem; }
  .metric-note.positive { color:var(--green); font-weight:650; }
  .panel { background:var(--surface); border:1px solid var(--line); border-radius:var(--radius); padding:1.2rem; box-shadow:var(--shadow); margin-bottom:1rem; }
  .panel-kicker { color:#94a3b8; font-size:.61rem; font-weight:760; letter-spacing:.13em; text-transform:uppercase; margin-bottom:1.05rem; }
  .dashboard-grid { display:grid; grid-template-columns:1fr 1fr; gap:1.5rem; margin-bottom:1.5rem; }
  .dashboard-grid .panel { min-height:320px; margin:0; }
  .stacked-bar { display:flex; height:9px; overflow:hidden; border-radius:999px; background:#eef2f6; margin:.25rem 0 1rem; }
  .stacked-segment { min-width:3px; height:100%; }
  .outcome-row { display:grid; grid-template-columns:minmax(108px,1fr) 2fr auto; gap:.75rem; align-items:center; min-height:2.1rem; }
  .outcome-name { display:flex; align-items:center; gap:.48rem; color:#334155; font-size:.72rem; }
  .outcome-dot { width:7px; height:7px; flex:0 0 7px; border-radius:50%; }
  .outcome-track { height:6px; overflow:hidden; border-radius:999px; background:#f1f5f9; }
  .outcome-fill { height:100%; border-radius:999px; }
  .outcome-value { color:#334155; font-size:.69rem; font-variant-numeric:tabular-nums; white-space:nowrap; }
  .outcome-value em { color:#94a3b8; font-style:normal; }
  .breakdown-row { display:flex; justify-content:space-between; align-items:center; gap:1rem; min-height:2.55rem; border-bottom:1px solid #eef2f6; color:#334155; font-size:.72rem; }
  .breakdown-row:last-child { border-bottom:0; }
  .count-chip { display:grid; place-items:center; min-width:26px; height:22px; padding:0 .36rem; border-radius:6px; background:var(--blue-soft); color:var(--blue); font-size:.65rem; font-weight:760; }
  .case-table-wrap { overflow-x:auto; }
  .case-table { width:100%; border-collapse:collapse; table-layout:fixed; }
  .case-table th { color:#94a3b8; font-size:.58rem; font-weight:760; letter-spacing:.09em; text-align:left; text-transform:uppercase; padding:0 .55rem .7rem; border-bottom:1px solid var(--line); }
  .case-table td { color:#475569; font-size:.67rem; padding:.67rem .55rem; border-bottom:1px solid #eef2f6; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .case-table tbody tr:last-child td { border-bottom:0; }
  .case-table .case-link { color:var(--blue); font-weight:720; }
  .case-table .money { color:#0f172a; font-variant-numeric:tabular-nums; }
  .case-table .status-pill { transform:scale(.9); transform-origin:left center; }
  .architecture-label { color:#94a3b8; font-size:.61rem; font-weight:760; letter-spacing:.13em; text-transform:uppercase; margin:.1rem 0 .8rem; }
  .architecture-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:1rem; }
  .architecture-grid .panel { min-height:145px; margin:0; }
  .architecture-icon { display:grid; place-items:center; width:28px; height:28px; margin-bottom:.65rem; border-radius:8px; background:var(--blue-soft); color:var(--blue); font-size:.72rem; font-weight:800; }
  .architecture-meta { color:var(--blue); font-size:.65rem; font-weight:700; margin-top:.7rem; }
  .empty-mini { color:#98a2b3; font-size:.8rem; padding:.75rem 0 .25rem; }

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
  .st-key-invoice-workflow-stepper { margin:-.25rem 0 1.1rem; }
  .st-key-invoice-workflow-stepper [data-testid="stRadio"] > label { display:none; }
  .st-key-invoice-workflow-stepper div[role="radiogroup"] {
    display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:.55rem;
  }
  .st-key-invoice-workflow-stepper div[role="radiogroup"] > label {
    display:flex; justify-content:center; min-height:2.55rem; padding:.58rem .75rem;
    border:1px solid var(--line); border-radius:9px; background:white;
    color:#64748b; font-size:.7rem; font-weight:700;
  }
  .st-key-invoice-workflow-stepper div[role="radiogroup"] > label > div:first-child { display:none; }
  .st-key-invoice-workflow-stepper div[role="radiogroup"] > label:has(input:checked) {
    border-color:var(--blue); background:var(--blue-soft); color:var(--blue);
    box-shadow:inset 0 0 0 1px rgba(49,91,234,.12);
  }
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
    .dashboard-grid { grid-template-columns:1fr; }
    .case-summary-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  }
  @media (max-width: 700px) {
    [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] { display:none !important; }
    .block-container { padding:3.65rem .75rem 3rem; }
    .st-key-mobile-navigation { display:block; margin:-.35rem 0 .9rem; }
    .st-key-mobile-navigation [data-testid="stRadio"] > label { display:none; }
    .st-key-mobile-navigation div[role="radiogroup"] { display:flex; flex-wrap:nowrap; gap:.35rem; overflow-x:auto; padding:.15rem 0 .25rem; scrollbar-width:none; }
    .st-key-mobile-navigation div[role="radiogroup"]::-webkit-scrollbar { display:none; }
    .st-key-mobile-navigation div[role="radiogroup"] > label { flex:0 0 auto; min-height:2rem; padding:.4rem .68rem; border:1px solid var(--line); border-radius:8px; background:white; color:#64748b; font-size:.68rem; font-weight:650; }
    .st-key-mobile-navigation div[role="radiogroup"] > label > div:first-child { display:none; }
    .st-key-mobile-navigation div[role="radiogroup"] > label:has(input:checked) { border-color:var(--blue); background:var(--blue); color:white; }
    .st-key-mobile-navigation div[role="radiogroup"] > label:has(input:checked) * { color:white !important; }
    .topbar { margin-bottom:1rem; }
    .breadcrumb, .topbar-update { display:none; }
    .topbar-right { width:100%; justify-content:space-between; }
    .page-header { display:block; }
    .page-header h1 { max-width:330px; }
    .metric-grid { margin-right:-.75rem; padding-right:.75rem; }
    .metric-card { min-width:176px; min-height:105px; }
    .dashboard-grid { gap:1rem; }
    .dashboard-grid .panel { min-height:auto; }
    .outcome-row { grid-template-columns:105px 1fr auto; gap:.45rem; }
    .st-key-invoice-workflow-stepper div[role="radiogroup"] { gap:.35rem; }
    .st-key-invoice-workflow-stepper div[role="radiogroup"] > label { padding:.5rem .35rem; }
    .architecture-grid { grid-template-columns:1fr; }
    .case-table { min-width:520px; }
    .case-table th:nth-child(4), .case-table td:nth-child(4),
    .case-table th:nth-child(5), .case-table td:nth-child(5),
    .case-table th:nth-child(6), .case-table td:nth-child(6) { display:none; }
    .case-summary-grid { grid-template-columns:1fr 1fr; }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *:before, *:after { scroll-behavior:auto !important; transition:none !important; animation:none !important; }
  }
</style>
<span class="ui-style-anchor" aria-hidden="true"></span>
"""


def sync_navigation(source: str, target: str) -> None:
    selected = st.session_state[source]
    st.session_state["active_page"] = selected
    st.session_state[target] = selected


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


def top_bar(page: str) -> None:
    render_html(
        f"""
        <div class="topbar">
          <div class="breadcrumb">Operations / <strong>{h(page)}</strong></div>
          <div class="topbar-right">
            <span class="system-health"><i></i>System healthy</span>
            <span class="topbar-update">Local control plane · ready</span>
            <span class="operator-avatar" aria-label="P2P operator">P2P</span>
          </div>
        </div>
        """,
    )


def page_header(eyebrow: str, title: str, description: str) -> None:
    render_html(
        f"""
        <div class="page-header">
          <div>
            <div class="eyebrow">{h(eyebrow)}</div>
            <h1>{h(title)}</h1>
            <p>{h(description)}</p>
          </div>
        </div>
        """,
    )


def metric_card(
    label: str,
    value: object,
    note: str,
    icon: str,
    note_tone: str = "",
) -> str:
    return f"""
      <div class="metric-card">
        <div class="metric-top"><span class="metric-label">{h(label)}</span><span class="metric-icon">{h(icon)}</span></div>
        <div class="metric-value">{h(value)}</div>
        <div class="metric-note {h(note_tone)}">{h(note)}</div>
      </div>
    """


def render_dashboard_analysis(metrics: dict) -> None:
    palette = {
        "APPROVE": ("#315bea", "Approved"),
        "REJECT": ("#dc3545", "Rejected"),
        "REQUEST_INFO": ("#ee9b18", "Information requested"),
        "ESCALATE": ("#94a3b8", "Escalated"),
        "UNKNOWN": ("#cbd5e1", "Other"),
    }
    actions = metrics.get("actions", {})
    total_actions = sum(actions.values())
    segments = []
    outcome_rows = []
    for action, count in sorted(actions.items(), key=lambda item: item[1], reverse=True):
        color, label = palette.get(action, palette["UNKNOWN"])
        percent = count / total_actions * 100 if total_actions else 0
        segments.append(
            f'<span class="stacked-segment" style="width:{percent:.1f}%;background:{color}"></span>'
        )
        outcome_rows.append(
            f"""
            <div class="outcome-row">
              <div class="outcome-name"><i class="outcome-dot" style="background:{color}"></i>{h(label)}</div>
              <div class="outcome-track"><div class="outcome-fill" style="width:{percent:.1f}%;background:{color}"></div></div>
              <div class="outcome-value">{count} <em>({percent:.0f}%)</em></div>
            </div>
            """
        )
    if not outcome_rows:
        outcome_rows.append('<div class="empty-mini">No finalized outcomes yet.</div>')

    exception_rows = []
    for exception, count in sorted(
        metrics.get("exception_types", {}).items(),
        key=lambda item: item[1],
        reverse=True,
    )[:7]:
        exception_rows.append(
            f"""
            <div class="breakdown-row">
              <span>{h(exception.replace("_", " ").title())}</span>
              <span class="count-chip">{h(count)}</span>
            </div>
            """
        )
    if not exception_rows:
        exception_rows.append('<div class="empty-mini">No exceptions detected yet.</div>')

    render_html(
        f"""
        <div class="dashboard-grid">
          <section class="panel" aria-label="Resolution outcomes">
            <div class="panel-kicker">Resolution outcomes</div>
            <div class="stacked-bar">{''.join(segments)}</div>
            {''.join(outcome_rows)}
          </section>
          <section class="panel" aria-label="Exception breakdown">
            <div class="panel-kicker">Exception breakdown</div>
            {''.join(exception_rows)}
          </section>
        </div>
        """,
    )


def relative_age(value: str | None) -> str:
    if not value:
        return "—"
    try:
        updated = datetime.fromisoformat(value)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        seconds = max(0, int((datetime.now(UTC) - updated).total_seconds()))
    except (TypeError, ValueError):
        return "—"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def case_status(case: dict) -> tuple[str, str, str]:
    if case.get("status") == "PENDING_REVIEW":
        return "PENDING_REVIEW", "Human review", "Review queue"
    outcome = case.get("state", {}).get("outcome", {})
    action = outcome.get("action", "COMPLETED")
    source = outcome.get("source", "AUTONOMOUS")
    if action == "APPROVE":
        label = "Auto-resolved" if source == "AUTONOMOUS" else "Approved"
    elif action == "REJECT":
        label = "Blocked"
    else:
        label = label_action(action)
    assigned = "Human reviewer" if str(source).startswith("HUMAN") else "System"
    return action, label, assigned


def render_recent_cases(cases: list[dict]) -> None:
    rows = []
    for case in cases[:6]:
        invoice = case.get("invoice", {})
        discrepancies = case.get("state", {}).get("match_result", {}).get("discrepancies", [])
        exception = (
            discrepancies[0].get("type", "Exception").replace("_", " ").title()
            if discrepancies
            else "Exact match"
        )
        status_value, status_label, assigned = case_status(case)
        rows.append(
            f"""
            <tr>
              <td class="case-link">{h(case.get("case_id", "Unknown"))}</td>
              <td>{h(invoice.get("supplier_name", "Unknown supplier"))}</td>
              <td class="money">{h(invoice.get("currency", ""))} {h(DecimalDisplay(invoice.get("total", 0)))}</td>
              <td>{h(exception)}</td>
              <td>{status_pill(status_value, status_label)}</td>
              <td>{h(assigned)}</td>
              <td>{h(relative_age(case.get("updated_at")))}</td>
            </tr>
            """,
        )
    if not rows:
        rows.append('<tr><td colspan="7" class="empty-mini">Processed cases will appear here.</td></tr>')
    render_html(
        f"""
        <section class="panel" aria-label="Recent cases">
          <div class="panel-kicker">Recent cases</div>
          <div class="case-table-wrap">
            <table class="case-table">
              <colgroup><col style="width:17%"><col style="width:18%"><col style="width:15%"><col style="width:17%"><col style="width:15%"><col style="width:12%"><col style="width:6%"></colgroup>
              <thead><tr><th>Case ID</th><th>Vendor</th><th>Amount</th><th>Type</th><th>Status</th><th>Assigned</th><th>Age</th></tr></thead>
              <tbody>{''.join(rows)}</tbody>
            </table>
          </div>
        </section>
        """,
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
        "Operations center",
        "Autonomous P2P Exception Handler",
        "AI-assisted invoice exception detection, investigation, and controlled resolution.",
    )
    metrics = app.metrics()
    cases = app.list_cases()
    escalated = sum(
        1
        for case in cases
        if case.get("state", {}).get("resolution", {}).get("action") == "ESCALATE"
    )
    render_html(
        '<div class="metric-grid">'
        + metric_card("Invoices processed", metrics["total"], "All workflow runs", "▣")
        + metric_card(
            "Auto-resolved",
            f"{metrics['autonomous_rate']:.1%}",
            "Policy-authorized outcomes",
            "✓",
            "positive",
        )
        + metric_card("Exceptions open", metrics["pending"], "Awaiting a reviewer", "△")
        + metric_card(
            "Avg confidence",
            f"{metrics['average_confidence']:.0%}",
            "Across AI proposals",
            "◎",
        )
        + metric_card("Escalated", escalated, "Model or policy escalation", "↗")
        + "</div>",
    )
    render_dashboard_analysis(metrics)
    render_recent_cases(cases)
    render_html(
        """
        <div class="architecture-label">Control architecture</div>
        <div class="architecture-grid">
          <article class="panel">
            <div class="architecture-icon">▤</div>
            <div class="decision-title">Evidence Engine</div>
            <div class="decision-copy">Validated invoices, purchase orders, receipts, supplier records, and duplicate history establish the factual record.</div>
            <div class="architecture-meta">Deterministic inputs</div>
          </article>
          <article class="panel">
            <div class="architecture-icon">⌘</div>
            <div class="decision-title">Reasoning Engine</div>
            <div class="decision-copy">Investigation, resolution, and critique use typed outputs inside a bounded revision loop.</div>
            <div class="architecture-meta">3 structured AI roles</div>
          </article>
          <article class="panel">
            <div class="architecture-icon">◇</div>
            <div class="decision-title">Authority Framework</div>
            <div class="decision-copy">Deterministic policy either finalizes the case or pauses the workflow for a human reviewer.</div>
            <div class="architecture-meta">Human-in-the-loop controls</div>
          </article>
        </div>
        """,
    )


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


def navigate_to_page(page: str) -> None:
    st.session_state["active_page"] = page
    st.session_state["desktop_navigation"] = page
    st.session_state["mobile_navigation"] = page


def render_validated_invoice(invoice: Invoice) -> None:
    render_html(
        f"""
        <div class="case-summary-grid">
          <div class="summary-card"><div class="summary-label">Invoice</div><div class="summary-value">{h(invoice.invoice_id)}</div></div>
          <div class="summary-card"><div class="summary-label">Supplier</div><div class="summary-value" title="{h(invoice.supplier_name)}">{h(invoice.supplier_name)}</div></div>
          <div class="summary-card"><div class="summary-label">Purchase order</div><div class="summary-value">{h(invoice.po_number or "Missing")}</div></div>
          <div class="summary-card"><div class="summary-label">Invoice total</div><div class="summary-value">{h(invoice.currency)} {h(DecimalDisplay(invoice.total))}</div></div>
        </div>
        """,
    )


def process_invoice_page(app: CaseService) -> None:
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
                    st.error("Invoice validation failed. Correct the highlighted structure or totals.")
                    st.code(str(exc), language=None)
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
                        st.error(f"Workflow could not be completed: {type(exc).__name__}: {exc}")
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
    if st.session_state.get("invoice_workflow_status") == "PENDING_REVIEW":
        st.warning(f"{case_id} is persisted and waiting for a reviewer.")
    else:
        st.success(f"{case_id} completed autonomously.")
    actions = st.columns(2)
    with actions[0]:
        st.button(
            "Process another invoice",
            width="stretch",
            on_click=reset_invoice_workflow,
        )
    with actions[1]:
        destination = (
            "Review Queue"
            if st.session_state.get("invoice_workflow_status") == "PENDING_REVIEW"
            else "Cases"
        )
        st.button(
            f"Open {destination.lower()}",
            type="primary",
            width="stretch",
            on_click=navigate_to_page,
            args=(destination,),
        )
    render_case(case)


def cases_page(app: CaseService) -> None:
    page_header(
        "Case archive",
        "Cases",
        "Inspect the evidence, reasoning, policy outcome, and immutable audit history for every run.",
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
    active_page = st.session_state.setdefault("active_page", NAV_ITEMS[0])
    st.session_state.setdefault("desktop_navigation", active_page)
    st.session_state.setdefault("mobile_navigation", active_page)
    with st.sidebar:
        render_html(
            """
            <div class="brand">
              <div class="brand-mark">◇</div>
              <div><div class="brand-name">P2P Control</div><div class="brand-kicker">Exception operations</div></div>
            </div>
            """,
        )
        st.radio(
            "Navigate",
            NAV_ITEMS,
            key="desktop_navigation",
            on_change=sync_navigation,
            args=("desktop_navigation", "mobile_navigation"),
            label_visibility="collapsed",
        )
        mode = "Demo" if app.runtime.demo_mode else "Live"
        model = "Mock provider" if app.runtime.demo_mode else os.getenv("OPENAI_MODEL", "Configured")
        render_html(
            f"""
            <div class="runtime-card">
              <div class="runtime-label">Runtime status</div>
              <div class="runtime-value"><span class="live-dot"></span>P2P-Agent v1.0</div>
              <div class="runtime-grid">
                <span>Mode</span><span>{h(mode)}</span>
                <span>Model</span><span title="{h(model)}">{h(model)}</span>
                <span>Policy</span><span>YAML controls</span>
                <span>State</span><span>Ready</span>
              </div>
            </div>
            """,
        )
    with st.container(key="mobile-navigation"):
        st.radio(
            "Mobile navigation",
            NAV_ITEMS,
            key="mobile_navigation",
            on_change=sync_navigation,
            args=("mobile_navigation", "desktop_navigation"),
            horizontal=True,
            label_visibility="collapsed",
        )
    page = st.session_state["active_page"]
    top_bar(page)
    {
        "Overview": dashboard,
        "Process Invoice": process_invoice_page,
        "Cases": cases_page,
        "Review Queue": review_page,
    }[page](app)


if __name__ == "__main__":
    main()
