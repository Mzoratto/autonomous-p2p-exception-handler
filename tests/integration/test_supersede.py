"""Superseding a held (REQUEST_INFO) case with a corrected invoice."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError
from sqlalchemy import event, update
from streamlit.testing.v1 import AppTest

from p2p.application.service import CaseService, create_runtime
from p2p.domain.models import DecisionType, HumanReview, Invoice, SupersedeRequest
from p2p.infrastructure.database import CaseRow, demo_invoices
from p2p.ports import SupersedeRefusedError
from p2p.ui.viewmodels import display_status, timeline

REPO = Path(__file__).parents[2]
PAGES = "src/p2p/ui/pages"
PO = "PO-1006"
HELD_LINES = {"1": Decimal("10")}


def corrected(invoice_id: str = "INV-LARGE-2", unit_price: str = "200", **changes) -> Invoice:
    """Scenario 03's invoice re-issued at the PO price (or another price) under a new number."""
    base = demo_invoices()["03_large_price_variance"]
    line = base.line_items[0]
    total = line.quantity * Decimal(unit_price)
    payload = base.model_dump()
    payload.update(
        invoice_id=invoice_id,
        line_items=[{**line.model_dump(), "unit_price": Decimal(unit_price), "line_total": total}],
        subtotal=total,
        total=total + base.tax,
    )
    payload.update(changes)
    return Invoice.model_validate(payload)


def hold(app: CaseService, reviewer: str = "Ana Reviewer", action=DecisionType.REQUEST_INFO) -> str:
    case_id = app.process_invoice(demo_invoices()["03_large_price_variance"])["case_id"]
    app.review_case(
        case_id, HumanReview(reviewer=reviewer, action=action, comment="Send the PO amendment")
    )
    return case_id


def link(held_id: str, reviewer: str = "Ana Reviewer", note: str = "Corrected at the PO price"):
    return SupersedeRequest(held_case_id=held_id, reviewer=reviewer, note=note)


def events(case: dict, kind: str) -> list[dict]:
    return [item for item in case["audit"] if item["event_type"] == kind]


def reserved(app: CaseService) -> dict[str, Decimal]:
    return app.runtime.repository.reserved_quantities(PO, "outside")


def test_correction_at_the_po_price_supersedes_the_held_case_and_completes_autonomously(app):
    held_id = hold(app)

    result = app.process_invoice(corrected(), supersede=link(held_id))

    outcome = result["state"]["outcome"]
    assert result["status"] == "COMPLETED"
    assert (outcome.action, outcome.source.value) == (DecisionType.APPROVE, "AUTONOMOUS")
    new = app.get_case(result["case_id"])
    [loaded] = events(new, "ERP_CONTEXT_LOADED")
    assert loaded["data"]["context"]["reserved_quantities"] == {}
    assert new["state"]["match_result"]["discrepancies"] == []
    held = app.get_case(held_id)
    assert held["superseded_by"] == result["case_id"]
    assert held["superseded_at"] is not None
    assert (held["status"], held["state"]["outcome"]["action"]) == ("COMPLETED", "REQUEST_INFO")
    [superseded] = events(held, "CASE_SUPERSEDED")
    [supersedes] = events(new, "SUPERSEDES_CASE")
    assert superseded["data"]["superseded_by"] == result["case_id"]
    assert superseded["data"]["new_invoice_id"] == "INV-LARGE-2"
    assert supersedes["data"]["held_case_id"] == held_id
    assert supersedes["data"]["held_invoice_id"] == "INV-LARGE"
    for data in (superseded["data"], supersedes["data"]):
        assert data["released"] == {"1": "10"}
        assert data["note"] == "Corrected at the PO price"
    # Only the new case reserves PO capacity now.
    assert reserved(app) == HELD_LINES
    assert app.runtime.repository.reserved_quantities(PO, result["case_id"]) == {}


def test_correction_without_a_link_is_still_flagged_against_the_held_case(app):
    held_id = hold(app)

    result = app.process_invoice(corrected())

    kinds = {item.type.value for item in result["state"]["match_result"].discrepancies}
    assert result["status"] == "PENDING_REVIEW"
    assert {"QUANTITY_EXCEEDS_ORDER", "QUANTITY_EXCEEDS_RECEIPT"} <= kinds
    assert app.get_case(held_id)["superseded_by"] is None


