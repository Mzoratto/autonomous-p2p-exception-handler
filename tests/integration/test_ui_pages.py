from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from p2p.application.service import CaseService, create_runtime
from p2p.domain.models import DecisionType
from p2p.infrastructure.database import demo_invoices

REPO = Path(__file__).parents[2]
PAGES = ["overview", "process", "review", "cases", "about"]


@pytest.fixture
def database_url(tmp_path, monkeypatch) -> str:
    url = f"sqlite:///{tmp_path / 'pages.db'}"
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("P2P_DATABASE_URL", url)
    monkeypatch.setenv("P2P_RULES_PATH", str(REPO / "config" / "business_rules.yaml"))
    return url


def process(database_url: str, *scenarios: str) -> list[str]:
    runtime = create_runtime(database_url=database_url, demo_mode=True)
    try:
        app = CaseService(runtime)
        invoices = demo_invoices()
        return [app.process_invoice(invoices[name])["case_id"] for name in scenarios]
    finally:
        runtime.close()


def open_page(page: str, **query_params: str) -> AppTest:
    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=15)
    for key, value in query_params.items():
        at.query_params[key] = value
    at.run()
    if page != "overview":
        at.switch_page(f"src/p2p/ui/pages/{page}.py").run()
    return at


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_on_empty_database(database_url, page):
    assert not open_page(page).exception


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_with_completed_pending_and_rejected_cases(database_url, page):
    process(database_url, "01_exact_match", "03_large_price_variance", "08_duplicate_invoice")
    assert not open_page(page).exception


def test_case_query_param_selects_the_case(database_url):
    case_ids = process(database_url, "01_exact_match", "08_duplicate_invoice")

    for case_id in case_ids:
        assert open_page("cases", case=case_id).selectbox(key="cases_case").value == case_id


def test_review_query_param_selects_a_pending_case(database_url):
    case_ids = process(database_url, "03_large_price_variance", "06_missing_po")

    for case_id in case_ids:
        at = open_page("review", case=case_id)
        assert [radio.key for radio in at.radio if radio.key.startswith("review_decision_")] == [
            f"review_decision_{case_id}"
        ]
        assert at.query_params["case"] == case_id


def test_review_queue_orders_by_severity_then_value(database_url):
    nopo, large, high = process(
        database_url, "06_missing_po", "03_large_price_variance", "10_ambiguous_high_value"
    )

    at = open_page("review")

    opened = [button.key for button in at.button if button.key.startswith("queue_open_")]
    # 03 and 06 are MATERIAL (03 is worth more), 10 is a WARNING value review.
    assert opened == [f"queue_open_{large}", f"queue_open_{nopo}", f"queue_open_{high}"]
    assert at.query_params["case"] == large


def tab_labels(at: AppTest) -> list[str]:
    return [tab.label for tab in at.tabs]


def test_case_tabs_survive_a_label_change_after_review(database_url):
    """R20: the keyed tabs keep working when the timeline count in a label changes."""
    [case_id] = process(database_url, "03_large_price_variance")
    at = open_page("cases", case=case_id)
    assert "Audit timeline · 12" in tab_labels(at)

    at.switch_page("src/p2p/ui/pages/review.py").run()
    at.radio(key=f"review_decision_{case_id}").set_value(DecisionType.REQUEST_INFO).run()
    at.button(key=f"review_submit_{case_id}").click().run()
    at.button(key="review_confirm_button").click().run()
    at.query_params["case"] = case_id
    at.switch_page("src/p2p/ui/pages/cases.py").run()

    assert not at.exception
    assert "Audit timeline · 14" in tab_labels(at)
