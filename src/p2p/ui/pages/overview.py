from __future__ import annotations

from datetime import UTC, datetime

import altair as alt
import pandas as pd
import streamlit as st

from p2p.ui.components.chips import h, icon, render_html
from p2p.ui.components.shell import page_header, switch_to, top_bar
from p2p.ui.runtime import service
from p2p.ui.viewmodels import (
    format_duration,
    format_money,
    label_exception,
    local_zone,
    parse_timestamp,
    severity_chip,
    waiting_labels,
)

SEVERITY_BARS = {
    "CRITICAL": "#d23a50",
    "MATERIAL": "#e0703a",
    "WARNING": "#d9a032",
    "INFO": "#7f9cc4",
}
OUTCOMES = {
    "review": ("Awaiting review", "#c98512", "paused"),
    "approved": ("Approved", "#1f9a5e", "auto + human"),
    "rejected": ("Rejected", "#d23a50", "released reservations"),
    "request_info": ("Request info", "#2f6fed", "evidence requested"),
}
UP_IS_BAD, UP_IS_GOOD = ("#a6380f", "#14653f"), ("#14653f", "#a6380f")


def money_by_currency(values: dict[str, str]) -> str:
    return " · ".join(format_money(amount, currency) for currency, amount in values.items()) or "—"


def trend_html(delta: float | None, text: str, colours: tuple[str, str]) -> str:
    if delta is None:
        return ""
    if delta > 0:
        colour, arrow = colours[0], "trending_up"
    elif delta < 0:
        colour, arrow = colours[1], "trending_down"
    else:
        colour, arrow = "var(--muted)", "trending_flat"
    return f'<span class="p2p-kpi-trend" style="color:{colour}">{icon(arrow)}{h(text)}</span>'


def kpi(label: str, value: str, note: str, trend: str = "") -> str:
    return f"""
    <div class="p2p-kpi">
      <span class="p2p-kpi-label">{h(label)}</span>
      <span class="p2p-kpi-value">{h(value)}</span>
      <span class="p2p-kpi-note">{h(note)}</span>
      {trend}
    </div>
    """


def percent(rate: float | None) -> str:
    return "—" if rate is None else f"{rate:.0%}"


def kpis(summary: dict) -> str:
    awaiting, auto, override = summary["awaiting"], summary["auto_resolution"], summary["override"]
    yesterday = awaiting["trend_vs_yesterday"]
    points = auto["trend_points"]
    oldest = awaiting["oldest_wait_seconds"]
    median = awaiting["median_wait_seconds"]
    cards = [
        kpi(
            "Awaiting review",
            str(awaiting["count"]),
            f"Oldest waiting {format_duration(oldest)}" if oldest is not None else "Queue clear",
            trend_html(
                yesterday,
                f"{yesterday:+d} since yesterday" if yesterday is not None else "",
                UP_IS_BAD,
            ),
        ),
        kpi(
            "Value at risk",
            money_by_currency(awaiting["value_by_currency"]),
            "Invoice value paused for review",
        ),
        kpi(
            "Auto-resolution rate",
            percent(auto["rate"]),
            f"{auto['autonomous']} of {auto['total']} without a reviewer",
            trend_html(
                points, f"{points:+.0f} pts this week" if points is not None else "", UP_IS_GOOD
            ),
        ),
        kpi(
            "Median wait",
            "—" if median is None else format_duration(median),
            "Since the case was paused",
        ),
        kpi(
            "Human override rate",
            percent(override["rate"]),
            f"{override['overridden']} of {override['ai_reviewed']} AI-recommended decisions were changed",
        ),
    ]
    return f'<div class="p2p-kpis">{"".join(cards)}</div>'


def run_all() -> None:
    app = service()
    results, failures = [], []
    with st.spinner("Running the ten demo scenarios…"):
        for name, invoice in app.demo_invoices().items():
            try:
                results.append(app.process_invoice(invoice))
            except Exception as exc:
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
    if results and all(result["is_replay"] for result in results):
        text = (
            f"Demo scenarios were already processed · {app.pending_review_count()} awaiting review"
        )
    else:
        pending = sum(result["status"] == "PENDING_REVIEW" for result in results)
        text = f"{len(results)} scenarios processed · {pending} need review"
    st.session_state["flash"] = {"kind": "toast", "text": text}
    if failures:
        st.session_state["overview_failures"] = failures
    st.rerun()


def header(now_label: str) -> None:
    page_header(
        "Operations center",
        "Overview",
        f"Invoice exceptions handled by the control plane. Updated {now_label}.",
    )


def actions(key: str, primary_run: bool) -> None:
    with st.container(horizontal=True, key=f"overview-actions-{key}"):
        if st.button(
            "Run all 10 scenarios",
            icon=":material/play_arrow:",
            type="primary" if primary_run else "secondary",
            key=f"overview_run_all_{key}",
        ):
            run_all()
        label = "Pick one scenario" if primary_run else "Process invoice"
        if st.button(
            label, type="secondary" if primary_run else "primary", key=f"overview_process_{key}"
        ):
            switch_to("process")


