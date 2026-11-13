from pathlib import Path

from streamlit.testing.v1 import AppTest

from p2p.application.service import CaseService
from p2p.ports import ModelAuthenticationError

REPO = Path(__file__).parents[2]


def test_unknown_model_while_processing_names_model_and_base_url(tmp_path, monkeypatch):
    monkeypatch.setenv("P2P_DATABASE_URL", f"sqlite:///{tmp_path / 'setup.db'}")
    monkeypatch.setenv("P2P_RULES_PATH", str(REPO / "config" / "business_rules.yaml"))
    monkeypatch.setenv("DEMO_MODE", "true")

    def not_found(self, invoice, case_id=None, supersede=None):
        raise ModelAuthenticationError(
            "The model `gpt-x` does not exist or you do not have access to it."
        )

    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=15).run()
    monkeypatch.setattr(CaseService, "process_invoice", not_found)
    at.switch_page("src/p2p/ui/pages/process.py").run()
    at.button(key="scenario_select_01").click().run()
    at.button(key="process_run").click().run()

    assert not at.exception
    screen = " ".join(element.value for element in [*at.markdown, *at.caption])
    for setting in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL", "DEMO_MODE=true"):
        assert setting in screen
