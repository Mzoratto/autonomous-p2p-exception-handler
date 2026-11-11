from p2p.infrastructure.database import demo_invoices


def test_summary_counts_cases_by_durable_status(app):
    invoices = demo_invoices()
    app.process_invoice(invoices["01_exact_match"])
    app.process_invoice(invoices["03_large_price_variance"])
    app.runtime.repository.claim_case("X", invoices["02_small_price_variance"])

    summary = app.summary()

    assert summary["total"] == 3
    assert summary["counts"]["approved"] == 1
    assert summary["counts"]["review"] == 1
    assert summary["counts"]["processing"] == 1