@pytest.mark.parametrize(
    "linker,same",
    [("Ana Reviewer", True), (" ana reviewer ", True), ("Bea Linker", False)],
)
def test_both_events_record_who_held_and_who_linked(app, linker, same):
    held_id = hold(app, reviewer="Ana Reviewer")

    new_id = app.process_invoice(corrected(), supersede=link(held_id, reviewer=linker))["case_id"]

    for case_id, kind in ((held_id, "CASE_SUPERSEDED"), (new_id, "SUPERSEDES_CASE")):
        [item] = events(app.get_case(case_id), kind)
        assert item["data"]["held_reviewer"] == "Ana Reviewer"
        assert item["data"]["reviewer"] == linker.strip()
        assert item["data"]["same_reviewer"] is same


def setup_pending(app):
    return app.process_invoice(demo_invoices()["03_large_price_variance"])["case_id"], corrected()


def setup_approved(app):
    return hold(app, action=DecisionType.APPROVE), corrected()


def setup_rejected(app):
    return hold(app, action=DecisionType.REJECT), corrected()


def setup_other_supplier(app):
    return hold(app), corrected(supplier_id="SUP-001", supplier_name="Acme Components")


def setup_other_po(app):
    return hold(app), corrected(po_number="PO-1001")


def setup_unknown(app):
    hold(app)
    return "CASE-UNKNOWN", corrected()


def setup_already_superseded(app):
    held_id = hold(app)
    app.process_invoice(corrected(), supersede=link(held_id))
    return held_id, corrected("INV-LARGE-3")


def setup_the_held_invoice_itself(app):
    return hold(app), demo_invoices()["03_large_price_variance"]


def setup_processed_without_link(app):
    held_id = hold(app)
    app.process_invoice(corrected())
    return held_id, corrected()


@pytest.mark.parametrize(
    "setup,message",
    [
        (setup_pending, "Request info"),
        (setup_approved, "Request info"),
        (setup_rejected, "Request info"),
        (setup_other_supplier, "same supplier and purchase order"),
        (setup_other_po, "same supplier and purchase order"),
        (setup_unknown, "No case CASE-UNKNOWN"),
        (setup_already_superseded, "already superseded"),
        (setup_the_held_invoice_itself, "held invoice itself"),
        (setup_processed_without_link, "without this link"),
    ],
)
def test_ineligible_links_are_refused_and_change_nothing(app, setup, message):
    held_id, invoice = setup(app)
    before_cases = len(app.list_cases())
    before_reserved = reserved(app)
    held_before = app.runtime.repository.get_case(held_id)
    events_before = len(app.runtime.repository.for_case(held_id))

    with pytest.raises(SupersedeRefusedError, match=message):
        app.process_invoice(invoice, supersede=link(held_id))

    assert len(app.list_cases()) == before_cases
    assert reserved(app) == before_reserved
    if held_before:
        after = app.runtime.repository.get_case(held_id)
        assert after["superseded_by"] == held_before["superseded_by"]
    assert len(app.runtime.repository.for_case(held_id)) == events_before


def test_same_number_with_new_content_is_still_refused_as_an_identity_conflict(app):
    held_id = hold(app)

    with pytest.raises(ValueError, match="different content"):
        app.process_invoice(corrected("INV-LARGE"), supersede=link(held_id))

    assert app.get_case(held_id)["superseded_by"] is None


@pytest.mark.parametrize("note", ["", "  ", "ok"])
def test_a_link_needs_a_note_of_three_characters(note):
    with pytest.raises(ValidationError):
        SupersedeRequest(held_case_id="CASE-1", reviewer="Ana Reviewer", note=note)


def test_two_concurrent_corrections_supersede_a_held_case_once(tmp_path):
    url = f"sqlite:///{tmp_path / 'race.db'}"
    runtimes = [create_runtime(database_url=url, demo_mode=True) for _ in range(2)]
    try:
        held_id = hold(CaseService(runtimes[0]))
        invoices = [corrected("INV-LARGE-2"), corrected("INV-LARGE-3")]

        def attempt(runtime, invoice):
            try:
                return CaseService(runtime).process_invoice(invoice, supersede=link(held_id))
            except SupersedeRefusedError as exc:
                return exc

        with ThreadPoolExecutor(2) as executor:
            outcomes = list(executor.map(attempt, runtimes, invoices))

        winners = [item for item in outcomes if isinstance(item, dict)]
        losers = [item for item in outcomes if isinstance(item, SupersedeRefusedError)]
        assert (len(winners), len(losers)) == (1, 1)
        app = CaseService(runtimes[0])
        held = app.get_case(held_id)
        assert held["superseded_by"] == winners[0]["case_id"]
        assert len(events(held, "CASE_SUPERSEDED")) == 1
        assert len(app.list_cases()) == 2
    finally:
        for runtime in runtimes:
            runtime.close()


