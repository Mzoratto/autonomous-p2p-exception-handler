from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from p2p.application.service import CaseService, create_runtime
from p2p.domain.models import DecisionType, HumanReview, Invoice
from p2p.ui.identity import current_reviewer, initials
from p2p.ui.viewmodels import (
    ACTION_STATUS_KEYS,
    OUTCOME_LABELS,
    display_status,
    format_money,
    label_action,
    label_event,
    label_exception,
    validation_rows,
)

NAV_ITEMS = ["Overview", "Process Invoice", "Cases", "Review Queue"]


def render_html(markup: str) -> None:
    st.html(markup)


st.set_page_config(
    page_title="P2P Exception Control Center",
    page_icon=":material/fact_check:",
    layout="wide",
    initial_sidebar_state="auto",
)

UI_STYLES = """
<style>
  :root {
    --ink: #131a26;
    --ink-2: #475264;
    --muted: #5b6576;
    --page: #f1f3f6;
    --surface: #ffffff;
    --surface-subtle: #f8f9fb;
    --line: #e2e5ea;
    --line-soft: #edf0f3;
    --blue: #2f6fed;
    --link: #1d4fc4;
    --blue-soft: #e8effd;
    --navy: #0e1525;
    --navy-card: #151e32;
    --sidebar-text: #dce4f2;
    --sidebar-muted: #a7b3c9;
    --sev-critical-fg: #a1122b; --sev-critical-bg: #fce7eb; --sev-critical-bar: #d23a50;
    --sev-material-fg: #a6380f; --sev-material-bg: #fcebe3; --sev-material-bar: #e0703a;
    --sev-warning-fg: #7f4d00; --sev-warning-bg: #fcf1dc; --sev-warning-bar: #d9a032;
    --sev-info-fg: #33507a; --sev-info-bg: #eaf0f8; --sev-info-bar: #7f9cc4;
    --sev-none-fg: #475264; --sev-none-bg: #eef0f3;
    --status-review-fg: #7f4d00; --status-review-bg: #fcf1dc; --status-review-dot: #c98512;
    --status-approved-fg: #14653f; --status-approved-bg: #e3f3ea; --status-approved-dot: #1f9a5e;
    --status-rejected-fg: #a1122b; --status-rejected-bg: #fce7eb; --status-rejected-dot: #d23a50;
    --status-request-info-fg: #1d4fc4; --status-request-info-bg: #e8effd; --status-request-info-dot: #2f6fed;
    --status-processing-fg: #475264; --status-processing-bg: #eef0f3; --status-processing-dot: #5b6576;
    --radius-card: 12px;
    --radius-control: 8px;
    --radius-chip: 999px;
    --mono: "IBM Plex Mono", ui-monospace, monospace;
  }

  html, body, [class*="css"] { color: var(--ink); }
  .block-container { max-width: 1480px; padding: 3.75rem 2rem 4rem; }
  .p2p-icon { font-family: 'Material Symbols Rounded'; font-size: 20px; line-height: 1; font-feature-settings: 'liga'; font-weight: normal; font-style: normal; letter-spacing: normal; text-transform: none; white-space: nowrap; }

  /* Sidebar */
  [data-testid="stSidebar"] {
    width: 240px !important; min-width: 240px !important; max-width: 240px !important;
    background: var(--navy); border-right: 0;
  }
  [data-testid="stSidebar"] > div:first-child { padding: 1.5rem 1rem; }
  [data-testid="stSidebar"] * { color: var(--sidebar-text); }
  [data-testid="stSidebar"] hr { border-color: rgba(255,255,255,.11); }
  [data-testid="stSidebar"] [data-testid="stRadio"] label {
    min-height: 40px; padding: 8px 12px;
    border-radius: var(--radius-control); font-size: 14px; font-weight: 500;
    transition: background .15s ease, color .15s ease;
  }
  [data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
    background: rgba(255,255,255,.07);
  }
  [data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) {
    background: var(--blue);
    color: #ffffff;
  }
  [data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) * {
    color: #ffffff !important;
  }
  [data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] > label > div:first-child {
    display: none;
  }
  .brand { display:flex; gap:10px; align-items:center; margin:0 4px 32px; }
  .brand-mark {
    display:grid; place-items:center; width:34px; height:34px; flex:0 0 34px;
    border:1.5px solid #5d8bf4; border-radius:var(--radius-control); color:#cfdcff !important;
    font-size:12px; font-weight:700; letter-spacing:.02em;
  }
  .brand-name { color:#ffffff !important; font-size:16px; font-weight:700; letter-spacing:-.01em; line-height:1.2; }
  .brand-kicker { color:var(--sidebar-muted) !important; font-size:12px; margin-top:2px; }
  .runtime-card {
    margin-top:24px; padding:14px; border-radius:10px;
    background:var(--navy-card); border:1px solid rgba(255,255,255,.08);
  }
  .runtime-label { color:var(--sidebar-muted) !important; font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.1em; margin-bottom:10px; }
  .runtime-row { display:flex; justify-content:space-between; gap:10px; font-size:13px; line-height:1.9; }
  .runtime-row span:first-child { color:var(--sidebar-muted) !important; white-space:nowrap; }
  .runtime-row span:last-child { color:var(--sidebar-text) !important; min-width:0; text-align:right; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .st-key-mobile-navigation { display:none; }

  /* Page structure */
  .topbar { display:flex; align-items:center; justify-content:space-between; gap:16px; min-height:34px; margin:-10px 0 22px; }
  .breadcrumb { color:var(--muted); font-size:13px; font-weight:500; }
  .breadcrumb strong { color:var(--ink); font-weight:600; }
  .topbar-right { display:flex; align-items:center; gap:16px; }
  .operator-avatar { display:grid; place-items:center; width:32px; height:32px; border-radius:50%; background:var(--blue-soft); color:var(--link); font-size:12px; font-weight:600; }
  .page-header { display:flex; align-items:flex-end; justify-content:space-between; gap:20px; margin-bottom:24px; }
  .eyebrow { color:var(--link); font-size:12px; font-weight:600; letter-spacing:.1em; text-transform:uppercase; margin-bottom:4px; }
  .page-header h1 { color:var(--ink); font-size:28px; font-weight:700; letter-spacing:-.02em; line-height:1.2; margin:0; padding:0; }
  .page-header p { color:var(--muted); font-size:14px; max-width:720px; margin:6px 0 0; }
  .section-title { color:var(--ink); font-size:16px; font-weight:600; margin:0 0 14px; }
  .section-subtitle { color:var(--muted); font-size:13px; margin-top:-10px; margin-bottom:12px; }

  /* Cards and metrics */
  .metric-grid { display:flex; gap:16px; margin-bottom:24px; overflow-x:auto; padding-bottom:3px; scrollbar-width:thin; scroll-snap-type:x proximity; }
  .metric-card { min-width:176px; min-height:116px; flex:1 1 0; scroll-snap-align:start; background:var(--surface); border:1px solid var(--line); border-radius:var(--radius-card); padding:16px; }
  .metric-top { display:flex; justify-content:space-between; align-items:center; gap:8px; }
  .metric-label { color:var(--muted); font-size:13px; font-weight:500; }
  .metric-icon { color:var(--blue); }
  .metric-value { color:var(--ink); font-size:28px; font-weight:700; letter-spacing:-.02em; line-height:1; margin-top:12px; font-variant-numeric:tabular-nums; }
  .metric-note { color:var(--muted); font-size:12px; margin-top:8px; }
  .metric-note.positive { color:var(--status-approved-fg); font-weight:500; }
  .panel { background:var(--surface); border:1px solid var(--line); border-radius:var(--radius-card); padding:20px; margin-bottom:16px; }
  .panel-kicker { color:var(--ink); font-size:16px; font-weight:600; margin-bottom:16px; }
  .dashboard-grid { display:grid; grid-template-columns:1fr 1fr; gap:24px; margin-bottom:24px; }
  .dashboard-grid .panel { min-height:320px; margin:0; }
  .stacked-bar { display:flex; height:10px; overflow:hidden; border-radius:var(--radius-chip); background:var(--line-soft); margin:4px 0 16px; }
  .stacked-segment { min-width:3px; height:100%; }
  .outcome-row { display:grid; grid-template-columns:minmax(108px,1fr) 2fr auto; gap:12px; align-items:center; min-height:34px; }
  .outcome-name { display:flex; align-items:center; gap:8px; color:var(--ink-2); font-size:13px; }
  .outcome-dot { width:8px; height:8px; flex:0 0 8px; border-radius:50%; }
  .outcome-track { height:6px; overflow:hidden; border-radius:var(--radius-chip); background:var(--line-soft); }
  .outcome-fill { height:100%; border-radius:var(--radius-chip); }
  .outcome-value { color:var(--ink-2); font-size:13px; font-variant-numeric:tabular-nums; white-space:nowrap; }
  .outcome-value em { color:var(--muted); font-style:normal; }
  .breakdown-row { display:flex; justify-content:space-between; align-items:center; gap:16px; min-height:40px; border-bottom:1px solid var(--line-soft); color:var(--ink-2); font-size:13px; }
  .breakdown-row:last-child { border-bottom:0; }
  .count-chip { display:grid; place-items:center; min-width:26px; height:22px; padding:0 6px; border-radius:6px; background:var(--blue-soft); color:var(--link); font-size:12px; font-weight:600; }
  .case-table-wrap { overflow-x:auto; }
  .case-table { width:100%; border-collapse:collapse; table-layout:fixed; }
  .case-table th { color:var(--muted); font-size:12px; font-weight:600; letter-spacing:.04em; text-align:left; text-transform:uppercase; padding:0 8px 10px; border-bottom:1px solid var(--line); }
  .case-table td { color:var(--ink-2); font-size:13px; padding:10px 8px; border-bottom:1px solid var(--line-soft); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .case-table tbody tr:last-child td { border-bottom:0; }
  .case-table .case-link { color:var(--link); font-family:var(--mono); font-size:12px; }
  .case-table .money { color:var(--ink); font-variant-numeric:tabular-nums; font-weight:500; }
  .architecture-label { color:var(--muted); font-size:12px; font-weight:600; letter-spacing:.1em; text-transform:uppercase; margin:2px 0 12px; }
  .architecture-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; }
  .architecture-grid .panel { min-height:145px; margin:0; }
  .architecture-icon { display:grid; place-items:center; width:32px; height:32px; margin-bottom:10px; border-radius:var(--radius-control); background:var(--blue-soft); color:var(--blue); }
  .architecture-meta { color:var(--link); font-size:12px; font-weight:600; margin-top:10px; }
  .empty-mini { color:var(--muted); font-size:13px; padding:12px 0 4px; }

  /* Status */
  .status-pill { display:inline-flex; align-items:center; gap:6px; width:max-content; border-radius:var(--radius-chip); padding:3px 10px; font-size:12px; font-weight:600; white-space:nowrap; }
  .status-pill:before { content:""; width:7px; height:7px; border-radius:50%; background:var(--pill-dot, currentColor); }
  .status-review, .status-escalated, .status-recovery { background:var(--status-review-bg); color:var(--status-review-fg); --pill-dot:var(--status-review-dot); }
  .status-approved { background:var(--status-approved-bg); color:var(--status-approved-fg); --pill-dot:var(--status-approved-dot); }
  .status-rejected { background:var(--status-rejected-bg); color:var(--status-rejected-fg); --pill-dot:var(--status-rejected-dot); }
  .status-request-info { background:var(--status-request-info-bg); color:var(--status-request-info-fg); --pill-dot:var(--status-request-info-dot); }
  .status-processing { background:var(--status-processing-bg); color:var(--status-processing-fg); --pill-dot:var(--status-processing-dot); }

  /* Case detail */
  .case-hero { position:relative; overflow:hidden; border-radius:16px; padding:20px 22px; margin:4px 0 16px; background:linear-gradient(125deg,#111f3c 0%,#172b51 68%,#1e3d72 100%); }
  .case-hero:after { content:""; position:absolute; width:220px; height:220px; border-radius:50%; right:-80px; top:-120px; background:rgba(74,133,255,.15); }
  .case-kicker { color:var(--sidebar-muted); font-size:12px; letter-spacing:.1em; text-transform:uppercase; }
  .case-title { color:#ffffff; font-size:20px; font-weight:700; letter-spacing:-.01em; margin:4px 0 12px; font-family:var(--mono); }
  .case-meta { display:flex; align-items:center; gap:10px; flex-wrap:wrap; color:var(--sidebar-text); font-size:13px; }
  .case-summary-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-bottom:18px; }
  .summary-card { background:var(--surface); border:1px solid var(--line); border-radius:var(--radius-card); padding:12px 14px; }
  .summary-label { color:var(--muted); font-size:12px; }
  .summary-value { color:var(--ink); font-size:14px; font-weight:600; margin-top:4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .timeline { margin:8px 0 16px; }
  .stage { position:relative; margin-left:12px; padding:0 0 18px 26px; border-left:1px solid var(--line); }
  .stage:last-child { border-left-color:transparent; padding-bottom:3px; }
  .stage-dot { position:absolute; left:-10px; top:0; display:grid; place-items:center; width:20px; height:20px; border-radius:50%; background:var(--status-approved-dot); color:#ffffff; border:3px solid var(--surface); }
  .stage-dot .p2p-icon { font-size:12px; font-weight:700; }
  .stage-dot.warn { background:var(--status-review-dot); }
  .stage-title { color:var(--ink); font-size:14px; font-weight:600; line-height:1.3; }
  .stage-detail { color:var(--muted); font-size:13px; margin-top:3px; }
  .guardrail { display:flex; align-items:flex-start; gap:12px; background:var(--status-review-bg); border:1px solid #f0d39f; border-radius:var(--radius-card); padding:14px 16px; margin:14px 0; color:var(--status-review-fg); font-size:13px; }
  .guardrail ul { margin:4px 0 0; padding-left:18px; }
  .guardrail li { margin:2px 0; }
  .guardrail-mark { display:grid; place-items:center; width:28px; height:28px; flex:0 0 28px; border-radius:var(--radius-control); background:#f4d69d; color:var(--status-review-fg); }
  .discrepancy { border:1px solid var(--line); border-radius:var(--radius-card); padding:12px 14px; margin:8px 0; background:var(--surface); }
  .discrepancy-head { display:flex; justify-content:space-between; gap:12px; align-items:center; }
  .discrepancy-type { color:var(--ink); font-size:14px; font-weight:600; }
  .discrepancy-copy { color:var(--ink-2); font-size:13px; line-height:1.5; margin-top:6px; }
  .severity-chip { border-radius:var(--radius-chip); padding:2px 8px; font-size:12px; font-weight:600; text-transform:uppercase; }
  .severity-critical { color:var(--sev-critical-fg); background:var(--sev-critical-bg); }
  .severity-material { color:var(--sev-material-fg); background:var(--sev-material-bg); }
  .severity-warning { color:var(--sev-warning-fg); background:var(--sev-warning-bg); }
  .severity-info { color:var(--sev-info-fg); background:var(--sev-info-bg); }
  .decision-card { background:var(--surface); border:1px solid var(--line); border-radius:var(--radius-card); padding:16px; margin:12px 0; }
  .decision-label { color:var(--muted); font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.06em; }
  .decision-title { color:var(--ink); font-size:16px; font-weight:600; margin:6px 0; }
  .decision-copy { color:var(--ink-2); font-size:13px; line-height:1.55; }
  .request-copy { background:var(--surface-subtle); border:1px solid var(--line); border-radius:var(--radius-control); color:var(--ink-2); padding:10px 12px; margin-top:10px; font: 12px/1.5 var(--mono); white-space:pre-wrap; }
  .audit-row { display:grid; grid-template-columns:12px 1fr; gap:12px; padding:2px 0 14px; }
  .audit-node { width:8px; height:8px; border-radius:50%; background:var(--blue); margin-top:5px; box-shadow:0 0 0 4px var(--blue-soft); }
  .audit-title { color:var(--ink); font-size:14px; font-weight:600; }
  .audit-meta { color:var(--muted); font-size:12px; margin-top:2px; }
  .field-errors { display:grid; grid-template-columns:minmax(120px,auto) 1fr; gap:6px 16px; margin:4px 0 12px; padding:12px 14px; border:1px solid #f3c3cc; border-radius:var(--radius-control); background:var(--surface); font-size:13px; }
  .field-errors code { font-family:var(--mono); font-size:12px; color:var(--sev-critical-fg); background:none; padding:0; }
  .field-errors span { color:var(--ink-2); }

  /* Empty states and workflow controls */
  .empty-state { text-align:center; border:1px dashed #cfd5de; border-radius:var(--radius-card); padding:36px 16px; background:rgba(255,255,255,.58); }
  .empty-icon { display:grid; place-items:center; width:44px; height:44px; margin:0 auto 12px; border-radius:var(--radius-card); color:var(--blue); background:var(--blue-soft); }
  .empty-title { color:var(--ink); font-size:16px; font-weight:600; }
  .empty-copy { color:var(--muted); font-size:14px; margin-top:6px; }
  .st-key-invoice-workflow-stepper { margin:-4px 0 18px; }
  .st-key-invoice-workflow-stepper [data-testid="stRadio"] > label { display:none; }
  .st-key-invoice-workflow-stepper div[role="radiogroup"] {
    display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px;
  }
  .st-key-invoice-workflow-stepper div[role="radiogroup"] > label {
    display:flex; justify-content:center; min-height:40px; padding:9px 12px;
    border:1px solid var(--line); border-radius:var(--radius-control); background:var(--surface);
    color:var(--muted); font-size:13px; font-weight:600;
  }
  .st-key-invoice-workflow-stepper div[role="radiogroup"] > label > div:first-child { display:none; }
  .st-key-invoice-workflow-stepper div[role="radiogroup"] > label:has(input:checked) {
    border-color:var(--blue); background:var(--blue-soft); color:var(--link);
  }
  div[data-testid="stVerticalBlockBorderWrapper"] { border-color:var(--line); border-radius:var(--radius-card); background:var(--surface); }

  /* Streamlit components */
  .stTabs [data-baseweb="tab-list"] { gap:4px; border-bottom:1px solid var(--line); }
  .stTabs [data-baseweb="tab"] { color:var(--ink-2); height:42px; padding:0 12px; font-size:14px; }
  .stTabs [aria-selected="true"] { color:var(--ink) !important; font-weight:600; }
  .stTabs [data-baseweb="tab-highlight"] { background-color:var(--blue); }
  .stButton > button { border-radius:var(--radius-control); min-height:40px; font-weight:500; }
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
    .st-key-mobile-navigation { display:block; margin:-6px 0 14px; }
    .st-key-mobile-navigation [data-testid="stRadio"] > label { display:none; }
    .st-key-mobile-navigation div[role="radiogroup"] { display:flex; flex-wrap:nowrap; gap:6px; overflow-x:auto; padding:2px 0 4px; scrollbar-width:none; }
    .st-key-mobile-navigation div[role="radiogroup"]::-webkit-scrollbar { display:none; }
    .st-key-mobile-navigation div[role="radiogroup"] > label { flex:0 0 auto; min-height:32px; padding:6px 11px; border:1px solid var(--line); border-radius:var(--radius-control); background:var(--surface); color:var(--muted); font-size:13px; font-weight:500; }
    .st-key-mobile-navigation div[role="radiogroup"] > label > div:first-child { display:none; }
    .st-key-mobile-navigation div[role="radiogroup"] > label:has(input:checked) { border-color:var(--blue); background:var(--blue); color:#ffffff; }
    .st-key-mobile-navigation div[role="radiogroup"] > label:has(input:checked) * { color:#ffffff !important; }
    .topbar { margin-bottom:16px; }
    .breadcrumb { display:none; }
    .topbar-right { width:100%; justify-content:flex-end; }
    .page-header { display:block; }
    .metric-grid { margin-right:-.75rem; padding-right:.75rem; }
    .metric-card { min-width:176px; min-height:105px; }
    .dashboard-grid { gap:16px; }
    .dashboard-grid .panel { min-height:auto; }
    .outcome-row { grid-template-columns:105px 1fr auto; gap:8px; }
    .st-key-invoice-workflow-stepper div[role="radiogroup"] { gap:6px; }
    .st-key-invoice-workflow-stepper div[role="radiogroup"] > label { padding:8px 6px; }
    .architecture-grid { grid-template-columns:1fr; }
    .case-table { min-width:520px; }
    .case-table th:nth-child(4), .case-table td:nth-child(4),
    .case-table th:nth-child(5), .case-table td:nth-child(5),
    .case-table th:nth-child(6), .case-table td:nth-child(6) { display:none; }
    .case-summary-grid { grid-template-columns:1fr 1fr; }
    .field-errors { grid-template-columns:1fr; }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *:before, *:after { scroll-behavior:auto !important; transition:none !important; animation:none !important; }
  }
</style>
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


def icon(name: str) -> str:
    return f'<span class="p2p-icon" aria-hidden="true">{h(name)}</span>'


def status_pill(key: str, label: str) -> str:
    css_key = key.replace("_", "-")
    return f'<span class="status-pill status-{h(css_key)}">{h(label)}</span>'


def action_pill(action: str) -> str:
    return status_pill(ACTION_STATUS_KEYS.get(action, "processing"), label_action(action))


def top_bar(page: str) -> None:
    reviewer = current_reviewer()
    render_html(
        f"""
        <div class="topbar">
          <div class="breadcrumb">Operations / <strong>{h(page)}</strong></div>
          <div class="topbar-right">
            <span class="operator-avatar" title="{h(reviewer)}" aria-label="Reviewer {h(reviewer)}">{h(initials(reviewer))}</span>
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
    icon_name: str,
    note_tone: str = "",
) -> str:
    return f"""
      <div class="metric-card">
        <div class="metric-top"><span class="metric-label">{h(label)}</span><span class="metric-icon">{icon(icon_name)}</span></div>
        <div class="metric-value">{h(value)}</div>
        <div class="metric-note {h(note_tone)}">{h(note)}</div>
      </div>
    """


