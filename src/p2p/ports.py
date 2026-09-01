from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

from p2p.domain.models import (
    AuditEvent,
    GoodsReceipt,
    Invoice,
    InvoiceSummary,
    PurchaseOrder,
    Supplier,
)

T = TypeVar("T", bound=BaseModel)


class ERPRepository(Protocol):
    def get_purchase_order(self, po_number: str | None) -> PurchaseOrder | None: ...

    def get_goods_receipts(self, po_number: str | None) -> list[GoodsReceipt]: ...

    def get_supplier(self, supplier_id: str) -> Supplier | None: ...

    def get_previous_invoices(self, supplier_id: str) -> list[InvoiceSummary]: ...


class CaseRepository(Protocol):
    def save_case(self, case_id: str, invoice: Invoice, status: str, state: dict) -> None: ...

    def get_case(self, case_id: str) -> dict | None: ...

    def list_cases(self, status: str | None = None) -> list[dict]: ...


class AuditRecorder(Protocol):
    def record(self, event: AuditEvent) -> None: ...

    def for_case(self, case_id: str) -> list[AuditEvent]: ...


class ModelProvider(Protocol):
    def structured_response(self, schema: type[T], messages: list[dict[str, str]]) -> T: ...
