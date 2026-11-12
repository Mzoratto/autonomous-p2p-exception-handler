import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.exc import OperationalError
from streamlit.testing.v1 import AppTest

from p2p.application.service import CaseService, create_runtime
from p2p.domain.models import DecisionType, HumanReview, Invoice
from p2p.infrastructure.database import CaseRow, Database, demo_invoices
from p2p.ui.viewmodels import display_status

REPO = Path(__file__).parents[2]

LEGACY_DDL = """
CREATE TABLE cases (
    case_id VARCHAR NOT NULL,
    invoice_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    invoice_payload TEXT NOT NULL,
    state_payload TEXT NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (case_id)
);
CREATE INDEX ix_cases_status ON cases (status);
CREATE INDEX ix_cases_invoice_id ON cases (invoice_id);
CREATE TABLE audit_events (
    event_id VARCHAR NOT NULL,
    case_id VARCHAR NOT NULL,
    timestamp DATETIME NOT NULL,
    event_type VARCHAR NOT NULL,
    actor VARCHAR NOT NULL,
    data_payload TEXT NOT NULL,
    PRIMARY KEY (event_id)
);
"""


def process(app: CaseService, *names: str) -> dict[str, str]:
    invoices = demo_invoices()
    return {name: app.process_invoice(invoices[name])["case_id"] for name in names}


def ids(rows: list[dict]) -> set[str]:
    return {row["case_id"] for row in rows}


def table_snapshot(path) -> tuple:
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT * FROM cases ORDER BY case_id").fetchall()
        indexes = connection.execute("PRAGMA index_list(cases)").fetchall()
    return rows, sorted(indexes)


def legacy_copy(source, legacy, case_ids, corrupt=()) -> list[tuple]:
    """Copy cases and their audit events into a database with the pre-projection schema."""
    with sqlite3.connect(source) as connection:
        cases = [
            connection.execute(
                "SELECT case_id, invoice_id, status, invoice_payload, state_payload, updated_at "
                "FROM cases WHERE case_id = ?",
                (case_id,),
            ).fetchone()
            for case_id in case_ids
        ]
        events = connection.execute(
            "SELECT event_id, case_id, timestamp, event_type, actor, data_payload FROM audit_events"
        ).fetchall()
    cases = [(*case[:4], "{not json", case[5]) if case[0] in corrupt else case for case in cases]
    with sqlite3.connect(legacy) as connection:
        connection.executescript(LEGACY_DDL)
        connection.executemany("INSERT INTO cases VALUES (?, ?, ?, ?, ?, ?)", cases)
        connection.executemany("INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)", events)
    return events


def test_migration_adds_columns_and_backfills_a_legacy_case(tmp_path):
    source = tmp_path / "source.db"
    runtime = create_runtime(
        database_url=f"sqlite:///{source}", demo_mode=True, checkpointer=InMemorySaver()
    )
    case_id = CaseService(runtime).process_invoice(demo_invoices()["01_exact_match"])["case_id"]
    runtime.close()
    legacy = tmp_path / "legacy.db"
    events = legacy_copy(source, legacy, [case_id])

    database = Database(f"sqlite:///{legacy}")
    database.create_all()

    with database.session_factory() as session:
        row = session.get(CaseRow, case_id)
        assert (row.supplier_name, row.currency, row.total_cents) == (
            "Acme Components",
            "EUR",
            100000,
        )
        assert (row.outcome_action, row.outcome_source) == ("APPROVE", "AUTONOMOUS")
        assert row.created_at == min(datetime.fromisoformat(event[2]) for event in events)
        assert row.completed_at is not None
        assert row.review_requested_at is None
    before = table_snapshot(legacy)
    assert {"ix_cases_po_number", "ix_cases_created_at"} <= {index[1] for index in before[1]}

    database.create_all()

    assert table_snapshot(legacy) == before


