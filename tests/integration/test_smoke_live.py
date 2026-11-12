"""The live smoke script, run against a fake OpenAI client (never the real API)."""

import hashlib
import importlib.util
import logging
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


def load_script():
    spec = importlib.util.spec_from_file_location("smoke_live", REPO / "scripts" / "smoke_live.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


class FakeOpenAI:
    """Stands in for openai.OpenAI; answers with the mock provider's structured outputs."""

    mode = "ok"
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
        if FakeOpenAI.mode == "timeout":
            raise openai.APITimeoutError(request=REQUEST)
        parsed = MockModelProvider().structured_response(text_format, input)
        return SimpleNamespace(
            output_parsed=parsed,
            id=f"resp_{FakeOpenAI.calls}",
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        )


@pytest.fixture
def source(tmp_path, monkeypatch) -> Path:
    """A seeded database with one processed case, standing in for the user's data."""
    path = tmp_path / "data" / "p2p.db"
    runtime = create_runtime(database_url=f"sqlite:///{path}", demo_mode=True)
    CaseService(runtime).process_invoice(demo_invoices()["01_exact_match"])
    runtime.close()
    # Close pooled connections too, so the WAL is checkpointed and the file is at rest.
    runtime.repository.database.engine.dispose()
    monkeypatch.setenv("P2P_DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("P2P_RULES_PATH", str(REPO / "config" / "business_rules.yaml"))
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")  # the table shows the configured model
    monkeypatch.setattr(providers, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(FakeOpenAI, "mode", "ok")
    monkeypatch.setattr(FakeOpenAI, "calls", 0)
    # Copies go under tmp_path; tempfile caches its directory, so patch it directly.
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    return path


def fingerprint(path: Path) -> tuple[str, int]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        cases = connection.execute("SELECT count(*) FROM cases").fetchone()[0]
    return hashlib.sha256(path.read_bytes()).hexdigest(), cases


def secrets_in(*texts: str) -> list[str]:
    return [part for part in (KEY, KEY[-4:]) if any(part in text for text in texts)]


def test_refuses_to_run_without_a_key(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "never-created.db"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("P2P_DATABASE_URL", f"sqlite:///{missing}")

    assert load_script().main([]) == 2

    assert "OPENAI_API_KEY" in capsys.readouterr().err
    assert not missing.exists()


def test_runs_every_demo_invoice_on_a_copy(source, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    before = fingerprint(source)

    assert load_script().main([]) == 0

    out, err = capsys.readouterr()
    rows = [line for line in out.splitlines() if "-SMOKE-" in line or "DUP-001" in line]
    assert len(rows) == len(demo_invoices())
    assert any("PENDING_REVIEW" in row and "gpt-4.1-mini" in row for row in rows)
    assert any("COMPLETED" in row and "AUTONOMOUS" in row for row in rows)
    assert FakeOpenAI.calls > 0
    assert fingerprint(source) == before
    assert not secrets_in(out, err)


def test_rejected_key_fails_the_run_without_revealing_the_key(source, monkeypatch, capsys, caplog):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setattr(FakeOpenAI, "mode", "auth")
    caplog.set_level(logging.DEBUG)

    assert load_script().main([]) == 1

    out, err = capsys.readouterr()
    assert "authentication" in out.lower()
    assert not secrets_in(out, err, caplog.text)
    copy = Path(
        next(line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("Copy: "))
    )
    with sqlite3.connect(copy) as connection:
        stored = " ".join(
            row[0] or "" for row in connection.execute("SELECT last_error FROM cases")
        )
    assert "ModelAuthenticationError" in stored
    assert not secrets_in(stored)


def test_model_outage_is_reported_but_does_not_fail_the_run(source, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setattr(FakeOpenAI, "mode", "timeout")

    assert load_script().main([]) == 0

    out = capsys.readouterr().out
    assert "Model unavailable" in out
    assert "PENDING_REVIEW" in out


def test_an_unhandled_exception_fails_the_run_and_the_rest_still_run(source, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    process = CaseService.process_invoice

    def broken_for_one(self, invoice, case_id=None):
        if invoice.invoice_id.startswith("INV-QTY"):
            raise OSError("disk full")
        return process(self, invoice, case_id)

    monkeypatch.setattr(CaseService, "process_invoice", broken_for_one)

    assert load_script().main([]) == 1

    out = capsys.readouterr().out
    assert "OSError: disk full" in out
    assert "INV-HIGH" in out  # the last scenario still ran
