# Autonomous P2P Exception Handler

**AI recommends. Policy authorizes. Humans own material risk.**

This project is a working simulation of an invoice-exception control plane for Procure-to-Pay (P2P) operations. It validates an invoice, collects the relevant purchase order and receipt evidence, runs a deterministic three-way match, and only brings in an AI model when the case genuinely needs interpretation.

The result is not a chatbot pretending to be an accounts-payable team. It is an auditable workflow where every calculation, recommendation, policy decision, review, and final outcome has a clear owner.

> This is a portfolio demonstration, not a payment product. It cannot move money, write to an ERP, or contact a supplier.

## Why I built it

Invoice exceptions look simple until the evidence disagrees. A price mismatch might be a supplier error, an outdated PO, a late goods receipt, or a valid commercial change that has not been recorded yet. Automating that work safely requires more than adding an LLM to a form.

I built this project to explore a more practical pattern:

- ordinary software establishes the financial facts;
- structured AI helps interpret ambiguous evidence;
- deterministic policy retains decision authority;
- a human takes over whenever a hard control or risk threshold is reached.

That separation makes the workflow easier to test, explain, and trust.

## How an invoice moves through the system

1. Pydantic models validate the invoice and its arithmetic using `Decimal` values.
2. A local ERP repository loads the purchase order, goods receipts, supplier, and invoice history once.
3. The matcher checks duplicates, documents, supplier and currency identity, line arithmetic, prices, and quantities.
4. Exact and immaterial cases go directly to the policy engine without an AI call.
5. Material exceptions go through an Investigator, Resolution Agent, and Critic with validated structured outputs.
6. The Critic can send a weak recommendation back for revision, but only for a configured number of iterations.
7. Policy either permits the outcome or pauses the graph for human review.
8. The final decision and its full event history are persisted for inspection in the UI.

```mermaid
flowchart TD
    A([Invoice]) --> B[Validate schema and arithmetic]
    B --> C[Load ERP evidence once]
    C --> D[Deterministic three-way match]
    D --> E{Material exception?}
    E -- No --> J[Deterministic policy]
    E -- Yes --> F[AI Investigator]
    F --> G[AI Resolution Agent]
    G --> H[AI Critic]
    H --> I{Valid or limit reached?}
    I -- Revise --> G
    I -- Continue --> J
    J --> K{Autonomous action allowed?}
    K -- Yes --> M[Finalize]
    K -- No --> L[Pause for human review]
    L --> N[Resume with reviewer decision]
    N --> M
    M --> O[(Case and audit storage)]
```

## Where AI fits—and where it does not

| Responsibility | Owner |
|---|---|
| Schema and invoice arithmetic | Deterministic domain logic |
| PO, receipt, supplier, and duplicate lookup | Deterministic repository |
| Price and quantity variance | Deterministic matcher |
| Thresholds and action authorization | Deterministic policy |
| Root-cause synthesis | AI Investigator |
| Proposed resolution or information request | AI Resolution Agent |
| Evidence and recommendation critique | AI Critic |
| Protected decision | Human reviewer |

Model confidence is an input to risk routing. It can make the system more cautious, but it cannot bypass a hard financial rule. Invalid or malformed model output fails closed to human review.

## A concrete demo path

Run the **large price variance** case from the dashboard. The initial AI proposal deliberately recommends an invalid approval. The Critic rejects it, the Resolution Agent revises the action to `REQUEST_INFO`, and policy pauses the case for a reviewer. Completing the review resumes the same LangGraph thread and records the human decision in the audit timeline.

The exact-match path provides the useful contrast: it never invokes the AI layer and can be approved autonomously when policy allows it.

## Architecture and repository map

- `src/p2p/domain` — financial models, matching, policy, and business rules; no LangGraph or infrastructure imports.
- `src/p2p/intelligence` — model-provider boundary plus Investigator, Resolver, and Critic services.
- `src/p2p/workflow` — typed LangGraph state, conditional routes, bounded reflection, and interrupt/resume.
- `src/p2p/infrastructure` — SQLite repositories, checkpoints, audit events, and seeded ERP fixtures.
- `src/p2p/application` — process and resume use cases, independent of Streamlit.
- `src/p2p/ui/app.py` — operations dashboard, invoice ingestion, case detail, review queue, and audit timeline.
- `config/business_rules.yaml` — financial tolerances, risk thresholds, hard controls, and reflection limit.
- `tests` — unit, integration, and end-to-end workflow behavior.

