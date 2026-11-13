import pytest

from p2p.intelligence.providers import ModelSettings, OpenAIModelProvider
from p2p.ports import ModelConfigurationError

OPENAI_DEFAULT = "https://api.openai.com/v1/"


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    for name in ("OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_base_url_means_openai_itself(monkeypatch, blank):
    """`OPENAI_BASE_URL=` copied from .env.example once made the SDK use "" and fail every call."""
    monkeypatch.setenv("OPENAI_BASE_URL", blank)

    assert str(OpenAIModelProvider().client.base_url) == OPENAI_DEFAULT


def test_valid_base_url_is_used(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", " https://proxy.example.com/v1 ")

    assert OpenAIModelProvider().client.base_url.host == "proxy.example.com"


@pytest.mark.parametrize(
    "url",
    [
        "not a url",
        "ftp://example.com/v1",
        "https://",
        "example.com/v1",
        "https://[::1",
        "https://proxy.example.com:abc/v1",
        "https://proxy.example.com:99999/v1",
    ],
)
def test_invalid_base_url_is_a_configuration_error(monkeypatch, url):
    monkeypatch.setenv("OPENAI_BASE_URL", url)

    with pytest.raises(ModelConfigurationError, match="^OPENAI_BASE_URL is not a valid URL$"):
        OpenAIModelProvider()


def test_blank_optional_settings_fall_back_to_their_defaults(monkeypatch):
    for name in (
        "OPENAI_MODEL",
        "OPENAI_TIMEOUT_SECONDS",
        "OPENAI_MAX_RETRIES",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
    ):
        monkeypatch.setenv(name, " ")

    provider = OpenAIModelProvider()

    assert provider.model == "gpt-4.1-mini"
    assert (provider.client.timeout, provider.client.max_retries) == (30.0, 1)
    # The SDK reads these itself; a blank value would be sent as an empty header.
    assert (provider.client.organization, provider.client.project) == (None, None)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("OPENAI_TIMEOUT_SECONDS", "soon"),
        ("OPENAI_TIMEOUT_SECONDS", "nan"),
        ("OPENAI_TIMEOUT_SECONDS", "inf"),
        ("OPENAI_MAX_RETRIES", "-1"),
    ],
)
def test_invalid_numbers_are_configuration_errors(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ModelConfigurationError, match=name):
        OpenAIModelProvider()


@pytest.mark.parametrize("blank", ["", "  "])
def test_blank_key_is_missing(monkeypatch, blank):
    monkeypatch.setenv("OPENAI_API_KEY", blank)

    with pytest.raises(ModelConfigurationError, match="^Live mode needs an API key$"):
        OpenAIModelProvider()


def test_a_base_url_without_a_host_is_refused_when_the_host_is_read():
    """`from_env` rejects such URLs; settings built another way must not report a blank host."""
    settings = ModelSettings(
        api_key="sk-test", model="gpt-4.1-mini", base_url="https:///v1", timeout=30.0, max_retries=2
    )

    with pytest.raises(ValueError, match="^base_url has no host$"):
        _ = settings.host
