from __future__ import annotations

import json
import os
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel

from p2p.domain.models import (
    CritiqueResult,
    DecisionType,
    DiscrepancyType,
    EvidenceItem,
    InvestigationResult,
    Resolution,
    Severity,
)

T = TypeVar("T", bound=BaseModel)


def _payload(messages: list[dict[str, str]]) -> dict:
    try:
        return json.loads(messages[-1]["content"])
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        raise ValueError("structured model context is not valid JSON") from exc


class MockModelProvider:
    """Deterministic provider that exercises the same structured service boundary as live mode."""

    def structured_response(self, schema: type[T], messages: list[dict[str, str]]) -> T:
        context = _payload(messages)
        if schema is InvestigationResult:
            return self._investigate(context)  # type: ignore[return-value]
        if schema is Resolution:
            return self._resolve(context)  # type: ignore[return-value]
        if schema is CritiqueResult:
            return self._critique(context)  # type: ignore[return-value]
        raise ValueError(f"Mock provider does not support schema {schema.__name__}")

    @staticmethod
    def _investigate(context: dict) -> InvestigationResult:
        discrepancies = context["match_result"]["discrepancies"]
        types = {item["type"] for item in discrepancies}
        evidence = [
            EvidenceItem(
                evidence_id=item["discrepancy_id"],
                description=item["explanation"],
                source="three_way_matcher",
            )
            for item in discrepancies
        ]
        if DiscrepancyType.DUPLICATE_INVOICE in types:
            root, action, confidence, missing = (
                "Previously recorded invoice identifier",
                DecisionType.REJECT,
                0.99,
                [],
            )
        elif DiscrepancyType.MISSING_PO in types:
            root, action, confidence, missing = (
                "No PO reference is available in ERP context",
                DecisionType.ESCALATE,
                0.72,
                ["Valid PO number or approved non-PO purchase evidence"],
            )
        elif DiscrepancyType.MISSING_GRN in types:
            root, action, confidence, missing = (
                "Receipt posting may be incomplete or the delivery is outstanding",
                DecisionType.REQUEST_INFO,
                0.82,
                ["Goods receipt or service-entry confirmation"],
            )
        elif DiscrepancyType.SUPPLIER_MISMATCH in types:
            root, action, confidence, missing = (
                "Invoice supplier conflicts with the PO vendor master assignment",
                DecisionType.ESCALATE,
                0.96,
                ["Buyer confirmation of the intended supplier"],
            )
        elif DiscrepancyType.QUANTITY_EXCEEDS_RECEIPT in types:
            root, action, confidence, missing = (
                "Invoice arrived before all quantities were received",
                DecisionType.REQUEST_INFO,
                0.91,
                ["Confirmation of remaining delivery or corrected invoice"],
            )
        elif DiscrepancyType.PRICE_VARIANCE in types:
            root, action, confidence, missing = (
                "Invoiced price differs from the current purchase order",
                DecisionType.REQUEST_INFO,
                0.88,
                ["Approved PO amendment or corrected invoice"],
            )
        else:
            root, action, confidence, missing = (
                "Invoice and receiving quantities differ",
                DecisionType.REQUEST_INFO,
                0.86,
                ["Receiving confirmation or corrected invoice"],
            )
        return InvestigationResult(
            root_cause=root,
            evidence=evidence,
            missing_information=missing,
            suggested_action=action,
            confidence=confidence,
        )

    @staticmethod
    def _resolve(context: dict) -> Resolution:
        investigation = context["investigation"]
        discrepancies = context["match_result"]["discrepancies"]
        material_price = any(
            item["type"] == DiscrepancyType.PRICE_VARIANCE
            and item["severity"] in {Severity.MATERIAL, Severity.CRITICAL}
            for item in discrepancies
        )
        reflection_count = context.get("reflection_count", 0)
        if material_price and reflection_count == 0:
            return Resolution(
                action=DecisionType.APPROVE,
                rationale="The price difference may represent an expected commercial update.",
                evidence_refs=[item["discrepancy_id"] for item in discrepancies],
                confidence=0.95,
            )
        action = DecisionType(investigation["suggested_action"])
        request = None
        if action is DecisionType.REQUEST_INFO:
            request = (
                "Please provide the missing receiving confirmation, approved PO amendment, "
                "or a corrected invoice that reconciles the identified discrepancy."
            )
        return Resolution(
            action=action,
            rationale=(
                f"The structured evidence supports {action.value}; no financial action is executed "
                "until deterministic policy authorization."
            ),
            evidence_refs=[item["evidence_id"] for item in investigation["evidence"]],
            confidence=float(investigation["confidence"]),
            information_request=request,
        )

    @staticmethod
    def _critique(context: dict) -> CritiqueResult:
        resolution = context["resolution"]
        discrepancies = context["match_result"]["discrepancies"]
        material = any(
            item["severity"] in {Severity.MATERIAL, Severity.CRITICAL} for item in discrepancies
        )
        if material and resolution["action"] == DecisionType.APPROVE:
            return CritiqueResult(
                valid=False,
                issues=["APPROVE is unsupported while a material discrepancy remains unresolved."],
                recommended_changes=["Request documentary evidence or escalate for human review."],
                confidence_adjustment=-0.20,
            )
        referenced = set(resolution.get("evidence_refs", []))
        available = {item["discrepancy_id"] for item in discrepancies}
        if available and not referenced.intersection(available):
            return CritiqueResult(
                valid=False,
                issues=["The rationale does not reference available discrepancy evidence."],
                recommended_changes=["Ground the decision in matcher evidence."],
                confidence_adjustment=-0.10,
            )
        return CritiqueResult(valid=True, confidence_adjustment=0)


class OpenAIModelProvider:
    """Live provider using OpenAI native Pydantic structured outputs."""

    def __init__(self, model: str | None = None):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required when DEMO_MODE is false")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        base_url = os.getenv("OPENAI_BASE_URL") or None
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def structured_response(self, schema: type[T], messages: list[dict[str, str]]) -> T:
        response = self.client.responses.parse(
            model=self.model,
            input=messages,
            text_format=schema,
        )
        if response.output_parsed is None:
            raise ValueError("model returned no validated structured output")
        return response.output_parsed
