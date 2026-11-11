from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit

import streamlit as st

from p2p.application.service import CaseService
from p2p.domain.models import Invoice
from p2p.ports import ModelAuthenticationError
from p2p.ui.components.chips import action_pill, h, icon, render_html, status_pill
from p2p.ui.components.shell import mark_authentication_failure
from p2p.ui.identity import current_reviewer
from p2p.ui.viewmodels import (
    CANNOT,
    EXCEEDS,
    WITHIN,
    AiStoryView,
    Attempt,
    HeaderView,
    MatchView,
    PolicyView,
    TimelineEvent,
    ai_story,
    case_header,
    default_tab,
    display_status,
    local_zone,
    match_view,
    policy_view,
    tab_labels,
    timeline,
)

TILE_ICONS = {"ok": "check_circle", "fail": "cancel", "na": "remove"}
RESULTS = {
    EXCEEDS: ("exceeds", "error"),
    CANNOT: ("cannot", "help"),
    WITHIN: ("within", "check_circle"),
}
CHECK_ICONS = {True: "check_circle", False: "cancel", None: "remove"}


def case_link(case_id: str) -> str:
    path = f"/cases?case={case_id}"
    url = st.context.url
    if not url:
        return path
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{path}"


def _chip(key: str, text: str) -> str:
    return f'<span class="p2p-severity-chip p2p-severity-{h(key)}">{h(text)}</span>'


def header_html(view: HeaderView) -> str:
    meta = "".join(
        f'<div class="p2p-meta"><span>{h(item.label)}</span><strong>{h(item.value)}'
        f"{_chip(item.chip.key, item.chip.text) if item.chip else ''}"
        f"{f'<em>{h(item.extra)}</em>' if item.extra else ''}</strong></div>"
        for item in view.meta
    )
    return f"""
    <section class="p2p-case-header">
      <div class="p2p-case-header-top">
        <div class="p2p-case-heading">
          <span class="p2p-case-id">{h(view.case_id)}</span>
          <h2>{h(view.title)}</h2>
        </div>
        {status_pill(view.status.key, view.status.label)}
      </div>
      <div class="p2p-meta-grid">{meta}</div>
    </section>
    """


def match_html(view: MatchView) -> str:
    if not view.ran:
        return '<p class="p2p-caption">The three-way match has not run for this case yet.</p>'
    tiles = "".join(
        f'<div class="p2p-tile p2p-tile-{tile.state}">{icon(TILE_ICONS[tile.state])}'
        f"<span><small>{h(tile.label)}</small>{h(tile.text)}</span></div>"
        for tile in view.tiles
    )
    grids = []
    for line in view.lines:
        rows = "".join(
            f"""
            <tr>
              <th scope="row">{h(row.field)}</th>
              <td class="p2p-num p2p-invoice-cell">{h(row.invoice)}</td>
              <td class="p2p-num">{h(row.po)}</td>
              <td class="p2p-num">{h(row.receipt)}</td>
              <td>{h(row.variance)}</td>
              <td><span class="p2p-result p2p-result-{RESULTS[row.result][0]}">{icon(RESULTS[row.result][1])}{h(row.result)}</span><small>{h(row.tolerance)}</small></td>
            </tr>
            """
            for row in line.rows
        )
        note = f'<p class="p2p-caption">{h(line.note)}</p>' if line.note else ""
        grids.append(
            f"""
            <h3 class="p2p-subhead">{h(line.title)}</h3>
            <div class="p2p-table-wrap">
              <table class="p2p-match-grid">
                <thead><tr><th></th><th class="p2p-num">Invoice</th><th class="p2p-num">PO</th><th class="p2p-num">Receipt</th><th>Variance</th><th>Tolerance</th></tr></thead>
                <tbody>{rows}</tbody>
              </table>
            </div>
            {note}
            """
        )
    discrepancies = "".join(
        f"""
        <div class="p2p-finding p2p-finding-{h(item.severity.key)}">
          {_chip(item.severity.key, item.severity.text)}
          <div><strong>{h(item.label)}</strong><span>{h(item.explanation)}</span><code>{h(item.reference)}</code></div>
        </div>
        """
        for item in view.discrepancies
    )
    return f"""
    <div class="p2p-tab-body">
      <div class="p2p-tiles">{tiles}</div>
      {"".join(grids)}
      {discrepancies}
    </div>
    """


