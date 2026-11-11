# Dashboard design: P2P Dashboard Revised

The design is the spec; each PR lists its deviations.

The build order, shared contracts and per-phase checks are in [implementation-plan.md](implementation-plan.md).

## Files

|File|Purpose|
|---|---|
|`P2P_Dashboard_Revised.dc.html`|The design prototype. Template lines 24-743 hold the markup and copy; the script from line 745 holds label maps and sample logic.|
|`support.js`|Runtime the prototype loads as `./support.js`. Without it nothing renders.|
|`implementation-plan.md`|The eight-phase plan (v3). One phase per PR.|

Both design files are byte-for-byte copies of the originals. Do not edit them.

## How to open the design

1. Clone the repository and keep `P2P_Dashboard_Revised.dc.html` and `support.js` in the same folder.
2. Open `docs/design/P2P_Dashboard_Revised.dc.html` in a browser by its `file://` URL.
3. The machine needs network access: the prototype fetches React and Babel from `unpkg.com` and IBM Plex plus Material Symbols Rounded from Google Fonts.

The prototype opens on the Review queue screen. Use the sidebar (a top row on narrow windows) to move between screens. To see the case detail, open Cases and click a table row.

The sample `rules()` and `mx()` functions in the script recompute tolerances in JavaScript. They are prototype logic and must not be ported; the UI shows domain results.

## Screens

|Screen|URL path|Query params|
|---|---|---|
|Overview|`/`| |
|Process invoice|`/process`| |
|Review queue|`/review`|`case=<case_id>`|
|Cases|`/cases`|`case=<case_id>`, `status=<review, approved, rejected, request_info>` (absent means all)|
|Case detail|`/cases?case=<case_id>`| |
|About|`/about`| |

## Tokens

`src/p2p/ui/theme.py` constants and CSS variables use the same names.

|Token|Value|
|---|---|
|ink / ink-2 / muted|`#131a26` / `#475264` / `#5b6576`|
|page / surface / surface-subtle / line / line-soft|`#f1f3f6` / `#ffffff` / `#f8f9fb` / `#e2e5ea` / `#edf0f3`|
|blue / link / blue-soft|`#2f6fed` / `#1d4fc4` / `#e8effd`|
|navy / navy-card / sidebar text / sidebar muted|`#0e1525` / `#151e32` / `#dce4f2` / `#a7b3c9`|
|Severity chip fg/bg|CRITICAL `#a1122b`/`#fce7eb`, MATERIAL `#a6380f`/`#fcebe3`, WARNING `#7f4d00`/`#fcf1dc`, INFO `#33507a`/`#eaf0f8`, none `#475264`/`#eef0f3`|
|Severity bar|CRITICAL `#d23a50`, MATERIAL `#e0703a`, WARNING `#d9a032`, INFO `#7f9cc4`|
|Status pill fg/bg/dot|review `#7f4d00`/`#fcf1dc`/`#c98512`, approved `#14653f`/`#e3f3ea`/`#1f9a5e`, rejected `#a1122b`/`#fce7eb`/`#d23a50`, request_info `#1d4fc4`/`#e8effd`/`#2f6fed`, escalated and recovery = review colours, processing `#475264`/`#eef0f3`/`#5b6576`|
|Radius|cards 12px, buttons and inputs 8px, chips 999px|
|Type|body 14px, minimum 12px, h1 28px/700, section h2 16px/600, mono `"IBM Plex Mono", ui-monospace, monospace`|

## Labels

### Actions and outcomes

|Value|Action label|Outcome label|
|---|---|---|
|APPROVE|Approve|Approved|
|REJECT|Reject|Rejected|
|REQUEST_INFO|Request info|Request info|
|ESCALATE|Escalate|Escalated|

### Exceptions

|Type|Label|
|---|---|
|PRICE_VARIANCE|Price variance|
|QUANTITY_VARIANCE|Quantity variance|
|QUANTITY_EXCEEDS_RECEIPT|Quantity exceeds receipt|
|QUANTITY_EXCEEDS_ORDER|Quantity exceeds order|
|MISSING_PO|Missing PO|
|MISSING_GRN|Missing goods receipt|
|DUPLICATE_INVOICE|Duplicate invoice|
|SUPPLIER_MISMATCH|Supplier mismatch|
|SUPPLIER_INELIGIBLE|Supplier ineligible|
|PO_NOT_OPEN|PO not open|
|CURRENCY_MISMATCH|Currency mismatch|
|UNMATCHED_LINE|Unmatched line|
|ARITHMETIC_INCONSISTENCY|Arithmetic inconsistency|
|HIGH_VALUE|High-value invoice|
|none|Exact match|

### Audit events

From the `EVT` map in the design script (line 757).

|Event type|Label|
|---|---|
|CASE_CREATED|Case created|
|INVOICE_VALIDATED|Invoice validated|
|ERP_CONTEXT_LOADED|ERP context loaded|
|MATCH_COMPLETED|Three-way match completed|
|EXCEPTION_IDENTIFIED|Exception identified|
|INVESTIGATION_COMPLETED|Investigation completed|
|RESOLUTION_PROPOSED|Resolution proposed|
|CRITIQUE_FAILED|Critique failed|
|RESOLUTION_REVISED|Resolution revised|
|CRITIQUE_PASSED|Critique passed|
|POLICY_CHECK_COMPLETED|Policy check completed|
|HUMAN_REVIEW_REQUESTED|Human review requested|
|HUMAN_DECISION_RECEIVED|Human decision received|
|CASE_COMPLETED|Case completed|

