"""Pure display helpers: labels, display status and value formatting."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from pydantic import ValidationError

ACTION_LABELS = {
    "APPROVE": "Approve",
    "REJECT": "Reject",
    "REQUEST_INFO": "Request info",
    "ESCALATE": "Escalate",
}

OUTCOME_LABELS = {
    "APPROVE": "Approved",
    "REJECT": "Rejected",
    "REQUEST_INFO": "Request info",
    "ESCALATE": "Escalated",
}

# Status-pill key for each decision action.
ACTION_STATUS_KEYS = {
    "APPROVE": "approved",
    "REJECT": "rejected",
    "REQUEST_INFO": "request_info",
    "ESCALATE": "escalated",
}

EXCEPTION_LABELS = {
    "PRICE_VARIANCE": "Price variance",
    "QUANTITY_VARIANCE": "Quantity variance",
    "QUANTITY_EXCEEDS_RECEIPT": "Quantity exceeds receipt",
    "QUANTITY_EXCEEDS_ORDER": "Quantity exceeds order",
    "MISSING_PO": "Missing PO",
    "MISSING_GRN": "Missing goods receipt",
    "DUPLICATE_INVOICE": "Duplicate invoice",
    "SUPPLIER_MISMATCH": "Supplier mismatch",
    "SUPPLIER_INELIGIBLE": "Supplier ineligible",
    "PO_NOT_OPEN": "PO not open",
    "CURRENCY_MISMATCH": "Currency mismatch",
    "UNMATCHED_LINE": "Unmatched line",
    "ARITHMETIC_INCONSISTENCY": "Arithmetic inconsistency",
    "HIGH_VALUE": "High-value invoice",
}
NO_EXCEPTION_LABEL = "Exact match"

EVENT_LABELS = {
    "CASE_CREATED": "Case created",
    "INVOICE_VALIDATED": "Invoice validated",
    "ERP_CONTEXT_LOADED": "ERP context loaded",
    "MATCH_COMPLETED": "Three-way match completed",
    "EXCEPTION_IDENTIFIED": "Exception identified",
    "INVESTIGATION_COMPLETED": "Investigation completed",
    "RESOLUTION_PROPOSED": "Resolution proposed",
    "CRITIQUE_FAILED": "Critique failed",
    "RESOLUTION_REVISED": "Resolution revised",
    "CRITIQUE_PASSED": "Critique passed",
    "POLICY_CHECK_COMPLETED": "Policy check completed",
    "HUMAN_REVIEW_REQUESTED": "Human review requested",
    "HUMAN_DECISION_RECEIVED": "Human decision received",
    "CASE_COMPLETED": "Case completed",
}

CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}
CENT = Decimal("0.01")


def _humanize(value: str) -> str:
    return value.replace("_", " ").capitalize()


def label_action(action: str) -> str:
    return ACTION_LABELS.get(action, _humanize(action))


def label_exception(exception_type: str | None) -> str:
    if not exception_type:
        return NO_EXCEPTION_LABEL
    return EXCEPTION_LABELS.get(exception_type, _humanize(exception_type))


def label_event(event_type: str) -> str:
    return EVENT_LABELS.get(event_type, _humanize(event_type))


@dataclass(frozen=True)
class StatusView:
    key: str
    label: str


def display_status(case: dict) -> StatusView:
    status = case.get("status")
    if status == "PENDING_REVIEW":
        return StatusView("review", "Awaiting review")
    if status == "COMPLETED":
        outcome = (case.get("state") or {}).get("outcome") or {}
        action = outcome.get("action")
        if action in OUTCOME_LABELS:
            source = "Autonomous" if outcome.get("source") == "AUTONOMOUS" else "Human"
            return StatusView(ACTION_STATUS_KEYS[action], f"{OUTCOME_LABELS[action]} · {source}")
    return StatusView("recovery", "Needs recovery")


def format_money(amount: object, currency: str) -> str:
    value = Decimal(str(amount)).quantize(CENT, rounding=ROUND_HALF_EVEN)
    code = currency.upper()
    prefix = CURRENCY_SYMBOLS.get(code, f"{code} ")
    sign = "-" if value < 0 else ""
    return f"{sign}{prefix}{abs(value):,.2f}"


def format_duration(seconds: float) -> str:
    total = int(seconds)
    if total < 60:
        return "just now"
    minutes = total // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def _field_path(loc: tuple) -> str:
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else str(part)
    return path or "invoice"


def validation_rows(exc: ValidationError) -> list[tuple[str, str]]:
    rows = []
    for error in exc.errors():
        field = "JSON" if error["type"] == "json_invalid" else _field_path(tuple(error["loc"]))
        rows.append((field, error["msg"].removeprefix("Value error, ")))
    return rows
