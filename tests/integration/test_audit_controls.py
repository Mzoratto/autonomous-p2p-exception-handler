"""Regression coverage for the September 2026 audit's financial and recovery probes."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError
from sqlalchemy import delete

from p2p.application.service import CaseService, create_runtime
from p2p.domain.config import BusinessRules
from p2p.domain.matching import ThreeWayMatcher
from p2p.domain.models import (
    CritiqueResult,
    DecisionType,
    Discrepancy,
    DiscrepancyType,
    ERPContext,
    HumanReview,
    InvestigationResult,
    Invoice,
    MatchResult,
    Resolution,
    Severity,
)
from p2p.domain.policy import DecisionPolicy
from p2p.infrastructure.database import PurchaseOrderRow, demo_invoices, seed_database
from p2p.intelligence.providers import MockModelProvider
from p2p.intelligence.services import Critic, Investigator, Resolver
from p2p.workflow.graph import build_graph
from p2p.workflow.nodes import WorkflowDependencies


def context(app, invoice):
    repo = app.runtime.repository
    return ERPContext(
        purchase_order=repo.get_purchase_order(invoice.po_number),
        goods_receipts=repo.get_goods_receipts(invoice.po_number),
        supplier=repo.get_supplier(invoice.supplier_id),
    )


@pytest.mark.parametrize("kind", list(DiscrepancyType))
def test_policy_exhaustive_material_gate(rules, kind):
    match = MatchResult(
        matched=False,
        summary="unsafe",
        discrepancies=[
            Discrepancy(type=kind, severity=Severity.CRITICAL, explanation="audit probe")
        ],
    )
    result = DecisionPolicy(rules).evaluate(
        demo_invoices()["01_exact_match"],
        match,
        Resolution(action=DecisionType.APPROVE, rationale="Hostile approval", confidence=1),
    )
    assert result.requires_human_review or result.enforced_action == DecisionType.REJECT


@pytest.mark.parametrize(
    "change",
    [
        "other_receipt_line",
        "missing_supplier",
        "inactive_supplier",
        "ordered_quantity",
        "closed",
        "cancelled",
    ],
)
def test_missing_and_ineligible_evidence(app, change):
    invoice = demo_invoices()["01_exact_match"]
    ctx = context(app, invoice)
    if change == "other_receipt_line":
        ctx.goods_receipts[0].line_items[0].po_line_id = "OTHER"
    elif change == "missing_supplier":
        ctx.supplier = None
    elif change == "inactive_supplier":
        ctx.supplier.active = False
    elif change == "ordered_quantity":
        ctx.purchase_order.line_items[0].ordered_quantity = Decimal("1")
    else:
        ctx.purchase_order.status = change.upper()
    result = ThreeWayMatcher(app.runtime.rules).match(invoice, ctx)
    assert not result.matched
    assert DecisionPolicy(app.runtime.rules).evaluate(invoice, result, None).requires_human_review


def test_duplicate_invoice_and_po_lines_rejected(app):
    invoice = demo_invoices()["01_exact_match"]
    payload = invoice.model_dump()
    payload["line_items"] *= 2
    payload.update(subtotal=Decimal("2000"), total=Decimal("2000"))
    with pytest.raises(ValidationError, match="unique"):
        Invoice.model_validate(payload)
    po = context(app, invoice).purchase_order
    payload = po.model_dump()
    payload["line_items"] *= 2
    with pytest.raises(ValidationError, match="unique"):
        type(po).model_validate(payload)


@pytest.mark.parametrize("price,expected", [("25.2499", True), ("25.25", True), ("25.2501", False)])
def test_exact_price_threshold(app, price, expected):
    invoice = demo_invoices()["04_quantity_variance"]
    invoice.line_items[0].quantity = Decimal("100")
    ctx = context(app, invoice)
    invoice.line_items[0].unit_price = Decimal(price)
    invoice.line_items[0].line_total = (invoice.line_items[0].quantity * Decimal(price)).quantize(
        Decimal(".01")
    )
    invoice.subtotal = invoice.total = invoice.line_items[0].line_total
    assert ThreeWayMatcher(app.runtime.rules).match(invoice, ctx).matched is expected


@pytest.mark.parametrize(
    "quantity,expected", [("10.0999", True), ("10.1", True), ("10.1001", False)]
)
def test_exact_quantity_threshold(app, quantity, expected):
    invoice = demo_invoices()["01_exact_match"]
    ctx = context(app, invoice)
    ctx.purchase_order.line_items[0].ordered_quantity = Decimal("20")
    invoice.line_items[0].quantity = Decimal(quantity)
    invoice.line_items[0].line_total = (Decimal(quantity) * 100).quantize(Decimal(".01"))
    invoice.subtotal = invoice.total = invoice.line_items[0].line_total
    assert ThreeWayMatcher(app.runtime.rules).match(invoice, ctx).matched is expected


def test_subcent_extended_price_is_material(app):
    invoice = demo_invoices()["01_exact_match"]
    ctx = context(app, invoice)
    ctx.purchase_order.line_items[0].unit_price = Decimal("1")
    ctx.purchase_order.line_items[0].ordered_quantity = Decimal("10000")
    ctx.goods_receipts[0].line_items[0].received_quantity = Decimal("10000")
    invoice.line_items[0].quantity = Decimal("10000")
    invoice.line_items[0].unit_price = Decimal("1.004")
    invoice.line_items[0].line_total = invoice.subtotal = invoice.total = Decimal("10040")
    assert not ThreeWayMatcher(app.runtime.rules).match(invoice, ctx).matched


@pytest.mark.parametrize(
    "fault",
    ["hostile", "investigator_timeout", "fake_evidence", "malformed", "negative_confidence"],
)
def test_hostile_typed_provider_never_authorizes_unmatched(app, fault):
    class Hostile(MockModelProvider):
        def structured_response(self, schema, messages):
            if schema is InvestigationResult:
                if fault == "investigator_timeout":
                    raise TimeoutError()
                if fault == "malformed":
                    return {"bad": True}
                result = super().structured_response(schema, messages)
                if fault == "fake_evidence":
                    result.evidence[0].evidence_id = "FAKE"
                return result
            if schema is Resolution:
                import json

                facts = json.loads(messages[-1]["content"])["match_result"]["discrepancies"]
                return Resolution(
                    action=DecisionType.APPROVE,
                    rationale="Invented amendment",
                    confidence=1,
                    evidence_refs=[item["discrepancy_id"] for item in facts],
                )
            return CritiqueResult(
                valid=True, confidence_adjustment=-1 if fault == "negative_confidence" else 0
            )

    provider = Hostile()
    rules = app.runtime.rules
    repo = app.runtime.repository
    app.runtime.graph = build_graph(
        WorkflowDependencies(
            repo,
            repo,
            ThreeWayMatcher(rules),
            DecisionPolicy(rules),
            Investigator(provider),
            Resolver(provider),
            Critic(provider),
            rules,
        ),
        InMemorySaver(),
    )
    invoice = demo_invoices()["01_exact_match"]
    invoice.line_items[0].line_id = "UNKNOWN"
    result = app.process_invoice(invoice)
    assert result["status"] == "PENDING_REVIEW"
    if fault in {"investigator_timeout", "fake_evidence", "malformed"}:
        assert "model_error" in result["state"]["policy_result"].triggered_rules
    if fault == "negative_confidence":
        assert result["state"]["resolution"].confidence == 0


def test_normalized_replay_conflicting_identity_and_capacity(app):
    invoice = demo_invoices()["01_exact_match"]
    first = app.process_invoice(invoice, "SAME")
    assert app.process_invoice(invoice)["case_id"] == first["case_id"]
    normalized = Invoice.model_validate({**invoice.model_dump(), "invoice_id": " inv-exact "})
    assert app.process_invoice(normalized)["case_id"] == "SAME"
    other = invoice.model_copy(update={"invoice_id": "SECOND"})
    with pytest.raises(ValueError, match="bound|identity"):
        app.process_invoice(other, "SAME")
    assert app.process_invoice(other)["status"] == "PENDING_REVIEW"
    assert len(app.list_cases()) == 2


@pytest.mark.parametrize(
    "action,released",
    [
        (DecisionType.REJECT, True),
        (DecisionType.REQUEST_INFO, False),
        (DecisionType.APPROVE, False),
    ],
)
def test_pending_and_terminal_reservations(app, action, released):
    pending = app.process_invoice(demo_invoices()["03_large_price_variance"])
    invoice = demo_invoices()["03_large_price_variance"]
    repo = app.runtime.repository
    assert repo.reserved_quantities(invoice.po_number, "other") == {
        "1": invoice.line_items[0].quantity
    }
    app.review_case(pending["case_id"], HumanReview(action=action))
    assert bool(repo.reserved_quantities(invoice.po_number, "other")) is not released


def test_review_save_failure_recovers_original_decision(app, monkeypatch):
    pending = app.process_invoice(demo_invoices()["03_large_price_variance"])
    repo = app.runtime.repository
    save = repo.save_case

    def fail_completed(case_id, invoice, status, state):
        if status == "COMPLETED":
            raise OSError("injected save failure")
        return save(case_id, invoice, status, state)

    monkeypatch.setattr(repo, "save_case", fail_completed)
    with pytest.raises(OSError):
        app.review_case(pending["case_id"], HumanReview(action=DecisionType.APPROVE))
    assert not any(e.event_type == "CASE_COMPLETED" for e in repo.for_case(pending["case_id"]))
    monkeypatch.setattr(repo, "save_case", save)
    with pytest.raises(ValueError, match="committed outcome"):
        app.review_case(pending["case_id"], HumanReview(action=DecisionType.REJECT))
    case = app.get_case(pending["case_id"])
    assert case["state"]["outcome"]["action"] == "APPROVE"
    assert case["submitted_review"]["action"] == "APPROVE"
    assert sum(e["event_type"] == "CASE_COMPLETED" for e in case["audit"]) == 1


def test_initial_save_failure_retains_case_and_recovers(app, monkeypatch):
    repo = app.runtime.repository
    save = repo.save_case
    monkeypatch.setattr(repo, "save_case", lambda *args: (_ for _ in ()).throw(OSError("save")))
    invoice = demo_invoices()["01_exact_match"]
    with pytest.raises(OSError):
        app.process_invoice(invoice, "RECOVER")
    assert repo.get_case("RECOVER") is not None
    assert not any(e.event_type == "CASE_COMPLETED" for e in repo.for_case("RECOVER"))
    monkeypatch.setattr(repo, "save_case", save)
    assert app.process_invoice(invoice)["status"] == "COMPLETED"


def test_stale_and_missing_checkpoint_refused(app):
    pending = app.process_invoice(demo_invoices()["03_large_price_variance"])
    with pytest.raises(ValueError, match="Stale"):
        app.review_case(
            pending["case_id"], HumanReview(action=DecisionType.APPROVE), checkpoint_id="old"
        )
    app.runtime.graph.checkpointer = InMemorySaver()
    with pytest.raises(ValueError, match="checkpoint is missing"):
        app.review_case(pending["case_id"], HumanReview(action=DecisionType.APPROVE))


def test_recommendation_history_has_full_versions_and_provenance(app):
    pending = app.process_invoice(demo_invoices()["03_large_price_variance"])
    events = app.get_case(pending["case_id"])["audit"]
    proposals = [
        e["data"]
        for e in events
        if e["event_type"] in {"RESOLUTION_PROPOSED", "RESOLUTION_REVISED"}
    ]
    assert [p["action"] for p in proposals] == ["APPROVE", "REQUEST_INFO"]
    for proposal in proposals:
        assert all(
            key in proposal
            for key in (
                "rationale",
                "evidence_refs",
                "information_request",
                "rules_sha256",
                "evidence_sha256",
                "attempt",
            )
        )


def test_startup_and_explicit_seed_preserve_cases(tmp_path):
    url = f"sqlite:///{tmp_path / 'durable.db'}"
    runtime = create_runtime(database_url=url, demo_mode=True)
    service = CaseService(runtime)
    case = service.process_invoice(demo_invoices()["01_exact_match"])
    with runtime.repository.database.session_factory() as session:
        session.execute(delete(PurchaseOrderRow).where(PurchaseOrderRow.po_number == "PO-1001"))
        session.commit()
    runtime.close()
    restarted = create_runtime(database_url=url, demo_mode=True)
    try:
        service = CaseService(restarted)
        assert service.get_case(case["case_id"])["status"] == "COMPLETED"
        seed_database(restarted.repository.database, invoice_dir=tmp_path / "fixtures")
        assert service.get_case(case["case_id"])["audit"]
    finally:
        restarted.close()


def test_concurrent_duplicate_ingestion_and_capacity(tmp_path):
    url = f"sqlite:///{tmp_path / 'concurrent.db'}"
    runtimes = [create_runtime(database_url=url, demo_mode=True) for _ in range(2)]
    services = [CaseService(runtime) for runtime in runtimes]
    invoice = demo_invoices()["01_exact_match"]
    try:
        with ThreadPoolExecutor(2) as executor:
            results = list(executor.map(lambda service: service.process_invoice(invoice), services))
        assert results[0]["case_id"] == results[1]["case_id"]
        assert len(services[0].list_cases()) == 1
        next_invoice = invoice.model_copy(update={"invoice_id": "OTHER"})
        assert services[1].process_invoice(next_invoice)["status"] == "PENDING_REVIEW"
    finally:
        for runtime in runtimes:
            runtime.close()


@pytest.mark.parametrize(
    "section,key",
    [
        ("duplicate_invoice", "auto_reject"),
        ("missing_po", "require_human_review"),
        ("missing_receipt", "require_human_review"),
    ],
)
def test_unsafe_rule_toggles_rejected(rules, section, key):
    payload = rules.model_dump()
    payload[section][key] = False
    with pytest.raises(ValidationError):
        BusinessRules.model_validate(payload)


def test_model_failure_alone_blocks_an_exact_match(rules):
    result = DecisionPolicy(rules).evaluate(
        demo_invoices()["01_exact_match"],
        MatchResult(matched=True, summary="exact"),
        Resolution(action=DecisionType.APPROVE, rationale="approve", confidence=1),
        model_error="timeout",
    )
    assert result.requires_human_review


def test_review_command_survives_failure_before_resume(app, monkeypatch):
    pending = app.process_invoice(demo_invoices()["03_large_price_variance"])
    invoke = app.runtime.graph.invoke
    monkeypatch.setattr(
        app.runtime.graph,
        "invoke",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("before resume")),
    )
    with pytest.raises(OSError):
        app.review_case(pending["case_id"], HumanReview(action=DecisionType.APPROVE))
    monkeypatch.setattr(app.runtime.graph, "invoke", invoke)
    with pytest.raises(ValueError, match="different review"):
        app.review_case(pending["case_id"], HumanReview(action=DecisionType.REJECT))
    assert (
        app.review_case(pending["case_id"], HumanReview(action=DecisionType.APPROVE))["state"][
            "outcome"
        ].action
        == DecisionType.APPROVE
    )


def test_restart_reconciles_failed_completion(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'restart-failure.db'}"
    runtime = create_runtime(database_url=url, demo_mode=True)
    app = CaseService(runtime)
    pending = app.process_invoice(demo_invoices()["03_large_price_variance"])
    save = runtime.repository.save_case

    def failing_save(case_id, invoice, status, state):
        if status == "COMPLETED":
            raise OSError("after checkpoint")
        return save(case_id, invoice, status, state)

    monkeypatch.setattr(runtime.repository, "save_case", failing_save)
    with pytest.raises(OSError):
        app.review_case(pending["case_id"], HumanReview(action=DecisionType.APPROVE))
    runtime.close()
    recovered = create_runtime(database_url=url, demo_mode=True)
    try:
        case = CaseService(recovered).get_case(pending["case_id"])
        assert case["status"] == "COMPLETED"
        assert case["state"]["outcome"]["action"] == "APPROVE"
        assert case["submitted_review"]["action"] == "APPROVE"
        assert sum(event["event_type"] == "CASE_COMPLETED" for event in case["audit"]) == 1
    finally:
        recovered.close()


def test_concurrent_distinct_invoices_cannot_share_receipt(tmp_path):
    url = f"sqlite:///{tmp_path / 'capacity.db'}"
    runtimes = [create_runtime(database_url=url, demo_mode=True) for _ in range(2)]
    invoices = [
        demo_invoices()["01_exact_match"].model_copy(update={"invoice_id": f"UNIQUE-{i}"})
        for i in range(2)
    ]
    try:
        with ThreadPoolExecutor(2) as executor:
            futures = [
                executor.submit(CaseService(runtime).process_invoice, invoice)
                for runtime, invoice in zip(runtimes, invoices, strict=True)
            ]
            results = [future.result() for future in futures]
        assert sorted(result["status"] for result in results) == ["COMPLETED", "PENDING_REVIEW"]
    finally:
        for runtime in runtimes:
            runtime.close()


def test_concurrent_conflicting_reviewers(tmp_path):
    url = f"sqlite:///{tmp_path / 'reviewers.db'}"
    runtimes = [create_runtime(database_url=url, demo_mode=True) for _ in range(2)]
    case_id = CaseService(runtimes[0]).process_invoice(demo_invoices()["03_large_price_variance"])[
        "case_id"
    ]

    def submit(runtime, action):
        try:
            return (
                CaseService(runtime)
                .review_case(case_id, HumanReview(action=action))["state"]["outcome"]
                .action
            )
        except ValueError:
            return "REFUSED"

    try:
        with ThreadPoolExecutor(2) as executor:
            futures = [
                executor.submit(submit, runtime, action)
                for runtime, action in zip(
                    runtimes, [DecisionType.APPROVE, DecisionType.REJECT], strict=True
                )
            ]
            results = [future.result() for future in futures]
        assert results.count("REFUSED") == 1
        committed = next(result for result in results if result != "REFUSED")
        case = CaseService(runtimes[0]).get_case(case_id)
        assert case["state"]["outcome"]["action"] == committed
        assert case["submitted_review"]["action"] == committed
    finally:
        for runtime in runtimes:
            runtime.close()


def test_zero_po_price_cannot_hide_positive_invoice_price(app):
    invoice = demo_invoices()["01_exact_match"]
    ctx = context(app, invoice)
    ctx.purchase_order.line_items[0].unit_price = Decimal("0")
    assert not ThreeWayMatcher(app.runtime.rules).match(invoice, ctx).matched


@pytest.mark.parametrize("price,expected", [("101.9999", True), ("102", True), ("102.0001", False)])
def test_exact_percentage_threshold(app, price, expected):
    invoice = demo_invoices()["01_exact_match"]
    ctx = context(app, invoice)
    invoice.line_items[0].unit_price = Decimal(price)
    invoice.line_items[0].line_total = (Decimal(price) * 10).quantize(Decimal(".01"))
    invoice.total = invoice.subtotal = invoice.line_items[0].line_total
    assert ThreeWayMatcher(app.runtime.rules).match(invoice, ctx).matched is expected


def test_partial_invoice_requires_review_then_remaining_capacity_can_approve(app):
    invoice = demo_invoices()["01_exact_match"]
    invoice.line_items[0].quantity = Decimal("4")
    invoice.line_items[0].line_total = invoice.total = invoice.subtotal = Decimal("400")
    pending = app.process_invoice(invoice)
    assert pending["status"] == "PENDING_REVIEW"
    app.review_case(pending["case_id"], HumanReview(action=DecisionType.APPROVE))
    invoice.invoice_id = "PART-TWO"
    invoice.line_items[0].quantity = Decimal("6")
    invoice.line_items[0].line_total = invoice.total = invoice.subtotal = Decimal("600")
    result = app.process_invoice(invoice)
    assert result["status"] == "COMPLETED"
    assert result["state"]["outcome"].action == DecisionType.APPROVE


def test_resolution_cannot_mix_valid_and_fabricated_references(app):
    from p2p.workflow.nodes import resolve_node

    invoice = demo_invoices()["03_large_price_variance"]
    match = ThreeWayMatcher(app.runtime.rules).match(invoice, context(app, invoice))

    class FakeReferenceProvider(MockModelProvider):
        def structured_response(self, schema, messages):
            return Resolution(
                action=DecisionType.APPROVE,
                rationale="fabricated",
                confidence=1,
                evidence_refs=[match.discrepancies[0].discrepancy_id, "FAKE"],
            )

    provider = FakeReferenceProvider()
    repo = app.runtime.repository
    rules = app.runtime.rules
    deps = WorkflowDependencies(
        repo,
        repo,
        ThreeWayMatcher(rules),
        DecisionPolicy(rules),
        Investigator(provider),
        Resolver(provider),
        Critic(provider),
        rules,
    )
    result = resolve_node(
        {
            "case_id": "REFERENCE",
            "invoice": invoice,
            "match_result": match,
            "investigation": InvestigationResult(
                root_cause="unknown",
                evidence=[],
                suggested_action=DecisionType.ESCALATE,
                confidence=0,
            ),
        },
        deps,
    )
    assert result["model_error"]
    assert result["resolution"].action == DecisionType.ESCALATE


def test_corrupted_input_does_not_poison_case_storage(app):
    invoice = demo_invoices()["01_exact_match"]
    invoice.line_items[0].line_total += 1
    with pytest.raises(ValidationError):
        app.process_invoice(invoice)
    assert app.list_cases() == []


def test_completed_replay_can_use_snapshot_when_checkpoint_is_lost(app):
    invoice = demo_invoices()["01_exact_match"]
    original = app.process_invoice(invoice)
    app.runtime.graph.checkpointer = InMemorySaver()
    replay = app.process_invoice(invoice)
    assert replay["case_id"] == original["case_id"]
    assert replay["state"]["outcome"].action == DecisionType.APPROVE
    assert len(app.list_cases()) == 1
