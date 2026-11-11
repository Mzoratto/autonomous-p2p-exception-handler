from types import SimpleNamespace

import httpx
import openai
import pytest

from p2p.domain.models import DecisionType, Resolution
from p2p.intelligence.providers import MockModelProvider, OpenAIModelProvider
from p2p.ports import ModelAuthenticationError, ModelUnavailableError

REQUEST = httpx.Request("POST", "https://api.openai.com/v1/responses")
MESSAGES = [{"role": "user", "content": "{}"}]
RESOLUTION = Resolution(
    action=DecisionType.REQUEST_INFO, rationale="Need the receipt.", confidence=0.8
)


class FakeResponses:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def provider(monkeypatch) -> OpenAIModelProvider:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    return OpenAIModelProvider()


def with_outcome(provider: OpenAIModelProvider, outcome) -> FakeResponses:
    responses = FakeResponses(outcome)
    provider.client = SimpleNamespace(responses=responses)
    return responses


def test_client_uses_configured_timeout_and_retries(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "3")

    client = OpenAIModelProvider().client

    assert (client.timeout, client.max_retries) == (12.0, 3)


def test_parsed_output_is_returned_and_the_call_is_recorded(provider):
    responses = with_outcome(
        provider,
        SimpleNamespace(
            output_parsed=RESOLUTION,
            id="resp_123",
            usage=SimpleNamespace(input_tokens=120, output_tokens=45),
        ),
    )

    assert provider.structured_response(Resolution, MESSAGES) == RESOLUTION
    assert responses.calls[0]["store"] is False
    call = provider.last_call
    assert {key: call[key] for key in ("provider", "model", "response_id")} == {
        "provider": "openai",
        "model": "gpt-test",
        "response_id": "resp_123",
    }
    assert (call["input_tokens"], call["output_tokens"]) == (120, 45)
    assert call["latency_ms"] >= 0


def test_missing_parsed_output_is_a_validation_failure(provider):
    with_outcome(provider, SimpleNamespace(output_parsed=None, id="resp_1", usage=None))

    with pytest.raises(ValueError, match="no validated structured output"):
        provider.structured_response(Resolution, MESSAGES)


@pytest.mark.parametrize(
    "error",
    [
        openai.APITimeoutError(request=REQUEST),
        openai.APIConnectionError(request=REQUEST),
        openai.RateLimitError(
            "slow down", response=httpx.Response(429, request=REQUEST), body=None
        ),
        openai.InternalServerError(
            "upstream", response=httpx.Response(503, request=REQUEST), body=None
        ),
    ],
)
def test_outages_become_model_unavailable(provider, error):
    with_outcome(provider, error)

    with pytest.raises(ModelUnavailableError):
        provider.structured_response(Resolution, MESSAGES)


@pytest.mark.parametrize(
    "error",
    [
        openai.AuthenticationError(
            "bad key", response=httpx.Response(401, request=REQUEST), body=None
        ),
        openai.PermissionDeniedError(
            "no access to model", response=httpx.Response(403, request=REQUEST), body=None
        ),
    ],
)
def test_rejected_credentials_are_a_configuration_fault_not_an_outage(provider, error):
    with_outcome(provider, error)

    with pytest.raises(ModelAuthenticationError):
        provider.structured_response(Resolution, MESSAGES)


def test_mock_provider_records_its_calls():
    provider = MockModelProvider()

    assert getattr(provider, "last_call", None) == {"provider": "mock", "model": "mock"}
