from p2p.infrastructure.database import demo_invoices


def test_metrics_count_cases_by_durable_status(app):
    invoices = demo_invoices()
    app.process_invoice(invoices["01_exact_match"])
    app.process_invoice(invoices["03_large_price_variance"])
    app.runtime.repository.claim_case("X", invoices["02_small_price_variance"])

    metrics = app.metrics()

    assert metrics["total"] == 3
    assert metrics["counts_by_status"] == {"COMPLETED": 1, "PENDING_REVIEW": 1, "PROCESSING": 1}
