from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from p2p.domain.config import BusinessRules
from p2p.domain.models import (
    MONEY,
    Discrepancy,
    DiscrepancyType,
    ERPContext,
    Invoice,
    MatchResult,
    Severity,
)


def variance(expected: Decimal, actual: Decimal) -> tuple[Decimal, Decimal]:
    absolute = actual - expected
    percent = Decimal("0") if expected == 0 else (absolute / expected * Decimal("100"))
    return absolute.quantize(Decimal("0.01")), percent.quantize(Decimal("0.01"))


class ThreeWayMatcher:
    """Pure deterministic invoice/PO/receipt comparison."""

    def __init__(self, rules: BusinessRules):
        self.rules = rules

    def match(self, invoice: Invoice, context: ERPContext) -> MatchResult:
        discrepancies = self._arithmetic_discrepancies(invoice)
        po = context.purchase_order

        duplicate = next(
            (item for item in context.previous_invoices if item.invoice_id == invoice.invoice_id),
            None,
        )
        if duplicate:
            discrepancies.append(
                Discrepancy(
                    type=DiscrepancyType.DUPLICATE_INVOICE,
                    severity=Severity.CRITICAL,
                    expected=duplicate.total,
                    actual=invoice.total,
                    explanation=f"Invoice {invoice.invoice_id} already exists for this supplier.",
                )
            )

        if po is None:
            discrepancies.append(
                Discrepancy(
                    type=DiscrepancyType.MISSING_PO,
                    severity=Severity.MATERIAL,
                    explanation="No purchase order could be retrieved for the invoice.",
                )
            )
            return self._result(invoice, discrepancies)

        if po.supplier_id != invoice.supplier_id:
            discrepancies.append(
                Discrepancy(
                    type=DiscrepancyType.SUPPLIER_MISMATCH,
                    severity=Severity.CRITICAL,
                    explanation=(
                        f"Invoice supplier {invoice.supplier_id} does not match PO supplier "
                        f"{po.supplier_id}."
                    ),
                )
            )
        if po.currency.upper() != invoice.currency.upper():
            discrepancies.append(
                Discrepancy(
                    type=DiscrepancyType.CURRENCY_MISMATCH,
                    severity=Severity.CRITICAL,
                    explanation=f"Invoice currency {invoice.currency} differs from PO {po.currency}.",
                )
            )

        received_by_line: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for receipt in context.goods_receipts:
            for line in receipt.line_items:
                received_by_line[line.po_line_id] += line.received_quantity

        if not context.goods_receipts:
            discrepancies.append(
                Discrepancy(
                    type=DiscrepancyType.MISSING_GRN,
                    severity=Severity.MATERIAL,
                    explanation=f"No goods receipt exists for {po.po_number}.",
                )
            )

        po_lines = {line.line_id: line for line in po.line_items}
        for invoice_line in invoice.line_items:
            po_line = po_lines.get(invoice_line.line_id)
            if po_line is None:
                discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.UNMATCHED_LINE,
                        severity=Severity.MATERIAL,
                        line_id=invoice_line.line_id,
                        explanation="Invoice line does not exist on the purchase order.",
                    )
                )
                continue

            price_abs, price_pct = variance(po_line.unit_price, invoice_line.unit_price)
            if price_abs != 0:
                within_tolerance = (
                    abs(price_pct) <= self.rules.price_variance.auto_approve_percent
                    and abs(price_abs * invoice_line.quantity)
                    <= self.rules.price_variance.auto_approve_absolute
                )
                discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.PRICE_VARIANCE,
                        severity=Severity.WARNING if within_tolerance else Severity.MATERIAL,
                        line_id=invoice_line.line_id,
                        expected=po_line.unit_price,
                        actual=invoice_line.unit_price,
                        variance_absolute=price_abs,
                        variance_percent=price_pct,
                        explanation=(
                            f"Unit price variance is {price_pct}% ({price_abs} {invoice.currency}); "
                            f"{'within' if within_tolerance else 'outside'} configured tolerance."
                        ),
                    )
                )

            received = received_by_line[invoice_line.line_id]
            qty_abs, qty_pct = variance(received, invoice_line.quantity)
            if (
                context.goods_receipts
                and abs(qty_pct) > self.rules.quantity_variance.tolerance_percent
            ):
                discrepancy_type = (
                    DiscrepancyType.QUANTITY_EXCEEDS_RECEIPT
                    if invoice_line.quantity > received
                    else DiscrepancyType.QUANTITY_VARIANCE
                )
                discrepancies.append(
                    Discrepancy(
                        type=discrepancy_type,
                        severity=Severity.MATERIAL,
                        line_id=invoice_line.line_id,
                        expected=received,
                        actual=invoice_line.quantity,
                        variance_absolute=qty_abs,
                        variance_percent=qty_pct,
                        explanation=(
                            f"Invoiced quantity {invoice_line.quantity} differs from received "
                            f"quantity {received} by {qty_pct}%."
                        ),
                    )
                )

        return self._result(invoice, discrepancies)

    @staticmethod
    def _arithmetic_discrepancies(invoice: Invoice) -> list[Discrepancy]:
        """Recheck invoice arithmetic in case validated data was later corrupted."""
        discrepancies: list[Discrepancy] = []
        for line in invoice.line_items:
            expected = (line.quantity * line.unit_price).quantize(MONEY)
            actual = line.line_total.quantize(MONEY)
            if actual != expected:
                absolute, percent = variance(expected, actual)
                discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.ARITHMETIC_INCONSISTENCY,
                        severity=Severity.CRITICAL,
                        line_id=line.line_id,
                        expected=expected,
                        actual=actual,
                        variance_absolute=absolute,
                        variance_percent=percent,
                        explanation=(
                            f"Line total {actual} does not equal quantity times unit price "
                            f"({expected})."
                        ),
                    )
                )

        expected_subtotal = sum(
            (line.line_total for line in invoice.line_items), Decimal("0")
        ).quantize(MONEY)
        actual_subtotal = invoice.subtotal.quantize(MONEY)
        if actual_subtotal != expected_subtotal:
            absolute, percent = variance(expected_subtotal, actual_subtotal)
            discrepancies.append(
                Discrepancy(
                    type=DiscrepancyType.ARITHMETIC_INCONSISTENCY,
                    severity=Severity.CRITICAL,
                    expected=expected_subtotal,
                    actual=actual_subtotal,
                    variance_absolute=absolute,
                    variance_percent=percent,
                    explanation=(
                        f"Invoice subtotal {actual_subtotal} does not equal the sum of line "
                        f"totals ({expected_subtotal})."
                    ),
                )
            )

        expected_total = (invoice.subtotal + invoice.tax).quantize(MONEY)
        actual_total = invoice.total.quantize(MONEY)
        if actual_total != expected_total:
            absolute, percent = variance(expected_total, actual_total)
            discrepancies.append(
                Discrepancy(
                    type=DiscrepancyType.ARITHMETIC_INCONSISTENCY,
                    severity=Severity.CRITICAL,
                    expected=expected_total,
                    actual=actual_total,
                    variance_absolute=absolute,
                    variance_percent=percent,
                    explanation=(
                        f"Invoice total {actual_total} does not equal subtotal plus tax "
                        f"({expected_total})."
                    ),
                )
            )

        return discrepancies

    @staticmethod
    def _result(invoice: Invoice, discrepancies: list[Discrepancy]) -> MatchResult:
        material = [
            item
            for item in discrepancies
            if item.severity in {Severity.MATERIAL, Severity.CRITICAL}
        ]
        if not discrepancies:
            summary = "Exact three-way match."
        elif not material:
            summary = "Matched with non-material variance inside configured tolerance."
        else:
            summary = f"{len(material)} material exception(s) require resolution."
        return MatchResult(
            matched=not material,
            discrepancies=discrepancies,
            checked_lines=len(invoice.line_items),
            summary=summary,
        )