LangGraph is used because this problem benefits from explicit state transitions, a real Resolver → Critic loop, and a durable pause/resume boundary. The application also keeps its own case snapshots so it does not depend on checkpoint internals as a query API.

## Run locally

You need Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
make setup
make seed
make run
```

Open <http://localhost:8501>. The seed command creates the SQLite database and ten JSON invoice scenarios used by the dashboard.

### Demo mode

`DEMO_MODE=true` is the default. It runs the real graph, matching, policy, checkpointing, audit, and UI with a deterministic structured-output provider. It needs no API key and makes no model network calls.

### Live-model mode

Place live credentials in `.env.local` or another ignored secret source:

```dotenv
DEMO_MODE=false
OPENAI_API_KEY=your-key-here
OPENAI_MODEL=gpt-4.1-mini
```

The live adapter uses the same typed provider interface and Pydantic response schemas as demo mode. Never commit a real key; `.env.local` is ignored by Git.

## Run with Docker

```bash
docker compose up --build
```

The dashboard is available at <http://localhost:8501>. A named volume retains SQLite case and checkpoint state between container restarts. The image does not copy local credential files.

## Test and lint

```bash
make test
make lint
```

The test suite runs without network access. It covers financial validation, post-validation arithmetic corruption, matching scenarios, tolerance policy, duplicate protection, workflow routing, bounded reflection, malformed model output, SQLite adapters, durable interrupts, and human resume behavior. GitHub Actions repeats the lint and test checks on pushes and pull requests.

## Included scenarios

| Scenario | Expected control behavior |
|---|---|
| Exact match | Autonomous approval |
| Small price variance | Approval inside the configured dual tolerance |
| Large price variance | Investigate → critique → revise → human review |
| Quantity variance | Investigate and review |
| Quantity exceeds receipt | Hard-control review |
| Missing PO | Never auto-approve |
| Missing goods receipt | Request evidence and review |
| Duplicate invoice | Deterministic rejection |
| Supplier mismatch | Hard-control review |
| Exact but high-value invoice | Value-threshold review |

## Configuration

All operational thresholds live in [`config/business_rules.yaml`](config/business_rules.yaml), including price and quantity tolerances, confidence gates, high-value review, duplicate behavior, missing-document controls, and the reflection limit. Prompts contain no financial thresholds.

Local runs resolve that file from the project directory. Packaged deployments use `P2P_RULES_PATH`, which avoids depending on the Python installation path.

## Deliberate boundaries

The current limitations are intentional and important to state clearly in a portfolio project:

- ERP records and actions are simulated locally; there are no production connectors or payment capabilities.
- Ingestion accepts structured JSON. OCR and PDF extraction are a separate document-understanding problem and are outside this version's scope.
- The local dashboard has no authentication or role-based authorization.
- SQLite is a good fit for a single-process demonstration, not a horizontally scaled deployment.
- Live-model quality, latency, and cost depend on the selected model and are not exercised by the offline test suite.

These boundaries keep the project focused on the difficult part it is meant to demonstrate: safe orchestration and decision authority. They should not be hidden or replaced with production claims.

## What I would build next

For a production pilot, I would prioritize the work in this order:

1. **Identity and durability** — authenticated users, maker-checker roles, Postgres-backed application/checkpoint storage, and immutable external audit retention.
2. **Real evidence** — read-only ERP connectors and document extraction with field-level provenance and confidence.
3. **Operational safety** — approved supplier communication tools, richer entity and jurisdiction policies, traces, evaluation datasets, and live-model cost/latency monitoring.

For the portfolio itself, the highest-value additions are smaller: a short walkthrough video, a public read-only deployment with synthetic data, and a compact evaluation report showing routing accuracy and failure behavior. Those communicate the engineering better than pretending the demo already has production integrations.

## What this project demonstrates

- domain-driven boundaries around probabilistic AI;
- deterministic financial controls and explainable routing;
- typed model outputs with safe failure behavior;
- stateful LangGraph orchestration with bounded reflection;
- human-in-the-loop interrupt and resume;
- auditable case history and adapter-based infrastructure;
- testable demo behavior without an API key.
