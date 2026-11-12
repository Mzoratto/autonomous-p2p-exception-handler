"""Code that runs in a second OS process for the two-process tests (spawned, so importable)."""

import time

from workflow_support import use_workflow

from p2p.application.service import CaseService, create_runtime
from p2p.domain.models import InvestigationResult
from p2p.infrastructure.database import demo_invoices
from p2p.intelligence.providers import MockModelProvider


class SlowInvestigator(MockModelProvider):
    """Signals when the Investigator is called, then takes `seconds` to answer."""

    def __init__(self, started, seconds: float):
        self.started = started
        self.seconds = seconds

    def structured_response(self, schema, messages):
        if schema is InvestigationResult:
            self.started.set()
            time.sleep(self.seconds)
        return super().structured_response(schema, messages)


def process_slowly(url: str, scenario: str, started, seconds: float, results) -> None:
    """Process one demo invoice in this process with a slow model; report the final status."""
    runtime = create_runtime(database_url=url, demo_mode=True)
    try:
        app = CaseService(runtime)
        use_workflow(
            app, SlowInvestigator(started, seconds), checkpointer=runtime.graph.checkpointer
        )
        results.put(app.process_invoice(demo_invoices()[scenario])["status"])
    finally:
        runtime.close()
