"""Recovery actions on the Cases, Review and Process pages, driven through AppTest."""

import json
import logging
import sqlite3
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from workflow_support import BrokenMatcher, use_workflow

from p2p.application.service import CaseService, create_runtime
from p2p.domain.models import DecisionType, HumanReview
from p2p.infrastructure.database import demo_invoices

REPO = Path(__file__).parents[2]
PAGES = "src/p2p/ui/pages"


@pytest.fixture
def database_url(tmp_path, monkeypatch) -> str:
    url = f"sqlite:///{tmp_path / 'recovery.db'}"
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("P2P_DATABASE_URL", url)
    monkeypatch.setenv("P2P_RULES_PATH", str(REPO / "config" / "business_rules.yaml"))
    return url


def stored_case(database_url: str, case_id: str) -> dict:
    runtime = create_runtime(database_url=database_url, demo_mode=True)
    try:
        return CaseService(runtime).get_case(case_id)
    finally:
        runtime.close()


def drop_checkpoint(database_url: str, case_id: str) -> None:
    with sqlite3.connect(database_url.removeprefix("sqlite:///")) as connection:
        for table in ("checkpoints", "writes"):
            connection.execute(f"DELETE FROM {table} WHERE thread_id = ?", (case_id,))


def failed_processing(database_url: str) -> str:
    """A case whose processing run failed: PROCESSING with a stored error."""
    runtime = create_runtime(database_url=database_url, demo_mode=True)
    try:
        app = CaseService(runtime)
        use_workflow(
            app,
            matcher=BrokenMatcher(OSError("disk full")),
            checkpointer=runtime.graph.checkpointer,
        )
        with pytest.raises(OSError):
            app.process_invoice(demo_invoices()["03_large_price_variance"])
        [row], _ = app.query_cases()
        return row["case_id"]
    finally:
        runtime.close()


def failed_review_run(database_url: str, monkeypatch) -> str:
    """A pending case whose confirmed review was bound, then its resume failed."""
    runtime = create_runtime(database_url=database_url, demo_mode=True)
    try:
        app = CaseService(runtime)
        case_id = app.process_invoice(demo_invoices()["03_large_price_variance"])["case_id"]

        def resume_fails(*args, **kwargs):
            raise OSError("disk full")

        with monkeypatch.context() as patch:
            patch.setattr(runtime.graph, "invoke", resume_fails)
            review = HumanReview(reviewer="Bea Original", action=DecisionType.REJECT, comment="dup")
            with pytest.raises(OSError):
                app.review_case(case_id, review)
        return case_id
    finally:
        runtime.close()


def open_page(page: str, **query_params: str) -> AppTest:
    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=15)
    for key, value in query_params.items():
        at.query_params[key] = value
    at.run()
    at.switch_page(f"{PAGES}/{page}.py").run()
    return at


def logged_failures(caplog) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name.startswith("p2p.ui") and record.levelno == logging.ERROR and record.exc_info
    ]


def test_retry_on_the_cases_page_finishes_a_failed_review_run(database_url, monkeypatch):
    """#8: Retry applies the review that was confirmed and bound before the run failed."""
    case_id = failed_review_run(database_url, monkeypatch)
    at = open_page("cases", case=case_id)
    assert any("OSError: disk full" in code.value for code in at.code)
    assert any(
        "It applies the Reject decision Bea Original already submitted." in warning.value
        for warning in at.warning
    )

    at.button(key=f"retry_{case_id}").click().run()

    assert not at.exception
    assert at.toast[0].value == "INV-LARGE processed again · Rejected · Human"
    case = stored_case(database_url, case_id)
    review = case["state"]["human_review"]
    assert (case["status"], case["last_error"]) == ("COMPLETED", None)
    assert (review["reviewer"], review["action"], review["comment"]) == (
        "Bea Original",
        "REJECT",
        "dup",
    )


