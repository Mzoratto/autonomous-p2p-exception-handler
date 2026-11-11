from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from p2p.application.service import CaseService, create_runtime
from p2p.domain.models import DecisionType

REPO = Path(__file__).parents[2]
PAGES = "src/p2p/ui/pages"


@pytest.fixture
def database_url(tmp_path, monkeypatch) -> str:
    url = f"sqlite:///{tmp_path / 'ui.db'}"
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("P2P_DATABASE_URL", url)
    monkeypatch.setenv("P2P_RULES_PATH", str(REPO / "config" / "business_rules.yaml"))
    return url


def stored_case(database_url: str, case_id: str) -> dict:
    runtime = create_runtime(database_url=database_url, demo_mode=True)
    try:
        return CaseService(runtime).get_case(case_id)
    finally:
        runtime.close()


def process_scenario(at: AppTest, scenario: str) -> str:
    at.switch_page(f"{PAGES}/process.py").run()
    at.selectbox(key="process_scenario").set_value(
        Path(f"data/demo/invoices/{scenario}.json")
    ).run()
    at.button(key="process_validate").click().run()
    at.button(key="process_run").click().run()
    assert not at.exception
    return at.session_state["invoice_workflow_case_id"]


def rendered_html(at: AppTest) -> str:
    return "".join(element.proto.body for element in at.get("html"))


def test_processing_exact_match_completes_autonomously(database_url):
    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=15).run()

    case_id = process_scenario(at, "01_exact_match")

    case = stored_case(database_url, case_id)
    assert case["status"] == "COMPLETED"
    assert case["state"]["outcome"]["action"] == "APPROVE"
    assert case["state"]["outcome"]["source"] == "AUTONOMOUS"


def test_process_result_shows_durable_status_after_human_review(database_url):
    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=15).run()
    case_id = process_scenario(at, "03_large_price_variance")
    assert any("waiting for a reviewer" in message.value for message in at.warning)

    at.switch_page(f"{PAGES}/review.py").run()
    at.radio(key="review_action").set_value(DecisionType.REQUEST_INFO).run()
    at.button(key="review_submit").click().run()
    assert not at.exception
    assert at.toast[0].value == "Request info recorded for INV-LARGE · 0 left in queue"

    at.switch_page(f"{PAGES}/process.py").run()
    assert not at.exception
    assert not any("waiting for a reviewer" in message.value for message in at.warning)
    assert "Request info · Human" in rendered_html(at)
    assert stored_case(database_url, case_id)["state"]["outcome"]["action"] == "REQUEST_INFO"
