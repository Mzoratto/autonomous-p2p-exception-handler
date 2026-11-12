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


def test_exhausted_quota_is_reported_as_an_outage_with_its_reason(provider):
    with_outcome(
        provider,
        openai.RateLimitError(
            "You exceeded your current quota, please check your plan and billing details.",
            response=httpx.Response(429, request=REQUEST),
            body={"code": "insufficient_quota", "type": "insufficient_quota"},
        ),
    )

    with pytest.raises(
        ModelUnavailableError, match=r"^OpenAI quota exhausted \(insufficient_quota\)$"
    ):
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


KEY_ECHO = "Incorrect API key provided: sk-proj-****7Hx2. Sent: sk-proj-LEAKCHECK-7Hx2"


@pytest.mark.parametrize(
    ("error", "raised_as"),
    [
        (
            openai.AuthenticationError(
                KEY_ECHO, response=httpx.Response(401, request=REQUEST), body=None
            ),
            ModelAuthenticationError,
        ),
        (
            openai.RateLimitError(
                KEY_ECHO, response=httpx.Response(429, request=REQUEST), body=None
            ),
            ModelUnavailableError,
        ),
        (
            openai.InternalServerError(
                KEY_ECHO, response=httpx.Response(503, request=REQUEST), body=None
            ),
            ModelUnavailableError,
        ),
        (openai.APIConnectionError(message=KEY_ECHO, request=REQUEST), ModelUnavailableError),
    ],
)
def test_model_errors_never_carry_the_key(monkeypatch, error, raised_as):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-LEAKCHECK-7Hx2")
    provider = OpenAIModelProvider()
    with_outcome(provider, error)

    with pytest.raises(raised_as) as raised:
        provider.structured_response(Resolution, MESSAGES)

    assert "Incorrect API key provided" in str(raised.value)
    assert "7Hx2" not in str(raised.value)
    # The original error, which holds the key, is not chained into logged tracebacks.
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


def chained(top: Exception, *causes: Exception) -> Exception:
    """`top` caused by causes[0], which was caused by causes[1], and so on."""
    link = top
    for cause in causes:
        link.__cause__ = cause
        link = cause
    return top


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_model_errors_name_their_root_cause_without_the_key(monkeypatch, depth):
    """A bare "Connection error." hid why live calls failed; the causes are now in the message."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-LEAKCHECK-7Hx2")
    provider = OpenAIModelProvider()
    causes = [OSError(f"level {level}") for level in range(1, 4)]
    causes[depth - 1] = ValueError(f"bad header for sk-proj-LEAKCHECK-7Hx2 at level {depth}")
    with_outcome(provider, chained(openai.APIConnectionError(request=REQUEST), *causes))

    with pytest.raises(ModelUnavailableError) as raised:
        provider.structured_response(Resolution, MESSAGES)

    message = str(raised.value)
    assert message.startswith("APIConnectionError: Connection error.; caused by ")
    assert f"ValueError: bad header for [redacted] at level {depth}" in message
    assert message.count("; caused by ") == 3
    assert "7Hx2" not in message
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


def test_only_three_levels_of_cause_are_described(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    provider = OpenAIModelProvider()
    causes = [OSError(f"level {level}") for level in range(1, 5)]
    with_outcome(provider, chained(openai.APIConnectionError(request=REQUEST), *causes))

    with pytest.raises(ModelUnavailableError) as raised:
        provider.structured_response(Resolution, MESSAGES)

    assert "level 3" in str(raised.value)
    assert "level 4" not in str(raised.value)
