"""A step that runs without a value an earlier node must write fails loudly, naming both."""

import pytest

from p2p.domain.matching import ThreeWayMatcher
from p2p.domain.models import (
    DecisionType,
    ERPContext,
    InvestigationResult,
    MatchResult,
    Resolution,
)
from p2p.domain.policy import DecisionPolicy
from p2p.infrastructure.database import demo_invoices
from p2p.intelligence.providers import MockModelProvider
from p2p.intelligence.services import Critic, Investigator, Resolver
from p2p.workflow.nodes import WorkflowDependencies, critique_node, investigate_node, resolve_node
from p2p.workflow.routing import route_after_policy
from p2p.workflow.state import MissingStateError


class CountingModel(MockModelProvider):
    def __init__(self):
        self.calls = 0

    def structured_response(self, schema, messages):
        self.calls += 1
        return super().structured_response(schema, messages)


def counting_dependencies(app, model: CountingModel) -> WorkflowDependencies:
    rules = app.runtime.rules
    return WorkflowDependencies(
        erp=app.runtime.repository,
        audit=app.runtime.repository,
        matcher=ThreeWayMatcher(rules),
        policy=DecisionPolicy(rules),
        investigator=Investigator(model),
        resolver=Resolver(model),
        critic=Critic(model),
        rules=rules,
    )


def state_without(key: str) -> dict:
    """Everything investigate, resolve and critique read, except `key`."""
    state = {
        "case_id": "CASE-STATE",
        "invoice": demo_invoices()["03_large_price_variance"],
        "erp_context": ERPContext(),
        "match_result": MatchResult(matched=False, summary="Price variance."),
        "investigation": InvestigationResult(
            root_cause="Unit price above the PO price.",
            evidence=[],
            suggested_action=DecisionType.ESCALATE,
            confidence=0.5,
        ),
        "resolution": Resolution(
            action=DecisionType.ESCALATE, rationale="Price above the PO.", confidence=0.5
        ),
    }
    del state[key]
    return state


# Before #21 these reads sat inside the model-failure handler, so a missing value became an
# ESCALATE resolution or a failed critique that looked like a model failure.


@pytest.mark.parametrize("key", ["erp_context", "match_result"])
def test_investigate_without_its_input_fails_instead_of_escalating(app, key):
    model = CountingModel()

    with pytest.raises(MissingStateError, match=f"^investigate ran without '{key}'"):
        investigate_node(state_without(key), counting_dependencies(app, model))

    assert model.calls == 0
    assert app.runtime.repository.for_case("CASE-STATE") == []


@pytest.mark.parametrize("key", ["match_result", "investigation"])
def test_resolve_without_its_input_fails_instead_of_escalating(app, key):
    model = CountingModel()

    with pytest.raises(MissingStateError, match=f"^resolve ran without '{key}'"):
        resolve_node(state_without(key), counting_dependencies(app, model))

    assert model.calls == 0
    assert app.runtime.repository.for_case("CASE-STATE") == []


@pytest.mark.parametrize("key", ["match_result", "investigation", "resolution"])
def test_critique_without_its_input_fails_instead_of_failing_the_critique(app, key):
    model = CountingModel()

    with pytest.raises(MissingStateError, match=f"^critique ran without '{key}'"):
        critique_node(state_without(key), counting_dependencies(app, model))

    assert model.calls == 0
    assert app.runtime.repository.for_case("CASE-STATE") == []


def test_a_router_without_its_input_names_itself_and_the_key():
    state = {"case_id": "CASE-STATE", "invoice": demo_invoices()["01_exact_match"]}

    with pytest.raises(KeyError) as error:
        route_after_policy(state)

    assert isinstance(error.value, MissingStateError)
    assert (error.value.step, error.value.key) == ("route_after_policy", "policy_result")
