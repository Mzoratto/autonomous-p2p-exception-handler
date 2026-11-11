from datetime import datetime, timedelta

from p2p.application.service import CaseService, create_runtime
from p2p.domain.models import DecisionType, HumanReview
from p2p.infrastructure.database import demo_invoices


def test_seeded_sqlite_repository_contains_required_erp_shapes(app):
    repo = app.runtime.repository
    assert repo.get_supplier("SUP-001").name == "Acme Components"
    assert repo.get_purchase_order("PO-1003") is not None
    assert len(repo.get_goods_receipts("PO-1003")) == 2
    assert repo.get_goods_receipts("PO-1004") == []
    assert len(demo_invoices()) == 10


def test_case_and_structured_audit_are_persisted(app):
    result = app.process_invoice(demo_invoices()["01_exact_match"])
    case = app.get_case(result["case_id"])
    assert case["invoice_id"] == "INV-EXACT"
    assert case["state"]["outcome"]["action"] == "APPROVE"
    assert case["audit"][0]["event_type"] == "CASE_CREATED"
    assert case["audit"][-1]["event_type"] == "CASE_COMPLETED"


def test_sqlite_checkpoint_resumes_after_runtime_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'durable.db'}"
    first_runtime = create_runtime(database_url=database_url, demo_mode=True)
    first_service = CaseService(first_runtime)
    pending = first_service.process_invoice(demo_invoices()["03_large_price_variance"])
    assert pending["status"] == "PENDING_REVIEW"
    first_runtime.close()

    second_runtime = create_runtime(database_url=database_url, demo_mode=True)
    try:
        completed = CaseService(second_runtime).review_case(
            pending["case_id"],
            HumanReview(
                reviewer="restart-test",
                action=DecisionType.REQUEST_INFO,
                comment="Resume from the durable checkpoint.",
            ),
        )
        assert completed["status"] == "COMPLETED"
        assert completed["state"]["outcome"].action is DecisionType.REQUEST_INFO
    finally:
        second_runtime.close()


def test_policy_checklist_survives_sqlite_checkpoint_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'checks.db'}"
    first_runtime = create_runtime(database_url=database_url, demo_mode=True)
    case_id = CaseService(first_runtime).process_invoice(
        demo_invoices()["03_large_price_variance"]
    )["case_id"]
    first_runtime.close()

    second_runtime = create_runtime(database_url=database_url, demo_mode=True)
    try:
        service = CaseService(second_runtime)
        assert len(service.get_case(case_id)["state"]["policy_result"]["checks"]) == 11
        completed = service.review_case(
            case_id, HumanReview(reviewer="restart-test", action=DecisionType.REQUEST_INFO)
        )
        assert completed["status"] == "COMPLETED"
    finally:
        second_runtime.close()


def test_case_and_audit_timestamps_are_utc_aware(app):
    case_id = app.process_invoice(demo_invoices()["03_large_price_variance"])["case_id"]

    case = app.get_case(case_id)

    stamps = [case["updated_at"], *(event["timestamp"] for event in case["audit"])]
    assert len(stamps) > 1
    assert all(datetime.fromisoformat(value).utcoffset() == timedelta(0) for value in stamps)
