from decimal import Decimal

import pytest
from workflow_support import erp_context

from p2p.domain.matching import ThreeWayMatcher
from p2p.domain.models import DiscrepancyType, Severity
from p2p.domain.policy import DecisionPolicy
from p2p.infrastructure.database import demo_invoices


def types(result):
    return {item.type for item in result.discrepancies}


def test_exact_three_way_match(app, rules):
    invoice = demo_invoices()["01_exact_match"]
    result = ThreeWayMatcher(rules).match(invoice, erp_context(app, invoice))
    assert result.matched is True
    assert result.discrepancies == []


def test_small_price_variance_is_visible_but_non_material(app, rules):
    invoice = demo_invoices()["02_small_price_variance"]
    result = ThreeWayMatcher(rules).match(invoice, erp_context(app, invoice))
    assert result.matched is True
    assert result.discrepancies[0].type is DiscrepancyType.PRICE_VARIANCE
    assert result.discrepancies[0].severity is Severity.WARNING


def test_large_price_variance_is_material(app, rules):
    invoice = demo_invoices()["03_large_price_variance"]
    result = ThreeWayMatcher(rules).match(invoice, erp_context(app, invoice))
    assert result.matched is False
    assert result.discrepancies[0].variance_percent == 10


def test_quantity_and_receipt_controls(app, rules):
    matcher = ThreeWayMatcher(rules)
    quantity = demo_invoices()["04_quantity_variance"]
    exceeds = demo_invoices()["05_quantity_exceeds_receipt"]
    assert DiscrepancyType.QUANTITY_VARIANCE in types(
        matcher.match(quantity, erp_context(app, quantity))
    )
    assert DiscrepancyType.QUANTITY_EXCEEDS_RECEIPT in types(
        matcher.match(exceeds, erp_context(app, exceeds))
    )


def test_missing_po_receipt_duplicate_and_supplier_mismatch(app, rules):
    matcher = ThreeWayMatcher(rules)
    expected = {
        "06_missing_po": DiscrepancyType.MISSING_PO,
        "07_missing_grn": DiscrepancyType.MISSING_GRN,
        "08_duplicate_invoice": DiscrepancyType.DUPLICATE_INVOICE,
        "09_supplier_mismatch": DiscrepancyType.SUPPLIER_MISMATCH,
    }
    for scenario, discrepancy_type in expected.items():
        invoice = demo_invoices()[scenario]
        assert discrepancy_type in types(matcher.match(invoice, erp_context(app, invoice)))


def test_currency_mismatch(app, rules):
    invoice = demo_invoices()["01_exact_match"]
    context = erp_context(app, invoice)
    context.purchase_order = context.purchase_order.model_copy(update={"currency": "USD"})
    result = ThreeWayMatcher(rules).match(invoice, context)
    assert DiscrepancyType.CURRENCY_MISMATCH in types(result)


@pytest.mark.parametrize("status", ["DRAFT", "ON_HOLD"])
def test_a_po_in_an_unknown_state_is_not_open_and_requires_review(app, rules, status):
    """Only OPEN is eligible: closed, cancelled and unknown PO states require review."""
    invoice = demo_invoices()["01_exact_match"]
    context = erp_context(app, invoice)
    context.purchase_order = context.purchase_order.model_copy(update={"status": status})

    result = ThreeWayMatcher(rules).match(invoice, context)

    assert result.matched is False
    assert DiscrepancyType.PO_NOT_OPEN in types(result)
    assert DecisionPolicy(rules).evaluate(invoice, result, None).requires_human_review


def test_matcher_catches_arithmetic_corruption_after_validation(app, rules):
    invoice = demo_invoices()["01_exact_match"].model_copy(deep=True)
    invoice.line_items[0].line_total += Decimal("1.00")

    result = ThreeWayMatcher(rules).match(invoice, erp_context(app, invoice))

    arithmetic = [
        item
        for item in result.discrepancies
        if item.type is DiscrepancyType.ARITHMETIC_INCONSISTENCY
    ]
    assert result.matched is False
    assert {item.severity for item in arithmetic} == {Severity.CRITICAL}
    assert any(item.line_id == invoice.line_items[0].line_id for item in arithmetic)
