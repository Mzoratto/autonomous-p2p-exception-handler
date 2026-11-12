"""Code that runs in a second OS process for the two-process tests (spawned, so importable)."""

from workflow_support import use_workflow

from p2p.application.service import CaseService, create_runtime
from p2p.domain.models import InvestigationResult
from p2p.infrastructure.database import demo_invoices
from p2p.intelligence.providers import MockModelProvider


class HeldInvestigator(MockModelProvider):
    """Signals when the Investigator is called, then holds the run until it is released."""

    def __init__(self, started, release, timeout: float):
        self.started = started
        self.release = release
        self.timeout = timeout

    def structured_response(self, schema, messages):
        if schema is InvestigationResult:
            self.started.set()
            self.release.wait(self.timeout)
        return super().structured_response(schema, messages)


def process_held(url: str, scenario: str, started, release, timeout: float, results) -> None:
    """Process one demo invoice in this process, paused inside the model call until released."""
    runtime = create_runtime(database_url=url, demo_mode=True)
    try:
        app = CaseService(runtime)
        use_workflow(
            app,
            HeldInvestigator(started, release, timeout),
            checkpointer=runtime.graph.checkpointer,
        )
        results.put(app.process_invoice(demo_invoices()[scenario])["status"])
    finally:
        runtime.close()
