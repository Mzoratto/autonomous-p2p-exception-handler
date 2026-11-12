"""Live smoke test: run the demo invoices through the real OpenAI model on a copy of your data.

Usage:  OPENAI_API_KEY=... make smoke-live        (or: .venv/bin/python scripts/smoke_live.py)

- Refuses to run unless OPENAI_API_KEY is set in the environment. The key is never printed;
  log output and errors pass through a redaction step first.
- Copies the database at P2P_DATABASE_URL (default sqlite:///data/demo/p2p.db) to a temporary
  file and works only on that copy. Its path is printed so you can open it in the app.
- Processes the ten demo invoices one at a time in live mode. Each invoice id gets a
  "-SMOKE-<time>" suffix so the model is really called even if your data already holds them;
  scenario 08 keeps DUP-001 so the duplicate rule is exercised. Use --keep-ids to keep all ids.
- Exit code 1 if any case hit an authentication error or an unhandled exception; 0 otherwise.
  A case sent to a reviewer because the model was unavailable is reported, not a failure.
  Exit code 2 when the key is missing or the database URL is not a SQLite file.
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
from pathlib import Path

from dotenv import load_dotenv

from p2p.application.service import CaseService, create_runtime
from p2p.intelligence.providers import redact_secret
from p2p.ports import ModelAuthenticationError

DEFAULT_URL = "sqlite:///data/demo/p2p.db"
DUPLICATE_SCENARIO = "08_duplicate_invoice"


@dataclass
class Row:
    invoice: str
    status: str
    source: str = "—"
    model: str = "—"
    tokens: str = "—"
    latency: str = "—"
    error: str = ""


class RedactingFormatter(logging.Formatter):
    """One line per record with the key removed; tracebacks are left out (the table has the error)."""

    def __init__(self, secret: str):
        super().__init__("%(levelname)s %(name)s %(message)s")
        self.secret = secret

    def format(self, record: logging.LogRecord) -> str:
        record = logging.makeLogRecord({**record.__dict__, "exc_info": None, "exc_text": None})
        return redact_secret(super().format(record), self.secret)


def copy_database(url: str) -> tuple[Path, Path, bool]:
    """Back up the source database into a new temporary directory; never write to the source."""
    source = Path(url.removeprefix("sqlite:///"))
    target = Path(tempfile.mkdtemp(prefix="p2p-smoke-")) / "p2p.db"
    exists = source.is_file()
    if exists:
        # The backup API gives a consistent copy, including pages still in the WAL file;
        # the source is opened read-only.
        with (
            sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True) as original,
            sqlite3.connect(target) as copy,
        ):
            original.backup(copy)
    return source, target, exists


def model_usage(case: dict) -> tuple[str, str, str]:
    """Models, total tokens and total latency of the AI calls recorded for a case."""
    calls = [
        event["data"]["model_call"]
        for event in case.get("audit") or []
        if isinstance(event["data"].get("model_call"), dict)
    ]
    if not calls:
        return "—", "—", "—"
    models = sorted({call.get("model") or "?" for call in calls})
    tokens = sum(
        (call.get("input_tokens") or 0) + (call.get("output_tokens") or 0) for call in calls
    )
    latency = sum(call.get("latency_ms") or 0 for call in calls)
    return ", ".join(models), str(tokens), f"{latency} ms"


def stored_status(app: CaseService, invoice_id: str) -> str:
    rows, _ = app.query_cases(search=invoice_id, limit=1)
    return rows[0]["status"] if rows else "not stored"


def run_invoice(app: CaseService, invoice, secret: str) -> tuple[Row, str | None]:
    """Process one invoice; return its row and, if it fails the smoke test, why."""
    try:
        result = app.process_invoice(invoice)
    except ModelAuthenticationError as exc:
        error = f"authentication failed: {redact_secret(str(exc), secret)}"
        return Row(invoice.invoice_id, stored_status(app, invoice.invoice_id), error=error), "auth"
    except Exception as exc:
        error = f"{type(exc).__name__}: {redact_secret(str(exc), secret)}"
        return Row(invoice.invoice_id, stored_status(app, invoice.invoice_id), error=error), "error"
    case = app.get_case(result["case_id"]) or {}
    state = case.get("state") or {}
    model, tokens, latency = model_usage(case)
    error = state.get("model_error") or case.get("last_error") or ""
    if result.get("is_replay"):
        error = "replay: this invoice is already in the data, so the model was not called"
    return (
        Row(
            invoice=invoice.invoice_id,
            status=case.get("status", result["status"]),
            source=(state.get("outcome") or {}).get("source") or "—",
            model=model,
            tokens=tokens,
            latency=latency,
            error=redact_secret(error, secret),
        ),
        None,
    )


def print_table(rows: list[Row]) -> None:
    headers = Row("Invoice", "Status", "Decision source", "Model", "Tokens", "Latency", "Error")
    fields = list(vars(headers))
    widths = {f: max(len(getattr(r, f)) for r in [headers, *rows]) for f in fields[:-1]}
    for row in [headers, *rows]:
        cells = [getattr(row, f).ljust(widths[f]) for f in fields[:-1]]
        print("  ".join([*cells, row.error]).rstrip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--keep-ids", action="store_true", help="process the demo invoices under their own ids"
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
    url = os.environ.get("P2P_DATABASE_URL", DEFAULT_URL)
    if not url.startswith("sqlite:///") or "?" in url:
        print("P2P_DATABASE_URL must be a sqlite:/// file path without options.", file=sys.stderr)
        return 2

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(RedactingFormatter(secret))
    handler.setLevel(logging.WARNING)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        source, copy, existed = copy_database(url)
        print(f"Source: {source}" + ("" if existed else " (not found; starting from demo data)"))
        print(f"Copy: {copy}")
        runtime = create_runtime(database_url=f"sqlite:///{copy}", demo_mode=False)
        print(f"Model: {runtime.model_name}")
        print()
        app = CaseService(runtime)
        tag = time.strftime("%H%M%S")
        rows, failed = [], False
        try:
            for name, invoice in app.demo_invoices().items():
                if not args.keep_ids and name != DUPLICATE_SCENARIO:
                    invoice = invoice.model_copy(
                        update={"invoice_id": f"{invoice.invoice_id}-SMOKE-{tag}"}
                    )
                if failed == "auth":
                    # Every further call would be rejected the same way.
                    rows.append(Row(invoice.invoice_id, "not run", error="skipped: key rejected"))
                    continue
                row, failure = run_invoice(app, invoice, secret)
                rows.append(row)
                failed = failed or failure
        finally:
            runtime.close()
        print_table(rows)
        print()
        if failed:
            print(
                "FAILED: at least one case hit an authentication error or an unhandled exception."
            )
            return 1
        print("OK: no authentication errors or unhandled exceptions.")
        return 0
    finally:
        root.removeHandler(handler)


if __name__ == "__main__":
    sys.exit(main())
