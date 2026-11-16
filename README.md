Autonomous P2P Exception Handler

A working simulation of invoice-exception handling for Procure-to-Pay (P2P), with an AI model in the loop and firm limits on what that model is allowed to decide.

It takes an invoice, loads the related purchase order and goods receipts, runs a deterministic three-way match, and calls a model only when a case needs interpretation. The rules decide what the system may do, and a human decides anything material. Every calculation, recommendation, policy decision and review is recorded with its owner.

This is a portfolio project, not a payment product. It can't move money, write to an ERP, or contact a supplier.

Why I built it

I worked in procurement and finance before moving into software, and invoice exceptions are one of those problems that look simple until the documents start disagreeing.

A price mismatch might be the supplier's mistake. It might also be an outdated PO, a goods receipt that was posted late, or a price change that was agreed and never recorded. You can't tell which from the invoice alone, so putting an LLM behind a form doesn't get you very far.

I split the work by who should be trusted with what:

Plain code establishes the financial facts.
The model helps interpret evidence when it is ambiguous, and has to answer in a structured format.
A deterministic policy engine decides what the system is allowed to do.
A person takes over when a hard control or a material-risk threshold is hit.

With that split, each part can be tested on its own, and any decision can be traced back to whoever made it.

How an invoice moves through the system
Pydantic models validate the invoice structure and arithmetic using Decimal values.
A local ERP repository loads the purchase order, goods receipts, supplier and invoice history, once.
The matcher checks for duplicates, required documents, supplier and currency consistency, line arithmetic, price differences and quantity differences.
Exact matches and immaterial exceptions go straight to the policy engine. No model call.
Material exceptions go through an Investigator, a Resolution Agent and a Critic. Each one returns validated structured output.
If the Critic thinks the recommendation is weak or unsupported, it sends it back to the Resolution Agent for another try. The number of rounds is capped in configuration.
Policy either allows the outcome or pauses the workflow for human review.
The final decision and the full event history are saved and can be inspected in the UI.
NoYesReviseContinueYesNoInvoiceValidate schema andarithmeticLoad ERP evidence onceDeterministic three-waymatchMaterial exception?Deterministic policyAI InvestigatorAI Resolution AgentAI CriticValid or limit reached?Autonomous actionallowed?FinalizePause for human reviewResume with reviewerdecisionCase and audit storage
What the AI does and doesn't do
Responsibility	Owner
Schema and invoice arithmetic	Deterministic domain logic
PO, receipt, supplier, and duplicate lookup	Deterministic repository
Price and quantity variance	Deterministic matcher
Thresholds and action authorization	Deterministic policy
Root-cause synthesis	AI Investigator
Proposed resolution or information request	AI Resolution Agent
Evidence and recommendation critique	AI Critic
Protected decision	Human reviewer

Model confidence is used as a risk signal only. Low confidence makes the workflow more cautious. High confidence never overrides a hard financial rule. If the model returns invalid or malformed structured output, the system fails closed and the case goes to human review.

Try it: two scenarios

Start with large price variance from the dashboard. The first AI proposal recommends an approval that isn't valid (this is on purpose). The Critic rejects it, the Resolution Agent changes the action to REQUEST_INFO, and policy pauses the case for human review. When the reviewer decides, the same LangGraph thread resumes and the human decision is added to the audit timeline.

Then run exact match. There is nothing to interpret, so the AI layer is never called, and the invoice is approved autonomously if policy allows it.

Repository map
src/p2p/domain: financial models, matching, policy and business rules. Nothing in this layer imports LangGraph or infrastructure code.
src/p2p/intelligence: the model-provider boundary, plus the Investigator, Resolver and Critic services.
src/p2p/workflow: typed LangGraph state, conditional routing, bounded reflection and interrupt/resume.
src/p2p/infrastructure: SQLite repositories, checkpoints, audit events and seeded ERP fixtures.
src/p2p/application: the process and resume use cases, independent of Streamlit.
src/p2p/ui/app.py: the operations dashboard, with invoice ingestion, case detail, the review queue and the audit timeline.
config/business_rules.yaml: financial tolerances, risk thresholds, hard controls and the reflection limit.
tests: unit, integration and end-to-end workflow tests.

I used LangGraph because the problem needs explicit state transitions, a real Resolver → Critic loop, and a pause/resume boundary that survives a restart. The application keeps case snapshots for queries and reconciles them against the durable checkpoints before returning a case view. Reviewer submissions are journaled before the resume. Final decision audit events and snapshots commit together.

