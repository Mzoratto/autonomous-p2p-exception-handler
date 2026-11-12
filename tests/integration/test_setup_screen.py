from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from p2p.application.service import CaseService
from p2p.ports import ModelAuthenticationError

REPO = Path(__file__).parents[2]


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("P2P_DATABASE_URL", f"sqlite:///{tmp_path / 'setup.db'}")
    monkeypatch.setenv("P2P_RULES_PATH", str(REPO / "config" / "business_rules.yaml"))
    monkeypatch.setenv("DEMO_MODE", "true")


def markdown(at: AppTest) -> str:
    return " ".join(element.value for element in at.markdown)


def test_live_mode_without_a_key_shows_setup_instead_of_a_traceback(environment, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "")  # Present but empty, so .env files cannot fill it.

    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=15).run()

    assert not at.exception
    assert [title.value for title in at.title] == ["Live mode needs an API key"]
    assert "DEMO_MODE=true" in markdown(at)


def test_invalid_base_url_shows_setup_with_its_own_fix(environment, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "proxy.example.com/v1")

    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=15).run()

    assert not at.exception
    assert [title.value for title in at.title] == ["OPENAI_BASE_URL is not a valid URL"]
    assert "https://proxy.example.com/v1" in markdown(at)


def test_rejected_key_while_processing_shows_setup_until_tried_again(environment, monkeypatch):
    def rejected(self, invoice, case_id=None, supersede=None):
        raise ModelAuthenticationError("Incorrect API key provided")

    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=15).run()
    monkeypatch.setattr(CaseService, "process_invoice", rejected)
    at.switch_page("src/p2p/ui/pages/process.py").run()
    at.button(key="scenario_select_01").click().run()
    at.button(key="process_run").click().run()

    assert not at.exception
    assert [title.value for title in at.title] == ["Live mode cannot authenticate"]

    monkeypatch.undo()
    at.button(key="setup_try_again").click().run()

    assert not at.exception
    assert not at.title
