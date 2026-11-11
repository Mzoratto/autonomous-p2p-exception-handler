from __future__ import annotations

from pathlib import Path

from p2p.ui.components.chips import h, render_html
from p2p.ui.components.shell import top_bar
from p2p.ui.runtime import service

DETERMINISTIC = "deterministic"
AI = "ai"
HUMAN = "human"

OWNERS = [
    ("Schema and invoice arithmetic", "Deterministic domain logic", DETERMINISTIC),
    ("PO, receipt, supplier and duplicate lookup", "Deterministic repository", DETERMINISTIC),
    ("Price and quantity variance", "Deterministic matcher", DETERMINISTIC),
    ("Thresholds and action authorization", "Deterministic policy", DETERMINISTIC),
    ("Root-cause synthesis", "AI Investigator", AI),
    ("Proposed resolution or information request", "AI Resolution Agent", AI),
    ("Evidence and recommendation critique", "AI Critic", AI),
    ("Protected decision", "Human reviewer", HUMAN),
]


def display_path(path: Path | None) -> str:
    if path is None:
        return "—"
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def render() -> None:
    runtime = service().runtime
    top_bar(["About"], "/about")
    if runtime.demo_mode:
        mode, live_model = "Demo · deterministic mock provider", "Not used in demo mode"
    else:
        mode, live_model = "Live · OpenAI", runtime.model_name
    facts = [
        ("Mode", mode),
        ("Live model", live_model),
        ("Rules path", display_path(runtime.rules_path)),
        ("Reflection limit", f"{runtime.rules.reflection.max_iterations} iterations"),
        ("Storage", runtime.repository.database.url),
    ]
    owner_rows = "".join(
        f'<div class="p2p-owner-row"><span>{h(responsibility)}</span>'
        f'<span class="p2p-owner p2p-owner-{kind}"><i></i>{h(owner)}</span></div>'
        for responsibility, owner, kind in OWNERS
    )
    fact_items = "".join(
        f'<div class="p2p-fact"><span>{h(key)}</span><strong>{h(value)}</strong></div>'
        for key, value in facts
    )
    render_html(
        f"""
        <div class="p2p-about">
          <div class="p2p-page-header">
            <div>
              <div class="p2p-eyebrow">About</div>
              <h1>AI recommends. Policy authorizes. Humans own material risk.</h1>
              <p>A working simulation of an invoice-exception control plane for Procure-to-Pay operations. It cannot move money, write to an ERP, or contact a supplier.</p>
            </div>
          </div>
          <div class="p2p-owner-table">
            <div class="p2p-owner-row p2p-owner-head"><span>Responsibility</span><span>Owner</span></div>
            {owner_rows}
          </div>
          <div class="p2p-facts">{fact_items}</div>
        </div>
        """,
    )


render()
