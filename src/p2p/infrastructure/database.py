from __future__ import annotations

import fcntl
import json
import os
import threading
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Boolean, DateTime, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from p2p.domain.models import (
    AuditEvent,
    AuditEventType,
    GoodsReceipt,
    GoodsReceiptLine,
    Invoice,
    InvoiceLine,
    InvoiceSummary,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
)


def _utcnow() -> datetime:
    """Storage keeps naive UTC; every value leaving the repository is tz-aware."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class SupplierRow(Base):
    __tablename__ = "suppliers"
    supplier_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    risk_rating: Mapped[str] = mapped_column(String, default="LOW")


class PurchaseOrderRow(Base):
    __tablename__ = "purchase_orders"
    po_number: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[str] = mapped_column(Text)


class GoodsReceiptRow(Base):
    __tablename__ = "goods_receipts"
    receipt_id: Mapped[str] = mapped_column(String, primary_key=True)
    po_number: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[str] = mapped_column(Text)


class InvoiceHistoryRow(Base):
    __tablename__ = "invoice_history"
    row_id: Mapped[str] = mapped_column(String, primary_key=True)
    supplier_id: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[str] = mapped_column(Text)


class CaseRow(Base):
    __tablename__ = "cases"
    case_id: Mapped[str] = mapped_column(String, primary_key=True)
    invoice_id: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, index=True)
    invoice_payload: Mapped[str] = mapped_column(Text)
    state_payload: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class CaseCommandRow(Base):
    __tablename__ = "case_commands"
    case_id: Mapped[str] = mapped_column(String, primary_key=True)
    identity: Mapped[str] = mapped_column(String, unique=True)
    invoice_payload: Mapped[str] = mapped_column(Text)
    review_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint_id: Mapped[str | None] = mapped_column(String, nullable=True)


_LOCKS: dict[str, Any] = {}
_LOCKS_GUARD = threading.Lock()


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(String, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    event_type: Mapped[str] = mapped_column(String)
    actor: Mapped[str] = mapped_column(String)
    data_payload: Mapped[str] = mapped_column(Text)


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    return str(value)


def to_json(value: Any) -> str:
    return json.dumps(value, default=_json_default, sort_keys=True)


class Database:
    def __init__(self, url: str = "sqlite:///data/demo/p2p.db"):
        if url.startswith("sqlite:///"):
            db_path = Path(url.removeprefix("sqlite:///"))
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self.url = url
        self.engine = create_engine(url)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)


class SqliteRepository:
    def __init__(self, database: Database):
        self.database = database

    @contextmanager
    def command_lock(self):
        """Serialize commands across threads/processes sharing this local SQLite file."""
        path = str(Path(self.database.url.removeprefix("sqlite:///")).resolve())
        with _LOCKS_GUARD:
            lock = _LOCKS.setdefault(path, threading.RLock())
        with lock, open(path + ".commands.lock", "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def has_data(self) -> bool:
        with self.database.session_factory() as session:
            return any(
                session.scalar(select(model).limit(1)) is not None
                for model in (
                    CaseRow,
                    CaseCommandRow,
                    SupplierRow,
                    PurchaseOrderRow,
                    GoodsReceiptRow,
                    InvoiceHistoryRow,
                    AuditEventRow,
                )
            )

    def claim_case(self, case_id: str, invoice: Invoice) -> tuple[str, bool]:
        payload = to_json(invoice)
        identity = to_json(
            [invoice.supplier_id.strip().upper(), invoice.invoice_id.strip().upper()]
        )
        with self.database.session_factory() as session:
            command = session.get(CaseCommandRow, case_id)
            if command and command.invoice_payload != payload:
                raise ValueError("Case ID is permanently bound to different invoice content")
            # Include pre-migration cases; never silently combine legacy duplicates.
            for row in session.scalars(select(CaseRow)).all():
                existing = Invoice.model_validate_json(row.invoice_payload)
                same_identity = (
                    existing.supplier_id.strip().upper(),
                    existing.invoice_id.strip().upper(),
                ) == (invoice.supplier_id, invoice.invoice_id)
                if row.case_id == case_id or same_identity:
                    if to_json(existing) != payload:
                        raise ValueError(
                            "Invoice or case identity already exists with different content"
                        )
                    return row.case_id, False
            session.add(CaseCommandRow(case_id=case_id, identity=identity, invoice_payload=payload))
            session.add(
                CaseRow(
                    case_id=case_id,
                    invoice_id=invoice.invoice_id,
                    status="PROCESSING",
                    invoice_payload=invoice.model_dump_json(),
                    state_payload="{}",
                )
            )
            session.commit()
        return case_id, True

    def bind_review(self, case_id: str, review: dict, checkpoint_id: str) -> dict:
        with self.database.session_factory() as session:
            command = session.get(CaseCommandRow, case_id)
            if command is None:
                case = session.get(CaseRow, case_id)
                invoice = Invoice.model_validate_json(case.invoice_payload)
                command = CaseCommandRow(
                    case_id=case_id,
                    identity=to_json([invoice.supplier_id, invoice.invoice_id]),
                    invoice_payload=to_json(invoice),
                )
                session.add(command)
            if command.review_payload:
                previous = json.loads(command.review_payload)
                if any(previous.get(k) != review.get(k) for k in ("action", "reviewer", "comment")):
                    raise ValueError(
                        "A different review is already durably submitted for this case"
                    )
                if command.checkpoint_id != checkpoint_id:
                    raise ValueError("Submitted review belongs to a different checkpoint revision")
                return previous
            command.review_payload = to_json(review)
            command.checkpoint_id = checkpoint_id
            session.commit()
            return review

    def submitted_review(self, case_id: str) -> dict | None:
        with self.database.session_factory() as session:
            row = session.get(CaseCommandRow, case_id)
            return json.loads(row.review_payload) if row and row.review_payload else None

    def reserved_quantities(
        self, po_number: str | None, exclude_case_id: str
    ) -> dict[str, Decimal]:
        quantities: dict[str, Decimal] = {}
        for case in self.list_cases():
            if case["case_id"] == exclude_case_id or case["invoice"].get("po_number") != po_number:
                continue
            if case["state"].get("outcome", {}).get("action") == "REJECT":
                continue
            # Pending, interrupted, failed, and REQUEST_INFO cases retain reservations.
            for line in case["invoice"]["line_items"]:
                quantities[line["line_id"]] = quantities.get(
                    line["line_id"], Decimal("0")
                ) + Decimal(line["quantity"])
        return quantities

    def get_purchase_order(self, po_number: str | None) -> PurchaseOrder | None:
        if not po_number:
            return None
        with self.database.session_factory() as session:
            row = session.get(PurchaseOrderRow, po_number)
            return PurchaseOrder.model_validate_json(row.payload) if row else None

    def get_goods_receipts(self, po_number: str | None) -> list[GoodsReceipt]:
        if not po_number:
            return []
        with self.database.session_factory() as session:
            rows = session.scalars(
                select(GoodsReceiptRow).where(GoodsReceiptRow.po_number == po_number)
            ).all()
            return [GoodsReceipt.model_validate_json(row.payload) for row in rows]

    def get_supplier(self, supplier_id: str) -> Supplier | None:
        with self.database.session_factory() as session:
            row = session.get(SupplierRow, supplier_id)
            if not row:
                return None
            return Supplier(
                supplier_id=row.supplier_id,
                name=row.name,
                active=row.active,
                risk_rating=row.risk_rating,
            )

    def get_previous_invoices(self, supplier_id: str) -> list[InvoiceSummary]:
        with self.database.session_factory() as session:
            rows = session.scalars(
                select(InvoiceHistoryRow).where(InvoiceHistoryRow.supplier_id == supplier_id)
            ).all()
            return [InvoiceSummary.model_validate_json(row.payload) for row in rows]

    def save_case(self, case_id: str, invoice: Invoice, status: str, state: dict) -> None:
        with self.database.session_factory() as session:
            row = session.get(CaseRow, case_id)
            if row is None:
                row = CaseRow(
                    case_id=case_id,
                    invoice_id=invoice.invoice_id,
                    status=status,
                    invoice_payload=invoice.model_dump_json(),
                    state_payload=to_json(state),
                )
                session.add(row)
            else:
                if to_json(Invoice.model_validate_json(row.invoice_payload)) != to_json(invoice):
                    raise ValueError("Case invoice content is immutable")
                payload = to_json(state)
                if row.status != status or row.state_payload != payload:
                    row.status = status
                    row.state_payload = payload
                    row.updated_at = _utcnow()
            if status == "COMPLETED":
                outcome = state.get("outcome")
                if not outcome:
                    raise ValueError("Completed case requires a validated outcome")
                for event_type, data in [
                    (AuditEventType.HUMAN_DECISION_RECEIVED, state.get("human_review")),
                    (AuditEventType.CASE_COMPLETED, outcome),
                ]:
                    if not data:
                        continue
                    event_id = f"{case_id}:{event_type.value}"
                    if not session.get(AuditEventRow, event_id):
                        session.add(
                            AuditEventRow(
                                event_id=event_id,
                                case_id=case_id,
                                timestamp=_utcnow(),
                                event_type=event_type.value,
                                actor="human_reviewer"
                                if event_type == AuditEventType.HUMAN_DECISION_RECEIVED
                                else "workflow",
                                data_payload=to_json(data),
                            )
                        )
            session.commit()

    def get_case(self, case_id: str) -> dict | None:
        with self.database.session_factory() as session:
            row = session.get(CaseRow, case_id)
            return self._case_dict(row) if row else None

    def list_cases(self, status: str | None = None) -> list[dict]:
        with self.database.session_factory() as session:
            statement = select(CaseRow).order_by(CaseRow.updated_at.desc())
            if status:
                statement = statement.where(CaseRow.status == status)
            return [self._case_dict(row) for row in session.scalars(statement).all()]

    @staticmethod
    def _case_dict(row: CaseRow) -> dict:
        return {
            "case_id": row.case_id,
            "invoice_id": row.invoice_id,
            "status": row.status,
            "invoice": json.loads(row.invoice_payload),
            "state": json.loads(row.state_payload),
            "updated_at": row.updated_at.replace(tzinfo=UTC).isoformat(),
        }

    def record(self, event: AuditEvent) -> None:
        with self.database.session_factory() as session:
            if session.get(AuditEventRow, event.event_id):
                return
            session.add(
                AuditEventRow(
                    event_id=event.event_id,
                    case_id=event.case_id,
                    timestamp=event.timestamp.replace(tzinfo=None),
                    event_type=event.event_type.value,
                    actor=event.actor,
                    data_payload=to_json(event.data),
                )
            )
            session.commit()

    def for_case(self, case_id: str) -> list[AuditEvent]:
        with self.database.session_factory() as session:
            rows = session.scalars(
                select(AuditEventRow)
                .where(AuditEventRow.case_id == case_id)
                .order_by(AuditEventRow.timestamp)
            ).all()
            return [
                AuditEvent(
                    event_id=row.event_id,
                    case_id=row.case_id,
                    timestamp=row.timestamp.replace(tzinfo=UTC),
                    event_type=row.event_type,
                    actor=row.actor,
                    data=json.loads(row.data_payload),
                )
                for row in rows
            ]


def demo_invoices() -> dict[str, Invoice]:
    def invoice(
        invoice_id: str,
        supplier_id: str,
        supplier_name: str,
        po_number: str | None,
        quantity: str,
        unit_price: str,
        line_id: str = "1",
        tax: str = "0",
    ) -> Invoice:
        subtotal = Decimal(quantity) * Decimal(unit_price)
        return Invoice(
            invoice_id=invoice_id,
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            po_number=po_number,
            invoice_date=date(2026, 8, 15),
            currency="EUR",
            line_items=[
                InvoiceLine(
                    line_id=line_id,
                    description="Industrial components",
                    quantity=Decimal(quantity),
                    unit_price=Decimal(unit_price),
                    line_total=subtotal,
                )
            ],
            subtotal=subtotal,
            tax=Decimal(tax),
            total=subtotal + Decimal(tax),
        )

    return {
        "01_exact_match": invoice(
            "INV-EXACT", "SUP-001", "Acme Components", "PO-1001", "10", "100"
        ),
        "02_small_price_variance": invoice(
            "INV-SMALL", "SUP-002", "Northstar Supply", "PO-1003", "100", "25.25"
        ),
        "03_large_price_variance": invoice(
            "INV-LARGE", "SUP-002", "Northstar Supply", "PO-1006", "10", "220"
        ),
        "04_quantity_variance": invoice(
            "INV-QTY", "SUP-002", "Northstar Supply", "PO-1003", "95", "25"
        ),
        "05_quantity_exceeds_receipt": invoice(
            "INV-OVER", "SUP-001", "Acme Components", "PO-1002", "20", "50"
        ),
        "06_missing_po": invoice("INV-NOPO", "SUP-001", "Acme Components", None, "2", "100"),
        "07_missing_grn": invoice(
            "INV-NOGRN", "SUP-003", "Blue River Services", "PO-1004", "5", "500"
        ),
        "08_duplicate_invoice": invoice(
            "DUP-001", "SUP-001", "Acme Components", "PO-1001", "10", "100"
        ),
        "09_supplier_mismatch": invoice(
            "INV-SUP", "SUP-002", "Northstar Supply", "PO-1001", "10", "100"
        ),
        "10_ambiguous_high_value": invoice(
            "INV-HIGH", "SUP-003", "Blue River Services", "PO-1005", "1", "60000"
        ),
    }


def seed_database(database: Database, invoice_dir: str | Path = "data/demo/invoices") -> None:
    database.create_all()
    suppliers = [
        Supplier(supplier_id="SUP-001", name="Acme Components", active=True, risk_rating="LOW"),
        Supplier(supplier_id="SUP-002", name="Northstar Supply", active=True, risk_rating="MEDIUM"),
        Supplier(supplier_id="SUP-003", name="Blue River Services", active=True, risk_rating="LOW"),
    ]
    pos = [
        PurchaseOrder(
            po_number="PO-1001",
            supplier_id="SUP-001",
            currency="EUR",
            line_items=[
                PurchaseOrderLine(
                    line_id="1",
                    description="Components",
                    ordered_quantity=Decimal("10"),
                    unit_price=Decimal("100"),
                )
            ],
        ),
        PurchaseOrder(
            po_number="PO-1002",
            supplier_id="SUP-001",
            currency="EUR",
            line_items=[
                PurchaseOrderLine(
                    line_id="1",
                    description="Components",
                    ordered_quantity=Decimal("20"),
                    unit_price=Decimal("50"),
                )
            ],
        ),
        PurchaseOrder(
            po_number="PO-1003",
            supplier_id="SUP-002",
            currency="EUR",
            line_items=[
                PurchaseOrderLine(
                    line_id="1",
                    description="Components",
                    ordered_quantity=Decimal("100"),
                    unit_price=Decimal("25"),
                )
            ],
        ),
        PurchaseOrder(
            po_number="PO-1004",
            supplier_id="SUP-003",
            currency="EUR",
            line_items=[
                PurchaseOrderLine(
                    line_id="1",
                    description="Services",
                    ordered_quantity=Decimal("5"),
                    unit_price=Decimal("500"),
                )
            ],
        ),
        PurchaseOrder(
            po_number="PO-1005",
            supplier_id="SUP-003",
            currency="EUR",
            line_items=[
                PurchaseOrderLine(
                    line_id="1",
                    description="Equipment",
                    ordered_quantity=Decimal("1"),
                    unit_price=Decimal("60000"),
                )
            ],
        ),
        PurchaseOrder(
            po_number="PO-1006",
            supplier_id="SUP-002",
            currency="EUR",
            line_items=[
                PurchaseOrderLine(
                    line_id="1",
                    description="Components",
                    ordered_quantity=Decimal("10"),
                    unit_price=Decimal("200"),
                )
            ],
        ),
    ]
    receipts = [
        GoodsReceipt(
            receipt_id="GR-1001",
            po_number="PO-1001",
            receipt_date=date(2026, 8, 1),
            line_items=[GoodsReceiptLine(po_line_id="1", received_quantity=Decimal("10"))],
        ),
        GoodsReceipt(
            receipt_id="GR-1002",
            po_number="PO-1002",
            receipt_date=date(2026, 8, 2),
            line_items=[GoodsReceiptLine(po_line_id="1", received_quantity=Decimal("10"))],
        ),
        GoodsReceipt(
            receipt_id="GR-1003A",
            po_number="PO-1003",
            receipt_date=date(2026, 8, 3),
            line_items=[GoodsReceiptLine(po_line_id="1", received_quantity=Decimal("40"))],
        ),
        GoodsReceipt(
            receipt_id="GR-1003B",
            po_number="PO-1003",
            receipt_date=date(2026, 8, 4),
            line_items=[GoodsReceiptLine(po_line_id="1", received_quantity=Decimal("60"))],
        ),
        GoodsReceipt(
            receipt_id="GR-1005",
            po_number="PO-1005",
            receipt_date=date(2026, 8, 5),
            line_items=[GoodsReceiptLine(po_line_id="1", received_quantity=Decimal("1"))],
        ),
        GoodsReceipt(
            receipt_id="GR-1006",
            po_number="PO-1006",
            receipt_date=date(2026, 8, 6),
            line_items=[GoodsReceiptLine(po_line_id="1", received_quantity=Decimal("10"))],
        ),
    ]
    history = [
        InvoiceSummary(
            invoice_id="DUP-001",
            supplier_id="SUP-001",
            po_number="PO-1001",
            total=Decimal("1000"),
            invoice_date=date(2026, 8, 10),
        ),
        InvoiceSummary(
            invoice_id="HIST-002",
            supplier_id="SUP-002",
            po_number="PO-1003",
            total=Decimal("2500"),
            invoice_date=date(2026, 7, 10),
        ),
    ]
    with database.session_factory() as session:

        def add_missing(rows):
            for row in rows:
                key = getattr(row, row.__mapper__.primary_key[0].key)
                if session.get(type(row), key) is None:
                    session.add(row)

        add_missing([SupplierRow(**item.model_dump()) for item in suppliers])
        add_missing(
            [
                PurchaseOrderRow(po_number=item.po_number, payload=item.model_dump_json())
                for item in pos
            ]
        )
        add_missing(
            [
                GoodsReceiptRow(
                    receipt_id=item.receipt_id,
                    po_number=item.po_number,
                    payload=item.model_dump_json(),
                )
                for item in receipts
            ]
        )
        add_missing(
            [
                InvoiceHistoryRow(
                    row_id=f"{item.supplier_id}:{item.invoice_id}",
                    supplier_id=item.supplier_id,
                    payload=item.model_dump_json(),
                )
                for item in history
            ]
        )
        session.commit()

    target = Path(invoice_dir)
    target.mkdir(parents=True, exist_ok=True)
    for name, item in demo_invoices().items():
        (target / f"{name}.json").write_text(item.model_dump_json(indent=2), encoding="utf-8")


def seed_cli() -> None:
    url = os.getenv("P2P_DATABASE_URL", "sqlite:///data/demo/p2p.db")
    seed_database(Database(url))
    print("Seeded 3 suppliers, 6 POs, 6 receipts, and 10 demo invoices.")


if __name__ == "__main__":
    seed_cli()
