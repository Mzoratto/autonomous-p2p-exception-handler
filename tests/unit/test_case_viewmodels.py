from datetime import UTC, datetime, timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from workflow_support import BrokenMatcher, use_workflow

from p2p.application.service import CaseService, create_runtime
from p2p.domain.casefacts import primary_exception
from p2p.domain.models import DecisionType, HumanReview
from p2p.infrastructure.database import demo_invoices
from p2p.ui.components.case_detail import ai_html
from p2p.ui.viewmodels import (
    CANNOT,
    EXCEEDS,
    AiStoryView,
    ai_story,
    case_header,
    default_tab,
    local_zone,
    match_view,
    outcome_view,
    policy_view,
    tab_labels,
    timeline,
    waiting_labels,
)

UTC_ZONE = (UTC, " UTC")


@pytest.fixture
def scenario_case(tmp_path):
    """Process one demo scenario on fresh storage and return its get_case() view."""
    runtimes = []

    def run(name: str, review: DecisionType | None = None) -> dict:
        runtime = create_runtime(
            database_url=f"sqlite:///{tmp_path / f'{name}.db'}",
            demo_mode=True,
            checkpointer=InMemorySaver(),
        )
        runtimes.append(runtime)
        service = CaseService(runtime)
        case_id = service.process_invoice(demo_invoices()[name])["case_id"]
        if review is not None:
            service.review_case(case_id, HumanReview(reviewer="Ana Reviewer", action=review))
        return service.get_case(case_id)

    yield run
    for runtime in runtimes:
        runtime.close()


def row(view, field):
    return next(item for item in view.lines[0].rows if item.field == field)


def test_large_price_variance_tells_the_revision_story(scenario_case, rules):
    case = scenario_case("03_large_price_variance")

    story = ai_story(case, rules)

    first, second = story.attempts
    assert (first.action, first.confidence, first.verdict) == ("APPROVE", 0.95, "failed")
    assert first.adjusted == pytest.approx(0.75)
    assert (second.action, second.confidence, second.verdict) == ("REQUEST_INFO", 0.88, "passed")
    assert story.title == "The Critic rejected the first proposal and forced a revision"
    assert [name for name, _, _ in story.diff] == [
        "Action",
        "Confidence",
        "Information request",
        "Rationale",
    ]
    assert story.diff[1][1] == "0.95 (0.75 after Critic)"
    assert default_tab(story) == "ai"
    assert row(match_view(case["state"], rules), "Unit price").result == EXCEEDS


def test_exact_match_does_not_invoke_ai_and_every_tile_is_ok(scenario_case, rules):
    case = scenario_case("01_exact_match")

    story = ai_story(case, rules)

    assert story.invoked is False
    assert story.not_invoked_text.startswith("Nothing ambiguous to interpret")
    assert [tile.state for tile in match_view(case["state"], rules).tiles] == ["ok"] * 5
    assert default_tab(story) == "match"


def test_duplicate_explains_deterministic_rejection(scenario_case, rules):
    case = scenario_case("08_duplicate_invoice")

    assert ai_story(case, rules).not_invoked_text.startswith("Duplicate invoices are rejected")
    view = policy_view(case["state"])
    assert view.verdict == "Duplicate control enforced rejection without AI."
    assert view.verdict_key == "rejected"


def test_high_value_exact_match_is_headlined_as_a_value_review(scenario_case, rules):
    case = scenario_case("10_ambiguous_high_value")

    exception = next(
        item
        for item in case_header(case, datetime.now(UTC), "Ana Reviewer").meta
        if item.label == "Exception"
    )

    assert exception.value == "High-value invoice"
    assert exception.chip.text == "WARNING"
    assert "at or above 50,000.00" in ai_story(case, rules).not_invoked_text
    assert primary_exception(case["state"]) == ("HIGH_VALUE", "WARNING", 0)


def test_missing_po_cannot_compare_prices_and_fails_three_rules(scenario_case, rules):
    case = scenario_case("06_missing_po")

    assert row(match_view(case["state"], rules), "Unit price").result == CANNOT
    view = policy_view(case["state"])
    assert view.failed == 3
    assert view.verdict.startswith("3 of 11 rules failed")


