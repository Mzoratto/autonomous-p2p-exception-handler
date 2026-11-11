from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from langgraph.types import interrupt

from p2p.domain.config import BusinessRules
from p2p.domain.matching import ThreeWayMatcher
from p2p.domain.models import (
    AuditEvent,
    AuditEventType,
    CaseOutcome,
    CritiqueResult,
    DecisionSource,
    DecisionType,
    ERPContext,
    HumanReview,
    InvestigationResult,
    Resolution,
)
from p2p.domain.policy import DecisionPolicy
from p2p.intelligence.services import Critic, Investigator, Resolver
from p2p.ports import AuditRecorder, ERPRepository
from p2p.workflow.state import P2PState


@dataclass(frozen=True)
class WorkflowDependencies:
    erp: ERPRepository
    audit: AuditRecorder
    matcher: ThreeWayMatcher
    policy: DecisionPolicy
    investigator: Investigator
    resolver: Resolver
    critic: Critic
    rules: BusinessRules


def _record(
    deps: WorkflowDependencies,
    state: P2PState,
    event_type: AuditEventType,
    actor: str,
    data: dict | None = None,
) -> None:
    if event_type in {AuditEventType.CASE_COMPLETED, AuditEventType.HUMAN_DECISION_RECEIVED}:
        return  # Published atomically with the reconciled case snapshot.
    provenance = {
        "rules_sha256": hashlib.sha256(deps.rules.model_dump_json().encode()).hexdigest(),
        "attempt": state.get("reflection_count", 0),
    }
    if state.get("erp_context"):
        provenance["evidence_sha256"] = hashlib.sha256(
            state["erp_context"].model_dump_json().encode()
        ).hexdigest()
    payload = {**(data or {}), **provenance}
    event_id = hashlib.sha256(
        json.dumps(
            [state["case_id"], event_type.value, payload], sort_keys=True, default=str
        ).encode()
    ).hexdigest()
    deps.audit.record(
        AuditEvent(
            case_id=state["case_id"],
            event_id=event_id,
            event_type=event_type,
            actor=actor,
            data=payload,
        )
    )


def ingest_node(state: P2PState, deps: WorkflowDependencies) -> dict:
    _record(deps, state, AuditEventType.CASE_CREATED, "workflow")
    _record(
        deps,
        state,
        AuditEventType.INVOICE_VALIDATED,
        "domain",
        {"invoice_id": state["invoice"].invoice_id, "total": str(state["invoice"].total)},
    )
    return {"reflection_count": 0, "reflection_exhausted": False, "model_error": None}


def load_context_node(state: P2PState, deps: WorkflowDependencies) -> dict:
    invoice = state["invoice"]
    context = ERPContext(
        reserved_quantities=deps.erp.reserved_quantities(invoice.po_number, state["case_id"]),
        purchase_order=deps.erp.get_purchase_order(invoice.po_number),
        goods_receipts=deps.erp.get_goods_receipts(invoice.po_number),
        supplier=deps.erp.get_supplier(invoice.supplier_id),
        previous_invoices=deps.erp.get_previous_invoices(invoice.supplier_id),
    )
    _record(
        deps,
        state,
        AuditEventType.ERP_CONTEXT_LOADED,
        "erp_repository",
        {
            "po_found": context.purchase_order is not None,
            "receipt_count": len(context.goods_receipts),
            "history_count": len(context.previous_invoices),
            "context": context.model_dump(mode="json"),
        },
    )
    return {"erp_context": context}


def match_node(state: P2PState, deps: WorkflowDependencies) -> dict:
    result = deps.matcher.match(state["invoice"], state["erp_context"])
    _record(
        deps,
        state,
        AuditEventType.MATCH_COMPLETED,
        "three_way_matcher",
        result.model_dump(mode="json"),
    )
    if result.discrepancies:
        _record(
            deps,
            state,
            AuditEventType.EXCEPTION_IDENTIFIED,
            "three_way_matcher",
            {"types": [item.type.value for item in result.discrepancies]},
        )
    return {"match_result": result}


def investigate_node(state: P2PState, deps: WorkflowDependencies) -> dict:
    try:
        result = deps.investigator.investigate(
            {
                "invoice": state["invoice"].model_dump(mode="json"),
                "erp_context": state["erp_context"].model_dump(mode="json"),
                "match_result": state["match_result"].model_dump(mode="json"),
            }
        )
        result = InvestigationResult.model_validate(result)
        available = {item.discrepancy_id: item for item in state["match_result"].discrepancies}
        for evidence in result.evidence:
            fact = available.get(evidence.evidence_id)
            if (
                fact is None
                or evidence.source != "three_way_matcher"
                or evidence.description != fact.explanation
            ):
                raise ValueError("Investigation cites unsupported or altered evidence")
        error = None
    except Exception as exc:
        result = InvestigationResult(
            root_cause="Model output could not be validated",
            evidence=[],
            missing_information=["Human investigation required"],
            suggested_action=DecisionType.ESCALATE,
            confidence=0,
        )
        error = f"{type(exc).__name__}: structured investigation failed"
    _record(
        deps,
        state,
        AuditEventType.INVESTIGATION_COMPLETED,
        "ai_investigator",
        {**result.model_dump(mode="json"), "error": error},
    )
    return {"investigation": result, "model_error": error}


