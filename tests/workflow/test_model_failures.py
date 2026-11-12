import pytest
from workflow_support import FailingInvestigator, use_workflow

from p2p.domain.models import AuditEventType
from p2p.infrastructure.database import demo_invoices
from p2p.intelligence.providers import MockModelProvider
from p2p.ports import ModelAuthenticationError, ModelUnavailableError


def event_types(app, case_id: str) -> list[AuditEventType]:
    return [event.event_type for event in app.runtime.repository.for_case(case_id)]


def test_model_outage_routes_straight_to_review_after_one_call(app):
    provider = FailingInvestigator(ModelUnavailableError("APITimeoutError: Request timed out."))
    use_workflow(app, provider)

    result = app.process_invoice(demo_invoices()["03_large_price_variance"])

    assert result["status"] == "PENDING_REVIEW"
    assert provider.calls == 1
    assert "model_error" in result["state"]["policy_result"].triggered_rules
    events = event_types(app, result["case_id"])
    assert AuditEventType.INVESTIGATION_COMPLETED in events
    assert AuditEventType.RESOLUTION_PROPOSED not in events


def test_exhausted_quota_sends_the_case_to_a_reviewer_as_model_unavailable(app):
    reason = "OpenAI quota exhausted (insufficient_quota)"
    use_workflow(app, FailingInvestigator(ModelUnavailableError(reason)))

    result = app.process_invoice(demo_invoices()["03_large_price_variance"])

    state = result["state"]
    policy = state["policy_result"]
    assert result["status"] == "PENDING_REVIEW"
    assert state["model_error"] == f"Model unavailable: {reason}"
    assert "The model was unavailable, so a reviewer must decide." in policy.reasons
    check = next(item for item in policy.checks if item.rule == "ai_recommendation")
    assert (check.passed, check.actual) == (False, "Model unavailable")


def test_rejected_api_key_stops_processing_and_leaves_the_case_recoverable(app):
    use_workflow(app, FailingInvestigator(ModelAuthenticationError("Incorrect API key")))
    invoice = demo_invoices()["03_large_price_variance"]

    with pytest.raises(ModelAuthenticationError):
        app.process_invoice(invoice)

    [row], _ = app.query_cases()
    assert row["status"] == "PROCESSING"
    assert row["last_error"] == "ModelAuthenticationError: Incorrect API key"
    assert AuditEventType.INVESTIGATION_COMPLETED not in event_types(app, row["case_id"])

    use_workflow(app, MockModelProvider(), checkpointer=app.runtime.graph.checkpointer)
    resumed = app.process_invoice(invoice)

    assert (resumed["case_id"], resumed["status"]) == (row["case_id"], "PENDING_REVIEW")
    assert app.query_cases()[0][0]["last_error"] is None
