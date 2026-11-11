from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph

from p2p.workflow.nodes import (
    WorkflowDependencies,
    critique_node,
    finalize_node,
    human_review_node,
    ingest_node,
    investigate_node,
    load_context_node,
    match_node,
    policy_check_node,
    resolve_node,
)
from p2p.workflow.routing import (
    route_after_critique,
    route_after_investigation,
    route_after_match,
    route_after_policy,
    route_after_resolution,
)
from p2p.workflow.state import P2PState


def build_graph(deps: WorkflowDependencies, checkpointer: Any):
    graph = StateGraph(P2PState)
    graph.add_node("ingest", partial(ingest_node, deps=deps))
    graph.add_node("load_context", partial(load_context_node, deps=deps))
    graph.add_node("match", partial(match_node, deps=deps))
    graph.add_node("investigate", partial(investigate_node, deps=deps))
    graph.add_node("resolve", partial(resolve_node, deps=deps))
    graph.add_node("critique", partial(critique_node, deps=deps))
    graph.add_node("policy_check", partial(policy_check_node, deps=deps))
    graph.add_node("human_review", partial(human_review_node, deps=deps))
    graph.add_node("finalize", partial(finalize_node, deps=deps))

    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "load_context")
    graph.add_edge("load_context", "match")
    graph.add_conditional_edges("match", route_after_match)
    graph.add_conditional_edges("investigate", route_after_investigation)
    graph.add_conditional_edges("resolve", route_after_resolution)
    graph.add_conditional_edges("critique", route_after_critique)
    graph.add_conditional_edges("policy_check", route_after_policy)
    graph.add_edge("human_review", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
