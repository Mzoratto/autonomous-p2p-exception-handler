# September 2026 audit remediation

The audit findings F01–F15 are addressed for the local, single-user simulation. This is not a production certification or evidence of live-model quality.

| Finding | Change | Regression evidence |
|---|---|---|
| F01 | Autonomous eligibility allows only matched results with known non-material price discrepancies; model/evidence errors, unsupported actions, and all other discrepancy types require review. Unsafe hard-control configuration toggles are rejected. | Exhaustive discrepancy parameterization; hostile typed provider; isolated model failure; unsafe configuration tests |
| F02 | Positive invoicing without receipt evidence for the line is material even when another receipt line exists. Zero-base price/quantity comparisons cannot vanish. | Missing receipt-line and zero-price tests |
| F03 | A normalized supplier/invoice identity is claimed transactionally with a case before execution. Replays return the original case; changed content is refused. Existing cases reserve line quantities against both order and receipt capacity until they are rejected or superseded: a reviewer can supersede a completed REQUEST_INFO case with the supplier's corrected invoice (same supplier and PO, new number), and the link is committed with the correction's claim, before its workflow reads reservations. | Replay, reservation lifecycle, concurrent duplicate and distinct ingestion tests; supersede tests for the link, refusals, release exactly once (including two concurrent corrections), commit and run failures, and the link surviving reads, saves and the migration (`tests/integration/test_supersede.py`) |
| F04 | Invoice and PO line IDs must be unique after whitespace stripping. | Duplicate-line validation tests |
| F05 | Invoicing cannot autonomously exceed remaining ordered quantity. Only OPEN POs and active, matching suppliers qualify. Partial invoicing outside receipt tolerance requires review. | Order limits, PO lifecycle and partial-invoice tests |
| F06 | Compare Decimal deltas and percentages before rounding, including extended unit-price variance. Monetary invoice totals explicitly use two decimal places and ROUND_HALF_EVEN. | Below/at/above absolute, percentage and quantity thresholds; subcent extended-price test |
| F07 | Durable review commands bind submissions to checkpoint revisions. Case reads reconcile checkpoints; completion and human-decision audit events commit together with the case snapshot. Conflicting retries are refused. | Save failures before/after resume, restart reconciliation, missing/stale checkpoints, concurrent reviewers |
| F08 | Startup seeds only empty storage. Explicit seeding inserts missing fixtures without deleting or replacing cases, audit events, commands, or existing ERP records. | Startup with deleted sentinel and repeated explicit seed tests |
| F09 | Missing, inactive or inconsistent supplier evidence requires review. | Supplier eligibility tests |
| F10 | Immutable case/invoice payload binding prevents thread reuse with conflicting content. | Case ID conflict and normalized replay tests |
| F11 | Investigator references must exactly match supplied discrepancy IDs, descriptions and source. Resolution references must all resolve to deterministic evidence. Invalid references set a persistent failure flag. Critic adjustments can reduce confidence but cannot raise it. AI narrative is labelled as interpretation. | Hostile evidence, mixed valid/fake references, malformed provider and confidence tests |
| F12 | Phase audit entries retain full structured investigation, recommendation and critique versions, attempt numbers, rules hashes, and evidence fingerprints. ERP evidence and matcher results are retained; the UI exposes recommendation history. | Full version/provenance test and UI workflow tests |
| F13 | Compose publishes only 127.0.0.1:8501. | Parsed Compose configuration |
| F14 | Process-page status and destination use the durable case and outcome source. | Process → review → REQUEST_INFO → process navigation test |
| F15 | pytest is constrained to >=9.0.3 ([security advisory](https://github.com/advisories/GHSA-6w46-j5rx-g56g)); uv.lock freezes local, CI and container application dependencies. CI runs a dependency scan. | Frozen setup, complete suite, lint, pip-audit |

## Persistence and recovery contract

Use `CaseService` for processing and review. Commands are serialized across threads and processes by an OS file lock alongside the SQLite file. This deliberately prioritizes correctness over throughput for the local demo; it is not a distributed lock or a substitute for a transactional ERP adapter. All instances must share the same canonical database path and lock file on a local filesystem. Do not remove the lock file while a process is running.

Case creation and invoice identity claims commit together before graph execution. Interrupted, failed, pending, approved and REQUEST_INFO cases reserve their requested quantities. Two things release them: a recorded REJECT, and a supersede link. REQUEST_INFO is terminal and keeps its reservation until a reviewer processes the supplier's corrected invoice as superseding it. The held case's `superseded_by` / `superseded_at`, and the `CASE_SUPERSEDED` (held case) and `SUPERSEDES_CASE` (correction) audit events, commit in the same transaction as the correction's claim, so the release happens exactly once and before the correction's workflow loads reservations. Only that command writes `superseded_by` / `superseded_at`; saves, reconciles, error records and the migration backfill never do. The held case keeps its status and its REQUEST_INFO outcome. A held case with no correction keeps its reservation; there is no release-hold or reopening workflow. Seeded historical invoice summaries provide duplicate identities, not a real ERP quantity ledger. Local cases supply the line-level allocation ledger.

A reviewer submission is durably stored before graph resume. Retrying the same action, reviewer and comment uses the stored submission, including its original timestamp. A different submission is refused. The UI/API exposes the submitted review. After checkpoint advancement followed by a failed snapshot save, reading the case or listing cases reconciles the committed checkpoint and publishes the final audit events atomically with the snapshot. Phase events represent execution attempts; they alone do not establish a committed final outcome.

For PROCESSING cases interrupted by a crash, resubmit the identical invoice: the service continues the existing checkpoint, or starts the already claimed case if no checkpoint was written. Never assign a different invoice to the case ID. If a pending case's checkpoint is missing, review fails with a restoration instruction; restore the database/checkpoint backup rather than create a replacement decision. Disk corruption, permissions and locked-storage errors remain explicit failures rather than approvals. These controls do not make independent external checkpoint deletion recoverable.

Legacy cases and audits are preserved and included in reservation/replay checks. Previously unsafe approvals are not silently changed; operators must review pre-fix results. New full recommendation history cannot reconstruct content omitted by old audit events.

## Verification and limits

Validation on 7 September 2026: **83 tests passed**, `make lint` passed, and `make audit` reported no known vulnerabilities in published dependencies (the local application is not a PyPI package and is checked through source/tests).

The offline suite includes fault injection, SQLite restart recovery, concurrent ingestion/review, adversarial typed model responses and Streamlit navigation. It does not establish live provider quality, arbitrary narrative truth, OS-level power-loss guarantees, or distributed concurrency safety. Evidence reference validation verifies supplied identifiers and content, not real-world document authenticity.

Docker verification also passed: the final image built, the health endpoint returned HTTP 200 over a loopback-only published port, and an exact completed case plus a pending review survived removal and recreation of the container using the same disposable named volume. The pending case then resumed to REJECT and exact-invoice replay returned the original case. The disposable container and volume were removed. The tested image manifest was `sha256:9be05753541f1a362574f76a946ec67633d8c03e8a2931ddbb48c25ef76756e6`.

Production still requires authenticated reviewers, authoritative ERP allocations, currency/tax-specific rules, migrations/backups and external immutable audit retention.

## Known limitations

- **Override source without an AI recommendation.** When a case reaches human review without an AI resolution (a high-value exact match, or a model outage, which stops the workflow from asking the model again after the failed call), `human_review_node` and `finalize_node` compare the reviewer's decision with a default APPROVE. A Reject or Request info is therefore stored with source `HUMAN_OVERRIDDEN` although no AI recommendation existed. The stored value is left as is. The dashboard, and its override metric, count an override only when the case has an AI resolution.
