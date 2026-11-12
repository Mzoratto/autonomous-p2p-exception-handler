"""Helpers for tests that rebuild a service's workflow around failing parts."""

from langgraph.checkpoint.memory import InMemorySaver

from p2p.domain.matching import ThreeWayMatcher
from p2p.domain.models import InvestigationResult
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


def use_workflow(app, provider=None, matcher=None, checkpointer=None) -> None:
    """Rebuild the app's graph with the given provider and matcher."""
    rules = app.runtime.rules
    provider = provider or MockModelProvider()
    deps = WorkflowDependencies(
        erp=app.runtime.repository,
        audit=app.runtime.repository,
        matcher=matcher or ThreeWayMatcher(rules),
        policy=DecisionPolicy(rules),
        investigator=Investigator(provider),
        resolver=Resolver(provider),
        critic=Critic(provider),
        rules=rules,
    )
    app.runtime.graph = build_graph(deps, checkpointer or InMemorySaver())