def test_a_failed_commit_leaves_no_link_no_case_and_the_reservation_held(app):
    held_id = hold(app)
    factory = app.runtime.repository.database.session_factory

    def fail_once(session):
        event.remove(factory, "before_commit", fail_once)
        raise OSError("injected commit failure")

    event.listen(factory, "before_commit", fail_once)
    with pytest.raises(OSError, match="injected"):
        app.process_invoice(corrected(), supersede=link(held_id))

    held = app.get_case(held_id)
    assert held["superseded_by"] is None
    assert events(held, "CASE_SUPERSEDED") == []
    assert [case["invoice_id"] for case in app.list_cases()] == ["INV-LARGE"]
    assert reserved(app) == HELD_LINES

    assert app.process_invoice(corrected(), supersede=link(held_id))["status"] == "COMPLETED"


def test_a_failed_run_keeps_the_link_and_resumes_without_duplicate_events(app, monkeypatch):
    held_id = hold(app)
    repo = app.runtime.repository
    save = repo.save_case

    def fail_completion(case_id, invoice, status, state):
        if status == "COMPLETED" and case_id != held_id:
            raise OSError("injected save failure")
        return save(case_id, invoice, status, state)

    monkeypatch.setattr(repo, "save_case", fail_completion)
    with pytest.raises(OSError):
        app.process_invoice(corrected(), supersede=link(held_id))
    monkeypatch.setattr(repo, "save_case", save)

    new_id = app.get_case(held_id)["superseded_by"]
    assert new_id is not None
    resumed = app.process_invoice(corrected(), supersede=link(held_id))

    assert (resumed["case_id"], resumed["status"]) == (new_id, "COMPLETED")
    assert len(events(app.get_case(held_id), "CASE_SUPERSEDED")) == 1
    assert len(events(app.get_case(new_id), "SUPERSEDES_CASE")) == 1
    assert reserved(app) == HELD_LINES


def test_only_the_supersede_command_writes_the_link(app, monkeypatch):
    """Reads, reconciles, saves, error records and the migration backfill keep the link."""
    held_id = hold(app)
    new_id = app.process_invoice(corrected(), supersede=link(held_id))["case_id"]
    repo = app.runtime.repository
    linked_at = repo.get_case(held_id)["superseded_at"]

    def assert_link() -> None:
        stored = repo.get_case(held_id)
        assert (stored["superseded_by"], stored["superseded_at"]) == (new_id, linked_at)
        assert reserved(app) == HELD_LINES
        audit = app.get_case(held_id)["audit"]
        assert len(events({"audit": audit}, "CASE_SUPERSEDED")) == 1
        assert len(events({"audit": audit}, "CASE_COMPLETED")) == 1

    for _ in range(3):
        case = app.get_case(held_id)
        app.list_cases()
        app.refresh_open_cases()
        app.query_cases(view="superseded")
        app.summary()
        repo.save_case(held_id, Invoice.model_validate(case["invoice"]), "COMPLETED", case["state"])
        repo.record_error(held_id, "RuntimeError: simulated")
        assert_link()

    save = repo.save_case
    monkeypatch.setattr(
        repo, "save_case", lambda *args: (_ for _ in ()).throw(OSError("snapshot save failed"))
    )
    with pytest.raises(OSError):
        app.get_case(held_id)
    monkeypatch.setattr(repo, "save_case", save)
    assert_link()

    with repo.database.session_factory() as session:
        session.execute(
            update(CaseRow.__table__)
            .where(CaseRow.__table__.c.case_id == held_id)
            .values(created_at=None)
        )
        session.commit()
    repo.database.create_all()  # backfills the row again
    assert_link()


