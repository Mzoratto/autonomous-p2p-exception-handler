from pathlib import Path

from streamlit.testing.v1 import AppTest


def widget_with_label(widgets, label: str):
    return next(widget for widget in widgets if widget.label == label)


def test_process_invoice_exposes_input_analysis_and_resolution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("P2P_DATABASE_URL", f"sqlite:///{tmp_path / 'ui.db'}")
    monkeypatch.setenv(
        "P2P_RULES_PATH",
        str(Path("config/business_rules.yaml").resolve()),
    )

    app_path = Path(__file__).parents[2] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=15).run()
    widget_with_label(app.radio, "Mobile navigation").set_value("Process Invoice").run()

    stage = widget_with_label(app.radio, "Workflow stage")
    assert stage.options == ["Input", "Analyze", "Resolve"]
    assert stage.value == "Input"

    widget_with_label(app.button, "Validate and continue").click().run()
    assert widget_with_label(app.radio, "Workflow stage").value == "Analyze"

    widget_with_label(app.button, "Run analysis").click().run()
    assert widget_with_label(app.radio, "Workflow stage").value == "Resolve"
    assert any("completed autonomously" in message.value for message in app.success)


def test_process_banner_refreshes_after_human_review(tmp_path, monkeypatch):
    from p2p.domain.models import DecisionType

    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("P2P_DATABASE_URL", f"sqlite:///{tmp_path / 'review-ui.db'}")
    monkeypatch.setenv("P2P_RULES_PATH", str(Path("config/business_rules.yaml").resolve()))
    app = AppTest.from_file(Path(__file__).parents[2] / "app.py", default_timeout=15).run()
    widget_with_label(app.radio, "Mobile navigation").set_value("Process Invoice").run()
    widget_with_label(app.selectbox, "Scenario").set_value(
        Path("data/demo/invoices/03_large_price_variance.json")
    ).run()
    widget_with_label(app.button, "Validate and continue").click().run()
    widget_with_label(app.button, "Run analysis").click().run()
    assert any("waiting for a reviewer" in message.value for message in app.warning)
    widget_with_label(app.radio, "Mobile navigation").set_value("Review Queue").run()
    widget_with_label(app.radio, "Action").set_value(DecisionType.REQUEST_INFO).run()
    widget_with_label(app.button, "Submit and resume workflow").click().run()
    assert not app.exception
    assert app.toast[0].value == "Request info recorded for INV-LARGE · 0 left in queue"
    widget_with_label(app.radio, "Mobile navigation").set_value("Process Invoice").run()
    widget_with_label(app.radio, "Workflow stage").set_value("Resolve").run()
    assert not app.exception
    assert not any("waiting for a reviewer" in message.value for message in app.warning)
    assert any("completed after human review" in message.value for message in app.success)