def test_migration_keeps_unreadable_rows_and_the_app_starts(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    runtime = create_runtime(
        database_url=f"sqlite:///{source}", demo_mode=True, checkpointer=InMemorySaver()
    )
    cases = process(
        CaseService(runtime),
        "01_exact_match",
        "02_small_price_variance",
        "03_large_price_variance",
        "04_quantity_variance",
        "05_quantity_exceeds_receipt",
    )
    valid, oversized, broken, non_scalar, undecodable = cases.values()
    runtime.close()
    legacy = tmp_path / "legacy.db"
    legacy_copy(source, legacy, list(cases.values()), corrupt={broken})
    with sqlite3.connect(legacy) as connection:
        # Valid JSON whose total in cents exceeds SQLite's signed 64-bit INTEGER.
        connection.execute(
            "UPDATE cases SET invoice_payload = json_set(invoice_payload, '$.total', "
            "'92233720368547758.08') WHERE case_id = ?",
            (oversized,),
        )
        # Valid JSON whose supplier name is a list, which SQLite cannot bind.
        connection.execute(
            "UPDATE cases SET invoice_payload = json_set(invoice_payload, '$.supplier_name', "
            "json('[]')) WHERE case_id = ?",
            (non_scalar,),
        )
        # TEXT that is not valid UTF-8: the driver cannot even load the row.
        connection.execute(
            "UPDATE cases SET state_payload = CAST(X'7BFF' AS TEXT) WHERE case_id = ?",
            (undecodable,),
        )

    database = Database(f"sqlite:///{legacy}")
    database.create_all()

    with database.session_factory() as session:
        row = session.get(CaseRow, broken)
        assert row.last_error == "Migration could not read stored state: JSONDecodeError"
        assert row.created_at == row.updated_at
        assert (row.supplier_name, row.total_cents, row.primary_exception) == (None, None, None)
        row = session.get(CaseRow, oversized)
        assert row.last_error == "Migration could not read stored state: ValueError"
        assert (row.supplier_name, row.total_cents) == (None, None)
        row = session.get(CaseRow, non_scalar)
        assert row.last_error == "Migration could not read stored state: ProgrammingError"
        assert (row.supplier_name, row.total_cents, row.created_at) == (None, None, row.updated_at)
        assert session.get(CaseRow, valid).supplier_name == "Acme Components"
    database.engine.dispose()
    with sqlite3.connect(legacy) as connection:
        assert connection.execute(
            "SELECT created_at = updated_at, last_error, supplier_name FROM cases "
            "WHERE case_id = ?",
            (undecodable,),
        ).fetchone() == (1, "Migration could not read stored state: OperationalError", None)

    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("P2P_DATABASE_URL", f"sqlite:///{legacy}")
    monkeypatch.setenv("P2P_RULES_PATH", str(REPO / "config" / "business_rules.yaml"))
    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=15).run()
    at.switch_page("src/p2p/ui/pages/cases.py").run()

    assert not at.exception
    table = at.dataframe[0].value
    status = dict(zip(table["Case"], table["Status"], strict=True))
    assert status == {
        valid: "Approved · Autonomous",
        oversized: "Needs recovery",
        broken: "Needs recovery",
        non_scalar: "Needs recovery",
        undecodable: "Needs recovery",
    }


def test_startup_and_processing_survive_an_undecodable_case(tmp_path):
    url = f"sqlite:///{tmp_path / 'p2p.db'}"
    runtime = create_runtime(database_url=url, demo_mode=True)
    broken = process(CaseService(runtime), "03_large_price_variance")["03_large_price_variance"]
    runtime.close()
    with sqlite3.connect(tmp_path / "p2p.db") as connection:
        connection.execute(
            "UPDATE cases SET state_payload = CAST(X'7BFF' AS TEXT) WHERE case_id = ?", (broken,)
        )

    runtime = create_runtime(database_url=url, demo_mode=True)
    try:
        app = CaseService(runtime)
        app.refresh_open_cases()
        result = app.process_invoice(demo_invoices()["01_exact_match"])

        assert result["status"] == "COMPLETED"
        rows = {row["case_id"]: row for row in app.query_cases(limit=None)[0]}
        assert rows[broken]["last_error"].startswith("ValueError: Stored case could not be read")
        assert display_status(rows[broken]).label == "Needs recovery"
        with pytest.raises(ValueError, match="Stored case could not be read"):
            app.get_case(broken)
    finally:
        runtime.close()


