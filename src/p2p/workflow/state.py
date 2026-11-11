from __future__ import annotations

from typing import TypedDict

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
    case_id: str
    invoice: Invoice
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
