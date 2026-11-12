"""The live smoke script, run against a fake OpenAI client (never the real API)."""

import hashlib
import importlib.util
import json
import logging
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

from p2p.application.service import CaseService, create_runtime
from p2p.infrastructure.database import demo_invoices
from p2p.intelligence import providers
from p2p.intelligence.providers import MockModelProvider

REPO = Path(__file__).parents[2]
KEY = "sk-test-SMOKE-SECRET-Q9Z7"
REQUEST = httpx.Request("POST", "https://api.openai.com/v1/responses")
COLUMNS = [
    "Invoice",
    "Expected",
    "Status",
    "Routed by",
    "Decision source",
    "Model",
    "Tokens",
    "Latency",
    "Error",
]


def load_script():
    spec = importlib.util.spec_from_file_location("smoke_live", REPO / "scripts" / "smoke_live.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


class FakeOpenAI:
    """Stands in for openai.OpenAI; answers with the mock provider's structured outputs."""

    mode = "ok"
    fail_invoices: frozenset[str] = frozenset()
    calls = 0

    def __init__(self, **settings):
        self.responses = self

    def parse(self, *, model, input, text_format, store):
        FakeOpenAI.calls += 1
        if FakeOpenAI.mode == "auth":
            # OpenAI echoes a masked key; this fake also echoes the full one, to prove both
            # are removed before anything is printed, logged or stored.
            raise openai.AuthenticationError(
                f"Incorrect API key provided: {KEY[:7]}****{KEY[-4:]} ({KEY})",
                response=httpx.Response(401, request=REQUEST),
                body=None,
            )
        context = json.dumps(input)
        if FakeOpenAI.mode == "timeout" or any(i in context for i in FakeOpenAI.fail_invoices):
            raise openai.APITimeoutError(request=REQUEST)
        parsed = MockModelProvider().structured_response(text_format, input)
        return SimpleNamespace(
            output_parsed=parsed,
            id=f"resp_{FakeOpenAI.calls}",
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        )


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A key in the environment, the fake client, and temporary files under tmp_path."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")  # the table shows the configured model
    monkeypatch.setenv("P2P_RULES_PATH", str(REPO / "config" / "business_rules.yaml"))
    for name in ("OPENAI_BASE_URL", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setattr(providers, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(FakeOpenAI, "mode", "ok")
    monkeypatch.setattr(FakeOpenAI, "fail_invoices", frozenset())
    monkeypatch.setattr(FakeOpenAI, "calls", 0)
    # Temporary databases go under tmp_path; tempfile caches its directory, so patch it.
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    # The default mode never reads P2P_DATABASE_URL; point it somewhere that must stay absent.
    monkeypatch.setenv("P2P_DATABASE_URL", f"sqlite:///{tmp_path / 'untouched' / 'p2p.db'}")
    return tmp_path


@pytest.fixture
def source(live, monkeypatch) -> Path:
    """A database whose demo PO lines are already reserved, standing in for the user's data."""
    path = live / "data" / "p2p.db"
    runtime = create_runtime(database_url=f"sqlite:///{path}", demo_mode=True)
    service = CaseService(runtime)
    for invoice in demo_invoices().values():
        service.process_invoice(invoice)
    runtime.close()
    # Close pooled connections too, so the WAL is checkpointed and the file is at rest.
    runtime.repository.database.engine.dispose()
    monkeypatch.setenv("P2P_DATABASE_URL", f"sqlite:///{path}")
    return path


def table(out: str) -> dict[str, dict[str, str]]:
    """The printed table, keyed by invoice id."""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("Invoice  "))
    rows = {}
    for line in lines[start + 1 :]:
        if not line.strip():
            break
        cells = re.split(r"\s{2,}", line.strip())
        rows[cells[0]] = dict(zip(COLUMNS, cells, strict=False))
    return rows


def fingerprint(path: Path) -> tuple[str, int]:
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
        cases = connection.execute("SELECT count(*) FROM cases").fetchone()[0]
    return hashlib.sha256(path.read_bytes()).hexdigest(), cases


def secrets_in(*texts: str) -> list[str]:
    return [part for part in (KEY, KEY[-4:]) if any(part in text for text in texts)]


def test_refuses_to_run_without_a_key(live, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY")

    assert load_script().main([]) == 2

    assert "OPENAI_API_KEY" in capsys.readouterr().err
    assert not (live / "untouched").exists()


def test_each_scenario_runs_on_its_own_fresh_seed_and_matches_the_mock(live, capsys):
    """On untouched POs every scenario reaches the outcome the mock provider produces."""
    assert load_script().main([]) == 0

    out, err = capsys.readouterr()
    rows = table(out)
    assert set(rows) == {invoice.invoice_id for invoice in demo_invoices().values()}
    assert all(row["Status"] == row["Expected"] for row in rows.values()), rows
    assert rows["INV-EXACT"]["Routed by"] == "autonomous"
    assert rows["INV-SMALL"]["Status"] == "COMPLETED · APPROVE"
    assert "material_variance" in rows["INV-LARGE"]["Routed by"]
    assert rows["INV-LARGE"]["Model"] == "gpt-4.1-mini" and rows["INV-LARGE"]["Tokens"] != "—"
    assert "Mode: fresh demo seed for each scenario" in out
    assert "Base URL host: api.openai.com" in out
    assert "Proxy variable set: no" in out
    assert "10 of 10 match the expected outcome" in out
    assert not (live / "untouched").exists()
    assert not secrets_in(out, err)


def test_from_db_works_on_a_copy_and_warns_about_existing_reservations(source, capsys):
    before = fingerprint(source)

    assert load_script().main(["--from-db"]) == 0

    out = capsys.readouterr().out
    assert f"Mode: copy of {source}" in out
    assert "WARNING: existing cases keep their PO reservations" in out
    rows = table(out)
    assert all("-SMOKE-" in invoice for invoice in rows if invoice != "DUP-001")
    # The reserved PO lines push the second small-price invoice over the order quantity.
    small = next(row for invoice, row in rows.items() if invoice.startswith("INV-SMALL"))
    assert small["Status"] == "PENDING_REVIEW" != small["Expected"]
    assert fingerprint(source) == before


def test_from_db_copies_a_source_whose_path_contains_a_uri_delimiter(source, monkeypatch, capsys):
    """A '#' in the path must not cut the SQLite URI short (wrong file, read-only flag lost)."""
    moved = source.parent.parent / "snapshot#1" / "p2p.db"
    moved.parent.mkdir()
    source.rename(moved)
    monkeypatch.setenv("P2P_DATABASE_URL", f"sqlite:///{moved}")
    before = fingerprint(moved)

    assert load_script().main(["--from-db"]) == 0

    small = next(row for inv, row in table(capsys.readouterr().out).items() if "INV-SMALL" in inv)
    assert small["Status"] == "PENDING_REVIEW"  # the reservations came from the requested file
    assert fingerprint(moved) == before
    assert not (source.parent.parent / "snapshot").exists()


def test_from_db_needs_the_requested_database_to_exist(live, monkeypatch, capsys):
    missing = live / "missing" / "p2p.db"
    monkeypatch.setenv("P2P_DATABASE_URL", f"sqlite:///{missing}")

    assert load_script().main(["--from-db"]) == 2

    assert f"{missing}, which does not exist" in capsys.readouterr().err
    assert FakeOpenAI.calls == 0


def test_replayed_cases_do_not_count_as_model_calls_of_this_run(live, monkeypatch, capsys):
    """With --keep-ids every case replays; responses recorded by an earlier run don't count."""
    path = live / "live-data" / "p2p.db"
    runtime = create_runtime(database_url=f"sqlite:///{path}", demo_mode=False)
    service = CaseService(runtime)
    for invoice in demo_invoices().values():
        service.process_invoice(invoice)  # answered by the fake client, so responses are recorded
    runtime.close()
    runtime.repository.database.engine.dispose()
    monkeypatch.setenv("P2P_DATABASE_URL", f"sqlite:///{path}")
    calls_before = FakeOpenAI.calls

    assert load_script().main(["--from-db", "--keep-ids"]) == 1

    out = capsys.readouterr().out
    assert FakeOpenAI.calls == calls_before
    assert "replay" in table(out)["INV-LARGE"]["Error"]
    assert "FAIL: no successful model call" in out


def test_base_url_host_and_proxy_names_are_shown_but_not_proxy_values(live, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example.com/v1")
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pass@corp-proxy:3128")

    assert load_script().main([]) == 0

    out = capsys.readouterr().out
    assert "Base URL host: proxy.example.com" in out
    assert "Proxy variable set: yes (HTTPS_PROXY)" in out
    assert "user:pass" not in out


def test_invalid_base_url_stops_before_any_call(live, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_BASE_URL", "proxy.example.com")

    assert load_script().main([]) == 2

    assert "OPENAI_BASE_URL is not a valid URL" in capsys.readouterr().err
    assert FakeOpenAI.calls == 0


def test_no_successful_model_call_fails_the_run(live, monkeypatch, capsys):
    monkeypatch.setattr(FakeOpenAI, "mode", "timeout")

    assert load_script().main([]) == 1

    out = capsys.readouterr().out
    assert "FAIL: no successful model call; no model response was recorded." in out
    assert "Model unavailable" in out


def test_one_unavailable_case_is_reported_but_does_not_fail_the_run(live, monkeypatch, capsys):
    monkeypatch.setattr(FakeOpenAI, "fail_invoices", frozenset({"INV-LARGE"}))

    assert load_script().main([]) == 0

    rows = table(capsys.readouterr().out)
    assert rows["INV-LARGE"]["Status"] == "PENDING_REVIEW"
    assert "model_error" in rows["INV-LARGE"]["Routed by"]
    assert "Model unavailable" in rows["INV-LARGE"]["Error"]


def test_rejected_key_fails_the_run_without_revealing_the_key(live, monkeypatch, capsys, caplog):
    monkeypatch.setattr(FakeOpenAI, "mode", "auth")
    caplog.set_level(logging.DEBUG)

    assert load_script().main([]) == 1

    out, err = capsys.readouterr()
    assert "authentication failed" in table(out)["INV-LARGE"]["Error"]
    assert not secrets_in(out, err, caplog.text)
    databases = Path(
        next(line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("Databases: "))
    )
    with sqlite3.connect(databases / "03_large_price_variance" / "p2p.db") as connection:
        stored = " ".join(
            row[0] or "" for row in connection.execute("SELECT last_error FROM cases")
        )
    assert "ModelAuthenticationError" in stored
    assert not secrets_in(stored)


def test_an_unhandled_exception_fails_the_run_and_the_rest_still_run(live, monkeypatch, capsys):
    process = CaseService.process_invoice

    def broken_for_one(self, invoice, case_id=None):
        if invoice.invoice_id.startswith("INV-QTY"):
            raise OSError("disk full")
        return process(self, invoice, case_id)

    monkeypatch.setattr(CaseService, "process_invoice", broken_for_one)

    assert load_script().main([]) == 1

    rows = table(capsys.readouterr().out)
    assert rows["INV-QTY"]["Error"] == "OSError: disk full"
    assert rows["INV-HIGH"]["Status"] == "PENDING_REVIEW"  # the last scenario still ran
