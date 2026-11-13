from workflow_support import erp_context

from p2p.domain.matching import ThreeWayMatcher
from p2p.domain.models import DecisionType, Resolution
from p2p.domain.policy import DecisionPolicy
from p2p.infrastructure.database import demo_invoices


def test_duplicate_rule_enforces_reject_even_if_ai_approves(app, rules):
    invoice = demo_invoices()["08_duplicate_invoice"]
    match = ThreeWayMatcher(rules).match(invoice, erp_context(app, invoice))
    result = DecisionPolicy(rules).evaluate(
        invoice,
        match,
        Resolution(action=DecisionType.APPROVE, rationale="unsafe", confidence=1),
    )
    assert result.permitted is True
    assert result.enforced_action is DecisionType.REJECT


def test_confidence_never_overrides_material_variance(app, rules):
    invoice = demo_invoices()["03_large_price_variance"]
    match = ThreeWayMatcher(rules).match(invoice, erp_context(app, invoice))
    result = DecisionPolicy(rules).evaluate(
        invoice,
        match,
        Resolution(action=DecisionType.APPROVE, rationale="confident", confidence=1),
    )
    assert result.permitted is False
    assert result.requires_human_review is True
    assert "material_variance" in result.triggered_rules