def needs_attention() -> None:
    rows, _ = service().query_cases(view="review", sort="priority", limit=5)
    now = datetime.now(UTC)
    with st.container(border=True, key="overview-attention"):
        with st.container(horizontal=True, vertical_alignment="center"):
            render_html('<h2 class="p2p-section-title p2p-flush">Needs attention</h2>')
            if st.button("Open queue", type="tertiary", key="overview_open_queue"):
                switch_to("review")
        render_html('<span class="p2p-caption">Sorted by severity, value and time waiting</span>')
        if not rows:
            render_html('<p class="p2p-caption">No cases are waiting for a human decision.</p>')
        for row in rows:
            chip = severity_chip(row["max_severity"])
            since = parse_timestamp(row["review_requested_at"] or row["updated_at"])
            with st.container(
                horizontal=True,
                wrap=False,
                vertical_alignment="center",
                key=f"attention-{row['case_id']}",
            ):
                render_html(
                    f"""
                    <div class="p2p-attention-row">
                      <span class="p2p-severity-chip p2p-severity-{h(chip.key)}">{h(chip.text)}</span>
                      <span class="p2p-attention-main"><strong>{h(row["supplier_name"])} · {h(label_exception(row["primary_exception"]))}</strong>
                        <code>{h(row["invoice_id"])} · waiting {h(format_duration((now - since).total_seconds()))}</code></span>
                      <span class="p2p-attention-amount">{h(format_money(row["total"], row["currency"]))}</span>
                    </div>
                    """
                )
                if st.button("Open", key=f"attention_open_{row['case_id']}"):
                    switch_to("review", case=row["case_id"])


def waiting_chart(summary: dict) -> None:
    sla = summary["sla_hours"]
    with st.container(border=True, key="overview-waiting"):
        render_html(
            f"""
            <div class="p2p-panel-head"><h2 class="p2p-section-title p2p-flush">Time waiting for review</h2>
            <span class="p2p-caption p2p-sla-legend"><i></i>{sla}h target</span></div>
            """
        )
        if not summary["waiting"]:
            render_html('<p class="p2p-caption">Nothing is waiting.</p>')
            return
        data = pd.DataFrame(
            [
                {
                    "case": label,
                    "invoice": item["invoice_id"],
                    "hours": round(item["wait_seconds"] / 3600, 2),
                    "waited": format_duration(item["wait_seconds"]),
                    "severity": item["max_severity"] or "INFO",
                }
                for item, label in zip(
                    summary["waiting"], waiting_labels(summary["waiting"]), strict=True
                )
            ]
        )
        bars = (
            alt.Chart(data)
            .mark_bar(cornerRadius=3, height=18)
            .encode(
                y=alt.Y(
                    "case:N", sort=alt.EncodingSortField("hours", order="descending"), title=None
                ),
                x=alt.X("hours:Q", title="Hours waiting", scale=alt.Scale(domainMin=0)),
                color=alt.Color(
                    "severity:N",
                    scale=alt.Scale(domain=list(SEVERITY_BARS), range=list(SEVERITY_BARS.values())),
                    legend=None,
                ),
                tooltip=["invoice", "severity", "waited"],
            )
        )
        target = (
            alt.Chart(pd.DataFrame({"hours": [sla]}))
            .mark_rule(color="#a1122b", strokeDash=[5, 4], size=2)
            .encode(x="hours:Q")
        )
        chart = (
            (bars + target)
            .properties(height=34 * len(data) + 40)
            .configure_axis(
                labelFont="IBM Plex Mono",
                titleFont="IBM Plex Sans",
                labelColor="#475264",
                titleColor="#5b6576",
            )
        )
        st.altair_chart(chart, width="stretch")
        render_html('<span class="p2p-caption">Bar colour shows severity.</span>')


def outcomes(summary: dict) -> str:
    entries = summary["outcomes"]
    total = sum(entry["count"] for entry in entries) or 1
    segments = "".join(
        f'<div style="width:{entry["count"] / total * 100:.2f}%;background:{OUTCOMES[entry["key"]][1]}"></div>'
        for entry in entries
        if entry["count"]
    )
    legend = "".join(
        f"""
        <div class="p2p-outcome-item"><i style="background:{OUTCOMES[entry["key"]][1]}"></i>
          <div><strong>{h(OUTCOMES[entry["key"]][0])} · {entry["count"]}</strong>
          <span>{h(money_by_currency(entry["value_by_currency"]))} · {h(OUTCOMES[entry["key"]][2])}</span></div>
        </div>
        """
        for entry in entries
    )
    return f"""
    <section class="p2p-card">
      <h2 class="p2p-section-title p2p-flush">Outcomes</h2>
      <div class="p2p-outcome-bar">{segments}</div>
      <div class="p2p-table-wrap p2p-borderless"><div class="p2p-outcome-legend">{legend}</div></div>
    </section>
    """


def render() -> None:
    app = service()
    top_bar(["Overview"], "/")
    tz, suffix = local_zone(st.context.timezone)
    header(datetime.now(UTC).astimezone(tz).strftime("%d %b, %H:%M") + suffix)
    failures = st.session_state.pop("overview_failures", None)
    if failures:
        st.error("Some scenarios could not be processed: " + "; ".join(failures))
    summary = app.summary()
    if summary["total"] == 0:
        actions("top", primary_run=False)
        render_html(
            f"""
            <div class="p2p-empty-state"><div class="p2p-empty-icon">{icon("inbox")}</div>
              <div class="p2p-empty-title">No cases yet</div>
              <div class="p2p-empty-copy">Run the ten demo scenarios to populate the dashboard, or process a single invoice to follow it through the workflow.</div>
            </div>
            """
        )
        actions("empty", primary_run=True)
        return
    actions("top", primary_run=False)
    render_html(kpis(summary))
    with st.container(horizontal=True, wrap=True, gap="medium", key="overview-panels"):
        needs_attention()
        waiting_chart(summary)
    render_html(outcomes(summary))


render()
