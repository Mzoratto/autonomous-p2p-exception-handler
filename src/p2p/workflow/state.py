from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Required, TypedDict, overload

from p2p.domain.models import (
    CaseOutcome,
    CritiqueResult,
    ERPContext,
    HumanReview,
    InvestigationResult,
    Invoice,
    MatchResult,
    PolicyResult,
    Resolution,
)


class P2PState(TypedDict, total=False):
    # The graph input. CaseService.process_invoice starts every run with both, and a resumed
    # run restores them from its checkpoint, so every node can rely on them.
    case_id: Required[str]
    invoice: Required[Invoice]
    # Written by one node for later ones; read them with `required()` where the graph
    # guarantees the writer ran first, or with `.get()` where they are truly optional.
    erp_context: ERPContext
    match_result: MatchResult
    investigation: InvestigationResult
    resolution: Resolution
    critique: CritiqueResult
    policy_result: PolicyResult
    human_review: HumanReview
    outcome: CaseOutcome
    reflection_count: int
    reflection_exhausted: bool
    model_error: str | None
    model_unavailable: bool


class MissingStateError(KeyError):
    """A workflow step ran without a value an earlier node should have written."""

    def __init__(self, step: str, key: str):
        super().__init__(f"{step} ran without '{key}' in the workflow state")
        self.step = step
        self.key = key

    def __str__(self) -> str:
        return str(self.args[0])


@overload
def required(state: P2PState, key: Literal["erp_context"], step: str) -> ERPContext: ...
@overload
def required(state: P2PState, key: Literal["match_result"], step: str) -> MatchResult: ...
@overload
def required(state: P2PState, key: Literal["investigation"], step: str) -> InvestigationResult: ...
@overload
def required(state: P2PState, key: Literal["resolution"], step: str) -> Resolution: ...
@overload
def required(state: P2PState, key: Literal["critique"], step: str) -> CritiqueResult: ...
@overload
def required(state: P2PState, key: Literal["policy_result"], step: str) -> PolicyResult: ...
def required(state: P2PState, key: str, step: str) -> object:
    """A value the graph guarantees `step` can read; its absence is a workflow fault.

    Raises MissingStateError (a KeyError) naming the step and the key, so a broken graph
    fails loudly instead of being read as "no value".
    """
    values: Mapping[str, object] = state
    if key not in values:
        raise MissingStateError(step, key)
    return values[key]
