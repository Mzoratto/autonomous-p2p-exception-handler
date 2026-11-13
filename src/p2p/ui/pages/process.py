from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st
from pydantic import ValidationError

from p2p.domain.models import Invoice, SupersedeRequest
from p2p.ports import ModelAuthenticationError, SupersedeRefusedError
from p2p.ui.components.chips import h, icon, render_html, status_pill
from p2p.ui.components.shell import (
    mark_authentication_failure,
    page_header,
    render_field_errors,
    show_failure,
    switch_to,
    top_bar,
)
from p2p.ui.identity import current_reviewer
from p2p.ui.runtime import service
from p2p.ui.scenarios import AI_REVIEW, SCENARIOS, VALUE_REVIEW
from p2p.ui.viewmodels import (
    display_status,
    format_money,
    local_zone,
    timeline,
    validation_rows,
)

STEPS = ["Choose invoice", "Validate", "Run workflow"]
STATE_KEYS = (
    "process_step",
    "process_label",
    "process_invoice",
    "process_errors",
    "process_case_id",
    "process_is_replay",
    "process_supersedes",
    "process_confirm",
)
TAG_CLASSES = {AI_REVIEW: "ai", VALUE_REVIEW: "value"}
MIN_NOTE = 3


def go_to(step: int) -> None:
    st.session_state["process_step"] = step


def reset() -> None:
    for key in STATE_KEYS:
        st.session_state.pop(key, None)
    st.query_params.pop("supersedes", None)


def clear_confirmation() -> None:
    st.session_state.pop("process_confirm", None)


def choose(label: str, invoice: Invoice | None, errors: list[tuple[str, str]] | None) -> None:
    st.session_state.update(
        process_label=label,
        process_invoice=invoice,
        process_errors=errors or [],
        process_step=1,
    )
    st.session_state.pop("process_case_id", None)
    st.session_state.pop("process_is_replay", None)


def parse(payload: str | bytes) -> tuple[Invoice | None, list[tuple[str, str]]]:
    try:
        return service().parse_invoice(payload), []
    except ValidationError as exc:
        return None, validation_rows(exc)
    except UnicodeDecodeError:
        return None, [("file", "The file is not valid UTF-8 text.")]


def stepper(current: int) -> None:
    with st.container(horizontal=True, wrap=True, key="process-stepper"):
        for index, label in enumerate(STEPS):
            done = index < current
            st.button(
                label,
                key=f"process_step_{index}",
                icon=":material/check_circle:" if done else None,
                type="primary" if index == current else "secondary",
                disabled=index > current,
                on_click=go_to if done else None,
                args=(index,) if done else None,
            )


def scenario_step() -> None:
    invoices = service().demo_invoices()
    held_id = st.session_state.get("process_supersedes")
    if held_id:
        st.info(
            f"Correcting held case {held_id}. Upload or paste the corrected invoice, "
            "then link it to the held case on the next step."
        )
    render_html('<h2 class="p2p-section-title">Demo scenarios</h2>')
    with st.container(horizontal=True, wrap=True, key="process-scenarios"):
        for scenario in SCENARIOS:
            invoice = invoices[scenario.invoice_key]
            tag = TAG_CLASSES.get(scenario.tag, "deterministic")
            with st.container(border=True, width=240, key=f"scenario-{scenario.key}"):
                render_html(
                    f"""
                    <div class="p2p-scenario">
                      <div class="p2p-scenario-top"><code>{h(scenario.key)}</code><span class="p2p-tag p2p-tag-{tag}">{h(scenario.tag)}</span></div>
                      <strong>{h(scenario.name)}</strong>
                      <span class="p2p-muted">{h(scenario.expected)}</span>
                      <code>{h(invoice.invoice_id)} · {h(format_money(invoice.total, invoice.currency))}</code>
                    </div>
                    """
                )
                if st.button("Select", key=f"scenario_select_{scenario.key}", width="stretch"):
                    choose(f"{scenario.key} · {scenario.name}", invoice, None)
                    st.rerun()

    with st.expander("Upload or paste JSON", expanded=bool(held_id)):
        upload = st.file_uploader("Invoice JSON", type=["json"], key="process_upload")
        pasted = st.text_area("Or paste invoice JSON", height=200, key="process_paste")
        if st.button("Validate invoice", key="process_validate"):
            if upload is not None:
                invoice, errors = parse(upload.getvalue())
                choose(f"Uploaded file · {upload.name}", invoice, errors)
                st.rerun()
            elif pasted.strip():
                invoice, errors = parse(pasted)
                choose("Pasted JSON", invoice, errors)
                st.rerun()
            else:
                st.error("Upload a file or paste invoice JSON first.")