def test_a_refused_retry_shows_and_logs_the_reason(database_url, monkeypatch, caplog):
    """#18: a refusal raised before the workflow runs is shown and logged, not discarded."""
    case_id = failed_processing(database_url)
    at = open_page("cases", case=case_id)

    def refuse(self, invoice, *args, **kwargs):
        raise ValueError("Checkpoint invoice identity does not match the persisted case")

    monkeypatch.setattr(CaseService, "process_invoice", refuse)
    at.button(key=f"retry_{case_id}").click().run()

    assert not at.exception
    assert any("could not finish" in error.value for error in at.error)
    assert any(
        code.value == "ValueError: Checkpoint invoice identity does not match the persisted case"
        for code in at.code
    )
    assert [record.getMessage() for record in logged_failures(caplog)] == [
        f"Retry of {case_id} failed"
    ]


def test_a_retry_whose_case_cannot_be_reloaded_reports_it(database_url, monkeypatch, caplog):
    """#18: the reload after a successful retry can raise; it must say the retry finished."""
    case_id = failed_processing(database_url)
    at = open_page("cases", case=case_id)
    process_invoice = CaseService.process_invoice

    def then_lose_checkpoint(self, invoice, *args, **kwargs):
        result = process_invoice(self, invoice, *args, **kwargs)
        drop_checkpoint(database_url, result["case_id"])
        return result

    monkeypatch.setattr(CaseService, "process_invoice", then_lose_checkpoint)
    at.button(key=f"retry_{case_id}").click().run()

    assert not at.exception
    assert [error.value for error in at.error if "Retry" in error.value] == [
        "Retry processing finished, but the case could not be reloaded. Refresh the page."
    ]
    assert not any("could not finish" in error.value for error in at.error)
    assert [record.getMessage() for record in logged_failures(caplog)] == [
        f"Retry of {case_id} finished, but the case could not be reloaded"
    ]


def test_retry_applies_only_the_confirmed_review_that_is_stored(database_url, monkeypatch):
    """#8: Retry applies the stored submission unchanged; it never takes a new decision."""
    case_id = failed_review_run(database_url, monkeypatch)
    submitted = stored_case(database_url, case_id)["submitted_review"]
    at = open_page("cases", case=case_id)

    at.button(key=f"retry_{case_id}").click().run()

    assert not at.exception
    case = stored_case(database_url, case_id)
    assert case["submitted_review"] == submitted
    assert {key: case["state"]["human_review"][key] for key in submitted} == submitted
    assert (case["state"]["outcome"]["action"], case["state"]["outcome"]["source"]) == (
        "REJECT",
        "HUMAN_OVERRIDDEN",
    )


def test_a_pending_case_without_a_confirmed_review_offers_no_retry(database_url):
    """#8: with no stored review, nothing exists for Retry to apply; the case stays pending."""
    runtime = create_runtime(database_url=database_url, demo_mode=True)
    try:
        app = CaseService(runtime)
        case_id = app.process_invoice(demo_invoices()["03_large_price_variance"])["case_id"]
        runtime.repository.record_error(case_id, "OSError: disk full")
    finally:
        runtime.close()
    before = stored_case(database_url, case_id)

    at = open_page("cases", case=case_id)

    assert not at.exception
    assert f"retry_{case_id}" not in {button.key for button in at.button}
    after = stored_case(database_url, case_id)
    assert (after["status"], after["checkpoint_id"]) == ("PENDING_REVIEW", before["checkpoint_id"])
    assert after["submitted_review"] is None
    assert after["state"].get("human_review") is None
    assert after["state"].get("outcome") is None


def test_retry_of_a_failed_processing_run_reprocesses_without_a_decision(database_url):
    """#8: with no stored review, Retry reprocesses; the case returns to review undecided."""
    case_id = failed_processing(database_url)
    at = open_page("cases", case=case_id)
    assert not any("already submitted" in warning.value for warning in at.warning)

    at.button(key=f"retry_{case_id}").click().run()

    assert not at.exception
    case = stored_case(database_url, case_id)
    assert (case["status"], case["last_error"]) == ("PENDING_REVIEW", None)
    assert case["submitted_review"] is None
    assert case["state"].get("human_review") is None
    assert case["state"].get("outcome") is None


