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
