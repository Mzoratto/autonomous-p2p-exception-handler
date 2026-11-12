"""Live smoke test: run the demo invoices through the real OpenAI model.

Usage:  OPENAI_API_KEY=... make smoke-live        (or: .venv/bin/python scripts/smoke_live.py)

- Refuses to run unless OPENAI_API_KEY is set in the environment. The key is never printed;
  log output and errors pass through a redaction step first.
- Default: every scenario runs on its own new database, seeded exactly as the demo seed, so
  each one starts with untouched purchase orders and reservations. The result then tests the
  model, not collisions with earlier cases.
- --from-db: copy the database at P2P_DATABASE_URL (default sqlite:///data/demo/p2p.db; it must
  exist) and run every scenario on that one copy instead. Existing cases keep their PO
  reservations, so results can differ from the Expected column. Invoice ids get a
  "-SMOKE-<time>" suffix so the model is really called (scenario 08 keeps DUP-001); --keep-ids
  turns that off.
- Prints, per invoice: the outcome the mock provider produces on a fresh seed (Expected), the
  live outcome, what routed it (policy rules, or "autonomous"), decision source, model, tokens,
  latency and any error. Only model responses received during this run are counted.
- Exit 1 if any case hit an authentication error or an unhandled exception, or if no model
  response was recorded in this run. A case sent to a reviewer because the model was
  unavailable is reported, not a failure. Exit 2 for missing or invalid settings.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from p2p.application.service import CaseService, create_runtime
from p2p.intelligence.providers import ModelSettings, redact_secret
from p2p.ports import ModelAuthenticationError, ModelConfigurationError

DEFAULT_URL = "sqlite:///data/demo/p2p.db"
DUPLICATE_SCENARIO = "08_duplicate_invoice"
PROXY_VARIABLES = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")


@dataclass
class Row:
    invoice: str
    expected: str = "—"
    status: str = "—"
    routed_by: str = "—"
    source: str = "—"
    model: str = "—"
    tokens: str = "—"
    latency: str = "—"
    error: str = ""
    called: bool = False  # a model call returned a response


class RedactingFormatter(logging.Formatter):
    """One line per record with the key removed; tracebacks are left out (the table has the error)."""

    def __init__(self, secret: str):
        super().__init__("%(levelname)s %(name)s %(message)s")
        self.secret = secret

    def format(self, record: logging.LogRecord) -> str:
        record = logging.makeLogRecord({**record.__dict__, "exc_info": None, "exc_text": None})
        return redact_secret(super().format(record), self.secret)


def copy_database(source: Path, target: Path) -> None:
    """Back up `source` to `target`; the source is only ever opened read-only."""
    # The backup API gives a consistent copy, including pages still in the WAL file.
    with (
        sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True) as original,
        sqlite3.connect(target) as copy,
    ):
        original.backup(copy)


def outcome(case: dict) -> str:
    status = case.get("status", "—")
    action = ((case.get("state") or {}).get("outcome") or {}).get("action")
    return f"{status} · {action}" if action else status


def routed_by(case: dict) -> str:
    state = case.get("state") or {}
    policy = state.get("policy_result") or {}
    if not policy:
        return "—"
    if not policy.get("requires_human_review"):
        return "autonomous"
    return ", ".join(policy.get("triggered_rules") or []) or "—"


def model_usage(case: dict, since: datetime) -> tuple[str, str, str, bool]:
    """Models, tokens and latency of the AI calls made since `since`, and whether one answered.

    Calls recorded earlier (a replayed or resumed case from the copied data) are not counted.
    """
    calls = [
        event["data"]["model_call"]
        for event in case.get("audit") or []
        if isinstance(event["data"].get("model_call"), dict)
        and datetime.fromisoformat(event["timestamp"]) >= since
    ]
    answered = [call for call in calls if call.get("response_id") or call.get("input_tokens")]
    if not answered:
        return "—", "—", "—", False
    models = sorted({call.get("model") or "?" for call in answered})
    tokens = sum((c.get("input_tokens") or 0) + (c.get("output_tokens") or 0) for c in answered)
    latency = sum(call.get("latency_ms") or 0 for call in answered)
    return ", ".join(models), str(tokens), f"{latency} ms", True


def stored_status(app: CaseService, invoice_id: str) -> str:
    rows, _ = app.query_cases(search=invoice_id, limit=1)
    return rows[0]["status"] if rows else "not stored"


def run_invoice(app: CaseService, invoice, secret: str) -> tuple[Row, str | None]:
    """Process one invoice; return its row and, if it fails the smoke test, why."""
    started = datetime.now(UTC)
    try:
        result = app.process_invoice(invoice)
    except ModelAuthenticationError as exc:
        error = f"authentication failed: {redact_secret(str(exc), secret)}"
        return Row(
            invoice.invoice_id, status=stored_status(app, invoice.invoice_id), error=error
        ), "auth"
    except Exception as exc:
        error = f"{type(exc).__name__}: {redact_secret(str(exc), secret)}"
        return Row(
            invoice.invoice_id, status=stored_status(app, invoice.invoice_id), error=error
        ), "error"
    case = app.get_case(result["case_id"]) or {}
    state = case.get("state") or {}
    model, tokens, latency, called = model_usage(case, started)
    error = state.get("model_error") or case.get("last_error") or ""
    if result.get("is_replay"):
        error = "replay: this invoice is already in the data, so the model was not called"
    row = Row(
        invoice=invoice.invoice_id,
        status=outcome(case),
        routed_by=routed_by(case),
        source=(state.get("outcome") or {}).get("source") or "—",
        model=model,
        tokens=tokens,
        latency=latency,
        error=redact_secret(error, secret),
        called=called,
    )
    return row, None


def expected_outcomes(invoices: dict, workdir: Path) -> dict[str, str]:
    """What the mock provider produces for each scenario on its own fresh demo seed."""
    expected = {}
    for name, invoice in invoices.items():
        runtime = create_runtime(
            database_url=f"sqlite:///{workdir / name / 'p2p.db'}", demo_mode=True
        )
        try:
            app = CaseService(runtime)
            expected[name] = outcome(app.get_case(app.process_invoice(invoice)["case_id"]) or {})
        except Exception as exc:
            expected[name] = f"error ({type(exc).__name__})"
        finally:
            runtime.close()
    return expected


def proxy_variables() -> list[str]:
    return [
        name
        for name in PROXY_VARIABLES
        if (os.environ.get(name) or os.environ.get(name.lower()) or "").strip()
    ]


def print_table(rows: list[Row]) -> None:
    headers = Row(
        "Invoice", "Expected", "Status", "Routed by", "Decision source", "Model", "Tokens",
        "Latency", "Error",
    )  # fmt: skip
    fields = [f for f in vars(headers) if f != "called"]
    widths = {f: max(len(getattr(r, f)) for r in [headers, *rows]) for f in fields[:-1]}
    for row in [headers, *rows]:
        cells = [getattr(row, f).ljust(widths[f]) for f in fields[:-1]]
        print("  ".join([*cells, row.error]).rstrip())


def run(args: argparse.Namespace, settings: ModelSettings, secret: str) -> int:
    invoices = CaseService.demo_invoices()
    proxies = proxy_variables()
    if args.from_db:
        url = os.environ.get("P2P_DATABASE_URL", DEFAULT_URL)
        if not url.startswith("sqlite:///") or "?" in url:
            print(
                "P2P_DATABASE_URL must be a sqlite:/// file path without options.", file=sys.stderr
            )
            return 2
        source = Path(url.removeprefix("sqlite:///"))
        if not source.is_file():
            # Seeding a demo copy here would test the demo data, not the database asked for.
            print(f"P2P_DATABASE_URL points to {source}, which does not exist.", file=sys.stderr)
            return 2
    workdir = Path(tempfile.mkdtemp(prefix="p2p-smoke-"))
    if args.from_db:
        copy = workdir / "copy" / "p2p.db"
        copy.parent.mkdir()
        copy_database(source, copy)
        print(f"Mode: copy of {source}")
        print(f"Databases: {copy.parent}")
    else:
        print("Mode: fresh demo seed for each scenario")
        print(f"Databases: {workdir}")
    print(f"Base URL host: {settings.host}")
    print(f"Proxy variable set: {'yes (' + ', '.join(proxies) + ')' if proxies else 'no'}")
    print(f"Model: {settings.model}")
    if args.from_db:
        print(
            "WARNING: existing cases keep their PO reservations and invoice history, so "
            "results can differ from the Expected column."
        )
    print()

    expected = expected_outcomes(invoices, workdir / "expected")
    rows: list[Row] = []
    failed: str | None = None
    shared = (
        create_runtime(database_url=f"sqlite:///{copy}", demo_mode=False) if args.from_db else None
    )
    tag = time.strftime("%H%M%S")
    try:
        for name, invoice in invoices.items():
            if args.from_db and not args.keep_ids and name != DUPLICATE_SCENARIO:
                invoice = invoice.model_copy(
                    update={"invoice_id": f"{invoice.invoice_id}-SMOKE-{tag}"}
                )
            if failed == "auth":
                # Every further call would be rejected the same way.
                row = Row(invoice.invoice_id, status="not run", error="skipped: key rejected")
            elif shared is not None:
                row, failure = run_invoice(CaseService(shared), invoice, secret)
                failed = failed or failure
            else:
                runtime = create_runtime(
                    database_url=f"sqlite:///{workdir / name / 'p2p.db'}", demo_mode=False
                )
                try:
                    row, failure = run_invoice(CaseService(runtime), invoice, secret)
                finally:
                    runtime.close()
                failed = failed or failure
            row.expected = expected[name]
            rows.append(row)
    finally:
        if shared is not None:
            shared.close()

    print_table(rows)
    print()
    matches = sum(row.status == row.expected for row in rows)
    print(f"{matches} of {len(rows)} match the expected outcome.")
    if failed:
        print("FAIL: at least one case hit an authentication error or an unhandled exception.")
        return 1
    if not any(row.called for row in rows):
        # A request may still have reached the service (a quota error, say); what is known
        # is that no call of this run got a model response back.
        print("FAIL: no successful model call; no model response was recorded.")
        return 1
    print(
        "OK: the model answered, and there were no authentication errors or unhandled exceptions."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--from-db", action="store_true", help="run on a copy of P2P_DATABASE_URL instead"
    )
    parser.add_argument(
        "--keep-ids", action="store_true", help="with --from-db, keep the demo invoice ids"
    )
    args = parser.parse_args(argv)

    # The key must come from the environment itself, not from a .env file.
    secret = os.environ.get("OPENAI_API_KEY", "").strip()
    if not secret:
        print(
            "OPENAI_API_KEY is not set. The live smoke test only runs against the real model; "
            "export the key in this shell and run it again.",
            file=sys.stderr,
        )
        return 2
    load_dotenv(".env.local")
    load_dotenv(".env")
    try:
        settings = ModelSettings.from_env()
    except ModelConfigurationError as exc:
        print(f"{exc.title}. {exc.fix}", file=sys.stderr)
        return 2

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(RedactingFormatter(secret))
    handler.setLevel(logging.WARNING)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        return run(args, settings, secret)
    finally:
        root.removeHandler(handler)


if __name__ == "__main__":
    sys.exit(main())
