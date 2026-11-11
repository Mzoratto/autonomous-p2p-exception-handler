import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from p2p.application.service import CaseService, create_runtime
from p2p.domain.models import DecisionType
from p2p.infrastructure.database import demo_invoices

REPO = Path(__file__).parents[2]
PAGES = "src/p2p/ui/pages"


@pytest.fixture
def database_url(tmp_path, monkeypatch) -> str:
    url = f"sqlite:///{tmp_path / 'ui.db'}"
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("P2P_DATABASE_URL", url)
    monkeypatch.setenv("P2P_RULES_PATH", str(REPO / "config" / "business_rules.yaml"))
    return url


@pytest.fixture
def at(database_url) -> AppTest:
    return AppTest.from_file(str(REPO / "app.py"), default_timeout=15).run()


def stored_case(database_url: str, case_id: str) -> dict:
    runtime = create_runtime(database_url=database_url, demo_mode=True)
    try:
        return CaseService(runtime).get_case(case_id)
    finally:
        runtime.close()


def html(at: AppTest) -> str:
    return "".join(element.proto.body for element in at.get("html"))


def run_scenario(at: AppTest, key: str) -> str:
    at.switch_page(f"{PAGES}/process.py").run()
    at.button(key=f"scenario_select_{key}").click().run()
    at.button(key="process_run").click().run()
    assert not at.exception
    return at.session_state["process_case_id"]


def keys(widgets) -> set[str]:
    return {widget.key for widget in widgets}


def test_processing_exact_match_completes_autonomously(database_url, at):
    case_id = run_scenario(at, "01")

    case = stored_case(database_url, case_id)
    assert case["status"] == "COMPLETED"
    assert case["state"]["outcome"]["action"] == "APPROVE"
    assert case["state"]["outcome"]["source"] == "AUTONOMOUS"


def test_review_requires_a_choice_a_note_and_a_confirmation(database_url, at):
    case_id = run_scenario(at, "03")
    at.button(key="process_open_case").click().run()
    assert f"review_decision_{case_id}" in keys(at.radio)
    # AppTest does not keep a page opened by st.switch_page for later runs; pin it.
    at.switch_page(f"{PAGES}/review.py").run()

    decision = at.radio(key=f"review_decision_{case_id}")
    submit = at.button(key=f"review_submit_{case_id}")
    assert decision.value is None
    assert submit.disabled

    decision.set_value(DecisionType.REJECT).run()
    at.button(key=f"review_submit_{case_id}").click().run()
    assert any("at least 3 characters to reject" in error.value for error in at.error)
    assert stored_case(database_url, case_id)["status"] == "PENDING_REVIEW"
    assert "review_confirm_button" not in keys(at.button)

    at.text_area(key=f"review_note_{case_id}").set_value("dup").run()
    at.button(key=f"review_submit_{case_id}").click().run()
    assert {"review_cancel", "review_confirm_button"} <= keys(at.button)
    at.button(key="review_confirm_button").click().run()

    assert not at.exception
    outcome = stored_case(database_url, case_id)["state"]["outcome"]
    assert (outcome["action"], outcome["source"]) == ("REJECT", "HUMAN_OVERRIDDEN")
    assert at.toast[0].value == "Reject recorded for INV-LARGE · 0 left in queue"


def test_cancel_closes_the_confirmation_without_a_decision(database_url, at):
    case_id = run_scenario(at, "03")
    at.switch_page(f"{PAGES}/review.py").run()
    at.radio(key=f"review_decision_{case_id}").set_value(DecisionType.REQUEST_INFO).run()
    at.button(key=f"review_submit_{case_id}").click().run()

    at.button(key="review_cancel").click().run()

    assert "review_confirm_button" not in keys(at.button)
    assert stored_case(database_url, case_id)["status"] == "PENDING_REVIEW"


def test_process_result_shows_durable_status_after_human_review(database_url, at):
    case_id = run_scenario(at, "03")
    at.switch_page(f"{PAGES}/review.py").run()
    at.radio(key=f"review_decision_{case_id}").set_value(DecisionType.REQUEST_INFO).run()
    at.button(key=f"review_submit_{case_id}").click().run()
    at.button(key="review_confirm_button").click().run()
    assert at.toast[0].value == "Request info recorded for INV-LARGE · 0 left in queue"

    at.switch_page(f"{PAGES}/process.py").run()

    assert not at.exception
    assert "INV-LARGE → Request info · Human" in html(at)


def test_running_the_same_invoice_twice_shows_the_replay_banner(at):
    first = run_scenario(at, "01")
    assert not at.info

    at.button(key="process_another").click().run()
    second = run_scenario(at, "01")

    assert second == first
    assert any("already processed" in message.value for message in at.info)


def test_invalid_pasted_invoice_lists_the_field_and_cannot_run(at):
    payload = demo_invoices()["01_exact_match"].model_dump(mode="json")
    payload["total"] = "999.99"
    at.switch_page(f"{PAGES}/process.py").run()

    at.text_area(key="process_paste").set_value(json.dumps(payload)).run()
    at.button(key="process_validate").click().run()

    assert at.error[0].value == "1 field(s) need fixing before this invoice can be processed"
    assert "total does not equal subtotal plus tax" in html(at)
    assert "process_run" not in keys(at.button)
