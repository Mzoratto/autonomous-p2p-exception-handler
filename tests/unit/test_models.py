from copy import deepcopy

import pytest
from pydantic import ValidationError

from p2p.infrastructure.database import demo_invoices


def test_invoice_rejects_inconsistent_totals():
    payload = demo_invoices()["01_exact_match"].model_dump(mode="json")
    payload["total"] = "999.99"
    with pytest.raises(ValidationError, match="subtotal plus tax"):
        from p2p.domain.models import Invoice

        Invoice.model_validate(payload)


def invoice_payload(quantity: str, unit_price: str, line_total: str, **totals: str) -> dict:
    """Demo invoice 01 with one changed line; subtotal and total follow line_total by default."""
    payload = demo_invoices()["01_exact_match"].model_dump(mode="json")
    payload["line_items"][0].update(quantity=quantity, unit_price=unit_price, line_total=line_total)
    payload.update({"subtotal": line_total, "total": line_total, **totals})
    return payload


@pytest.mark.parametrize(
    "payload,fields",
    [
        pytest.param(
            invoice_payload("10", "1e30", "1e31"),
            {"line_items.0.unit_price", "line_items.0.line_total", "subtotal", "total"},
            id="#13: 1e30 overflows exact-cent rounding",
        ),
        pytest.param(
            invoice_payload("10", "1e999999", "0"),
            {"line_items.0.unit_price"},
            id="review 1: quantity x unit price overflows Decimal",
        ),
        pytest.param(
            invoice_payload("1e-30", "1e30", "1"),
            {"line_items.0.quantity", "line_items.0.unit_price"},
            id="review 2: a unit price too large for cents behind a tiny quantity",
        ),
        pytest.param(
            invoice_payload("10", "100", "1000", tax="1e15", total="1000000000001000"),
            {"tax", "total"},
            id="money above 15 integer digits",
        ),
    ],
)
def test_invoice_rejects_an_amount_outside_its_bounds_as_a_field_error(payload, fields):
    """#13: an amount too large or too precise is reported on its own field, never a crash."""
    from p2p.domain.models import Invoice

    with pytest.raises(ValidationError) as caught:
        Invoice.model_validate(payload)
    assert {".".join(map(str, error["loc"])) for error in caught.value.errors()} == fields


@pytest.mark.parametrize(
    "quantity,unit_price,line_total",
    [
        ("1250.5", "0.004512", "5.64"),
        ("999999999.999999", "1", "999999999.999999"),
        ("1000", "999999999999.999999", "999999999999999.999"),
    ],
)
def test_invoice_accepts_amounts_at_its_bounds(quantity, unit_price, line_total):
    """Fractional units, sub-cent unit prices and amounts up to the bounds still validate."""
    from p2p.domain.models import Invoice

    Invoice.model_validate(invoice_payload(quantity, unit_price, line_total))


def test_ingestion_normalizes_currency_and_identifiers():
    payload = deepcopy(demo_invoices()["01_exact_match"].model_dump(mode="json"))
    payload["currency"] = "eur"
    payload["supplier_id"] = "sup-001"
    payload["po_number"] = "po-1001"
    from p2p.domain.models import Invoice

    parsed = Invoice.model_validate(payload)
    assert parsed.currency == "EUR"
    assert parsed.supplier_id == "SUP-001"
    assert parsed.po_number == "PO-1001"
