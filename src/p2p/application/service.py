from __future__ import annotations

import logging
import os
import sqlite3
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, get_type_hints
from uuid import uuid4

from dotenv import load_dotenv
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import TypeAdapter

from p2p.domain.config import BusinessRules
from p2p.domain.matching import ThreeWayMatcher
from p2p.domain.models import (
    CaseOutcome,
    CritiqueResult,
    DecisionSource,
    DecisionType,
    Discrepancy,
    DiscrepancyType,
    ERPContext,
    EvidenceItem,
    GoodsReceipt,
    GoodsReceiptLine,
    HumanReview,
    InvestigationResult,
    Invoice,
    InvoiceLine,
    InvoiceSummary,
    MatchResult,
    PolicyCheck,
    PolicyResult,
    PurchaseOrder,
    PurchaseOrderLine,
    Resolution,
    Severity,
    SupersedeRequest,
    Supplier,
)
from p2p.domain.policy import DecisionPolicy
from p2p.infrastructure.database import (
    BUSY_TIMEOUT_MS,
    Database,
    SqliteRepository,
    demo_invoices,
    seed_database,
)
from p2p.intelligence.providers import MockModelProvider, OpenAIModelProvider
from p2p.intelligence.services import Critic, Investigator, Resolver
from p2p.workflow.graph import build_graph
from p2p.workflow.nodes import WorkflowDependencies
from p2p.workflow.state import P2PState


def _is_true(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").lower() in {"1", "true", "yes", "on"}


HUMAN_SOURCES = {"HUMAN_APPROVED", "HUMAN_OVERRIDDEN"}

logger = logging.getLogger("p2p.service")


def _value_by_currency(rows: list[dict]) -> dict[str, str]:
    """Sum stored totals by currency; a row the migration could not read has no total."""
    totals: dict[str, Decimal] = {}
    for row in rows:
        if row["total"] is None:
            continue
        totals[row["currency"]] = totals.get(row["currency"], Decimal("0")) + Decimal(row["total"])
    return {currency: f"{amount:.2f}" for currency, amount in sorted(totals.items())}


def resolve_rules_path(explicit_path: str | Path | None = None) -> Path:
    """Resolve externally managed rules in packaged and editable executions."""
    configured = explicit_path or os.getenv("P2P_RULES_PATH")
    if configured:
        candidate = Path(configured).expanduser()
        candidate = candidate if candidate.is_absolute() else Path.cwd() / candidate
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"Configured P2P rules file does not exist: {candidate}")

    candidates = [
        Path.cwd() / "config" / "business_rules.yaml",
        Path(__file__).resolve().parents[3] / "config" / "business_rules.yaml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(
        f"Could not locate business_rules.yaml. Searched: {searched}. "
        "Set P2P_RULES_PATH to an explicit file."
    )


@dataclass
class Runtime:
    repository: SqliteRepository
    graph: Any
    rules: BusinessRules
    demo_mode: bool
    checkpoint_connection: sqlite3.Connection | None = None
    rules_path: Path | None = None
    model_name: str = "Mock provider"

    def close(self) -> None:
        if self.checkpoint_connection:
            self.checkpoint_connection.close()


def create_runtime(
    database_url: str | None = None,
    demo_mode: bool | None = None,
    checkpointer: Any | None = None,
    auto_seed: bool = True,
    rules_path: str | Path | None = None,
) -> Runtime:
    load_dotenv(".env.local")
    load_dotenv(".env")
    url = database_url or os.getenv("P2P_DATABASE_URL", "sqlite:///data/demo/p2p.db")
    resolved_rules_path = resolve_rules_path(rules_path)
    rules = BusinessRules.load(resolved_rules_path)
    database = Database(url)
    repository = SqliteRepository(database)
    # Several processes may open the same file at once; migrate and seed one at a time.
    with repository.command_lock():
        database.create_all()
        if auto_seed and not repository.has_data():
            seed_database(database)

    use_demo = _is_true(os.getenv("DEMO_MODE", "true")) if demo_mode is None else demo_mode
    provider: MockModelProvider | OpenAIModelProvider
    if use_demo:
        provider, model_name = MockModelProvider(), "Mock provider"
    else:
        live = OpenAIModelProvider()
        provider, model_name = live, live.model
    deps = WorkflowDependencies(
        erp=repository,
        audit=repository,
        matcher=ThreeWayMatcher(rules),
        policy=DecisionPolicy(rules),
        investigator=Investigator(provider),
        resolver=Resolver(provider),
        critic=Critic(provider),
        rules=rules,
    )

    connection: sqlite3.Connection | None = None
    if checkpointer is None:
        # Database() already rejected anything but a sqlite:/// file path.
        connection = sqlite3.connect(url.removeprefix("sqlite:///"), check_same_thread=False)
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        serializer = JsonPlusSerializer(
            allowed_msgpack_modules=[
                CaseOutcome,
                CritiqueResult,
                DecisionSource,
                DecisionType,
                Discrepancy,
                DiscrepancyType,
                ERPContext,
                EvidenceItem,
                GoodsReceipt,
                GoodsReceiptLine,
                HumanReview,
                InvestigationResult,
                Invoice,
                InvoiceLine,
                InvoiceSummary,
                MatchResult,
                PolicyCheck,
                PolicyResult,
                PurchaseOrder,
                PurchaseOrderLine,
                Resolution,
                Severity,
                Supplier,
            ]
        )
        checkpointer = SqliteSaver(connection, serde=serializer)
    graph = build_graph(deps, checkpointer)
    return Runtime(
        repository,
        graph,
        rules,
        use_demo,
        connection,
        rules_path=resolved_rules_path,
        model_name=model_name,
    )