def test_held_timeline_shows_completion_then_supersede_and_reconcile_keeps_both(app):
    held_id = hold(app)
    new_id = app.process_invoice(corrected(), supersede=link(held_id))["case_id"]
    now = datetime.now(UTC)

    for _ in range(3):
        held = app.get_case(held_id)
        labels = [item.label for item in timeline(held, (UTC, " UTC"), now)]
        assert labels[-3:] == ["Human decision received", "Case completed", "Case superseded"]
        assert labels.count("Case completed") == labels.count("Case superseded") == 1

    new_labels = [item.label for item in timeline(app.get_case(new_id), (UTC, " UTC"), now)]
    assert new_labels[:2] == ["Supersedes held case", "Case created"]


def test_status_views_and_outcomes_count_a_superseded_case_once(app):
    held_id = hold(app)
    new_id = app.process_invoice(corrected(), supersede=link(held_id))["case_id"]
    query = app.runtime.repository.query_cases

    assert display_status(app.get_case(held_id)).label == "Request info · Superseded"
    [row] = query(view="superseded")[0]
    assert (row["case_id"], display_status(row).label) == (held_id, "Request info · Superseded")
    assert query(view="request_info") == ([], 0)
    assert {row["case_id"] for row in query(view="approved")[0]} == {new_id}
    counts = app.view_counts()
    assert (counts["request_info"], counts["superseded"]) == (0, 1)
    outcomes = {entry["key"]: entry["count"] for entry in app.summary()["outcomes"]}
    assert outcomes == {
        "review": 0,
        "approved": 1,
        "rejected": 0,
        "request_info": 0,
        "superseded": 1,
    }


def test_held_cases_offered_for_a_correction_match_its_supplier_and_po(app):
    held_id = hold(app)

    assert [row["case_id"] for row in app.held_cases_for(corrected())] == [held_id]
    assert app.held_cases_for(corrected(po_number="PO-1001")) == []
    app.process_invoice(corrected(), supersede=link(held_id))
    assert app.held_cases_for(corrected("INV-LARGE-3")) == []


def test_migration_adds_the_link_columns_to_an_existing_database(tmp_path):
    path = tmp_path / "existing.db"
    url = f"sqlite:///{path}"
    runtime = create_runtime(database_url=url, demo_mode=True, checkpointer=InMemorySaver())
    held_id = hold(CaseService(runtime))
    runtime.close()
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE cases DROP COLUMN superseded_by")
        connection.execute("ALTER TABLE cases DROP COLUMN superseded_at")

    runtime = create_runtime(database_url=url, demo_mode=True, checkpointer=InMemorySaver())
    try:
        app = CaseService(runtime)
        assert app.runtime.repository.get_case(held_id)["superseded_by"] is None
        assert reserved(app) == HELD_LINES
        new_id = app.process_invoice(corrected(), supersede=link(held_id))["case_id"]
        assert app.runtime.repository.get_case(held_id)["superseded_by"] == new_id
    finally:
        runtime.close()


# --- UI ---------------------------------------------------------------------------------------


@pytest.fixture
def database_url(tmp_path, monkeypatch) -> str:
    url = f"sqlite:///{tmp_path / 'ui.db'}"
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("P2P_DATABASE_URL", url)
    monkeypatch.setenv("P2P_RULES_PATH", str(REPO / "config" / "business_rules.yaml"))
    monkeypatch.setenv("P2P_REVIEWER_NAME", "Ana Reviewer")
    return url


def with_service(database_url: str, action):
    runtime = create_runtime(database_url=database_url, demo_mode=True)
    try:
        return action(CaseService(runtime))
    finally:
        runtime.close()


def html(at: AppTest) -> str:
    return "".join(element.proto.body for element in at.get("html"))


def keys(widgets) -> set[str]:
    return {widget.key for widget in widgets}


def open_process(held_id: str) -> AppTest:
    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=15)
    at.query_params["supersedes"] = held_id
    at.run()
    at.switch_page(f"{PAGES}/process.py").run()
    at.text_area(key="process_paste").set_value(corrected().model_dump_json())
    at.button(key="process_validate").click().run()
    return at


def test_held_case_detail_offers_to_process_a_corrected_invoice(database_url):
    held_id = with_service(database_url, hold)
    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=15)
    at.query_params["case"] = held_id
    at.run()
    at.switch_page(f"{PAGES}/cases.py").run()

    assert not at.exception
    assert "cases_supersede" in keys(at.button)
    at.button(key="cases_supersede").click().run()
    assert not at.exception


