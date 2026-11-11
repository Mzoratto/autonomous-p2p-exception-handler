import sqlite3
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


def drop_checkpoint(database_url: str, case_id: str) -> None:
    with sqlite3.connect(database_url.removeprefix("sqlite:///")) as connection:
        for table in ("checkpoints", "writes"):
            connection.execute(f"DELETE FROM {table} WHERE thread_id = ?", (case_id,))


def test_review_queue_stays_usable_when_a_case_lost_its_checkpoint(database_url):
    broken, healthy = process(database_url, "03_large_price_variance", "06_missing_po")
    drop_checkpoint(database_url, broken)

    at = open_page("review", case=broken)

    assert not at.exception
    assert any(f"{broken} needs recovery" in error.value for error in at.error)
    queue = {button.key for button in at.button if button.key.startswith("queue_open_")}
    assert queue == {f"queue_open_{broken}", f"queue_open_{healthy}"}
    assert not any(radio.key.startswith("review_decision_") for radio in at.radio)

    at.button(key=f"queue_open_{healthy}").click().run()
    assert f"review_decision_{healthy}" in {radio.key for radio in at.radio}


def test_case_detail_reports_a_case_that_lost_its_checkpoint(database_url):
    [broken] = process(database_url, "03_large_price_variance")
    drop_checkpoint(database_url, broken)

    at = open_page("cases", case=broken)

    assert not at.exception
    assert any(f"{broken} needs recovery" in error.value for error in at.error)


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_on_empty_database(database_url, page):
    assert not open_page(page).exception


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_with_completed_pending_and_rejected_cases(database_url, page):
    process(database_url, "01_exact_match", "03_large_price_variance", "08_duplicate_invoice")
    assert not open_page(page).exception


def html(at: AppTest) -> str:
    return "".join(element.proto.body for element in at.get("html"))


def test_case_query_param_opens_the_case_detail(database_url):
    case_ids = process(database_url, "01_exact_match", "08_duplicate_invoice")

    for case_id in case_ids:
        at = open_page("cases", case=case_id)
        assert not at.exception
        assert f'<span class="p2p-case-id">{case_id}</span>' in html(at)


def test_unknown_case_query_param_reports_it(database_url):
    at = open_page("cases", case="CASE-NOPE")

    assert [error.value for error in at.error] == ["Case CASE-NOPE was not found."]


def test_status_query_param_filters_the_case_table(database_url):
    rejected = process(
        database_url, "01_exact_match", "03_large_price_variance", "08_duplicate_invoice"
    )[2]

    at = open_page("cases", status="rejected")

    assert at.pills(key="cases_view").value == "rejected"
    assert list(at.dataframe[0].value["Case"]) == [rejected]


def test_overview_shows_queue_value_and_auto_resolution(database_url):
    process(
        database_url,
        "01_exact_match",
        "03_large_price_variance",
        "08_duplicate_invoice",
        "10_ambiguous_high_value",
    )

    body = html(open_page("overview"))

    assert "€62,200.00" in body
    assert "2 of 4 without a reviewer" in body
    assert "0 of 0 AI-recommended decisions were changed" in body


def test_running_all_scenarios_from_an_empty_overview(database_url):
    at = open_page("overview")

    at.button(key="overview_run_all_empty").click().run()

    assert not at.exception
    assert at.toast[0].value == "10 scenarios processed · 7 need review"
    at.button(key="overview_run_all_top").click().run()
    assert at.toast[0].value == "Demo scenarios were already processed · 7 awaiting review"


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