class CaseService:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime

    @staticmethod
    def parse_invoice(payload: str | bytes | dict) -> Invoice:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            return Invoice.model_validate_json(payload)
        return Invoice.model_validate(payload)

    @staticmethod
    def demo_invoices() -> dict[str, Invoice]:
        return demo_invoices()

    def _result(self, case: dict, is_replay: bool = False) -> dict:
        snapshot = self.runtime.graph.get_state({"configurable": {"thread_id": case["case_id"]}})
        state = snapshot.values
        if not state:
            schemas = get_type_hints(P2PState)
            state = {
                key: TypeAdapter(schemas[key]).validate_python(value)
                for key, value in case["state"].items()
                if key in schemas
            }
        return {
            "case_id": case["case_id"],
            "status": case["status"],
            "state": state,
            "is_replay": is_replay,
        }

    def _stored_case(self, case_id: str) -> dict:
        """A case this command already knows exists; a missing row is a storage fault."""
        case = self.runtime.repository.get_case(case_id)
        if case is None:
            raise LookupError(f"No persisted case exists for {case_id}")
        return case

    def _reconcile(self, case_id: str) -> dict:
        repository = self.runtime.repository
        case = self._stored_case(case_id)
        snapshot = self.runtime.graph.get_state({"configurable": {"thread_id": case_id}})
        if not snapshot.values:
            if case["status"] == "PENDING_REVIEW":
                raise ValueError(
                    "Review checkpoint is missing; restore the checkpoint database before reviewing"
                )
            return case
        state = self._clean_state(snapshot.values)
        if (
            state.get("case_id") != case_id
            or state["invoice"].model_dump(mode="json") != case["invoice"]
        ):
            raise ValueError("Checkpoint invoice identity does not match the persisted case")
        submitted = repository.submitted_review(case_id)
        if (
            submitted
            and state.get("human_review")
            and state["human_review"].model_dump(mode="json") != submitted
        ):
            raise ValueError("Checkpoint review conflicts with the durable submitted decision")
        if state.get("outcome") and not snapshot.next:
            CaseOutcome.model_validate(state["outcome"])
            status = "COMPLETED"
        elif any(task.interrupts for task in snapshot.tasks):
            status = "PENDING_REVIEW"
        else:
            status = "PROCESSING"
        repository.save_case(case_id, state["invoice"], status, state)
        return self._stored_case(case_id)

    def _run(self, case_id: str, action: str, graph_input: Any, config: dict) -> dict:
        """Run the graph for one command; a failure is stored on the case, then re-raised."""
        started = time.monotonic()
        logger.info("%s started for %s", action, case_id)
        try:
            self.runtime.graph.invoke(graph_input, config=config)
            case = self._reconcile(case_id)
        except Exception as exc:
            logger.exception("%s failed for %s", action, case_id)
            try:
                self.runtime.repository.record_error(case_id, f"{type(exc).__name__}: {exc}")
            except Exception:
                logger.exception("Could not record the failure for %s", case_id)
            raise
        logger.info(
            "%s finished for %s: %s in %d ms",
            action,
            case_id,
            case["status"],
            (time.monotonic() - started) * 1000,
        )
        return self._result(case)

    def process_invoice(
        self,
        invoice: Invoice,
        case_id: str | None = None,
        supersede: SupersedeRequest | None = None,
    ) -> dict:
        """Process an invoice; with `supersede`, it first replaces that held case.

        The link is made in the same transaction as the claim, before the workflow loads
        reservations, so the correction is checked without the held case's quantities.
        """
        invoice = Invoice.model_validate(invoice.model_dump())
        repository = self.runtime.repository
        with repository.command_lock():
            case_id, created = repository.claim_case(
                case_id or f"CASE-{uuid4().hex[:10].upper()}", invoice, supersede
            )
            config = {"configurable": {"thread_id": case_id}}
            # A replay returns a case that was already settled before this call; a case that
            # was still PROCESSING and is recovered here is not a replay.
            settled_before = self._stored_case(case_id)["status"] != "PROCESSING"
            case = self._reconcile(case_id)
            if case["status"] != "PROCESSING":
                return self._result(case, is_replay=not created and settled_before)
            snapshot = self.runtime.graph.get_state(config)
            graph_input = {"case_id": case_id, "invoice": invoice} if not snapshot.values else None
            return self._run(case_id, "Processing", graph_input, config)

    def review_case(
        self, case_id: str, review: HumanReview, checkpoint_id: str | None = None
    ) -> dict:
        review = HumanReview.model_validate(review.model_dump())
        repository = self.runtime.repository
        with repository.command_lock():
            if not repository.get_case(case_id):
                raise LookupError(f"No persisted case exists for {case_id}")
            case = self._reconcile(case_id)
            if case["status"] != "PENDING_REVIEW":
                raise ValueError(
                    "Only pending-review cases can be resumed; any committed outcome has been recovered"
                )
            config = {"configurable": {"thread_id": case_id}}
            snapshot = self.runtime.graph.get_state(config)
            revision = snapshot.config["configurable"]["checkpoint_id"]
            if checkpoint_id is not None and checkpoint_id != revision:
                raise ValueError("Stale review checkpoint; reload the case before reviewing")
            if not any(task.interrupts for task in snapshot.tasks) or snapshot.next != (
                "human_review",
            ):
                raise ValueError("No matching active human-review interrupt")
            submitted = repository.bind_review(case_id, review.model_dump(mode="json"), revision)
            return self._run(case_id, "Review", Command(resume=submitted), config)

    def held_cases_for(self, invoice: Invoice) -> list[dict]:
        """Held cases this invoice may supersede: same supplier and PO, Request info, unlinked."""
        if not invoice.po_number:
            return []
        return self.runtime.repository.held_cases(invoice.supplier_id, invoice.po_number)

    def get_case(self, case_id: str) -> dict | None:
        """The reconciled case, or the stored row marked `busy` while a command holds the lock."""
        repository = self.runtime.repository
        with repository.try_command_lock() as acquired:
            case = repository.get_case(case_id)
            if not case:
                return case
            if acquired:
                case = self._reconcile(case_id)
                snapshot = self.runtime.graph.get_state({"configurable": {"thread_id": case_id}})
                case["checkpoint_id"] = (
                    snapshot.config["configurable"].get("checkpoint_id")
                    if snapshot.config
                    else None
                )
            else:
                case["checkpoint_id"] = None
            case["busy"] = not acquired
            case["submitted_review"] = repository.submitted_review(case_id)
            case["audit"] = [
                event.model_dump(mode="json") for event in repository.for_case(case_id)
            ]
            return case

    def refresh_open_cases(self) -> None:
        """Reconcile every case that is not COMPLETED; a broken case is recorded, not raised.

        Skipped while a command runs: the command reconciles its own case, and the page must
        not wait for it.
        """
        repository = self.runtime.repository
        with repository.try_command_lock() as acquired:
            if not acquired:
                return
            for case_id in repository.open_case_ids():
                try:
                    self._reconcile(case_id)
                except ValueError as exc:
                    repository.record_error(case_id, f"{type(exc).__name__}: {exc}")

    def list_cases(self, status: str | None = None) -> list[dict]:
        repository = self.runtime.repository
        with repository.command_lock():
            for case_id in repository.open_case_ids():
                self._reconcile(case_id)
            return repository.list_cases(status)

    def query_cases(self, **criteria: Any) -> tuple[list[dict], int]:
        """Stored rows; while a command holds the lock, PROCESSING rows are marked `busy`."""
        repository = self.runtime.repository
        with repository.try_command_lock() as acquired:
            rows, total = repository.query_cases(**criteria)
        if not acquired:
            for row in rows:
                row["busy"] = row["status"] == "PROCESSING"
        return rows, total

    def view_counts(self) -> dict[str, int]:
        return self.runtime.repository.view_counts()

    def pending_review_count(self) -> int:
        return self.view_counts()["review"]

    def summary(self, now: datetime | None = None) -> dict[str, Any]:
        """Overview figures computed from the projected case columns."""
        now = now or datetime.now(UTC)
        rows, total = self.runtime.repository.query_cases(limit=None)
        for row in rows:
            for key in ("created_at", "review_requested_at", "completed_at"):
                row[key] = datetime.fromisoformat(row[key]) if row[key] else None
        pending = [row for row in rows if row["status"] == "PENDING_REVIEW"]
        completed = [row for row in rows if row["status"] == "COMPLETED"]

        def waited(row: dict) -> float:
            since = row["review_requested_at"] or row["created_at"] or now
            return max(0.0, (now - since).total_seconds())

        waits = [waited(row) for row in pending]
        day_ago = now - timedelta(days=1)
        if any(row["created_at"] and row["created_at"] <= day_ago for row in rows):
            pending_day_ago = sum(
                1
                for row in rows
                if row["review_requested_at"]
                and row["review_requested_at"] <= day_ago
                and (row["completed_at"] is None or row["completed_at"] > day_ago)
            )
            trend_vs_yesterday: int | None = len(pending) - pending_day_ago
        else:
            trend_vs_yesterday = None

        def autonomous(selection: list[dict]) -> int:
            return sum(
                1
                for row in selection
                if row["status"] == "COMPLETED" and row["outcome_source"] == "AUTONOMOUS"
            )

        def created_between(start: datetime, end: datetime) -> list[dict]:
            return [row for row in rows if row["created_at"] and start < row["created_at"] <= end]

        this_week = created_between(now - timedelta(days=7), now)
        last_week = created_between(now - timedelta(days=14), now - timedelta(days=7))
        trend_points = (
            (autonomous(this_week) / len(this_week) - autonomous(last_week) / len(last_week)) * 100
            if this_week and last_week
            else None
        )
        ai_reviewed = [
            row for row in completed if row["ai_action"] and row["outcome_source"] in HUMAN_SOURCES
        ]
        overridden = sum(1 for row in ai_reviewed if row["outcome_source"] == "HUMAN_OVERRIDDEN")
        outcome_rows = {
            "review": pending,
            "approved": [row for row in completed if row["outcome_action"] == "APPROVE"],
            "rejected": [row for row in completed if row["outcome_action"] == "REJECT"],
            "request_info": [
                row
                for row in completed
                if row["outcome_action"] == "REQUEST_INFO" and not row["superseded_by"]
            ],
            "superseded": [row for row in rows if row["superseded_by"]],
        }
        longest = sorted(pending, key=waited, reverse=True)[:12]
        counts = self.view_counts()
        counts["processing"] = total - len(pending) - len(completed)
        return {
            "total": total,
            "counts": counts,
            "awaiting": {
                "count": len(pending),
                "oldest_wait_seconds": max(waits) if waits else None,
                "median_wait_seconds": statistics.median(waits) if waits else None,
                "value_by_currency": _value_by_currency(pending),
                "trend_vs_yesterday": trend_vs_yesterday,
            },
            "auto_resolution": {
                "autonomous": autonomous(rows),
                "total": total,
                "rate": autonomous(rows) / total if total else None,
                "trend_points": trend_points,
            },
            "override": {
                "overridden": overridden,
                "ai_reviewed": len(ai_reviewed),
                "rate": overridden / len(ai_reviewed) if ai_reviewed else None,
            },
            "outcomes": [
                {
                    "key": key,
                    "count": len(selection),
                    "value_by_currency": _value_by_currency(selection),
                }
                for key, selection in outcome_rows.items()
            ],
            "waiting": [
                {
                    "case_id": row["case_id"],
                    "invoice_id": row["invoice_id"],
                    "max_severity": row["max_severity"],
                    "wait_seconds": waited(row),
                }
                for row in longest
            ],
            "sla_hours": self.runtime.rules.review.sla_hours,
        }

    @staticmethod
    def _clean_state(state: dict) -> dict:
        return {key: value for key, value in state.items() if key != "__interrupt__"}