def test_process_page_supersedes_the_held_case_after_a_note_and_a_confirmation(database_url):
    held_id = with_service(database_url, hold)
    at = open_process(held_id)

    assert at.selectbox(key="process_supersede_case").value == held_id
    at.button(key="process_run").click().run()
    assert any("at least 3 characters" in error.value for error in at.error)
    assert "process_supersede_confirm" not in keys(at.button)

    at.text_area(key="process_supersede_note").set_value("Corrected at the PO price").run()
    at.button(key="process_run").click().run()
    assert {"process_supersede_cancel", "process_supersede_confirm"} <= keys(at.button)
    at.button(key="process_supersede_confirm").click().run()

    assert not at.exception
    new_id = at.session_state["process_case_id"]
    held = with_service(database_url, lambda app: app.get_case(held_id))
    new = with_service(database_url, lambda app: app.get_case(new_id))
    assert held["superseded_by"] == new_id
    assert (new["state"]["outcome"]["action"], new["state"]["outcome"]["source"]) == (
        "APPROVE",
        "AUTONOMOUS",
    )
    assert any(f"Supersedes {held_id}" in message.value for message in at.info)
    # The held case is now superseded; the page must not keep offering to correct it.
    assert "supersedes" not in at.query_params
    assert "process_supersedes" not in at.session_state


def test_correcting_a_held_case_after_an_earlier_run_starts_a_new_flow_with_the_link(database_url):
    """The Cases button once reopened the earlier result; "Process another" then lost the link."""
    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=15).run()
    at.switch_page(f"{PAGES}/process.py").run()
    at.button(key="scenario_select_03").click().run()
    at.button(key="process_run").click().run()
    held_id = at.session_state["process_case_id"]
    with_service(
        database_url,
        lambda app: app.review_case(
            held_id,
            HumanReview(
                reviewer="Ana Reviewer",
                action=DecisionType.REQUEST_INFO,
                comment="Send the PO amendment",
            ),
        ),
    )

    # Same session, entering as "Process corrected invoice" does (cases.py: switch_to).
    at.query_params["supersedes"] = held_id
    at.switch_page(f"{PAGES}/process.py").run()

    assert not at.exception
    assert any(f"Correcting held case {held_id}" in message.value for message in at.info)
    assert "process_paste" in keys(at.text_area)
    assert "process_another" not in keys(at.button)

    at.text_area(key="process_paste").set_value(corrected().model_dump_json())
    at.button(key="process_validate").click().run()
    assert at.selectbox(key="process_supersede_case").value == held_id
    at.text_area(key="process_supersede_note").set_value("Corrected at the PO price").run()
    at.button(key="process_run").click().run()
    at.button(key="process_supersede_confirm").click().run()

    assert not at.exception
    new_id = at.session_state["process_case_id"]
    new = with_service(database_url, lambda app: app.get_case(new_id))
    held = with_service(database_url, lambda app: app.get_case(held_id))
    assert held["superseded_by"] == new_id
    kinds = {item["type"] for item in new["state"]["match_result"]["discrepancies"]}
    assert not kinds & {"QUANTITY_EXCEEDS_ORDER", "QUANTITY_EXCEEDS_RECEIPT"}


def test_cancel_closes_the_supersede_confirmation_without_a_link(database_url):
    held_id = with_service(database_url, hold)
    at = open_process(held_id)
    at.text_area(key="process_supersede_note").set_value("Corrected at the PO price").run()
    at.button(key="process_run").click().run()

    at.button(key="process_supersede_cancel").click().run()

    assert "process_supersede_confirm" not in keys(at.button)
    assert with_service(database_url, lambda app: app.get_case(held_id))["superseded_by"] is None


@pytest.mark.parametrize("linker,shown", [("Ana Reviewer", True), ("Bea Linker", False)])
def test_case_detail_of_both_cases_flags_the_same_reviewer(database_url, linker, shown):
    def supersede(app):
        held_id = hold(app, reviewer="Ana Reviewer")
        new_id = app.process_invoice(corrected(), supersede=link(held_id, reviewer=linker))[
            "case_id"
        ]
        return held_id, new_id

    for case_id in with_service(database_url, supersede):
        at = AppTest.from_file(str(REPO / "app.py"), default_timeout=15)
        at.query_params["case"] = case_id
        at.run()
        at.switch_page(f"{PAGES}/cases.py").run()
        assert not at.exception
        assert ("Same reviewer" in html(at)) is shown
