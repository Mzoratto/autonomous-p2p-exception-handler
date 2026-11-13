from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class RulesModel(BaseModel):
    # A misspelled key would otherwise fall back to its default without a word.
    model_config = ConfigDict(extra="forbid")


class PriceVarianceRules(RulesModel):
    auto_approve_percent: Decimal = Field(ge=0)
    auto_approve_absolute: Decimal = Field(ge=0)


class QuantityVarianceRules(RulesModel):
    tolerance_percent: Decimal = Field(ge=0)


class ConfidenceRules(RulesModel):
    auto_resolve: float = Field(ge=0, le=1)


class RiskRules(RulesModel):
    human_review_invoice_value: Decimal = Field(gt=0)


class DuplicateRules(RulesModel):
    auto_reject: Literal[True] = True


class MissingPORules(RulesModel):
    require_human_review: Literal[True] = True


class MissingReceiptRules(RulesModel):
    require_human_review: Literal[True] = True


class ReflectionRules(RulesModel):
    max_iterations: int = Field(default=3, ge=1, le=10)


class ReviewRules(RulesModel):
    sla_hours: int = Field(default=24, ge=1, le=720)


class BusinessRules(RulesModel):
    price_variance: PriceVarianceRules
    quantity_variance: QuantityVarianceRules
    confidence: ConfidenceRules
    risk: RiskRules
    duplicate_invoice: DuplicateRules
    missing_po: MissingPORules
    missing_receipt: MissingReceiptRules
    reflection: ReflectionRules
    review: ReviewRules = Field(default_factory=ReviewRules)

    @classmethod
    def load(cls, path: str | Path) -> BusinessRules:
        with Path(path).open(encoding="utf-8") as handle:
            return cls.model_validate(yaml.safe_load(handle))