def attempt_html(attempt: Attempt) -> str:
    rejected = attempt.verdict == "failed"
    confidence = f"conf {attempt.confidence:.2f}"
    if attempt.adjusted is not None and attempt.adjusted != attempt.confidence:
        confidence += f" → {attempt.adjusted:.2f}"
    request = (
        f'<p class="p2p-request">Information request: {h(attempt.information_request)}</p>'
        if attempt.information_request
        else ""
    )
    if attempt.verdict is None:
        critique = ""
    else:
        if rejected:
            title = "Critic rejected"
            if attempt.adjustment:
                title += f" · confidence {attempt.adjustment:+.2f}"
        else:
            title = "Critic passed"
        lines = "".join(f"<span>{h(issue)}</span>" for issue in attempt.issues)
        lines += "".join(
            f'<span class="p2p-muted">Recommended: {h(change)}</span>' for change in attempt.changes
        )
        if not lines and not rejected:
            lines = "<span>Recommendation is grounded in matcher evidence.</span>"
        critique = f"""
        <div class="p2p-critique p2p-critique-{"failed" if rejected else "passed"}">
          {icon("gpp_bad" if rejected else "verified")}
          <div><strong>{h(title)}</strong>{lines}</div>
        </div>
        """
    return f"""
    <div class="p2p-attempt{" p2p-attempt-rejected" if rejected else ""}">
      <div class="p2p-attempt-body">
        <div class="p2p-attempt-head">
          <span class="p2p-label">Attempt {attempt.number} · Resolution agent</span>
          <span>{action_pill(attempt.action)}<span class="p2p-muted">{h(confidence)}</span></span>
        </div>
        <p>{h(attempt.rationale)}</p>
        {request}
      </div>
      {critique}
    </div>
    """


def ai_html(story: AiStoryView) -> str:
    if not story.invoked:
        return f"""
        <div class="p2p-tab-body">
          <div class="p2p-callout">{icon("bolt")}<div><strong>AI not invoked</strong><span>{h(story.not_invoked_text)}</span></div></div>
        </div>
        """
    investigation = story.investigation
    error = (
        f'<div class="p2p-callout p2p-callout-warning">{icon("warning")}<div><strong>Model output failed validation; the case was routed to a reviewer.</strong><code>{h(story.model_error)}</code></div></div>'
        if story.model_error
        else ""
    )
    diff = ""
    if story.diff:
        rows = "".join(
            f'<div class="p2p-diff-row"><span>{h(name)}</span><del>{h(before)}</del>{icon("arrow_forward")}<ins>{h(after)}</ins></div>'
            for name, before, after in story.diff
        )
        diff = f'<div class="p2p-diff"><div class="p2p-diff-head">What changed between attempts</div>{rows}</div>'
    return f"""
    <div class="p2p-tab-body">
      <h3 class="p2p-subhead">{h(story.title)}</h3>
      {error}
      <div class="p2p-investigator">
        <span class="p2p-label">Investigator</span>
        <strong>{h(investigation.root_cause)}</strong>
        <span class="p2p-muted">Missing: {h(investigation.missing)} · suggests {h(investigation.action)} · confidence {investigation.confidence:.2f}</span>
      </div>
      {"".join(attempt_html(attempt) for attempt in story.attempts)}
      {diff}
    </div>
    """