Run locally

You need Python 3.11+ and uv.

bash
cp .env.example .env
make setup
make seed
make run

Open http://localhost:8501.

A few things to know:

make setup installs the frozen uv.lock resolution.
make seed inserts any missing ERP fixtures and writes ten JSON invoice scenarios. It doesn't delete existing cases or audit history. On startup, the app seeds only if storage is empty.
make run starts Streamlit headless, without usage statistics, listening on localhost only. The dashboard has no login. To reach it from another machine, use make run ADDRESS=0.0.0.0. (The Docker image always binds 0.0.0.0 inside the container, and Compose publishes it on loopback.)
Demo mode

DEMO_MODE=true is the default.

The workflow graph, matching logic, policy engine, checkpointing, audit trail and UI all run for real in demo mode. The only mocked piece is the model provider, which returns deterministic structured outputs. You can explore the whole workflow without an API key and without any network calls to a model.

Live-model mode

To use a live model, put credentials in .env.local or another ignored secret source:

dotenv
DEMO_MODE=false
OPENAI_API_KEY=your-key-here
OPENAI_MODEL=gpt-4.1-mini

Never commit a real API key. .env.local is ignored by Git.

The live adapter uses the same typed provider interface and Pydantic response schemas as demo mode. Requests are sent with store=False. Each AI audit event records the model, response id, token counts and latency of the call. Timeout, retry and endpoint settings are listed under Environment variables.

Failures are handled in two different ways:

If the model service can't be reached (timeout, connection error, rate limit, server error, or an exhausted quota), the workflow stops asking the model, records the failure and sends the case to a reviewer. The reviewer sees "Model unavailable" and the reason.
If the problem is configuration (a rejected API key, a key without access to the configured model, or a 404 for the model, which usually means a mistyped OPENAI_MODEL or an OPENAI_BASE_URL with the wrong path), processing stops and the case stays recoverable. The dashboard shows a setup screen that names OPENAI_API_KEY, OPENAI_MODEL and OPENAI_BASE_URL. Starting with DEMO_MODE=false and no key shows the same screen.
Environment variables

Values are read from the process environment, then .env.local, then .env. A variable that already exists is never overridden by a file.

