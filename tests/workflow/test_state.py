"""A step that runs without a value an earlier node must write fails loudly, naming both."""

import pytest

from p2p.domain.matching import ThreeWayMatcher
from p2p.domain.models import ERPContext
from p2p.domain.policy import DecisionPolicy
from p2p.infrastructure.database import demo_invoices
from p2p.intelligence.providers import MockModelProvider
from p2p.intelligence.services import Critic, Investigator, Resolver
from p2p.workflow.nodes import WorkflowDependencies, investigate_node
from p2p.workflow.routing import route_after_policy
from p2p.workflow.state import MissingStateError


class CountingModel(MockModelProvider):
    def __init__(self):
        self.calls = 0

    def structured_response(self, schema, messages):
        self.calls += 1
        return super().structured_response(schema, messages)


def test_investigate_without_a_match_result_fails_instead_of_escalating(app):
    """Before, the read sat inside the model-failure handler and became an ESCALATE fallback."""
    rules, model = app.runtime.rules, CountingModel()
    deps = WorkflowDependencies(
        erp=app.runtime.repository,
        audit=app.runtime.repository,
        matcher=ThreeWayMatcher(rules),
        policy=DecisionPolicy(rules),
        investigator=Investigator(model),
        resolver=Resolver(model),
        critic=Critic(model),
        rules=rules,
    )
    state = {
        "case_id": "CASE-STATE",
        "invoice": demo_invoices()["03_large_price_variance"],
        "erp_context": ERPContext(),
    }

    with pytest.raises(MissingStateError, match="investigate ran without 'match_result'"):
        investigate_node(state, deps)

    assert model.calls == 0
    assert app.runtime.repository.for_case("CASE-STATE") == []


def test_a_router_without_its_input_names_itself_and_the_key():
    state = {"case_id": "CASE-STATE", "invoice": demo_invoices()["01_exact_match"]}

    with pytest.raises(KeyError) as error:
        route_after_policy(state)

    assert isinstance(error.value, MissingStateError)
    assert (error.value.step, error.value.key) == ("route_after_policy", "policy_result")
