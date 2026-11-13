"""The Cases list labels a case that a run is processing right now."""

import threading

from workflow_support import use_workflow

from p2p.domain.matching import ThreeWayMatcher
from p2p.infrastructure.database import demo_invoices
from p2p.ui.viewmodels import display_status


def test_the_case_list_shows_a_running_case_as_processing(app):
    """#19: a query_cases row of the case being run reads "Processing", as get_case does."""
    entered, release = threading.Event(), threading.Event()

    class HeldMatcher(ThreeWayMatcher):
        def match(self, *args, **kwargs):
            entered.set()
            release.wait(10)
            return super().match(*args, **kwargs)

    use_workflow(app, matcher=HeldMatcher(app.runtime.rules))
    worker = threading.Thread(target=app.process_invoice, args=(demo_invoices()["01_exact_match"],))
    worker.start()
    try:
        assert entered.wait(10)
        [row], _ = app.query_cases(limit=None)
        status = row["status"], display_status(row).label
    finally:
        release.set()
        worker.join()

    assert status == ("PROCESSING", "Processing")
    [row], _ = app.query_cases(limit=None)
    assert display_status(row).label == "Approved · Autonomous"