### Display status

`display_status(case) -> StatusView(key, label)`; the first matching row wins.

|#|Condition|Key|Label|
|---|---|---|---|
|1|`last_error` set and status not COMPLETED (from Phase 5)|`recovery`|Needs recovery|
|2|`busy` true (from Phase 6)|`processing`|Processing|
|3|status PENDING_REVIEW|`review`|Awaiting review|
|4|status COMPLETED|`approved` / `rejected` / `request_info` / `escalated`|`<outcome label> · Autonomous` when `outcome.source == "AUTONOMOUS"`, else `<outcome label> · Human`|
|5|anything else|`recovery`|Needs recovery|

## Real scenario outcomes

The prototype uses sample data. The build shows what the domain produces, which differs: scenario 02 is WARNING (the design shows INFO), 05 and 06 are MATERIAL (the design shows CRITICAL).

Measured on `main` @ `4d4abf8` with fresh storage per scenario, in demo mode:

|Scenario|Discrepancies|AI result|Policy `triggered_rules`|Status|Events|
|---|---|---|---|---|---|
|01 exact|none|not invoked|three_way_match|COMPLETED, APPROVE, AUTONOMOUS|6|
|02 small price|PRICE_VARIANCE WARNING|not invoked|three_way_match|COMPLETED, APPROVE, AUTONOMOUS|7|
|03 large price|PRICE_VARIANCE MATERIAL|APPROVE 0.95 rejected by Critic (−0.20), revised to REQUEST_INFO 0.88; `reflection_count` 1|deterministic_eligibility, material_variance, confidence.auto_resolve|PENDING_REVIEW|12|
|04 quantity|QUANTITY_VARIANCE MATERIAL|REQUEST_INFO 0.86|deterministic_eligibility, material_variance, confidence.auto_resolve|PENDING_REVIEW|10|
|05 exceeds receipt|QUANTITY_EXCEEDS_RECEIPT MATERIAL|REQUEST_INFO 0.91|deterministic_eligibility, hard_financial_control|PENDING_REVIEW|10|
|06 missing PO|MISSING_PO MATERIAL|ESCALATE 0.72|deterministic_eligibility, allowed_action, missing_po.require_human_review, confidence.auto_resolve|PENDING_REVIEW|10|
|07 missing GRN|MISSING_GRN MATERIAL|REQUEST_INFO 0.82|deterministic_eligibility, missing_receipt.require_human_review, confidence.auto_resolve|PENDING_REVIEW|10|
|08 duplicate|DUPLICATE_INVOICE CRITICAL|not invoked (before Phase 3: REJECT 0.99)|duplicate_invoice.auto_reject|COMPLETED, REJECT, AUTONOMOUS|7 (10 before Phase 3)|
|09 supplier mismatch|SUPPLIER_MISMATCH CRITICAL|ESCALATE 0.96|deterministic_eligibility, allowed_action, hard_financial_control|PENDING_REVIEW|10|
|10 high value|none|not invoked|risk.human_review_invoice_value|PENDING_REVIEW|6|

Running all ten in order in one database: still 3 completed and 7 pending; 04 becomes QUANTITY_EXCEEDS_ORDER + QUANTITY_EXCEEDS_RECEIPT, and 08 and 09 gain those two as well, because the scenarios share PO capacity.

## Reference screenshots

The images are not on `main`. They live on the orphan branch [`screenshots`](https://github.com/Mzoratto/autonomous-p2p-exception-handler/tree/screenshots) under `docs/design/reference/`. Per-phase build screenshots go to `docs/design/screenshots/phase-<n>/` on the same branch.

Capture settings: full page, device scale factor 2, viewport 1440 × 900, 900 × 900 and 390 × 844 CSS pixels, WebP quality 82. The prototype's sidebar is one viewport tall, so in a full-page capture it ends before the bottom of a longer page. "Case detail" is the first row of the Cases table (`CASE-E15A7C8830`, a pending case, so it includes the decision panel).

|Screen|1440 px|900 px|390 px|
|---|---|---|---|
|Overview|[overview-1440.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/overview-1440.webp?raw=true)|[overview-900.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/overview-900.webp?raw=true)|[overview-390.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/overview-390.webp?raw=true)|
|Process invoice|[process-1440.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/process-1440.webp?raw=true)|[process-900.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/process-900.webp?raw=true)|[process-390.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/process-390.webp?raw=true)|
|Review queue|[review-1440.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/review-1440.webp?raw=true)|[review-900.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/review-900.webp?raw=true)|[review-390.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/review-390.webp?raw=true)|
|Cases|[cases-1440.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/cases-1440.webp?raw=true)|[cases-900.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/cases-900.webp?raw=true)|[cases-390.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/cases-390.webp?raw=true)|
|Case detail|[case-1440.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/case-1440.webp?raw=true)|[case-900.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/case-900.webp?raw=true)|[case-390.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/case-390.webp?raw=true)|
|About|[about-1440.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/about-1440.webp?raw=true)|[about-900.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/about-900.webp?raw=true)|[about-390.webp](https://github.com/Mzoratto/autonomous-p2p-exception-handler/blob/screenshots/docs/design/reference/about-390.webp?raw=true)|
