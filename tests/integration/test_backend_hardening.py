import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete
from streamlit.testing.v1 import AppTest
from workflow_support import BrokenMatcher, FailingModel, use_workflow

from p2p.application.service import CaseService, create_runtime
from p2p.domain.models import CritiqueResult, DecisionType, HumanReview, InvestigationResult
from p2p.infrastructure.database import CASES, Database, demo_invoices
from p2p.ports import ModelUnavailableError
from p2p.ui.viewmodels import display_status

REPO = Path(__file__).parents[2]


@contextmanager
def command_running(app: CaseService):
    """Hold the command lock from another thread, as a long workflow run would."""
    held, release = threading.Event(), threading.Event()

    def hold() -> None:
        with app.runtime.repository.command_lock():
            held.set()
            release.wait(10)

    worker = threading.Thread(target=hold)
    worker.start()
    assert held.wait(5)
    try:
        yield
    finally:
        release.set()
        worker.join()


def test_reads_return_stored_rows_while_a_command_runs(app):
    case_id = app.process_invoice(demo_invoices()["03_large_price_variance"])["case_id"]

    with command_running(app):
        started = time.monotonic()
        app.refresh_open_cases()
        case = app.get_case(case_id)
        rows, total = app.query_cases()
        elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert (case["busy"], case["checkpoint_id"], case["status"]) == (True, None, "PENDING_REVIEW")
    assert display_status(case).label == "Processing"
    assert case["audit"]
    assert (total, rows[0]["case_id"]) == (1, case_id)

    case = app.get_case(case_id)
    assert case["busy"] is False
    assert case["checkpoint_id"]


def test_review_page_waits_for_a_busy_case_instead_of_offering_a_decision(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'busy.db'}"
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("P2P_DATABASE_URL", url)
    monkeypatch.setenv("P2P_RULES_PATH", str(REPO / "config" / "business_rules.yaml"))
    runtime = create_runtime(database_url=url, demo_mode=True)
    app = CaseService(runtime)
    try:
        case_id = app.process_invoice(demo_invoices()["03_large_price_variance"])["case_id"]
        at = AppTest.from_file(str(REPO / "app.py"), default_timeout=15)
        at.query_params["case"] = case_id
        at.run()  # Startup migrates under the command lock; start the app before the command.
        with command_running(app):
            started = time.monotonic()
            at.switch_page("src/p2p/ui/pages/review.py").run()
            elapsed = time.monotonic() - started
    finally:
        runtime.close()

    assert elapsed < 5.0

    assert not at.exception
    assert f"review_decision_{case_id}" not in {radio.key for radio in at.radio}
    assert any("being processed" in caption.value for caption in at.caption)


def test_sqlite_runs_in_wal_mode_with_a_busy_timeout(tmp_path):
    runtime = create_runtime(database_url=f"sqlite:///{tmp_path / 'wal.db'}", demo_mode=True)
    try:
        with runtime.repository.database.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
        checkpoint = runtime.checkpoint_connection
        assert checkpoint.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://localhost/p2p",
        "sqlite://",
        "sqlite:///:memory:",
        # SQLAlchemy reads ?timeout as an option, but the checkpointer would open
        # "p2p.db?timeout=10" as a second file.
        "sqlite:///p2p.db?timeout=10",
    ],
)
def test_only_sqlite_file_urls_are_accepted(url):
    with pytest.raises(ValueError, match="sqlite:/// file path"):
        Database(url)


def test_processing_reads_only_invoices_that_share_its_identity_or_purchase_order(tmp_path):
    url = f"sqlite:///{tmp_path / 'scoped.db'}"
    runtime = create_runtime(database_url=url, demo_mode=True)
    try:
        app = CaseService(runtime)
        other = app.process_invoice(demo_invoices()["03_large_price_variance"])["case_id"]
        with sqlite3.connect(tmp_path / "scoped.db") as connection:
            connection.execute(
                "UPDATE cases SET invoice_payload = '{not json' WHERE case_id = ?", (other,)
            )

        result = app.process_invoice(demo_invoices()["01_exact_match"])
    finally:
        runtime.close()

    assert result["status"] == "COMPLETED"


def test_every_model_step_records_which_model_answered(app):
    case_id = app.process_invoice(demo_invoices()["03_large_price_variance"])["case_id"]

    calls = {
        event["event_type"]: event["data"].get("model_call")
        for event in app.get_case(case_id)["audit"]
        if event["actor"].startswith("ai_")
    }

    assert set(calls) >= {"INVESTIGATION_COMPLETED", "RESOLUTION_PROPOSED", "CRITIQUE_FAILED"}
    assert all(call == {"provider": "mock", "model": "mock"} for call in calls.values())