def render_dashboard_analysis(metrics: dict) -> None:
    palette = {
        "APPROVE": "#1f9a5e",
        "REJECT": "#d23a50",
        "REQUEST_INFO": "#2f6fed",
        "ESCALATE": "#c98512",
    }
    actions = metrics.get("actions", {})
    total_actions = sum(actions.values())
    segments = []
    outcome_rows = []
    for action, count in sorted(actions.items(), key=lambda item: item[1], reverse=True):
        color = palette.get(action, "#cfd5de")
        label = OUTCOME_LABELS.get(action, "Other")
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
              <span>{h(label_exception(exception))}</span>
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
            <div class="stacked-bar">{"".join(segments)}</div>
            {"".join(outcome_rows)}
          </section>
          <section class="panel" aria-label="Exception breakdown">
            <div class="panel-kicker">Exception breakdown</div>
            {"".join(exception_rows)}
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


def render_recent_cases(cases: list[dict]) -> None:
    rows = []
    for case in cases[:6]:
        invoice = case.get("invoice", {})
        discrepancies = case.get("state", {}).get("match_result", {}).get("discrepancies", [])
        exception = label_exception(discrepancies[0].get("type") if discrepancies else None)
        status = display_status(case)
        source = (case.get("state", {}).get("outcome") or {}).get("source", "")
        if case.get("status") == "PENDING_REVIEW":
            assigned = "Review queue"
        else:
            assigned = "Human reviewer" if str(source).startswith("HUMAN") else "System"
        rows.append(
            f"""
            <tr>
              <td class="case-link">{h(case.get("case_id", "Unknown"))}</td>
              <td>{h(invoice.get("supplier_name", "Unknown supplier"))}</td>
              <td class="money">{h(format_money(invoice.get("total", 0), invoice.get("currency", "")))}</td>
              <td>{h(exception)}</td>
              <td>{status_pill(status.key, status.label)}</td>
              <td>{h(assigned)}</td>
              <td>{h(relative_age(case.get("updated_at")))}</td>
            </tr>
            """,
        )
    if not rows:
        rows.append(
            '<tr><td colspan="7" class="empty-mini">Processed cases will appear here.</td></tr>'
        )
    render_html(
        f"""
        <section class="panel" aria-label="Recent cases">
          <div class="panel-kicker">Recent cases</div>
          <div class="case-table-wrap">
            <table class="case-table">
              <colgroup><col style="width:17%"><col style="width:18%"><col style="width:15%"><col style="width:17%"><col style="width:15%"><col style="width:12%"><col style="width:6%"></colgroup>
              <thead><tr><th>Case ID</th><th>Vendor</th><th>Amount</th><th>Type</th><th>Status</th><th>Assigned</th><th>Age</th></tr></thead>
              <tbody>{"".join(rows)}</tbody>
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
        severity = str(item.get("severity", "info")).lower()
        render_html(
            f"""
            <div class="discrepancy">
              <div class="discrepancy-head">
                <div class="discrepancy-type">{h(label_exception(item.get("type")))}</div>
                <span class="severity-chip severity-{h(severity)}">{h(severity)}</span>
              </div>
              <div class="discrepancy-copy">{h(item.get("explanation", "No explanation provided."))}</div>
            </div>
            """,
        )


def stage(title: str, detail: str, warning: bool = False) -> str:
    warning_class = " warn" if warning else ""
    marker = icon("priority_high" if warning else "check")
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
    status = display_status(case)
    render_html(
        f"""
        <div class="case-hero">
          <div class="case-kicker">Exception case</div>
          <div class="case-title">{h(case["case_id"])}</div>
          <div class="case-meta">
            {status_pill(status.key, status.label)}
            <span>{h(invoice["supplier_name"])}</span><span>·</span><span>{h(invoice["invoice_id"])}</span>
          </div>
        </div>
        <div class="case-summary-grid">
          <div class="summary-card"><div class="summary-label">Invoice</div><div class="summary-value">{h(invoice["invoice_id"])}</div></div>
          <div class="summary-card"><div class="summary-label">Supplier</div><div class="summary-value" title="{h(invoice["supplier_name"])}">{h(invoice["supplier_name"])}</div></div>
          <div class="summary-card"><div class="summary-label">Purchase order</div><div class="summary-value">{h(invoice.get("po_number") or "Missing")}</div></div>
          <div class="summary-card"><div class="summary-label">Invoice total</div><div class="summary-value">{h(format_money(invoice["total"], invoice["currency"]))}</div></div>
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
            reasons = "".join(f"<li>{h(reason)}</li>" for reason in policy["reasons"])
            verdict = (
                "Autonomous action permitted" if policy["permitted"] else "Human review required"
            )
            render_html(
                f"""
                <div class="guardrail">
                  <div class="guardrail-mark">{icon("policy")}</div>
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
                <div class="decision-card">
                  <div class="decision-label">AI interpretation · verify against evidence</div>
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
                  <div class="decision-title">{action_pill(resolution["action"])}</div>
                  <div class="decision-copy">{h(resolution["rationale"])}</div>
                  {f'<div class="request-copy">{h(request)}</div>' if request else ""}
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
                  <div class="decision-title">{action_pill(outcome["action"])}</div>
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
                    <div class="audit-title">{h(label_event(event["event_type"]))}</div>
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
    render_html(
        '<div class="metric-grid">'
        + metric_card("Invoices processed", metrics["total"], "All workflow runs", "receipt_long")
        + metric_card(
            "Auto-resolved",
            f"{metrics['autonomous_rate']:.1%}",
            "Policy-authorized outcomes",
            "verified",
            "positive",
        )
        + metric_card(
            "Awaiting review",
            metrics["counts_by_status"].get("PENDING_REVIEW", 0),
            "Awaiting a reviewer",
            "pending_actions",
        )
        + metric_card(
            "Avg confidence",
            f"{metrics['average_confidence']:.0%}",
            "Across AI proposals",
            "speed",
        )
        + "</div>",
    )
    render_dashboard_analysis(metrics)
    render_recent_cases(cases)
    render_html(
        f"""
        <div class="architecture-label">Control architecture</div>
        <div class="architecture-grid">
          <article class="panel">
            <div class="architecture-icon">{icon("database")}</div>
            <div class="decision-title">Evidence Engine</div>
            <div class="decision-copy">Validated invoices, purchase orders, receipts, supplier records, and duplicate history establish the factual record.</div>
            <div class="architecture-meta">Deterministic inputs</div>
          </article>
          <article class="panel">
            <div class="architecture-icon">{icon("psychology")}</div>
            <div class="decision-title">Reasoning Engine</div>
            <div class="decision-copy">Investigation, resolution, and critique use typed outputs inside a bounded revision loop.</div>
            <div class="architecture-meta">3 structured AI roles</div>
          </article>
          <article class="panel">
            <div class="architecture-icon">{icon("gavel")}</div>
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
          <div class="summary-card"><div class="summary-label">Invoice total</div><div class="summary-value">{h(format_money(invoice.total, invoice.currency))}</div></div>
        </div>
        """,
    )


def render_field_errors(rows: list[tuple[str, str]]) -> None:
    st.error(f"{len(rows)} field(s) need fixing before this invoice can be processed")
    items = "".join(f"<code>{h(field)}</code><span>{h(message)}</span>" for field, message in rows)
    render_html(f'<div class="field-errors">{items}</div>')


def show_failure(message: str, exc: Exception) -> None:
    st.error(message)
    with st.expander("Technical details"):
        st.code(f"{type(exc).__name__}: {exc}", language=None)


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


def cases_page(app: CaseService) -> None:
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


def review_page(app: CaseService) -> None:
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


def render_flash() -> None:
    flash = st.session_state.pop("flash", None)
    if not flash:
        return
    if flash["kind"] == "toast":
        st.toast(flash["text"], icon=":material/check_circle:")
    else:
        st.error(flash["text"])


def main() -> None:
    app = service()
    render_html(UI_STYLES)
    render_flash()
    active_page = st.session_state.setdefault("active_page", NAV_ITEMS[0])
    st.session_state.setdefault("desktop_navigation", active_page)
    st.session_state.setdefault("mobile_navigation", active_page)
    runtime = app.runtime
    with st.sidebar:
        render_html(
            """
            <div class="brand">
              <div class="brand-mark">P2P</div>
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
        mode = "Demo" if runtime.demo_mode else "Live"
        rules_name = runtime.rules_path.name if runtime.rules_path else "—"
        render_html(
            f"""
            <div class="runtime-card">
              <div class="runtime-label">Runtime</div>
              <div class="runtime-row"><span>Mode</span><span>{h(mode)}</span></div>
              <div class="runtime-row"><span>Model</span><span title="{h(runtime.model_name)}">{h(runtime.model_name)}</span></div>
              <div class="runtime-row"><span>Rules</span><span title="{h(rules_name)}">{h(rules_name)}</span></div>
              <div class="runtime-row"><span>Reflection limit</span><span>{h(runtime.rules.reflection.max_iterations)}</span></div>
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