def test_pending_header_counts_wait_from_the_review_request(scenario_case):
    case = scenario_case("03_large_price_variance")
    requested = next(
        event["timestamp"]
        for event in case["audit"]
        if event["event_type"] == "HUMAN_REVIEW_REQUESTED"
    )
    now = datetime.fromisoformat(requested) + timedelta(hours=2, minutes=14)

    meta = {item.label: item.value for item in case_header(case, now, "Ana Reviewer").meta}

    assert meta["Open for"] == "2h 14m"
    assert meta["Assigned to"] == "Ana Reviewer"


def test_timeline_events_carry_deltas_and_shortened_hashes(scenario_case):
    case = scenario_case("03_large_price_variance")

    events = timeline(case, UTC_ZONE, datetime.now(UTC))

    assert len(events) == 12
    assert events[0].delta == "start"
    assert all(event.delta.startswith("+") for event in events[1:])
    assert all(event.time.endswith(" UTC") for event in events)
    failed = next(event for event in events if event.label == "Critique failed")
    assert failed.dot == "#d23a50"
    details = dict(failed.details)
    assert details["rules_sha256"].endswith("…") and len(details["rules_sha256"]) == 13


def test_tab_labels_count_exceptions_attempts_failures_and_events(scenario_case, rules):
    case = scenario_case("03_large_price_variance")
    state = case["state"]

    labels = tab_labels(
        match_view(state, rules), ai_story(case, rules), policy_view(state), len(case["audit"])
    )

    assert labels == {
        "match": "Three-way match · 1 exception",
        "ai": "AI reasoning · 2 attempts",
        "policy": "Policy · 2 failed",
        "timeline": "Audit timeline · 12",
    }


def test_reject_without_ai_recommendation_is_not_shown_as_an_override(scenario_case):
    """R17: storage records HUMAN_OVERRIDDEN, the dashboard must not call it an override."""
    case = scenario_case("10_ambiguous_high_value", review=DecisionType.REJECT)

    assert case["state"]["outcome"]["source"] == "HUMAN_OVERRIDDEN"
    rows = dict(outcome_view(case))
    assert rows["AI recommendation"] == "AI not invoked"
    assert "overridden" not in " ".join(rows.values()).lower()


def test_unknown_browser_zone_falls_back_to_utc():
    assert local_zone("Not/AZone") == UTC_ZONE
    assert local_zone(None) == UTC_ZONE
    assert local_zone("Europe/Prague")[1] == ""


def test_waiting_labels_keep_cases_with_the_same_invoice_id_apart():
    waiting = [
        {"case_id": "CASE-A", "invoice_id": "INV-1"},
        {"case_id": "CASE-B", "invoice_id": "INV-1"},
        {"case_id": "CASE-C", "invoice_id": "INV-2"},
    ]

    assert waiting_labels(waiting) == ["INV-1 · CASE-A", "INV-1 · CASE-B", "INV-2"]


def test_a_run_that_failed_before_matching_is_not_shown_as_matched(app, rules):
    use_workflow(app, matcher=BrokenMatcher(OSError("disk full")))
    with pytest.raises(OSError):
        app.process_invoice(demo_invoices()["03_large_price_variance"])
    [stored], _ = app.query_cases()
    case = app.get_case(stored["case_id"])

    header = case_header(case, datetime.now(UTC), "Ana Reviewer", UTC_ZONE)
    match = match_view(case["state"], rules)
    labels = tab_labels(match, ai_story(case, rules), policy_view(case["state"]), 3)

    assert next(item.value for item in header.meta if item.label == "Exception") == "Not checked"
    assert labels["match"] == "Three-way match · Not run"
    assert (match.ran, match.tiles, match.lines) == (False, [], [])


def test_an_invoked_ai_story_without_an_investigation_is_refused():
    """`ai_story` always builds an investigation when the AI was invoked; anything else is a bug."""
    with pytest.raises(ValueError, match="^An invoked AI story has no investigation$"):
        ai_html(AiStoryView(invoked=True))
