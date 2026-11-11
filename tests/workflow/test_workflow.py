import pytest
from langgraph.checkpoint.memory import InMemorySaver

from p2p.domain.matching import ThreeWayMatcher
from p2p.domain.models import (
    AuditEventType,
    CritiqueResult,
    DecisionSource,
    DecisionType,
    HumanReview,
)
from p2p.domain.policy import DecisionPolicy
from p2p.infrastructure.database import demo_invoices
from p2p.intelligence.providers import MockModelProvider
from p2p.intelligence.services import Critic, Investigator, Resolver
from p2p.workflow.graph import build_graph
from p2p.workflow.nodes import WorkflowDependencies


def test_exact_match_auto_approves_without_ai(app):
    result = app.process_invoice(demo_invoices()["01_exact_match"])
    state = result["state"]
    assert result["status"] == "COMPLETED"
    assert state["outcome"].action is DecisionType.APPROVE
    assert state["outcome"].source is DecisionSource.AUTONOMOUS
    assert "investigation" not in state


def test_reflexion_is_a_real_graph_loop_then_policy_interrupts(app):
    result = app.process_invoice(demo_invoices()["03_large_price_variance"])
    state = result["state"]
    assert result["status"] == "PENDING_REVIEW"
    assert state["reflection_count"] == 1
    assert state["critique"].valid is True
    assert state["resolution"].action is DecisionType.REQUEST_INFO
    events = [event.event_type for event in app.runtime.repository.for_case(result["case_id"])]
    assert AuditEventType.CRITIQUE_FAILED in events
    assert AuditEventType.RESOLUTION_REVISED in events
    assert AuditEventType.CRITIQUE_PASSED in events
    assert events.index(AuditEventType.CRITIQUE_FAILED) < events.index(
        AuditEventType.RESOLUTION_REVISED
    )


def test_failed_critique_records_the_post_critic_confidence(app):
    result = app.process_invoice(demo_invoices()["03_large_price_variance"])
    failed = next(
        event
        for event in app.runtime.repository.for_case(result["case_id"])
        if event.event_type is AuditEventType.CRITIQUE_FAILED
    )
    assert failed.data["adjusted_confidence"] == pytest.approx(0.75)


def test_human_decision_resumes_checkpoint_and_finalizes(app):
    pending = app.process_invoice(demo_invoices()["03_large_price_variance"])
    completed = app.review_case(
        pending["case_id"],
        HumanReview(
            reviewer="ap-manager",
            action=DecisionType.REQUEST_INFO,
            comment="Obtain an approved PO amendment.",
        ),
    )
    assert completed["status"] == "COMPLETED"
    assert completed["state"]["outcome"].action is DecisionType.REQUEST_INFO
    assert completed["state"]["outcome"].source is DecisionSource.HUMAN_APPROVED
    persisted = app.get_case(pending["case_id"])
    assert persisted["status"] == "COMPLETED"
    assert persisted["state"]["human_review"]["reviewer"] == "ap-manager"


def test_high_value_exact_match_is_interrupted_by_policy(app):
    result = app.process_invoice(demo_invoices()["10_ambiguous_high_value"])
    assert result["status"] == "PENDING_REVIEW"
    assert result["state"]["match_result"].matched is True
    assert "risk.human_review_invoice_value" in result["state"]["policy_result"].triggered_rules


def test_duplicate_auto_rejects_without_human(app):
    result = app.process_invoice(demo_invoices()["08_duplicate_invoice"])
    assert result["status"] == "COMPLETED"
    assert result["state"]["outcome"].action is DecisionType.REJECT
    assert result["state"]["outcome"].source is DecisionSource.AUTONOMOUS
    assert "investigation" not in result["state"]
    audit = app.get_case(result["case_id"])["audit"]
    assert AuditEventType.INVESTIGATION_COMPLETED.value not in {
        event["event_type"] for event in audit
    }


def test_max_reflection_iterations_force_human_review(app):
    class AlwaysInvalidCriticProvider(MockModelProvider):
        def structured_response(self, schema, messages):
            if schema is CritiqueResult:
                return CritiqueResult(
                    valid=False,
                    issues=["Deliberately unresolved test critique."],
                    recommended_changes=["Revise again."],
                    confidence_adjustment=-0.1,
                )
            return super().structured_response(schema, messages)

    provider = AlwaysInvalidCriticProvider()
    rules = app.runtime.rules
    deps = WorkflowDependencies(
        erp=app.runtime.repository,
        audit=app.runtime.repository,
        matcher=ThreeWayMatcher(rules),
        policy=DecisionPolicy(rules),
        investigator=Investigator(provider),
        resolver=Resolver(provider),
        critic=Critic(provider),
        rules=rules,
    )
    app.runtime.graph = build_graph(deps, InMemorySaver())
    result = app.process_invoice(demo_invoices()["03_large_price_variance"])
    assert result["status"] == "PENDING_REVIEW"
    assert result["state"]["reflection_count"] == 3
    assert result["state"]["reflection_exhausted"] is True
    assert "reflection.max_iterations" in result["state"]["policy_result"].triggered_rules


def test_malformed_model_output_fails_closed_to_human_review(app):
    class MalformedProvider(MockModelProvider):
        def structured_response(self, schema, messages):
            raise ValueError("provider returned malformed structured output")

    provider = MalformedProvider()
    rules = app.runtime.rules
    deps = WorkflowDependencies(
        erp=app.runtime.repository,
        audit=app.runtime.repository,
        matcher=ThreeWayMatcher(rules),
        policy=DecisionPolicy(rules),
        investigator=Investigator(provider),
        resolver=Resolver(provider),
        critic=Critic(provider),
        rules=rules,
    )
    app.runtime.graph = build_graph(deps, InMemorySaver())

    result = app.process_invoice(demo_invoices()["03_large_price_variance"])

    state = result["state"]
    assert result["status"] == "PENDING_REVIEW"
    assert state["resolution"].action is DecisionType.ESCALATE
    assert state["reflection_exhausted"] is True
    assert state["model_error"] == "ValueError: structured critique failed"
    assert "reflection.max_iterations" in state["policy_result"].triggered_rules
    events = [event.event_type for event in app.runtime.repository.for_case(result["case_id"])]
    assert AuditEventType.INVESTIGATION_COMPLETED in events
    assert AuditEventType.CRITIQUE_FAILED in events
