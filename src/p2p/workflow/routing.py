from __future__ import annotations

from p2p.workflow.state import P2PState


def route_after_match(state: P2PState) -> str:
    return "policy_check" if state["match_result"].matched else "investigate"


def route_after_critique(state: P2PState) -> str:
    if state["critique"].valid or state.get("reflection_exhausted", False):
        return "policy_check"
    return "resolve"


def route_after_policy(state: P2PState) -> str:
    return "human_review" if state["policy_result"].requires_human_review else "finalize"
