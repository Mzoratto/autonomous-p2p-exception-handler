from decimal import Decimal

import pytest
from pydantic import ValidationError

from p2p.domain.models import Invoice
from p2p.infrastructure.database import demo_invoices
from p2p.ui.viewmodels import (
    StatusView,
    display_status,
    format_duration,
    format_money,
    validation_rows,
)


def completed(action: str, source: str) -> dict:
    return {"status": "COMPLETED", "state": {"outcome": {"action": action, "source": source}}}


def test_pending_review_case_is_awaiting_review():
    assert display_status({"status": "PENDING_REVIEW", "state": {}}) == StatusView(
        "review", "Awaiting review"
    )


@pytest.mark.parametrize(
    ("action", "key", "outcome"),
    [
        ("APPROVE", "approved", "Approved"),
        ("REJECT", "rejected", "Rejected"),
        ("REQUEST_INFO", "request_info", "Request info"),
        ("ESCALATE", "escalated", "Escalated"),
    ],
)
@pytest.mark.parametrize(
    ("source", "suffix"),
    [("AUTONOMOUS", "Autonomous"), ("HUMAN_APPROVED", "Human"), ("HUMAN_OVERRIDDEN", "Human")],
)
def test_completed_case_label_combines_outcome_and_decision_source(
    action, key, outcome, source, suffix
):
    assert display_status(completed(action, source)) == StatusView(key, f"{outcome} · {suffix}")


def test_processing_case_needs_recovery():
    assert display_status({"status": "PROCESSING", "state": {}}) == StatusView(
        "recovery", "Needs recovery"
    )


def test_a_case_being_retried_shows_processing_not_recovery():
    case = {"status": "PROCESSING", "state": {}, "last_error": "OSError: disk full", "busy": True}

    assert display_status(case) == StatusView("processing", "Processing")


def test_completed_case_without_outcome_needs_recovery():
    assert display_status({"status": "COMPLETED", "state": {}}).key == "recovery"


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    [
        ("2200", "EUR", "€2,200.00"),
        (Decimal("60000"), "USD", "$60,000.00"),
        ("0.005", "GBP", "£0.00"),
        ("0.015", "GBP", "£0.02"),
        ("1234.5", "CHF", "CHF 1,234.50"),
        ("-12.3", "EUR", "-€12.30"),
    ],
)
def test_format_money(amount, currency, expected):
    assert format_money(amount, currency) == expected


def test_a_missing_amount_is_shown_as_a_dash():
    assert format_money(None, None) == "—"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (59, "just now"),
        (60, "1m"),
        (3599, "59m"),
        (3600, "1h 0m"),
        (8040, "2h 14m"),
        (86399, "23h 59m"),
        (86400, "1d 0h"),
        (97200, "1d 3h"),
    ],
)
def test_format_duration_boundaries(seconds, expected):
    assert format_duration(seconds) == expected


def invoice_payload() -> dict:
    return demo_invoices()["01_exact_match"].model_dump(mode="json")


def rows_for(payload) -> list[tuple[str, str]]:
    with pytest.raises(ValidationError) as caught:
        if isinstance(payload, str):
            Invoice.model_validate_json(payload)
        else:
            Invoice.model_validate(payload)
    return validation_rows(caught.value)


def test_validation_rows_report_wrong_total_on_the_invoice():
    payload = invoice_payload()
    payload["total"] = "999.99"
    assert rows_for(payload) == [("invoice", "total does not equal subtotal plus tax")]


def test_validation_rows_report_wrong_line_total_with_line_index():
    payload = invoice_payload()
    payload["line_items"][0]["line_total"] = "1.00"
    [(field, message)] = rows_for(payload)
    assert field == "line_items[0]"
    assert message.startswith("line_total must equal quantity × unit_price")


def test_validation_rows_report_invalid_json():
    [(field, message)] = rows_for('{"invoice_id": ')
    assert field == "JSON"
    assert message.startswith("Invalid JSON")
