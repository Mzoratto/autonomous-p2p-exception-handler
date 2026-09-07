from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_EVEN, Decimal

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


def variance(expected: Decimal, actual: Decimal) -> tuple[Decimal, Decimal | None]:
    absolute = actual - expected
    # A percentage against a zero base is undefined, never evidence of a match.
    percent = None if expected == 0 else absolute / expected * Decimal("100")
    return absolute, percent


class ThreeWayMatcher:
    """Pure deterministic invoice/PO/receipt comparison."""

    def __init__(self, rules: BusinessRules):
        self.rules = rules

    def match(self, invoice: Invoice, context: ERPContext) -> MatchResult:
        discrepancies = self._arithmetic_discrepancies(invoice)
        po = context.purchase_order
        if (
            context.supplier is None
            or not context.supplier.active
            or context.supplier.supplier_id != invoice.supplier_id
        ):
            discrepancies.append(
                Discrepancy(
                    type=DiscrepancyType.SUPPLIER_INELIGIBLE,
                    severity=Severity.CRITICAL,
                    explanation="Supplier master is missing, inactive, or inconsistent.",
                )
            )

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

        if po.status != "OPEN":
            discrepancies.append(
                Discrepancy(
                    type=DiscrepancyType.PO_NOT_OPEN,
                    severity=Severity.CRITICAL,
                    explanation="Only OPEN purchase orders are eligible for autonomous approval.",
                )
            )

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
            if receipt.po_number != po.po_number:
                continue
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

            reserved = context.reserved_quantities.get(invoice_line.line_id, Decimal("0"))
            remaining_order = max(Decimal("0"), po_line.ordered_quantity - reserved)
            if invoice_line.quantity > remaining_order:
                discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.QUANTITY_EXCEEDS_ORDER,
                        severity=Severity.CRITICAL,
                        line_id=invoice_line.line_id,
                        expected=remaining_order,
                        actual=invoice_line.quantity,
                        explanation="Invoice exceeds remaining ordered quantity.",
                    )
                )

            price_abs, price_pct = variance(po_line.unit_price, invoice_line.unit_price)
            if price_abs != 0:
                within_tolerance = (
                    (po_line.unit_price != 0)
                    and abs(price_abs) * 100
                    <= po_line.unit_price * self.rules.price_variance.auto_approve_percent
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
                            f"Unit price variance is {price_pct if price_pct is not None else 'undefined (zero base)'} ({price_abs} {invoice.currency}); "
                            f"{'within' if within_tolerance else 'outside'} configured tolerance."
                        ),
                    )
                )

            received = max(Decimal("0"), received_by_line[invoice_line.line_id] - reserved)
            qty_abs, qty_pct = variance(received, invoice_line.quantity)
            if context.goods_receipts and (
                received == 0
                or abs(qty_abs) * 100 > received * self.rules.quantity_variance.tolerance_percent
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
                            f"quantity {received}; variance percent: {qty_pct if qty_pct is not None else 'undefined (zero base)'}."
                        ),
                    )
                )

        return self._result(invoice, discrepancies)

    @staticmethod
    def _arithmetic_discrepancies(invoice: Invoice) -> list[Discrepancy]:
        """Recheck invoice arithmetic in case validated data was later corrupted."""
        discrepancies: list[Discrepancy] = []
        for line in invoice.line_items:
            expected = (line.quantity * line.unit_price).quantize(MONEY, rounding=ROUND_HALF_EVEN)
            actual = line.line_total.quantize(MONEY, rounding=ROUND_HALF_EVEN)
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
        ).quantize(MONEY, rounding=ROUND_HALF_EVEN)
        actual_subtotal = invoice.subtotal.quantize(MONEY, rounding=ROUND_HALF_EVEN)
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

        expected_total = (invoice.subtotal + invoice.tax).quantize(MONEY, rounding=ROUND_HALF_EVEN)
        actual_total = invoice.total.quantize(MONEY, rounding=ROUND_HALF_EVEN)
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