def test_a_refused_review_shows_and_logs_the_reason(database_url, caplog):
    """#18: a review refused before the workflow runs names the refusal and is logged."""
    runtime = create_runtime(database_url=database_url, demo_mode=True)
    try:
        app = CaseService(runtime)
        case_id = app.process_invoice(demo_invoices()["03_large_price_variance"])["case_id"]
    finally:
        runtime.close()
    at = open_page("review", case=case_id)
    at.radio(key=f"review_decision_{case_id}").set_value(DecisionType.REQUEST_INFO).run()
    at.button(key=f"review_submit_{case_id}").click().run()

    # Another reviewer's decision is bound while this confirmation is open.
    runtime = create_runtime(database_url=database_url, demo_mode=True)
    try:
        app = CaseService(runtime)
        other = HumanReview(reviewer="Bea Original", action=DecisionType.REJECT, comment="dup")
        runtime.repository.bind_review(
            case_id, other.model_dump(mode="json"), app.get_case(case_id)["checkpoint_id"]
        )
    finally:
        runtime.close()
    at.button(key="review_confirm_button").click().run()

    assert not at.exception
    assert any("could not be applied" in error.value for error in at.error)
    assert any(
        code.value == "ValueError: A different review is already durably submitted for this case"
        for code in at.code
    )
    assert [record.getMessage() for record in logged_failures(caplog)] == [
        f"Review of {case_id} could not be applied"
    ]


def test_the_process_result_reports_a_case_that_cannot_be_reconciled(database_url, caplog):
    """#18: the result step's get_case can raise a reconcile ValueError; it must not crash."""
    at = open_page("process")
    at.button(key="scenario_select_03").click().run()
    at.button(key="process_run").click().run()
    case_id = at.session_state["process_case_id"]
    drop_checkpoint(database_url, case_id)

    at.run()

    assert not at.exception
    assert any(
        code.value
        == "ValueError: Review checkpoint is missing; restore the checkpoint database before reviewing"
        for code in at.code
    )
    assert "process_another" in {button.key for button in at.button}
    assert [record.getMessage() for record in logged_failures(caplog)] == [
        f"Processed case {case_id} could not be loaded"
    ]


def invoice_payload(quantity: str, unit_price: str, line_total: str) -> dict:
    payload = demo_invoices()["01_exact_match"].model_dump(mode="json")
    payload["line_items"][0].update(quantity=quantity, unit_price=unit_price, line_total=line_total)
    payload.update(subtotal=line_total, total=line_total)
    return payload


OUT_OF_BOUNDS = [
    pytest.param(("10", "1e30", "1e31"), id="#13: 1e30 overflows exact-cent rounding"),
    pytest.param(("10", "1e999999", "0"), id="review 1: quantity x unit price overflows"),
    pytest.param(("1e-30", "1e30", "1"), id="review 2: unit price too large for cents"),
]


@pytest.mark.parametrize("line", OUT_OF_BOUNDS)
def test_an_amount_out_of_bounds_is_a_field_error_on_upload(database_url, line):
    """#13: an amount too large or too precise must be listed as a field error, not crash."""
    at = open_page("process")

    at.text_area(key="process_paste").set_value(json.dumps(invoice_payload(*line))).run()
    at.button(key="process_validate").click().run()

    assert not at.exception
    assert any("need fixing" in error.value for error in at.error)
    assert "line_items[0]" in "".join(element.proto.body for element in at.get("html"))


def test_a_unit_price_too_large_for_cents_cannot_be_run_or_stored(database_url):
    """#13 (review 2): it used to validate, run, and then crash the Cases and Review detail."""
    at = open_page("process")

    payload = invoice_payload("1e-30", "1e30", "1")
    at.text_area(key="process_paste").set_value(json.dumps(payload)).run()
    at.button(key="process_validate").click().run()

    assert "process_run" not in {button.key for button in at.button}
    runtime = create_runtime(database_url=database_url, demo_mode=True)
    try:
        assert CaseService(runtime).query_cases()[0] == []
    finally:
        runtime.close()