def test_decision_events_carry_the_time_the_decision_was_made(app):
    case_id = app.process_invoice(demo_invoices()["03_large_price_variance"])["case_id"]
    reviewed_at = datetime(2026, 1, 5, 9, 30, tzinfo=UTC)
    review = HumanReview(action=DecisionType.REQUEST_INFO, reviewed_at=reviewed_at)
    time.sleep(0.01)  # The save happens measurably later than the stated decision time.

    outcome = app.review_case(case_id, review)["state"]["outcome"]

    stamps = {
        event["event_type"]: datetime.fromisoformat(event["timestamp"])
        for event in app.get_case(case_id)["audit"]
    }
    assert stamps["HUMAN_DECISION_RECEIVED"] == reviewed_at
    assert stamps["CASE_COMPLETED"] == outcome.completed_at


def test_a_failed_run_can_be_retried_from_the_case_page(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'retry.db'}"
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("P2P_DATABASE_URL", url)
    monkeypatch.setenv("P2P_RULES_PATH", str(REPO / "config" / "business_rules.yaml"))
    runtime = create_runtime(database_url=url, demo_mode=True)
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
    finally:
        runtime.close()

    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=15)
    at.query_params["case"] = row["case_id"]
    at.run()
    at.switch_page("src/p2p/ui/pages/cases.py").run()

    assert not at.exception
    assert any("OSError: disk full" in code.value for code in at.code)

    at.button(key=f"retry_{row['case_id']}").click().run()

    assert not at.exception
    assert at.toast[0].value == "INV-LARGE processed again · Awaiting review"


def test_commands_log_their_start_outcome_and_duration(app, caplog):
    caplog.set_level(logging.INFO, logger="p2p")

    case_id = app.process_invoice(demo_invoices()["01_exact_match"])["case_id"]

    messages = [record.getMessage() for record in caplog.records if record.name == "p2p.service"]
    assert messages[0] == f"Processing started for {case_id}"
    assert messages[1].startswith(f"Processing finished for {case_id}: COMPLETED in ")


def test_a_failed_review_run_keeps_its_error_until_the_review_is_applied(app, monkeypatch):
    case_id = app.process_invoice(demo_invoices()["03_large_price_variance"])["case_id"]
    review = HumanReview(reviewer="Ana Reviewer", action=DecisionType.REJECT, comment="dup")
    invoke = app.runtime.graph.invoke

    def resume_fails(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(app.runtime.graph, "invoke", resume_fails)
    with pytest.raises(OSError):
        app.review_case(case_id, review)
    monkeypatch.setattr(app.runtime.graph, "invoke", invoke)

    app.refresh_open_cases()
    case = app.get_case(case_id)
    assert (case["status"], case["last_error"]) == ("PENDING_REVIEW", "OSError: disk full")
    assert display_status(case).label == "Needs recovery"

    app.review_case(case_id, review, checkpoint_id=case["checkpoint_id"])

    assert app.get_case(case_id)["last_error"] is None


@pytest.mark.parametrize(
    ("schema", "note"),
    [
        # The Investigator never answered: there is no recommendation at all.
        (InvestigationResult, "Model unavailable, so the AI made no recommendation."),
        # The Resolver answered but the Critic did not: the recommendation is unchecked.
        (CritiqueResult, "Model unavailable before the Critic checked this recommendation."),
    ],
)
def test_reviewer_sees_model_unavailable_not_a_validation_failure(
    tmp_path, monkeypatch, schema, note
):
    url = f"sqlite:///{tmp_path / 'quota.db'}"
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("P2P_DATABASE_URL", url)
    monkeypatch.setenv("P2P_RULES_PATH", str(REPO / "config" / "business_rules.yaml"))
    runtime = create_runtime(database_url=url, demo_mode=True)
    try:
        app = CaseService(runtime)
        quota = ModelUnavailableError("OpenAI quota exhausted (insufficient_quota)")
        use_workflow(app, FailingModel(quota, schema), checkpointer=runtime.graph.checkpointer)
        case_id = app.process_invoice(demo_invoices()["03_large_price_variance"])["case_id"]
    finally:
        runtime.close()

    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=15)
    at.query_params["case"] = case_id
    at.run()
    at.switch_page("src/p2p/ui/pages/review.py").run()

    assert not at.exception
    page = " ".join(element.proto.body for element in at.get("html"))
    assert note in page
    assert "Model output failed validation" not in page
    assert "insufficient_quota" in page


def test_a_case_row_lost_after_the_claim_is_a_storage_fault_not_a_missing_case(app, monkeypatch):
    """`_stored_case`: the claim wrote the row, so its absence must stop the command loudly."""
    repository = app.runtime.repository
    claim = repository.claim_case

    def claim_then_lose_the_row(case_id, invoice, supersede=None):
        claimed = claim(case_id, invoice, supersede)
        with repository.database.engine.begin() as connection:
            connection.execute(delete(CASES).where(CASES.c.case_id == claimed[0]))
        return claimed

    monkeypatch.setattr(repository, "claim_case", claim_then_lose_the_row)

    with pytest.raises(LookupError, match="^No persisted case exists for CASE-LOST$"):
        app.process_invoice(demo_invoices()["01_exact_match"], case_id="CASE-LOST")
