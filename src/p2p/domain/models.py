from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

MONEY = Decimal("0.01")


class DecisionType(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_INFO = "REQUEST_INFO"
    ESCALATE = "ESCALATE"


class DecisionSource(StrEnum):
    AUTONOMOUS = "AUTONOMOUS"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    HUMAN_OVERRIDDEN = "HUMAN_OVERRIDDEN"


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    MATERIAL = "MATERIAL"
    CRITICAL = "CRITICAL"


class DiscrepancyType(StrEnum):
    SUPPLIER_INELIGIBLE = "SUPPLIER_INELIGIBLE"
    PO_NOT_OPEN = "PO_NOT_OPEN"
    QUANTITY_EXCEEDS_ORDER = "QUANTITY_EXCEEDS_ORDER"
    MISSING_PO = "MISSING_PO"
    MISSING_GRN = "MISSING_GRN"
    SUPPLIER_MISMATCH = "SUPPLIER_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    PRICE_VARIANCE = "PRICE_VARIANCE"
    QUANTITY_VARIANCE = "QUANTITY_VARIANCE"
    QUANTITY_EXCEEDS_RECEIPT = "QUANTITY_EXCEEDS_RECEIPT"
    UNMATCHED_LINE = "UNMATCHED_LINE"
    ARITHMETIC_INCONSISTENCY = "ARITHMETIC_INCONSISTENCY"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE"


class InvoiceLine(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    line_id: str = Field(min_length=1)
    description: str
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    line_total: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def validate_line_total(self) -> InvoiceLine:
        expected = (self.quantity * self.unit_price).quantize(MONEY, rounding=ROUND_HALF_EVEN)
        if self.line_total.quantize(MONEY, rounding=ROUND_HALF_EVEN) != expected:
            raise ValueError(f"line_total must equal quantity × unit_price ({expected})")
        return self


class Invoice(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    invoice_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    supplier_name: str
    po_number: str | None = None
    invoice_date: date
    currency: str = Field(min_length=3, max_length=3)
    line_items: list[InvoiceLine] = Field(min_length=1)
    subtotal: Decimal = Field(ge=0)
    tax: Decimal = Field(ge=0)
    total: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> Invoice:
        if len({line.line_id for line in self.line_items}) != len(self.line_items):
            raise ValueError("Invoice line IDs must be unique")
        self.invoice_id = self.invoice_id.upper()
        self.currency = self.currency.upper()
        self.supplier_id = self.supplier_id.upper()
        self.po_number = self.po_number.upper() if self.po_number else None
        lines_total = sum((line.line_total for line in self.line_items), Decimal("0"))
        if self.subtotal.quantize(MONEY, rounding=ROUND_HALF_EVEN) != lines_total.quantize(
            MONEY, rounding=ROUND_HALF_EVEN
        ):
            raise ValueError("subtotal does not equal the sum of line totals")
        if self.total.quantize(MONEY, rounding=ROUND_HALF_EVEN) != (
            self.subtotal + self.tax
        ).quantize(MONEY, rounding=ROUND_HALF_EVEN):
            raise ValueError("total does not equal subtotal plus tax")
        return self


class PurchaseOrderLine(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    line_id: str = Field(min_length=1)
    description: str
    ordered_quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)


class PurchaseOrder(BaseModel):
    po_number: str
    supplier_id: str
    currency: str
    status: str = "OPEN"
    line_items: list[PurchaseOrderLine]

    @model_validator(mode="after")
    def unique_lines(self) -> PurchaseOrder:
        if len({line.line_id for line in self.line_items}) != len(self.line_items):
            raise ValueError("PO line IDs must be unique")
        return self


class GoodsReceiptLine(BaseModel):
    po_line_id: str
    received_quantity: Decimal = Field(gt=0)


class GoodsReceipt(BaseModel):
    receipt_id: str
    po_number: str
    receipt_date: date
    line_items: list[GoodsReceiptLine]


class Supplier(BaseModel):
    supplier_id: str
    name: str
    active: bool = True
    risk_rating: str = "LOW"


class InvoiceSummary(BaseModel):
    invoice_id: str
    supplier_id: str
    po_number: str | None
    total: Decimal
    invoice_date: date


class ERPContext(BaseModel):
    reserved_quantities: dict[str, Decimal] = Field(default_factory=dict)
    purchase_order: PurchaseOrder | None = None
    goods_receipts: list[GoodsReceipt] = Field(default_factory=list)
    supplier: Supplier | None = None
    previous_invoices: list[InvoiceSummary] = Field(default_factory=list)


class Discrepancy(BaseModel):
    discrepancy_id: str = Field(default_factory=lambda: f"DISC-{uuid4().hex[:8].upper()}")
    type: DiscrepancyType
    severity: Severity
    line_id: str | None = None
    expected: Decimal | None = None
    actual: Decimal | None = None
    variance_absolute: Decimal | None = None
    variance_percent: Decimal | None = None
    explanation: str


class MatchResult(BaseModel):
    matched: bool
    discrepancies: list[Discrepancy] = Field(default_factory=list)
    checked_lines: int = 0
    summary: str


class EvidenceItem(BaseModel):
    evidence_id: str
    description: str
    source: str


class InvestigationResult(BaseModel):
    root_cause: str
    evidence: list[EvidenceItem]
    missing_information: list[str] = Field(default_factory=list)
    suggested_action: DecisionType
    confidence: float = Field(ge=0, le=1)


class Resolution(BaseModel):
    action: DecisionType
    rationale: str
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    information_request: str | None = None


class CritiqueResult(BaseModel):
    valid: bool
    issues: list[str] = Field(default_factory=list)
    recommended_changes: list[str] = Field(default_factory=list)
    confidence_adjustment: float = Field(default=0, ge=-1, le=1)


class PolicyCheck(BaseModel):
    """One row of the policy checklist; `passed` is None when the check does not apply."""

    rule: str
    label: str
    threshold: str
    actual: str
    passed: bool | None


class PolicyResult(BaseModel):
    permitted: bool
    requires_human_review: bool
    reasons: list[str]
    triggered_rules: list[str] = Field(default_factory=list)
    enforced_action: DecisionType | None = None
    checks: list[PolicyCheck] = Field(default_factory=list)


class HumanReview(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    reviewer: str = Field(default="demo-reviewer", min_length=1)
    action: DecisionType
    comment: str | None = None
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def disallow_escalate(self) -> HumanReview:
        if self.action is DecisionType.ESCALATE:
            raise ValueError("human review action must be APPROVE, REJECT, or REQUEST_INFO")
        return self


class CaseOutcome(BaseModel):
    action: DecisionType
    source: DecisionSource
    rationale: str
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SupersedeRequest(BaseModel):
    """A reviewer links a corrected invoice to a held (REQUEST_INFO) case it replaces."""

    model_config = ConfigDict(str_strip_whitespace=True)
    held_case_id: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    note: str = Field(min_length=3)


class AuditEventType(StrEnum):
    CASE_CREATED = "CASE_CREATED"
    INVOICE_VALIDATED = "INVOICE_VALIDATED"
    ERP_CONTEXT_LOADED = "ERP_CONTEXT_LOADED"
    MATCH_COMPLETED = "MATCH_COMPLETED"
    EXCEPTION_IDENTIFIED = "EXCEPTION_IDENTIFIED"
    INVESTIGATION_COMPLETED = "INVESTIGATION_COMPLETED"
    RESOLUTION_PROPOSED = "RESOLUTION_PROPOSED"
    CRITIQUE_FAILED = "CRITIQUE_FAILED"
    RESOLUTION_REVISED = "RESOLUTION_REVISED"
    CRITIQUE_PASSED = "CRITIQUE_PASSED"
    POLICY_CHECK_COMPLETED = "POLICY_CHECK_COMPLETED"
    HUMAN_REVIEW_REQUESTED = "HUMAN_REVIEW_REQUESTED"
    HUMAN_DECISION_RECEIVED = "HUMAN_DECISION_RECEIVED"
    CASE_COMPLETED = "CASE_COMPLETED"
    CASE_SUPERSEDED = "CASE_SUPERSEDED"
    SUPERSEDES_CASE = "SUPERSEDES_CASE"


class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    case_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_type: AuditEventType
    actor: str
    data: dict[str, Any] = Field(default_factory=dict)