def resolve_node(state: P2PState, deps: WorkflowDependencies) -> dict:
    try:
        result = deps.resolver.resolve(
            {
                "invoice": state["invoice"].model_dump(mode="json"),
                "match_result": state["match_result"].model_dump(mode="json"),
                "investigation": state["investigation"].model_dump(mode="json"),
                "critique": state["critique"].model_dump(mode="json")
                if state.get("critique")
                else None,
                "reflection_count": state.get("reflection_count", 0),
            }
        )
        result = Resolution.model_validate(result)
        available = {item.discrepancy_id for item in state["match_result"].discrepancies}
        if not set(result.evidence_refs).issubset(available) or (
            available and not result.evidence_refs
        ):
            raise ValueError("Resolution must cite supplied deterministic evidence")
        error = state.get("model_error")
    except Exception as exc:
        result = Resolution(
            action=DecisionType.ESCALATE,
            rationale="Structured resolution generation failed; deterministic fallback escalates.",
            confidence=0,
        )
        error = f"{type(exc).__name__}: structured resolution failed"
    event_type = (
        AuditEventType.RESOLUTION_REVISED
        if state.get("reflection_count", 0) > 0
        else AuditEventType.RESOLUTION_PROPOSED
    )
    _record(
        deps,
        state,
        event_type,
        "ai_resolution_agent",
        {**result.model_dump(mode="json"), "error": error},
    )
    return {"resolution": result, "model_error": error}


def critique_node(state: P2PState, deps: WorkflowDependencies) -> dict:
    try:
        critique = deps.critic.critique(
            {
                "match_result": state["match_result"].model_dump(mode="json"),
                "investigation": state["investigation"].model_dump(mode="json"),
                "resolution": state["resolution"].model_dump(mode="json"),
            }
        )
        critique = CritiqueResult.model_validate(critique)
        error = state.get("model_error")
    except Exception as exc:
        critique = CritiqueResult(
            valid=False,
            issues=["Critic output could not be validated."],
            recommended_changes=["Escalate for human review."],
            confidence_adjustment=-1,
        )
        error = f"{type(exc).__name__}: structured critique failed"

    count = state.get("reflection_count", 0) + (0 if critique.valid else 1)
    exhausted = not critique.valid and count >= deps.rules.reflection.max_iterations
    confidence = state["resolution"].confidence
    adjusted = max(0, min(confidence, confidence + critique.confidence_adjustment))
    _record(
        deps,
        state,
        AuditEventType.CRITIQUE_PASSED if critique.valid else AuditEventType.CRITIQUE_FAILED,
        "ai_critic",
        {
            **critique.model_dump(mode="json"),
            "iteration": count,
            "exhausted": exhausted,
            "adjusted_confidence": adjusted,
        },
    )
    return {
        "resolution": state["resolution"].model_copy(update={"confidence": adjusted}),
        "critique": critique,
        "reflection_count": count,
        "reflection_exhausted": exhausted,
        "model_error": error,
    }


def policy_check_node(state: P2PState, deps: WorkflowDependencies) -> dict:
    result = deps.policy.evaluate(
        invoice=state["invoice"],
        match_result=state["match_result"],
        resolution=state.get("resolution"),
        reflection_exhausted=state.get("reflection_exhausted", False),
        model_error=state.get("model_error"),
    )
    _record(
        deps,
        state,
        AuditEventType.POLICY_CHECK_COMPLETED,
        "policy_engine",
        {
            "permitted": result.permitted,
            "human_review": result.requires_human_review,
            "rules": result.triggered_rules,
        },
    )
    if result.requires_human_review:
        _record(
            deps,
            state,
            AuditEventType.HUMAN_REVIEW_REQUESTED,
            "policy_engine",
            {"reasons": result.reasons},
        )
    return {"policy_result": result}


def human_review_node(state: P2PState, deps: WorkflowDependencies) -> dict:
    proposed = state.get("resolution")
    response = interrupt(
        {
            "case_id": state["case_id"],
            "invoice_id": state["invoice"].invoice_id,
            "invoice_total": str(state["invoice"].total),
            "proposed_action": proposed.action.value if proposed else DecisionType.APPROVE.value,
            "policy_reasons": state["policy_result"].reasons,
            "discrepancies": [
                item.model_dump(mode="json") for item in state["match_result"].discrepancies
            ],
        }
    )
    review = HumanReview.model_validate(response)
    _record(
        deps,
        state,
        AuditEventType.HUMAN_DECISION_RECEIVED,
        "human_reviewer",
        {"action": review.action.value, "reviewer": review.reviewer, "comment": review.comment},
    )
    return {"human_review": review}


def finalize_node(state: P2PState, deps: WorkflowDependencies) -> dict:
    policy = state["policy_result"]
    proposed = state.get("resolution")
    proposed_action = proposed.action if proposed else DecisionType.APPROVE
    if state.get("human_review"):
        action = state["human_review"].action
        source = (
            DecisionSource.HUMAN_APPROVED
            if action is proposed_action
            else DecisionSource.HUMAN_OVERRIDDEN
        )
        rationale = state["human_review"].comment or "Reviewer accepted the recorded action."
    else:
        action = policy.enforced_action or proposed_action
        source = DecisionSource.AUTONOMOUS
        rationale = "; ".join(policy.reasons)
    outcome = CaseOutcome(action=action, source=source, rationale=rationale)
    _record(
        deps,
        state,
        AuditEventType.CASE_COMPLETED,
        "workflow",
        {"action": outcome.action.value, "source": outcome.source.value},
    )
    return {"outcome": outcome}