def checklist(invoice: Invoice) -> list[tuple[str, str]]:
    rows = [
        ("schema", "All required fields present; types valid"),
        ("invoice_id", f"{invoice.invoice_id} (normalised to upper case)"),
    ]
    rows += [
        (f"line_items[{index}]", f"{line.quantity} × {line.unit_price} = {line.line_total}")
        for index, line in enumerate(invoice.line_items)
    ]
    rows += [
        ("subtotal", f"Sum of line totals = {invoice.subtotal}"),
        ("total", f"Subtotal + tax ({invoice.tax}) = {invoice.total}"),
        ("currency", invoice.currency),
    ]
    return rows


def run(invoice: Invoice, supersede: SupersedeRequest | None = None) -> None:
    try:
        with st.spinner("Running workflow…"):
            result = service().process_invoice(invoice, supersede=supersede)
    except ModelAuthenticationError:
        mark_authentication_failure()
    except SupersedeRefusedError as exc:
        st.session_state["flash"] = {"kind": "error", "text": str(exc)}
        st.rerun()
    except ValueError as exc:
        if "different content" in str(exc):
            st.error(
                "An invoice with this number already exists for this supplier with different content."
            )
        else:
            show_failure(
                "The workflow could not finish. The case is saved; run the same invoice again to resume.",
                exc,
            )
        return
    except Exception as exc:
        show_failure(
            "The workflow could not finish. The case is saved; run the same invoice again to resume.",
            exc,
        )
        return
    st.session_state.update(
        process_case_id=result["case_id"],
        process_is_replay=result["is_replay"],
        process_step=2,
    )
    # The run is over: a superseded held case is no longer offered, and after a plain run the next
    # "Process corrected invoice" starts a new flow instead of reopening this result.
    st.session_state.pop("process_supersedes", None)
    st.query_params.pop("supersedes", None)
    st.rerun()


def supersede_choice(invoice: Invoice) -> tuple[dict | None, str]:
    """The held case this invoice replaces (None for a plain run) and the reviewer's note."""
    requested = st.session_state.get("process_supersedes")
    held = {row["case_id"]: row for row in service().held_cases_for(invoice)}
    if requested and requested not in held:
        st.warning(
            f"{requested} can't be superseded by {invoice.invoice_id}. A correction replaces a "
            "completed Request info case of the same supplier and purchase order that no other "
            "invoice has superseded."
        )
    if not held:
        return None, ""
    options = [None, *held]
    with st.container(border=True, key="process-supersede"):
        render_html(
            '<h3 class="p2p-panel-title">Corrects a held invoice</h3>'
            '<p class="p2p-caption">Link this invoice to the held case it replaces. The held case '
            "keeps its Request info outcome and releases its reserved quantities to this invoice. "
            "The link can't be undone.</p>"
        )
        choice = st.selectbox(
            "Held case",
            options,
            index=options.index(requested) if requested in held else 0,
            format_func=lambda case_id: (
                "No, process as a new invoice"
                if case_id is None
                else f"{case_id} · {held[case_id]['invoice_id']} · "
                f"{format_money(held[case_id]['total'], held[case_id]['currency'])}"
            ),
            key="process_supersede_case",
        )
        if choice is None:
            return None, ""
        note = st.text_area(
            "Note",
            placeholder="Why does this invoice replace the held case?",
            key="process_supersede_note",
        )
    return held[choice], note.strip()


def confirm_supersede(invoice: Invoice) -> None:
    request = st.session_state["process_confirm"]

    def body() -> None:
        render_html(
            f"""
            <p class="p2p-dialog-copy">{h(invoice.invoice_id)} replaces held case {h(request["held_case_id"])}. The held case keeps its Request info outcome and releases its reserved quantities; then the workflow checks {h(invoice.invoice_id)} as usual. The link is recorded on both cases and can't be undone.</p>
            <div class="p2p-note-echo">{h(f"Note: {request['note']}")}</div>
            """
        )
        with st.container(horizontal=True, horizontal_alignment="right"):
            if st.button("Cancel", key="process_supersede_cancel"):
                clear_confirmation()
                st.rerun()
            if st.button("Confirm supersede", key="process_supersede_confirm", type="primary"):
                clear_confirmation()
                run(
                    invoice,
                    SupersedeRequest(
                        held_case_id=request["held_case_id"],
                        reviewer=current_reviewer(),
                        note=request["note"],
                    ),
                )

    st.dialog(
        f"Supersede {request['held_invoice_id']} with {invoice.invoice_id}?",
        on_dismiss=clear_confirmation,
    )(body)()


