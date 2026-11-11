"""Pure facts derived from a case's JSON-shaped state, shared by the UI and storage."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

SEVERITY_RANK = {"CRITICAL": 4, "MATERIAL": 3, "WARNING": 2, "INFO": 1}
HIGH_VALUE_RULE = "risk.human_review_invoice_value"
CENT = Decimal("0.01")
MAX_CENTS = 2**63 - 1  # SQLite INTEGER is a signed 64-bit value.


def primary_exception(state: dict) -> tuple[str | None, str | None, int]:
    """Return (type, severity, number of other discrepancies) for the case's headline exception.

    The most severe discrepancy wins, first in list order on ties. An exact match that policy
    paused for its value reports HIGH_VALUE / WARNING.
    """
    discrepancies = (state.get("match_result") or {}).get("discrepancies") or []
    if discrepancies:
        primary = max(discrepancies, key=lambda item: SEVERITY_RANK.get(item["severity"], 0))
        return primary["type"], primary["severity"], len(discrepancies) - 1
    triggered = (state.get("policy_result") or {}).get("triggered_rules") or []
    if HIGH_VALUE_RULE in triggered:
        return "HIGH_VALUE", "WARNING", 0
    return None, None, 0


def total_cents(total: object) -> int:
    cents = int(Decimal(str(total)).quantize(CENT, rounding=ROUND_HALF_EVEN) * 100)
    if not -MAX_CENTS - 1 <= cents <= MAX_CENTS:
        raise ValueError("Invoice total is too large to store")
    return cents


def project(invoice: dict, state: dict) -> dict:
    """The queryable case columns (everything except timestamps) for one invoice and state."""
    exception, severity, _ = primary_exception(state)
    resolution = state.get("resolution") or {}
    outcome = state.get("outcome") or {}
    confidence = resolution.get("confidence")
    return {
        "supplier_id": invoice["supplier_id"],
        "supplier_name": invoice["supplier_name"],
        "po_number": invoice.get("po_number"),
        "currency": invoice["currency"],
        "total_cents": total_cents(invoice["total"]),
        "primary_exception": exception,
        "max_severity": severity,
        "severity_rank": SEVERITY_RANK.get(severity or "", 0),
        "ai_action": resolution.get("action"),
        "ai_confidence": None if confidence is None else float(confidence),
        "outcome_action": outcome.get("action"),
        "outcome_source": outcome.get("source"),
    }
