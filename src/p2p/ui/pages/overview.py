from __future__ import annotations

from datetime import UTC, datetime

from p2p.application.service import CaseService
from p2p.ui.components.chips import h, icon, render_html, status_pill
from p2p.ui.components.shell import page_header
from p2p.ui.viewmodels import OUTCOME_LABELS, display_status, format_money, label_exception


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


def render(app: CaseService) -> None:
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
