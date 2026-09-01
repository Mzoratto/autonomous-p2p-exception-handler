from p2p.domain.matching import ThreeWayMatcher
from p2p.domain.models import DecisionType, ERPContext, Resolution
from p2p.domain.policy import DecisionPolicy
from p2p.infrastructure.database import demo_invoices


def get_context(app, invoice):
    repo = app.runtime.repository
    return ERPContext(
        purchase_order=repo.get_purchase_order(invoice.po_number),
        goods_receipts=repo.get_goods_receipts(invoice.po_number),
        supplier=repo.get_supplier(invoice.supplier_id),
        previous_invoices=repo.get_previous_invoices(invoice.supplier_id),
    )


def test_duplicate_rule_enforces_reject_even_if_ai_approves(app, rules):
    invoice = demo_invoices()["08_duplicate_invoice"]
    match = ThreeWayMatcher(rules).match(invoice, get_context(app, invoice))
    result = DecisionPolicy(rules).evaluate(
        invoice,
        match,
        Resolution(action=DecisionType.APPROVE, rationale="unsafe", confidence=1),
    )
    assert result.permitted is True
    assert result.enforced_action is DecisionType.REJECT


def test_confidence_never_overrides_material_variance(app, rules):
    invoice = demo_invoices()["03_large_price_variance"]
    match = ThreeWayMatcher(rules).match(invoice, get_context(app, invoice))
    result = DecisionPolicy(rules).evaluate(
        invoice,
        match,
        Resolution(action=DecisionType.APPROVE, rationale="confident", confidence=1),
    )
    assert result.permitted is False
    assert result.requires_human_review is True
    assert "material_variance" in result.triggered_rules
