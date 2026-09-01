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
