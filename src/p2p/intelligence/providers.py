from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import TypeVar
from urllib.parse import urlsplit

import openai
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
from p2p.ports import ModelAuthenticationError, ModelConfigurationError, ModelUnavailableError

T = TypeVar("T", bound=BaseModel)


def _payload(messages: list[dict[str, str]]) -> dict:
    try:
        return json.loads(messages[-1]["content"])
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        raise ValueError("structured model context is not valid JSON") from exc


class MockModelProvider:
    """Deterministic provider that exercises the same structured service boundary as live mode."""

    last_call: dict = {"provider": "mock", "model": "mock"}

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
        if DiscrepancyType.MISSING_PO in types:
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


# OpenAI-style keys, whole or masked as OpenAI prints them ("sk-proj-****abcd").
_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_*\-]{4,}")


def redact_secret(text: str, secret: str | None = None) -> str:
    """Remove an API key, whole or masked, from text that may be shown or logged."""
    if secret:
        text = text.replace(secret, "[redacted]")
    return _KEY_PATTERN.sub("[redacted]", text)


OPENAI_DEFAULT_HOST = "api.openai.com"
_SETUP_FIX = (
    "Fix the value in .env.local (or remove it) and restart the app, "
    "or set DEMO_MODE=true to run on the built-in mock model."
)


def drop_blank_openai_settings() -> None:
    """Treat an empty or whitespace-only OPENAI_* variable as unset.

    `OPENAI_BASE_URL=` copied from an example file is the usual case. The OpenAI SDK reads
    several of these variables itself (base URL, organization, project, headers) and would
    use the empty string, so they are removed from the environment, not just ignored here.
    """
    for name in [n for n, v in os.environ.items() if n.startswith("OPENAI_") and not v.strip()]:
        del os.environ[name]


def _number(name: str, default: str, kind: type, minimum: float, unit: str) -> float | int:
    raw = os.environ.get(name, default).strip()
    try:
        value = kind(raw)
    except ValueError:
        value = None
    if value is None or value < minimum:
        raise ModelConfigurationError(f"{name} must be {unit}", _SETUP_FIX)
    return value


@dataclass(frozen=True)
class ModelSettings:
    """Live-model settings from the environment, after blank values are dropped."""

    api_key: str
    model: str
    base_url: str | None  # None: OpenAI itself
    timeout: float
    max_retries: int

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).hostname if self.base_url else OPENAI_DEFAULT_HOST

    @classmethod
    def from_env(cls) -> ModelSettings:
        drop_blank_openai_settings()
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ModelConfigurationError(
                "Live mode needs an API key",
                "Set a valid OPENAI_API_KEY in .env.local and restart the app, "
                "or set DEMO_MODE=true to run on the built-in mock model.",
            )
        base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or None
        if base_url is not None:
            parts = urlsplit(base_url)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                raise ModelConfigurationError(
                    "OPENAI_BASE_URL is not a valid URL",
                    "Set OPENAI_BASE_URL to an http(s) URL with a host, such as "
                    "https://proxy.example.com/v1, or remove it to call OpenAI directly. "
                    + _SETUP_FIX,
                )
        return cls(
            api_key=api_key,
            model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini").strip(),
            base_url=base_url,
            timeout=_number(
                "OPENAI_TIMEOUT_SECONDS", "30", float, 0.001, "a positive number of seconds"
            ),
            max_retries=_number("OPENAI_MAX_RETRIES", "1", int, 0, "a whole number of 0 or more"),
        )


CAUSE_DEPTH = 3


def describe_error(exc: BaseException, secret: str | None, with_type: bool = True) -> str:
    """The error and up to three underlying causes as one redacted line.

    Only text is kept: the exceptions themselves (which may hold the key) are never attached.
    """
    parts = [redact_secret(f"{type(exc).__name__}: {exc}" if with_type else str(exc), secret)]
    link: BaseException | None = exc
    for _ in range(CAUSE_DEPTH):
        link = link.__cause__ or (None if link.__suppress_context__ else link.__context__)
        if link is None:
            break
        parts.append(redact_secret(f"{type(link).__name__}: {link}", secret))
    return "; caused by ".join(parts)


class OpenAIModelProvider:
    """Live provider using OpenAI native Pydantic structured outputs."""

    def __init__(self, model: str | None = None):
        settings = ModelSettings.from_env()
        self._api_key = settings.api_key
        self.settings = settings
        self.model = model or settings.model
        self.client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout,
            max_retries=settings.max_retries,
        )
        self.last_call: dict | None = None

    def structured_response(self, schema: type[T], messages: list[dict[str, str]]) -> T:
        self.last_call = None  # Never report the previous call for a request that failed.
        started = time.monotonic()
        try:
            response = self.client.responses.parse(
                model=self.model,
                input=messages,
                text_format=schema,
                store=False,
            )
        # Every mapped error is raised "from None": the original exception may echo the key,
        # and a chained cause would print it in logged tracebacks. The new message names the
        # original error and up to three underlying causes, all redacted.
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            # A rejected key, or one without access to the model, is a configuration fault;
            # routing cases to review would hide it.
            raise ModelAuthenticationError(
                describe_error(exc, self._api_key, with_type=False)
            ) from None
        except openai.RateLimitError as exc:
            # An exhausted quota is an outage like any other: the case goes to a reviewer.
            # Its reason is named, since waiting will not clear it.
            if exc.code == "insufficient_quota":
                raise ModelUnavailableError("OpenAI quota exhausted (insufficient_quota)") from None
            raise ModelUnavailableError(describe_error(exc, self._api_key)) from None
        except (openai.APIConnectionError, openai.InternalServerError) as exc:
            # APIConnectionError includes timeouts; its cause (DNS, TLS, proxy, bad base URL)
            # is what an operator needs to see.
            raise ModelUnavailableError(describe_error(exc, self._api_key)) from None
        usage = getattr(response, "usage", None)
        self.last_call = {
            "provider": "openai",
            "model": self.model,
            "response_id": getattr(response, "id", None),
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
        if response.output_parsed is None:
            raise ValueError("model returned no validated structured output")
        return response.output_parsed
