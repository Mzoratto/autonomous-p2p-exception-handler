from __future__ import annotations

from p2p.domain.models import DiscrepancyType
from p2p.workflow.state import P2PState, required


def route_after_match(state: P2PState) -> str:
    """Matched invoices and duplicates go straight to policy; duplicates are always rejected."""
    result = required(state, "match_result", "route_after_match")
    if result.matched or any(
        item.type is DiscrepancyType.DUPLICATE_INVOICE for item in result.discrepancies
    ):
        return "policy_check"
    return "investigate"


def route_after_investigation(state: P2PState) -> str:
    """An unreachable model is not asked again; policy sends the case to a reviewer."""
    return "policy_check" if state.get("model_unavailable") else "resolve"


def route_after_resolution(state: P2PState) -> str:
    return "policy_check" if state.get("model_unavailable") else "critique"


def route_after_critique(state: P2PState) -> str:
    if (
        state.get("model_unavailable")
        or required(state, "critique", "route_after_critique").valid
        or state.get("reflection_exhausted", False)
    ):
        return "policy_check"
    return "resolve"


def route_after_policy(state: P2PState) -> str:
    policy = required(state, "policy_result", "route_after_policy")
    return "human_review" if policy.requires_human_review else "finalize"