def test_migration_leaves_a_row_unmarked_when_the_database_is_busy(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    runtime = create_runtime(
        database_url=f"sqlite:///{source}", demo_mode=True, checkpointer=InMemorySaver()
    )
    case_id = CaseService(runtime).process_invoice(demo_invoices()["01_exact_match"])["case_id"]
    runtime.close()
    legacy = tmp_path / "legacy.db"
    legacy_copy(source, legacy, [case_id])

    def locked(session, row):
        busy = sqlite3.OperationalError("database is locked")
        busy.sqlite_errorcode = sqlite3.SQLITE_BUSY
        raise OperationalError("UPDATE cases", {}, busy)

    monkeypatch.setattr(Database, "_backfill", staticmethod(locked))
    with pytest.raises(OperationalError):
        Database(f"sqlite:///{legacy}").create_all()
    monkeypatch.undo()

    with sqlite3.connect(legacy) as connection:
        assert connection.execute("SELECT created_at, last_error FROM cases").fetchone() == (
            None,
            None,
        )
    database = Database(f"sqlite:///{legacy}")
    database.create_all()
    with database.session_factory() as session:
        row = session.get(CaseRow, case_id)
        assert (row.supplier_name, row.last_error) == ("Acme Components", None)


@pytest.fixture
def five(app) -> dict[str, str]:
    return process(
        app,
        "01_exact_match",
        "03_large_price_variance",
        "06_missing_po",
        "08_duplicate_invoice",
        "10_ambiguous_high_value",
    )


def test_query_views_match_status_and_outcome(app, five):
    query = app.runtime.repository.query_cases

    assert ids(query(view="review")[0]) == {
        five["03_large_price_variance"],
        five["06_missing_po"],
        five["10_ambiguous_high_value"],
    }
    assert ids(query(view="approved")[0]) == {five["01_exact_match"]}
    assert ids(query(view="rejected")[0]) == {five["08_duplicate_invoice"]}
    assert query(view="request_info") == ([], 0)
    assert query(view="all")[1] == 5
    assert app.view_counts() == {
        "all": 5,
        "review": 3,
        "approved": 1,
        "rejected": 1,
        "request_info": 0,
        "superseded": 0,
    }


def test_search_is_case_insensitive_and_treats_wildcards_literally(app, five):
    literal = Invoice.model_validate(
        {**demo_invoices()["02_small_price_variance"].model_dump(), "invoice_id": "INV%50_A"}
    )
    literal_id = app.process_invoice(literal)["case_id"]
    query = app.runtime.repository.query_cases

    assert ids(query(search="inv-l")[0]) == {five["03_large_price_variance"]}
    assert len(query(search="ACME")[0]) == 3
    assert ids(query(search="v%5")[0]) == {literal_id}
    assert ids(query(search="0_a")[0]) == {literal_id}
    assert query(search="po-9999") == ([], 0)


def test_sorts_and_paging(app, five):
    query = app.runtime.repository.query_cases

    amounts = [row["total"] for row in query(sort="amount", descending=False)[0]]
    assert amounts == sorted(amounts, key=float)
    assert query(sort="amount")[0][0]["total"] == "60000.00"
    priority = query(view="review", sort="priority")[0]
    assert [row["case_id"] for row in priority] == [
        five["03_large_price_variance"],
        five["06_missing_po"],
        five["10_ambiguous_high_value"],
    ]
    for sort in ("case", "supplier", "exception", "confidence", "status", "updated"):
        assert query(sort=sort)[1] == 5
    page, total = query(sort="case", descending=False, limit=2, offset=2)
    assert total == 5
    assert [row["case_id"] for row in page] == sorted(five.values())[2:4]
    with pytest.raises(ValueError, match="view"):
        query(view="escalated")
    with pytest.raises(ValueError, match="sort"):
        query(sort="severity")


def test_summary_counts_values_rates_and_overrides(app):
    cases = process(
        app,
        "01_exact_match",
        "03_large_price_variance",
        "08_duplicate_invoice",
        "10_ambiguous_high_value",
    )
    app.runtime.repository.claim_case("X", demo_invoices()["02_small_price_variance"])

    summary = app.summary()

    assert summary["total"] == 5
    assert summary["counts"] == {
        "all": 5,
        "review": 2,
        "approved": 1,
        "rejected": 1,
        "request_info": 0,
        "superseded": 0,
        "processing": 1,
    }
    assert summary["awaiting"]["value_by_currency"] == {"EUR": "62200.00"}
    assert (summary["auto_resolution"]["autonomous"], summary["auto_resolution"]["total"]) == (2, 5)
    assert summary["override"] == {"overridden": 0, "ai_reviewed": 0, "rate": None}
    assert summary["sla_hours"] == 24

    app.review_case(
        cases["10_ambiguous_high_value"], HumanReview(action=DecisionType.REJECT, comment="no")
    )
    assert app.summary()["override"] == {"overridden": 0, "ai_reviewed": 0, "rate": None}

    app.review_case(
        cases["03_large_price_variance"], HumanReview(action=DecisionType.REJECT, comment="no")
    )
    assert app.summary()["override"] == {"overridden": 1, "ai_reviewed": 1, "rate": 1.0}


def test_summary_waits_and_trends_follow_the_clock(app):
    cases = process(app, "01_exact_match", "03_large_price_variance", "06_missing_po")
    now = datetime.now(UTC)

    fresh = app.summary(now)
    later = app.summary(now + timedelta(days=2))

    assert fresh["awaiting"]["trend_vs_yesterday"] is None
    assert fresh["auto_resolution"]["trend_points"] is None
    assert later["awaiting"]["trend_vs_yesterday"] == 0
    assert later["awaiting"]["oldest_wait_seconds"] >= 2 * 24 * 3600
    assert [item["case_id"] for item in later["waiting"]] == [
        cases["03_large_price_variance"],
        cases["06_missing_po"],
    ]


def test_case_with_a_missing_checkpoint_is_flagged_and_cleared_once_restored(app):
    case_id = process(app, "03_large_price_variance")["03_large_price_variance"]
    checkpointer = app.runtime.graph.checkpointer
    app.runtime.graph.checkpointer = InMemorySaver()

    app.refresh_open_cases()

    [row], total = app.runtime.repository.query_cases()
    assert total == 1
    assert row["case_id"] == case_id
    assert "checkpoint is missing" in row["last_error"]
    assert row["status"] == "PENDING_REVIEW"
    assert display_status(row).label == "Needs recovery"

    app.runtime.graph.checkpointer = checkpointer
    app.refresh_open_cases()

    [row], _ = app.runtime.repository.query_cases()
    assert row["last_error"] is None
    assert display_status(row).label == "Awaiting review"


def test_reconciling_an_unchanged_case_writes_nothing(app):
    """R18: refreshing open cases on every page run must not write to the database."""
    process(app, "03_large_price_variance")
    repo = app.runtime.repository
    app.refresh_open_cases()

    def total_changes() -> int:
        with repo.database.engine.connect() as connection:
            return connection.connection.dbapi_connection.total_changes

    before = (repo.query_cases()[0][0]["updated_at"], total_changes())
    app.refresh_open_cases()

    assert (repo.query_cases()[0][0]["updated_at"], total_changes()) == before


def test_read_path_stays_fast_with_three_hundred_cases(app):
    repo = app.runtime.repository
    exact = demo_invoices()["01_exact_match"]
    with repo.database.session_factory() as session:
        for index in range(280):
            invoice = exact.model_copy(update={"invoice_id": f"SYN-{index:04d}"})
            session.add(
                CaseRow(
                    case_id=f"CASE-SYN{index:04d}",
                    invoice_id=invoice.invoice_id,
                    status="COMPLETED",
                    invoice_payload=invoice.model_dump_json(),
                    state_payload='{"outcome": {"action": "APPROVE", "source": "AUTONOMOUS"}}',
                    supplier_id=invoice.supplier_id,
                    supplier_name=invoice.supplier_name,
                    po_number=invoice.po_number,
                    currency="EUR",
                    total_cents=100000,
                    severity_rank=0,
                    outcome_action="APPROVE",
                    outcome_source="AUTONOMOUS",
                    created_at=datetime(2026, 1, 1),
                    completed_at=datetime(2026, 1, 1),
                )
            )
        session.commit()
    template = demo_invoices()["06_missing_po"]
    for index in range(20):
        app.process_invoice(
            Invoice.model_validate({**template.model_dump(), "invoice_id": f"INV-NOPO-{index}"})
        )

    started = time.perf_counter()
    app.refresh_open_cases()
    summary = app.summary()
    rows, total = app.query_cases(view="all", sort="updated", limit=50)
    elapsed = time.perf_counter() - started

    print(f"read path for {total} cases: {elapsed:.3f} s")
    assert (summary["total"], total, len(rows)) == (300, 300, 50)
    assert summary["counts"]["review"] == 20
    assert elapsed < 1.0
