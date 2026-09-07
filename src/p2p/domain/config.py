from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class PriceVarianceRules(BaseModel):
    auto_approve_percent: Decimal = Field(ge=0)
    auto_approve_absolute: Decimal = Field(ge=0)


class QuantityVarianceRules(BaseModel):
    tolerance_percent: Decimal = Field(ge=0)


class ConfidenceRules(BaseModel):
    auto_resolve: float = Field(ge=0, le=1)


class RiskRules(BaseModel):
    human_review_invoice_value: Decimal = Field(gt=0)


class DuplicateRules(BaseModel):
    auto_reject: Literal[True] = True


class MissingPORules(BaseModel):
    require_human_review: Literal[True] = True


class MissingReceiptRules(BaseModel):
    require_human_review: Literal[True] = True


class ReflectionRules(BaseModel):
    max_iterations: int = Field(default=3, ge=1, le=10)


class BusinessRules(BaseModel):
    price_variance: PriceVarianceRules
    quantity_variance: QuantityVarianceRules
    confidence: ConfidenceRules
    risk: RiskRules
    duplicate_invoice: DuplicateRules
    missing_po: MissingPORules
    missing_receipt: MissingReceiptRules
    reflection: ReflectionRules

    @classmethod
    def load(cls, path: str | Path) -> BusinessRules:
        with Path(path).open(encoding="utf-8") as handle:
            return cls.model_validate(yaml.safe_load(handle))
