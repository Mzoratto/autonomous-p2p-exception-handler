import pytest
from langgraph.checkpoint.memory import InMemorySaver

from p2p.application.service import CaseService, create_runtime
from p2p.domain.models import (
    DecisionType,
    Discrepancy,
    DiscrepancyType,
    MatchResult,
    Resolution,
    Severity,
)
from p2p.domain.policy import DecisionPolicy
from p2p.infrastructure.database import demo_invoices

RULE_ORDER = [
    "price_tolerance",
    "quantity_receipt_tolerance",
    "quantity_order_limit",
    "purchase_order_open",
    "goods_receipt_posted",
    "supplier_eligible",
    "not_duplicate",
    "value_threshold",
    "model_confidence",
    "document_consistency",
    "ai_recommendation",
]

# Measured with fresh storage per scenario; see docs/design/README.md.
GOLDEN = {
    "01_exact_match": (["three_way_match"], set()),
    "02_small_price_variance": (["three_way_match"], set()),
    "03_large_price_variance": (
        ["deterministic_eligibility", "material_variance", "confidence.auto_resolve"],
        {"price_tolerance", "model_confidence"},
    ),
    "04_quantity_variance": (
        ["deterministic_eligibility", "material_variance", "confidence.auto_resolve"],
        {"quantity_receipt_tolerance", "model_confidence"},
    ),
    "05_quantity_exceeds_receipt": (
        ["deterministic_eligibility", "hard_financial_control"],
        {"quantity_receipt_tolerance"},
    ),
    "06_missing_po": (
        [
            "deterministic_eligibility",
            "allowed_action",
            "missing_po.require_human_review",
            "confidence.auto_resolve",
        ],
        {"purchase_order_open", "model_confidence", "ai_recommendation"},
    ),
    "07_missing_grn": (
        [
            "deterministic_eligibility",
            "missing_receipt.require_human_review",
            "confidence.auto_resolve",
        ],
        {"goods_receipt_posted", "model_confidence"},
    ),
    "08_duplicate_invoice": (["duplicate_invoice.auto_reject"], {"not_duplicate"}),
    "09_supplier_mismatch": (
        ["deterministic_eligibility", "allowed_action", "hard_financial_control"],
        {"supplier_eligible", "ai_recommendation"},
    ),
    "10_ambiguous_high_value": (["risk.human_review_invoice_value"], {"value_threshold"}),
}


def policy_for(tmp_path, scenario: str):
    runtime = create_runtime(
        database_url=f"sqlite:///{tmp_path / f'{scenario}.db'}",
        demo_mode=True,
        checkpointer=InMemorySaver(),
    )
    try:
        return CaseService(runtime).process_invoice(demo_invoices()[scenario])["state"][
            "policy_result"
        ]
    finally:
        runtime.close()


def failing(result) -> set[str]:
    return {check.rule for check in result.checks if check.passed is False}


@pytest.mark.parametrize("scenario", sorted(GOLDEN))
def test_scenario_triggers_and_failed_checks(tmp_path, scenario):
    triggered, failed = GOLDEN[scenario]

    result = policy_for(tmp_path, scenario)

    assert result.triggered_rules == triggered
    assert failing(result) == failed
    assert [check.rule for check in result.checks] == RULE_ORDER
    if result.requires_human_review:
        assert failed
    if result.permitted and result.enforced_action is DecisionType.APPROVE:
        assert not failed


@pytest.mark.parametrize("kind", list(DiscrepancyType))
def test_every_discrepancy_type_fails_a_check(rules, kind):
    match = MatchResult(
        matched=False,
        summary="unsafe",
        discrepancies=[Discrepancy(type=kind, severity=Severity.CRITICAL, explanation="probe")],
    )
    result = DecisionPolicy(rules).evaluate(
        demo_invoices()["01_exact_match"],
        match,
        Resolution(action=DecisionType.APPROVE, rationale="Hostile approval", confidence=1),
    )

    assert failing(result)


def test_unmatched_result_without_a_typed_failure_fails_document_consistency(rules):
    match = MatchResult(matched=False, summary="incomplete", discrepancies=[])

    result = DecisionPolicy(rules).evaluate(demo_invoices()["01_exact_match"], match, None)

    assert result.requires_human_review
    assert failing(result) == {"document_consistency"}
    consistency = next(check for check in result.checks if check.rule == "document_consistency")
    assert consistency.actual == "Three-way match did not complete"


def test_model_error_and_exhausted_reflection_fail_ai_recommendation(rules):
    match = MatchResult(matched=True, summary="Exact three-way match.")
    resolution = Resolution(action=DecisionType.APPROVE, rationale="ok", confidence=0.95)

    result = DecisionPolicy(rules).evaluate(
        demo_invoices()["01_exact_match"],
        match,
        resolution,
        reflection_exhausted=True,
        model_error="ValueError: structured critique failed",
    )

    ai = next(check for check in result.checks if check.rule == "ai_recommendation")
    assert ai.passed is False
    assert ai.actual == "Model or evidence validation failed; Reflection limit reached"


def test_thresholds_are_formatted_from_the_rules(rules):
    match = MatchResult(matched=True, summary="Exact three-way match.")

    checks = {
        check.rule: check.threshold
        for check in DecisionPolicy(rules)
        .evaluate(demo_invoices()["01_exact_match"], match, None)
        .checks
    }

    assert checks["price_tolerance"] == "≤ 2% and ≤ 25.00 per line, in invoice currency"
    assert checks["quantity_receipt_tolerance"] == "≤ 1% vs received"
    assert checks["value_threshold"] == "< 50,000.00 in invoice currency"
    assert checks["model_confidence"] == "≥ 0.90 to auto-resolve"
