from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from p2p.domain.config import BusinessRules
from p2p.domain.models import (
    DecisionType,
    Discrepancy,
    DiscrepancyType,
    Invoice,
    MatchResult,
    PolicyCheck,
    PolicyResult,
    Resolution,
    Severity,
)

AUTONOMOUS_ACTIONS = {DecisionType.APPROVE, DecisionType.REJECT, DecisionType.REQUEST_INFO}
CONSISTENCY_TYPES = {
    DiscrepancyType.CURRENCY_MISMATCH,
    DiscrepancyType.UNMATCHED_LINE,
    DiscrepancyType.ARITHMETIC_INCONSISTENCY,
}
MATERIAL_SEVERITIES = {Severity.MATERIAL, Severity.CRITICAL}


def _percent(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _amount(value: Decimal) -> str:
    return f"{value:,.2f}"


def _explain(items: Iterable[Discrepancy]) -> str:
    return "; ".join(item.explanation for item in items)


class DecisionPolicy:
    """Deterministic authorization boundary. AI recommendations cannot bypass it."""

    def __init__(self, rules: BusinessRules):
        self.rules = rules

    def _over_value(self, invoice: Invoice) -> bool:
        return invoice.total >= self.rules.risk.human_review_invoice_value

    def _low_confidence(self, resolution: Resolution | None) -> bool:
        return resolution is not None and resolution.confidence < self.rules.confidence.auto_resolve

    def _checks(
        self,
        invoice: Invoice,
        match_result: MatchResult,
        resolution: Resolution | None,
        reflection_exhausted: bool,
        model_error: str | None,
        model_unavailable: bool = False,
    ) -> list[PolicyCheck]:
        """The checklist shown to reviewers; it explains `evaluate`, it never decides."""
        rules = self.rules
        discrepancies = match_result.discrepancies
        types = {item.type for item in discrepancies}
        no_po = DiscrepancyType.MISSING_PO in types

        def of(*kinds: DiscrepancyType) -> list[Discrepancy]:
            return [item for item in discrepancies if item.type in kinds]

        def check(
            rule: str,
            label: str,
            threshold: str,
            failing: list[Discrepancy],
            otherwise: tuple[bool | None, str],
        ) -> PolicyCheck:
            if failing:
                return PolicyCheck(
                    rule=rule,
                    label=label,
                    threshold=threshold,
                    actual=_explain(failing),
                    passed=False,
                )
            passed, actual = otherwise
            return PolicyCheck(
                rule=rule, label=label, threshold=threshold, actual=actual, passed=passed
            )

        prices = of(DiscrepancyType.PRICE_VARIANCE)
        price_failing = [item for item in prices if item.severity in MATERIAL_SEVERITIES]
        if no_po:
            price_otherwise: tuple[bool | None, str] = (None, "No PO to compare")
        elif prices:
            price_otherwise = (True, _explain(prices))
        else:
            price_otherwise = (True, "No price variance")

        checks = [
            check(
                "price_tolerance",
                "Price variance within tolerance",
                f"≤ {_percent(rules.price_variance.auto_approve_percent)}% and "
                f"≤ {_amount(rules.price_variance.auto_approve_absolute)} per line, "
                "in invoice currency",
                price_failing,
                price_otherwise,
            ),
            check(
                "quantity_receipt_tolerance",
                "Quantity within receipt tolerance",
                f"≤ {_percent(rules.quantity_variance.tolerance_percent)}% vs received",
                of(DiscrepancyType.QUANTITY_VARIANCE, DiscrepancyType.QUANTITY_EXCEEDS_RECEIPT),
                (None, "No receipt to compare")
                if no_po or DiscrepancyType.MISSING_GRN in types
                else (True, "No quantity variance"),
            ),
            check(
                "quantity_order_limit",
                "Quantity within PO order",
                "≤ remaining ordered quantity",
                of(DiscrepancyType.QUANTITY_EXCEEDS_ORDER),
                (None, "No PO to compare") if no_po else (True, "Within ordered quantity"),
            ),
            check(
                "purchase_order_open",
                "Purchase order exists and is OPEN",
                "Required",
                of(DiscrepancyType.MISSING_PO, DiscrepancyType.PO_NOT_OPEN),
                (True, f"{invoice.po_number} · OPEN"),
            ),
            check(
                "goods_receipt_posted",
                "Goods receipt posted",
                "Required",
                of(DiscrepancyType.MISSING_GRN),
                (None, "No PO to check") if no_po else (True, "Receipt found"),
            ),
            check(
                "supplier_eligible",
                "Supplier matches PO and is active",
                "Required",
                of(DiscrepancyType.SUPPLIER_MISMATCH, DiscrepancyType.SUPPLIER_INELIGIBLE),
                (True, f"{invoice.supplier_id} · active"),
            ),
            check(
                "not_duplicate",
                "Not a duplicate invoice",
                "Duplicates are rejected automatically",
                of(DiscrepancyType.DUPLICATE_INVOICE),
                (True, "No prior invoice with this number"),
            ),
            PolicyCheck(
                rule="value_threshold",
                label="Invoice value below review threshold",
                threshold=f"< {_amount(rules.risk.human_review_invoice_value)} in invoice currency",
                actual=f"{invoice.total:,.2f} {invoice.currency}",
                passed=not self._over_value(invoice),
            ),
            PolicyCheck(
                rule="model_confidence",
                label="Model confidence",
                threshold=f"≥ {rules.confidence.auto_resolve:.2f} to auto-resolve",
                actual=f"{resolution.confidence:.2f}" if resolution else "AI not invoked",
                passed=None if resolution is None else not self._low_confidence(resolution),
            ),
        ]

        consistency = check(
            "document_consistency",
            "Lines, currency and arithmetic consistent",
            "Required",
            of(*CONSISTENCY_TYPES),
            (True, "Consistent"),
        )
        if (
            consistency.passed
            and not match_result.matched
            and not any(item.passed is False for item in checks[:7])
        ):
            consistency = consistency.model_copy(
                update={"passed": False, "actual": "Three-way match did not complete"}
            )
        checks.append(consistency)

        ai_problems = []
        if model_error:
            ai_problems.append(
                "Model unavailable" if model_unavailable else "Model or evidence validation failed"
            )
        if resolution and resolution.action not in AUTONOMOUS_ACTIONS:
            ai_problems.append(f"{resolution.action} must be decided by a reviewer")
        if reflection_exhausted:
            ai_problems.append("Reflection limit reached")
        if ai_problems:
            ai_passed, ai_actual = False, "; ".join(ai_problems)
        elif resolution is None:
            ai_passed, ai_actual = None, "AI not invoked"
        else:
            ai_passed, ai_actual = True, f"{resolution.action} proposed"
        checks.append(
            PolicyCheck(
                rule="ai_recommendation",
                label="AI recommendation usable",
                threshold="Valid output, permitted action, within reflection limit",
                actual=ai_actual,
                passed=ai_passed,
            )
        )
        return checks

    def evaluate(
        self,
        invoice: Invoice,
        match_result: MatchResult,
        resolution: Resolution | None,
        reflection_exhausted: bool = False,
        model_error: str | None = None,
        model_unavailable: bool = False,
    ) -> PolicyResult:
        checks = self._checks(
            invoice, match_result, resolution, reflection_exhausted, model_error, model_unavailable
        )
        reasons: list[str] = []
        triggered: list[str] = []
        types = {item.type for item in match_result.discrepancies}

        if DiscrepancyType.DUPLICATE_INVOICE in types and self.rules.duplicate_invoice.auto_reject:
            return PolicyResult(
                permitted=True,
                requires_human_review=False,
                reasons=["Configured duplicate-invoice rule mandates rejection."],
                triggered_rules=["duplicate_invoice.auto_reject"],
                enforced_action=DecisionType.REJECT,
                checks=checks,
            )

        if model_error:
            reasons.append(
                "The model was unavailable, so a reviewer must decide."
                if model_unavailable
                else "An unresolved model or evidence validation failure requires review."
            )
            triggered.append("model_error")
        if not match_result.matched or any(
            item.type != DiscrepancyType.PRICE_VARIANCE
            or item.severity not in {Severity.INFO, Severity.WARNING}
            for item in match_result.discrepancies
        ):
            reasons.append("Deterministic autonomous eligibility is not established.")
            triggered.append("deterministic_eligibility")
        if resolution and resolution.action not in AUTONOMOUS_ACTIONS:
            reasons.append("The proposed action requires human review.")
            triggered.append("allowed_action")

        if self._over_value(invoice):
            reasons.append("Invoice value meets or exceeds the human-review threshold.")
            triggered.append("risk.human_review_invoice_value")
        if DiscrepancyType.MISSING_PO in types and self.rules.missing_po.require_human_review:
            reasons.append("A missing PO may never be auto-approved.")
            triggered.append("missing_po.require_human_review")
        if DiscrepancyType.MISSING_GRN in types and self.rules.missing_receipt.require_human_review:
            reasons.append("Missing receipt requires a reviewer.")
            triggered.append("missing_receipt.require_human_review")

        hard_review_types = {
            DiscrepancyType.SUPPLIER_MISMATCH,
            DiscrepancyType.CURRENCY_MISMATCH,
            DiscrepancyType.QUANTITY_EXCEEDS_RECEIPT,
        }
        if types & hard_review_types:
            reasons.append("A hard financial-control discrepancy is present.")
            triggered.append("hard_financial_control")

        outside_tolerance = any(
            item.type in {DiscrepancyType.PRICE_VARIANCE, DiscrepancyType.QUANTITY_VARIANCE}
            and item.severity in MATERIAL_SEVERITIES
            for item in match_result.discrepancies
        )
        if outside_tolerance:
            reasons.append("A material variance exceeds configured tolerance.")
            triggered.append("material_variance")
        if reflection_exhausted:
            reasons.append("The Reflexion limit was reached without a valid recommendation.")
            triggered.append("reflection.max_iterations")
        if self._low_confidence(resolution):
            reasons.append("AI confidence is below the autonomous-resolution threshold.")
            triggered.append("confidence.auto_resolve")

        proposed_action = resolution.action if resolution else DecisionType.APPROVE
        if not reasons:
            reason = (
                "Exact match passed all deterministic controls."
                if not match_result.discrepancies
                else "Only configured non-material tolerances were triggered."
            )
            return PolicyResult(
                permitted=True,
                requires_human_review=False,
                reasons=[reason],
                triggered_rules=["three_way_match"],
                enforced_action=proposed_action,
                checks=checks,
            )

        return PolicyResult(
            permitted=False,
            requires_human_review=True,
            reasons=reasons,
            triggered_rules=triggered,
            enforced_action=None,
            checks=checks,
        )
