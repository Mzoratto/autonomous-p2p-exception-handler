from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_type_hints
from uuid import uuid4

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver
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
    PolicyResult,
    PurchaseOrder,
    PurchaseOrderLine,
    Resolution,
    Severity,
    Supplier,
)
from p2p.domain.policy import DecisionPolicy
from p2p.infrastructure.database import Database, SqliteRepository, seed_database
from p2p.intelligence.providers import MockModelProvider, OpenAIModelProvider
from p2p.intelligence.services import Critic, Investigator, Resolver
from p2p.workflow.graph import build_graph
from p2p.workflow.nodes import WorkflowDependencies
from p2p.workflow.state import P2PState


def _is_true(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").lower() in {"1", "true", "yes", "on"}


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
    rules = BusinessRules.load(resolve_rules_path(rules_path))
    database = Database(url)
    database.create_all()
    repository = SqliteRepository(database)
    with repository.command_lock():
        if auto_seed and not repository.has_data():
            seed_database(database)

    use_demo = _is_true(os.getenv("DEMO_MODE", "true")) if demo_mode is None else demo_mode
    provider = MockModelProvider() if use_demo else OpenAIModelProvider()
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
        if url.startswith("sqlite:///"):
            checkpoint_path = url.removeprefix("sqlite:///")
            connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
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
                    PolicyResult,
                    PurchaseOrder,
                    PurchaseOrderLine,
                    Resolution,
                    Severity,
                    Supplier,
                ]
            )
            checkpointer = SqliteSaver(connection, serde=serializer)
        else:
            checkpointer = InMemorySaver()
    graph = build_graph(deps, checkpointer)
    return Runtime(repository, graph, rules, use_demo, connection)


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

    def _result(self, case: dict) -> dict:
        snapshot = self.runtime.graph.get_state({"configurable": {"thread_id": case["case_id"]}})
        state = snapshot.values
        if not state:
            schemas = get_type_hints(P2PState)
            state = {
                key: TypeAdapter(schemas[key]).validate_python(value)
                for key, value in case["state"].items()
                if key in schemas
            }
        return {"case_id": case["case_id"], "status": case["status"], "state": state}

    def _reconcile(self, case_id: str) -> dict:
        repository = self.runtime.repository
        case = repository.get_case(case_id)
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
        return repository.get_case(case_id)

    def process_invoice(self, invoice: Invoice, case_id: str | None = None) -> dict:
        invoice = Invoice.model_validate(invoice.model_dump())
        repository = self.runtime.repository
        with repository.command_lock():
            case_id, _ = repository.claim_case(
                case_id or f"CASE-{uuid4().hex[:10].upper()}", invoice
            )
            config = {"configurable": {"thread_id": case_id}}
            case = self._reconcile(case_id)
            if case["status"] != "PROCESSING":
                return self._result(case)
            snapshot = self.runtime.graph.get_state(config)
            self.runtime.graph.invoke(
                {"case_id": case_id, "invoice": invoice} if not snapshot.values else None,
                config=config,
            )
            return self._result(self._reconcile(case_id))

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
            self.runtime.graph.invoke(Command(resume=submitted), config=config)
            return self._result(self._reconcile(case_id))

    def get_case(self, case_id: str) -> dict | None:
        with self.runtime.repository.command_lock():
            case = self.runtime.repository.get_case(case_id)
            if case:
                case = self._reconcile(case_id)
                snapshot = self.runtime.graph.get_state({"configurable": {"thread_id": case_id}})
                case["checkpoint_id"] = (
                    snapshot.config["configurable"].get("checkpoint_id")
                    if snapshot.config
                    else None
                )
                case["submitted_review"] = self.runtime.repository.submitted_review(case_id)
                case["audit"] = [
                    event.model_dump(mode="json")
                    for event in self.runtime.repository.for_case(case_id)
                ]
            return case

    def list_cases(self, status: str | None = None) -> list[dict]:
        with self.runtime.repository.command_lock():
            cases = [
                self._reconcile(item["case_id"]) for item in self.runtime.repository.list_cases()
            ]
            return [case for case in cases if status is None or case["status"] == status]

    def metrics(self) -> dict[str, Any]:
        cases = self.list_cases()
        completed = [item for item in cases if item["status"] == "COMPLETED"]
        outcomes = [item["state"].get("outcome", {}) for item in completed]
        autonomous = [item for item in outcomes if item.get("source") == "AUTONOMOUS"]
        confidences = [
            float(item["state"]["resolution"]["confidence"])
            for item in cases
            if item["state"].get("resolution")
        ]
        actions: dict[str, int] = {}
        exception_types: dict[str, int] = {}
        for outcome in outcomes:
            action = outcome.get("action", "UNKNOWN")
            actions[action] = actions.get(action, 0) + 1
        for item in cases:
            for discrepancy in item["state"].get("match_result", {}).get("discrepancies", []):
                name = discrepancy["type"]
                exception_types[name] = exception_types.get(name, 0) + 1
        return {
            "total": len(cases),
            "pending": len(cases) - len(completed),
            "autonomous_rate": len(autonomous) / len(completed) if completed else 0,
            "average_confidence": sum(confidences) / len(confidences) if confidences else 0,
            "actions": actions,
            "exception_types": exception_types,
        }

    @staticmethod
    def _clean_state(state: dict) -> dict:
        return {key: value for key, value in state.items() if key != "__interrupt__"}
