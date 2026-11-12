from __future__ import annotations

import json
from typing import Any

from p2p.domain.models import CritiqueResult, InvestigationResult, Resolution
from p2p.intelligence.prompts import CRITIC_SYSTEM, INVESTIGATOR_SYSTEM, RESOLVER_SYSTEM
from p2p.ports import ChatMessage, ModelProvider


def _messages(system: str, context: dict[str, Any]) -> list[ChatMessage]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(context, default=str, sort_keys=True)},
    ]


class Investigator:
    def __init__(self, provider: ModelProvider):
        self.provider = provider

    def investigate(self, context: dict[str, Any]) -> InvestigationResult:
        return self.provider.structured_response(
            InvestigationResult, _messages(INVESTIGATOR_SYSTEM, context)
        )


class Resolver:
    def __init__(self, provider: ModelProvider):
        self.provider = provider

    def resolve(self, context: dict[str, Any]) -> Resolution:
        return self.provider.structured_response(Resolution, _messages(RESOLVER_SYSTEM, context))


class Critic:
    def __init__(self, provider: ModelProvider):
        self.provider = provider

    def critique(self, context: dict[str, Any]) -> CritiqueResult:
        return self.provider.structured_response(CritiqueResult, _messages(CRITIC_SYSTEM, context))
