"""Pure facts derived from a case's JSON-shaped state, shared by the UI and storage."""

from __future__ import annotations

SEVERITY_RANK = {"CRITICAL": 4, "MATERIAL": 3, "WARNING": 2, "INFO": 1}
HIGH_VALUE_RULE = "risk.human_review_invoice_value"


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
