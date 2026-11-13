"""Shared test builders: an invoice's ERP context, and a service's workflow around given parts."""

from langgraph.checkpoint.memory import InMemorySaver

from p2p.domain.matching import ThreeWayMatcher
from p2p.domain.models import ERPContext, InvestigationResult
from p2p.domain.policy import DecisionPolicy
from p2p.intelligence.providers import MockModelProvider
from p2p.intelligence.services import Critic, Investigator, Resolver
from p2p.workflow.graph import build_graph
from p2p.workflow.nodes import WorkflowDependencies


class FailingModel(MockModelProvider):
    """Counts model calls and fails every call for one schema (the Investigator's by default)."""

    def __init__(self, error: Exception, schema: type = InvestigationResult):
        self.error = error
        self.schema = schema
        self.calls = 0

    def structured_response(self, schema, messages):
        self.calls += 1
        if schema is self.schema:
            raise self.error
        return super().structured_response(schema, messages)


class BrokenMatcher:
    """A matcher that raises, standing in for any failure inside a workflow run."""

    def __init__(self, error: Exception):
        self.error = error

    def match(self, *args, **kwargs):
        raise self.error


def erp_context(app, invoice) -> ERPContext:
    """The ERP records the workflow loads for an invoice, read from the app's repository."""
    repo = app.runtime.repository
    return ERPContext(
        purchase_order=repo.get_purchase_order(invoice.po_number),
        goods_receipts=repo.get_goods_receipts(invoice.po_number),
        supplier=repo.get_supplier(invoice.supplier_id),
        previous_invoices=repo.get_previous_invoices(invoice.supplier_id),
    )


def workflow_dependencies(app, provider=None, matcher=None) -> WorkflowDependencies:
    """The app's workflow parts around the given model provider and matcher."""
    rules = app.runtime.rules
    provider = provider or MockModelProvider()
    return WorkflowDependencies(
        erp=app.runtime.repository,
        audit=app.runtime.repository,
        matcher=matcher or ThreeWayMatcher(rules),
        policy=DecisionPolicy(rules),
        investigator=Investigator(provider),
        resolver=Resolver(provider),
        critic=Critic(provider),
        rules=rules,
    )


def use_workflow(app, provider=None, matcher=None, checkpointer=None) -> None:
    """Rebuild the app's graph with the given provider and matcher."""
    deps = workflow_dependencies(app, provider, matcher)
    app.runtime.graph = build_graph(deps, checkpointer or InMemorySaver())