def validate_step() -> None:
    invoice: Invoice | None = st.session_state.get("process_invoice")
    errors = st.session_state.get("process_errors") or []
    label = st.session_state.get("process_label", "")
    title = f"Validate {invoice.invoice_id}" if invoice else "Validate invoice"
    with st.container(border=True, key="process-validate-card"):
        render_html(f'<div class="p2p-step-head"><span>{h(label)}</span><h2>{h(title)}</h2></div>')
        if errors or invoice is None:
            render_field_errors(errors)
            st.button("Back", key="process_back", on_click=go_to, args=(0,))
            return
        rows = "".join(
            f'<div class="p2p-validation-row">{icon("check_circle")}<code>{h(field)}</code><span>{h(text)}</span></div>'
            for field, text in checklist(invoice)
        )
        render_html(f'<div class="p2p-validation">{rows}</div>')
        held, note = supersede_choice(invoice)
        with st.container(horizontal=True, horizontal_alignment="right"):
            st.button("Back", key="process_back", on_click=go_to, args=(0,))
            run_clicked = st.button("Run workflow", type="primary", key="process_run")
        if run_clicked:
            if held is None:
                run(invoice)
            elif len(note) < MIN_NOTE:
                st.error(f"Add a note of at least {MIN_NOTE} characters to supersede a held case.")
            else:
                st.session_state["process_confirm"] = {
                    "held_case_id": held["case_id"],
                    "held_invoice_id": held["invoice_id"],
                    "note": note,
                }
    if st.session_state.get("process_confirm"):
        confirm_supersede(invoice)


def result_step() -> None:
    app = service()
    case_id: str | None = st.session_state.get("process_case_id")
    case = app.get_case(case_id) if case_id else None
    if not case_id or not case:
        st.error("The processed case could not be loaded.")
        st.button("Process another", key="process_another", on_click=reset)
        return
    status = display_status(case)
    label = st.session_state.get("process_label", "")
    with st.container(border=True, key="process-result-card"):
        render_html(
            f"""
            <div class="p2p-step-head p2p-step-head-row">
              <div><span>{h(label)}</span><h2>{h(case["invoice_id"])} → {h(status.label)}</h2></div>
              {status_pill(status.key, status.label)}
            </div>
            """
        )
        if st.session_state.get("process_is_replay"):
            st.info(
                "This invoice was already processed. Identical invoices return their original "
                f"case, {case_id}."
            )
        held = next(
            (item["data"] for item in case["audit"] if item["event_type"] == "SUPERSEDES_CASE"),
            None,
        )
        if held:
            st.info(
                f"Supersedes {held['held_case_id']} ({held['held_invoice_id']}). Its reserved "
                "quantities were released to this invoice."
            )
        if status.key == "recovery":
            st.warning(f"{case_id} needs recovery. Run the same invoice again to resume.")
        events = timeline(case, local_zone(st.context.timezone), datetime.now(UTC))
        rows = "".join(
            f'<div class="p2p-run-event"><i style="background:{event.dot}"></i><span>{h(event.label)}</span><code>{h(event.delta)}</code></div>'
            for event in events
        )
        render_html(f'<div class="p2p-run-events">{rows}</div>')
        with st.container(horizontal=True, horizontal_alignment="right"):
            st.button("Process another", key="process_another", on_click=reset)
            if st.button("Open case", type="primary", key="process_open_case"):
                switch_to("review" if case["status"] == "PENDING_REVIEW" else "cases", case=case_id)


def render() -> None:
    requested = st.query_params.get("supersedes")
    if requested and requested != st.session_state.get("process_supersedes"):
        # A new correction request starts its own flow instead of reopening an earlier result.
        for key in STATE_KEYS:
            st.session_state.pop(key, None)
        st.session_state["process_supersedes"] = requested
    step = st.session_state.get("process_step", 0)
    top_bar(["Process invoice"], "/process")
    page_header(
        "New workflow",
        "Process an invoice",
        "Pick a scenario, validate it, then run the controlled workflow.",
    )
    stepper(step)
    if step == 0:
        scenario_step()
    elif step == 1:
        validate_step()
    else:
        result_step()


render()