An empty or whitespace-only OPENAI_* value counts as unset, so OPENAI_BASE_URL= copied from an example file calls OpenAI directly. Invalid values (a base URL without http(s):// and a host, a non-numeric timeout or retry count) show the setup screen with the fix, instead of failing on every call.

Variable	Default	Effect
DEMO_MODE	true	true runs on the deterministic mock model; false calls OpenAI
OPENAI_API_KEY	unset	Required when DEMO_MODE=false; without it the dashboard shows the setup screen
OPENAI_MODEL	gpt-4.1-mini	Model used by the Investigator, Resolver and Critic
OPENAI_BASE_URL	unset (OpenAI itself)	Proxy or OpenAI-compatible endpoint; an http(s) URL with a host
OPENAI_TIMEOUT_SECONDS	30	Per-request timeout
OPENAI_MAX_RETRIES	1	SDK retries before a request counts as failed (an outage)
P2P_DATABASE_URL	sqlite:///data/demo/p2p.db	SQLite file for cases, audit events and checkpoints; must be a sqlite:/// file path without query options such as ?timeout=10
P2P_RULES_PATH	config/business_rules.yaml in the project	Business rules file
P2P_REVIEWER_NAME	Demo Reviewer	Name that signs human reviews; its initials appear in the top bar
P2P_LOG_LEVEL	INFO	Log level for the p2p loggers (standard error): DEBUG, INFO, WARNING, ERROR or CRITICAL; any other value stops the dashboard with an error naming the setting
Run with Docker
bash
docker compose up --build

The dashboard is at http://localhost:8501. It has no authentication, so Compose binds it to host loopback only. The image does not copy local credential files.

Compose passes the settings from Environment variables to the container. It takes them from the shell environment and from a .env file next to docker-compose.yml. Compose does not read .env.local. P2P_DATABASE_URL and P2P_RULES_PATH are fixed to the paths inside the image.

About the image:

Base images are Python 3.11.18 (slim, Debian bookworm) and uv 0.11.29, both pinned by version and digest.
The app runs as the unprivileged user app (uid 10001). Only /app/data is writable. It holds the database, checkpoints, WAL files and the command lock file.
A HEALTHCHECK polls Streamlit's /_stcore/health.
The named volume keeps cases and checkpoints across container restarts. A new volume takes its ownership from the image. A volume created by an older image that ran as root has to be handed to the new user once:
bash
docker compose run --rm --user root p2p-handler chown -R 10001 /app/data/demo
Before going live
1. Smoke-test the live model
bash
export OPENAI_API_KEY=...   # in the shell, not only in .env.local
make smoke-live

This runs each of the ten demo invoices on its own new database, seeded exactly like the demo data. Every scenario starts with untouched purchase orders and reservations, so the run tests the model and not collisions with earlier cases.

The output starts with the mode, the base URL host, and whether a proxy variable is set. Proxy values are not shown because they can hold credentials, and the key is never printed. Then, for each invoice, you get:

Expected: what the mock provider produces for that scenario on a fresh seed
the live Status
Routed by: the policy rules that sent it to review, or autonomous
decision source, model, tokens, latency and any error

At the end there is a count of how many outcomes match Expected.

The script exits 0 on a pass. A case that went to a reviewer because the model was unavailable is reported but doesn't fail the run, and neither does a model that reaches a different outcome from the mock. It exits 1 on a rejected key, on any unhandled exception, or when no model response was recorded in this run. Only responses received during this run count. A replayed case from your data does not.

To run it against your own data:

bash
P2P_DATABASE_URL=sqlite:///path/to/your.db make smoke-live ARGS=--from-db

This runs all ten on a copy of that database under new invoice ids. The database must exist. The script warns that existing cases keep their PO reservations, so results can differ from Expected.

2. Check the browser

Open the app in Safari 16+ or Firefox 110+ as well as Chrome. The case detail layout uses CSS container queries.

3. Hand old Docker volumes to the new user

A volume created by an image from before 1.0.0, which ran as root, needs the one-time chown shown in the Docker section.

Test and lint
bash
make test
make coverage  # tests with a coverage report; fails under the floor in pyproject.toml
make lint
make typecheck  # pyright (pinned in the dev extra) on src/
make audit  # dependency advisory lookup; requires network

The test suite runs without network access. It covers financial validation, arithmetic corrupted after validation, matching scenarios, tolerance policy, duplicate protection, workflow routing, bounded reflection, malformed model output, model outages, the SQLite adapters, durable interrupts, human resume, and two OS processes sharing one database.

The audit regression tests add hostile typed model output, fractional thresholds, concurrent ingestion and review, save failures, restart recovery and post-review UI navigation. See audit remediation and recovery.

GitHub Actions runs:

lint, format check and tests with coverage on every supported Python version (3.11 to 3.14)
the dependency audit
a pyright type check (typecheck)
a job that builds the Docker image, starts it and waits for its health check

tests/integration/test_audit_controls.py is a frozen acceptance test. Change it only in a PR that says why.

The required status checks on main mirror the CI Python matrix, the typecheck job and the docker job (test (Python 3.11) to test (Python 3.14), typecheck, docker image). A PR that changes the matrix or renames a job has to update the branch protection in the same PR.

Included scenarios
Scenario	Expected control behavior
Exact match	Autonomous approval
Small price variance	Approval inside the configured dual tolerance
Large price variance	Investigate → critique → revise → human review
Quantity variance	Investigate and review
Quantity exceeds receipt	Hard-control review
Missing PO	Never auto-approve
Missing goods receipt	Request evidence and review
Duplicate invoice	Deterministic rejection
Supplier mismatch	Hard-control review
Exact but high-value invoice	Value-threshold review

The expected routes assume fresh storage. Processing an identical invoice again returns its original case. Different invoices on the same PO draw on the same remaining capacity, so if you run several scenarios together, the later ones can route differently. That is intended.

Financial and evidence rules

Autonomous approval requires a deterministic match, an active matching supplier, an OPEN PO, enough remaining ordered and received quantity, and no unresolved model failure. Only known non-material price discrepancies may pass the final allowlist.

Duplicates. Duplicate identities from seeded history are rejected. Newly submitted identities are claimed atomically and replayed idempotently.

Reservations. Pending and failed cases reserve quantities. APPROVE and terminal REQUEST_INFO keep their reservations, and REJECT releases them. A REQUEST_INFO case also releases them when a reviewer supersedes it with the supplier's corrected invoice (Cases → case detail → "Process corrected invoice"; same supplier and PO, new invoice number).

Quantities and PO state. Partial invoices outside the receipt tolerance require a reviewer. The quantity tolerance applies to receipt variance, while the PO order limit is strict. CLOSED, cancelled and unknown PO states require review.

Suppliers. Supplier existence and active are enforced. risk_rating is display-only.

Precision. Threshold comparisons keep full Decimal precision. Monetary totals use two decimal places with ROUND_HALF_EVEN. This demo does not implement per-currency precision or tax policy.

Model evidence. Investigator evidence must copy the supplied matcher records exactly, and every reference in a recommendation must resolve to one of them. Model narratives are interpretations. They are not verified amendments or receipts. Critic confidence adjustments can only lower the effective confidence.

Configuration

Operational thresholds live in config/business_rules.yaml: price and quantity tolerances, confidence gates, high-value review rules, duplicate handling, missing-document controls, the reflection limit, and the review target (review.sla_hours, default 24) drawn on the Overview's waiting chart. Financial thresholds are kept out of prompts on purpose.

The duplicate rejection and missing-document review switches must stay true, and startup rejects unsafe combinations. Startup also rejects unknown or misspelled keys (for example reflection.max_iteration) and names each one in the error. Before upgrading, remove any keys the current version doesn't declare.

For local runs, the rules file is resolved from the project directory. Packaged deployments can set P2P_RULES_PATH, so nothing depends on the Python installation path.

The dashboard has no login. Reviews are signed with the name in P2P_REVIEWER_NAME (default Demo Reviewer), and its initials appear in the top bar.

Storage

P2P_DATABASE_URL must be a sqlite:/// file path. The database runs in WAL mode, so pages keep loading while a case is being processed. A case that is mid-run shows as "Processing" until the run finishes. When a run fails, the error is stored on the case, which then shows as "Needs recovery" with the error under "Technical details" and a "Retry processing" button that resumes it from its last saved step.

The case table stores a queryable summary of each case (supplier, amount, primary exception, AI recommendation, outcome and key timestamps) next to the full payloads. Opening a database created before these columns existed adds them and backfills existing cases from their payloads and audit events. The step runs once and is safe to repeat.

Logs

Logs go to standard error at the level in P2P_LOG_LEVEL (default INFO). They include the start, outcome and duration of every processing and review run, and every model failure.

Scope

This is an end-to-end prototype about one problem: keeping AI-assisted invoice exception handling controlled, explainable and auditable. To keep it self-contained and easy to evaluate, I left some things out:

ERP data is synthetic and accessed through repository adapters. The application can't post transactions or move money.
Invoices come in as structured JSON. OCR and PDF extraction are a separate problem from the decision workflow.
The dashboard is meant for a local, single-user demo, so there is no authentication or role management.
SQLite provides durable local case storage and LangGraph checkpoints. The persistence interfaces are written so they can be replaced in a distributed deployment.
Mock responses make the whole workflow reproducible without an API key. A live-model adapter exists too, but the offline tests cover orchestration and policy behavior. They don't measure provider quality, latency or cost.

The controls are verified inside this local simulation only. They don't establish production ERP provenance, distributed transaction guarantees or live-model reliability. The recovery contract and limitations are in the remediation report.

What a production pilot would need

The same architecture could be extended in three stages.

Identity and durable controls. Authenticated users, maker-checker roles, Postgres-backed application and checkpoint storage, and immutable external audit retention.
Enterprise evidence integration. Read-only ERP sources, and invoice-document extraction with field-level provenance and confidence.
Operational assurance. Approved supplier communication channels, entity- and jurisdiction-specific policies, distributed tracing, evaluation datasets, and monitoring of live-model quality, latency and cost.

Next on my list for the portfolio version: a public read-only deployment with synthetic data, a short product walkthrough, and an evaluation report covering routing accuracy, failure modes and policy outcomes.

What I worked on here

If you're reading this to see what I can do, these are the parts I'd point you to:

Domain boundaries that keep the model's reasoning away from financial decision authority.
Deterministic three-way matching, configurable policy controls and risk routing that explains itself.
A stateful LangGraph workflow with typed state, bounded reflection, durable interrupts and human resume.
Structured model outputs enforced with Pydantic validation, failing closed when they don't validate.
Auditable case histories built on adapter-based repositories and persistent checkpoints.
A responsive operations dashboard for ingestion, case inspection, review and audit exploration.
Reproducible seeded scenarios, offline unit and integration tests, Docker packaging and GitHub Actions CI.
