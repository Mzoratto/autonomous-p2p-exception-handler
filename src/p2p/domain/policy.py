from __future__ import annotations

from p2p.domain.config import BusinessRules
from p2p.domain.models import (
    DecisionType,
    DiscrepancyType,
    Invoice,
    MatchResult,
    PolicyResult,
    Resolution,
    Severity,
)


class DecisionPolicy:
    """Deterministic authorization boundary. AI recommendations cannot bypass it."""

    def __init__(self, rules: BusinessRules):
        self.rules = rules

    def evaluate(
        self,
        invoice: Invoice,
        match_result: MatchResult,
        resolution: Resolution | None,
        reflection_exhausted: bool = False,
        model_error: str | None = None,
    ) -> PolicyResult:
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
            )

        if model_error:
            reasons.append("An unresolved model or evidence validation failure requires review.")
            triggered.append("model_error")
        if not match_result.matched or any(
            item.type != DiscrepancyType.PRICE_VARIANCE
            or item.severity not in {Severity.INFO, Severity.WARNING}
            for item in match_result.discrepancies
        ):
            reasons.append("Deterministic autonomous eligibility is not established.")
            triggered.append("deterministic_eligibility")
        if resolution and resolution.action not in {
            DecisionType.APPROVE,
            DecisionType.REJECT,
            DecisionType.REQUEST_INFO,
        }:
            reasons.append("The proposed action requires human review.")
            triggered.append("allowed_action")

        if invoice.total >= self.rules.risk.human_review_invoice_value:
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
            and item.severity in {Severity.MATERIAL, Severity.CRITICAL}
            for item in match_result.discrepancies
        )
        if outside_tolerance:
            reasons.append("A material variance exceeds configured tolerance.")
            triggered.append("material_variance")
        if reflection_exhausted:
            reasons.append("The Reflexion limit was reached without a valid recommendation.")
            triggered.append("reflection.max_iterations")
        if resolution and resolution.confidence < self.rules.confidence.auto_resolve:
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
            )

        return PolicyResult(
            permitted=False,
            requires_human_review=True,
            reasons=reasons,
            triggered_rules=triggered,
            enforced_action=None,
        )
