from __future__ import annotations

from decimal import Decimal
from typing import Literal, Protocol, TypedDict, TypeVar

from pydantic import BaseModel

from p2p.domain.models import (
    AuditEvent,
    GoodsReceipt,
    InvoiceSummary,
    PurchaseOrder,
    Supplier,
)

T = TypeVar("T", bound=BaseModel)


class ChatMessage(TypedDict):
    """One message to the model; the services send a system prompt and a JSON user message."""

    role: Literal["system", "user"]
    content: str


class ERPRepository(Protocol):
    def reserved_quantities(
        self, po_number: str | None, exclude_case_id: str
    ) -> dict[str, Decimal]: ...

    def get_purchase_order(self, po_number: str | None) -> PurchaseOrder | None: ...

    def get_goods_receipts(self, po_number: str | None) -> list[GoodsReceipt]: ...

    def get_supplier(self, supplier_id: str) -> Supplier | None: ...

    def get_previous_invoices(self, supplier_id: str) -> list[InvoiceSummary]: ...


class AuditRecorder(Protocol):
    def record(self, event: AuditEvent) -> None: ...

    def for_case(self, case_id: str) -> list[AuditEvent]: ...


class ModelProvider(Protocol):
    def structured_response(self, schema: type[T], messages: list[ChatMessage]) -> T: ...


class ModelUnavailableError(RuntimeError):
    """The model service could not be reached or is overloaded; the case goes to a reviewer."""


class ModelAuthenticationError(RuntimeError):
    """The model service rejected the key, denied it the model, or did not find the model (401/403/404).

    A configuration fault in `OPENAI_API_KEY`, `OPENAI_MODEL` or `OPENAI_BASE_URL`, not an outage.
    """


class ModelConfigurationError(RuntimeError):
    """Live-mode settings that cannot work; the dashboard shows the setup screen instead.

    The message is the setup screen's title; `fix` says what to change.
    """

    def __init__(self, title: str, fix: str):
        super().__init__(title)
        self.title = title
        self.fix = fix


class SupersedeRefusedError(ValueError):
    """A corrected invoice cannot supersede the named held case; nothing was stored.

    The message is written for the reviewer.
    """
