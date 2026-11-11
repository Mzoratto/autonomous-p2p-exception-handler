"""Demo scenario cards for the Process page. Names and expected behaviour follow the design."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    key: str
    invoice_key: str
    name: str
    expected: str
    tag: str


DETERMINISTIC = "Deterministic"
VALUE_REVIEW = "Value review"
AI_REVIEW = "AI + review"

SCENARIOS = [
    Scenario("01", "01_exact_match", "Exact match", "Autonomous approval", DETERMINISTIC),
    Scenario(
        "02",
        "02_small_price_variance",
        "Small price variance",
        "Approval inside the configured dual tolerance",
        DETERMINISTIC,
    ),
    Scenario(
        "03",
        "03_large_price_variance",
        "Large price variance",
        "Investigate → critique → revise → human review",
        AI_REVIEW,
    ),
    Scenario(
        "04", "04_quantity_variance", "Quantity variance", "Investigate and review", AI_REVIEW
    ),
    Scenario(
        "05",
        "05_quantity_exceeds_receipt",
        "Quantity exceeds receipt",
        "Hard-control review",
        AI_REVIEW,
    ),
    Scenario("06", "06_missing_po", "Missing PO", "Never auto-approve", AI_REVIEW),
    Scenario(
        "07", "07_missing_grn", "Missing goods receipt", "Request evidence and review", AI_REVIEW
    ),
    Scenario(
        "08", "08_duplicate_invoice", "Duplicate invoice", "Deterministic rejection", DETERMINISTIC
    ),
    Scenario("09", "09_supplier_mismatch", "Supplier mismatch", "Hard-control review", AI_REVIEW),
    Scenario(
        "10",
        "10_ambiguous_high_value",
        "Exact but high-value invoice",
        "Value-threshold review",
        VALUE_REVIEW,
    ),
]
