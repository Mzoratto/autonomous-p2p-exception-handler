# Changelog

All notable changes to this project are recorded here. Versions follow [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-12

First release: the dashboard rebuilt to the "P2P Dashboard Revised" design in eight phases, with the September 2026 audit controls (F01–F15) kept intact. The plan is in [`docs/design/implementation-plan.md`](docs/design/implementation-plan.md).

### Before the phases
- **#1** Financial approval controls and durable review recovery (September 2026 audit remediation).

### Phases
- **Phase 0, #2:** design files, the implementation plan, and reference screenshots on the `screenshots` branch.
- **Dependencies, #3:** Streamlit 1.67.0 and Pillow 13, clearing the open Pillow advisories.
- **Phase 1, #4:** design tokens, self-hosted IBM Plex, Material Symbols icons, and fixes for severity and status colours, the review toast, markdown leakage in HTML, the runtime card and the toolbar.
- **Phase 2, #5:** `st.navigation` shell with page URLs (`/`, `/process`, `/review`, `/cases`, `/about`), top bar with breadcrumb and review count, the About page, and the module split.
- **Phase 3, #6:** case detail with four tabs: three-way match grid, AI reasoning with Critic revisions, an 11-row policy checklist (`PolicyCheck`), and the audit timeline in local time. Duplicates now skip the AI.
- **Phase 4, #7:** review workbench (priority queue, case, decision panel with confirmation dialog) and the three-step Process flow with replay detection.
- **Phase 5, #8:** queryable case summary columns with an automatic, repeatable migration; query API; Overview KPIs, needs-attention list, waiting chart and outcomes; Cases list with filters, search and detail.
  - **#9:** Streamlit floor raised to 1.67.
  - **#10:** one-click "Finish recovery" for a stored review, and a migration that tolerates unreadable rows.
- **Phase 6, #11:**
  - non-blocking reads while a command runs, with SQLite WAL and a busy timeout
  - OpenAI timeout, retries and `store=False`; outages route to a reviewer; a rejected key stops processing
  - failures recorded on the case, with "Retry processing"
  - command logging and the setup screen
- **Phase 7A, #12:**
  - non-root Docker image with pinned bases and a health check
  - CI on Python 3.11–3.14 with a 96% coverage floor and a Docker build-and-health job
  - a two-process SQLite test
  - an exhausted quota reported as "Model unavailable"
- **Phase 7B, #13:** responsive and accessibility pass (focus rings, 12 px minimum, target sizes, AA contrast), container-width layouts on the Review page, an indexed identity lookup, and documentation.

### Release
- `make smoke-live` (`scripts/smoke_live.py`): runs the demo invoices through the live model on a copy of your database and reports status, decision source, model, tokens, latency and errors.
- The OpenAI key is redacted from model errors, so it no longer reaches logs, stored case errors or the screen.
- Version 1.0.0.

### Remaining known limitations

Copied from `docs/design/implementation-plan.md`:

- **R17, override source without an AI proposal.** When a case reaches review with no AI resolution (a high-value exact match, or a model outage before the Resolver answered), `human_review_node` and `finalize_node` compare the decision with a default APPROVE, so a human Reject or Request info is stored as `HUMAN_OVERRIDDEN`. The stored value is unchanged; the dashboard and the override metric count an override only when an AI resolution exists (`docs/audit-remediation.md`).
- **No authentication.** The reviewer name comes from `P2P_REVIEWER_NAME`; there is no login or assignment model.
- **One SQLite file, commands serialized.** Processing and reviews take a global file lock, so they run one at a time; the lock relies on `flock` and a local file system. Reads do not wait.
- **Live model untested against the real API.** The OpenAI provider (timeouts, retries, 401/403, 429 including `insufficient_quota`, 5xx) is tested with an injected fake client only.
- **Unreadable stored data.** A case whose state cannot be read shows "Needs recovery" with technical details, but its audit trail is not shown. An unreadable stored invoice on the same PO, or with the same invoice number, still blocks processing of a new invoice, because duplicate and reservation checks must read it.
- **Identity lookup trusts normalized ids.** `claim_case` compares `invoice_id` directly (indexed). A legacy row written outside the `Invoice` model with a non-normalized id (lower case, surrounding spaces) would not be found.
- **Resolver outage.** If the Resolver is unreachable, its fallback ESCALATE (confidence 0) appears as the recommendation chip; the reviewer note says the model was unavailable.
- **Docker volumes from the old root image** need a one-time `chown` to uid 10001 (README).
- **Python 3.15** is not in the CI matrix: uv 0.11.29 has no 3.15 build to test against.
- **Streamlit-owned controls.** The tab-strip scroll arrows are 18 × 35 px (below the 24 px target size; tabs are also reachable by keyboard and swipe), and disabled buttons use Streamlit's low-contrast grey (inactive controls are exempt from WCAG contrast).
- **Accepted design approximations.** Nav count instead of a badge, Streamlit's collapsing sidebar instead of a top row on narrow screens, coloured text instead of chips in the Cases table, a popover with a copyable link, a top bar that is not sticky; KPI trends stay hidden until enough history exists.
- **Not built from the Phase 7 plan:** sticky review queue and decision columns at ≥ 960 px (not in the Phase 7 scope that was approved).

[1.0.0]: https://github.com/Mzoratto/autonomous-p2p-exception-handler/releases/tag/v1.0.0