def policy_html(view: PolicyView | None, rules_name: str) -> str:
    if view is None:
        return '<div class="p2p-tab-body"><p class="p2p-caption">Policy has not run for this case yet.</p></div>'
    verdict_icon = {"approved": "verified", "rejected": "block", "review": "gavel"}[
        view.verdict_key
    ]
    if view.checks:
        body = "".join(
            f"""
            <div class="p2p-check p2p-check-{str(check["passed"]).lower()}">
              {icon(CHECK_ICONS[check["passed"]])}
              <strong>{h(check["label"])}</strong>
              <span class="p2p-muted">{h(check["threshold"])}</span>
              <span class="p2p-check-actual">{h(check["actual"])}</span>
            </div>
            """
            for check in view.checks
        )
    else:
        body = "<ul>" + "".join(f"<li>{h(reason)}</li>" for reason in view.reasons) + "</ul>"
        body += f'<p class="p2p-caption">{h(view.caption)}</p>'
    return f"""
    <div class="p2p-tab-body">
      <div class="p2p-verdict p2p-verdict-{view.verdict_key}">{icon(verdict_icon)}{h(view.verdict)}</div>
      <div class="p2p-checklist">{body}</div>
      <code class="p2p-source">Source: {h(rules_name)}</code>
    </div>
    """


def event_html(event: TimelineEvent) -> str:
    details = "".join(
        f'<div class="p2p-kv"><code>{h(key)}</code><span>{h(value)}</span></div>'
        for key, value in event.details
    )
    return f"""
    <div class="p2p-event-card">
      <span class="p2p-label">Event details</span>
      <strong>{h(event.label)}</strong>
      <span class="p2p-muted">{h(event.time)} · {h(event.relative)} · {h(event.actor)}</span>
      {details}
    </div>
    """


def render_timeline(case_id: str, events: list[TimelineEvent]) -> None:
    if not events:
        st.info("No audit events recorded.")
        return
    left, right = st.columns([3, 2])
    with left:
        selected = st.radio(
            "Event",
            range(len(events)),
            index=len(events) - 1,
            format_func=lambda index: f"{events[index].time} · {events[index].label}",
            captions=[f"{event.actor} · {event.delta}" for event in events],
            key=f"timeline_{case_id}",
            label_visibility="collapsed",
        )
    with right:
        render_html(event_html(events[selected]))


def retry_case(case: dict, app: CaseService) -> None:
    """Resume a failed run from its last checkpoint; call from the script body."""
    try:
        result = app.process_invoice(Invoice.model_validate(case["invoice"]))
    except ModelAuthenticationError:
        mark_authentication_failure()
    except Exception:
        st.session_state["flash"] = {
            "kind": "error",
            "text": "The workflow could not finish. The case is saved; retry it again later.",
        }
        st.rerun()
    status = display_status(app.get_case(result["case_id"]) or {})
    st.session_state["flash"] = {
        "kind": "toast",
        "text": f"{case['invoice_id']} processed again · {status.label}",
    }
    st.rerun()


def recovery_notice(case: dict, app: CaseService) -> None:
    if display_status(case).key != "recovery":
        return
    st.warning(f"{case['case_id']} needs recovery. Retrying resumes it from its last saved step.")
    if case.get("last_error"):
        with st.expander("Technical details"):
            st.code(case["last_error"], language=None)
    if st.button("Retry processing", icon=":material/replay:", key=f"retry_{case['case_id']}"):
        retry_case(case, app)


def render_case_detail(case: dict, app: CaseService) -> None:
    if not case:
        st.error("The selected case could not be loaded.")
        return
    runtime = app.runtime
    rules = runtime.rules
    state = case.get("state") or {}
    now = datetime.now(UTC)
    zone = local_zone(st.context.timezone)

    header = case_header(case, now, current_reviewer(), zone)
    match = match_view(state, rules)
    story = ai_story(case, rules)
    policy = policy_view(state)
    events = timeline(case, zone, now)

    render_html(header_html(header))
    recovery_notice(case, app)
    with st.popover(":material/link: Copy link"):
        st.code(case_link(case["case_id"]), language=None)
    labels = tab_labels(match, story, policy, len(events))
    tabs = dict(
        zip(
            labels,
            st.tabs(
                list(labels.values()),
                default=labels[default_tab(story)],
                key=f"case_tabs_{case['case_id']}",
            ),
            strict=True,
        )
    )
    with tabs["match"]:
        render_html(match_html(match))
    with tabs["ai"]:
        render_html(ai_html(story))
    with tabs["policy"]:
        rules_name = runtime.rules_path.name if runtime.rules_path else "business rules"
        render_html(policy_html(policy, rules_name))
    with tabs["timeline"]:
        render_timeline(case["case_id"], events)
