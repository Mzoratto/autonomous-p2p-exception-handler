"""Pure display helpers: labels, display status, value formatting and case view-models."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from decimal import ROUND_HALF_EVEN, Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from p2p.domain.casefacts import HIGH_VALUE_RULE, primary_exception
from p2p.domain.config import BusinessRules

ACTION_LABELS = {
    "APPROVE": "Approve",
    "REJECT": "Reject",
    "REQUEST_INFO": "Request info",
    "ESCALATE": "Escalate",
}

OUTCOME_LABELS = {
    "APPROVE": "Approved",
    "REJECT": "Rejected",
    "REQUEST_INFO": "Request info",
    "ESCALATE": "Escalated",
}

# Status-pill key for each decision action.
ACTION_STATUS_KEYS = {
    "APPROVE": "approved",
    "REJECT": "rejected",
    "REQUEST_INFO": "request_info",
    "ESCALATE": "escalated",
}

EXCEPTION_LABELS = {
    "PRICE_VARIANCE": "Price variance",
    "QUANTITY_VARIANCE": "Quantity variance",
    "QUANTITY_EXCEEDS_RECEIPT": "Quantity exceeds receipt",
    "QUANTITY_EXCEEDS_ORDER": "Quantity exceeds order",
    "MISSING_PO": "Missing PO",
    "MISSING_GRN": "Missing goods receipt",
    "DUPLICATE_INVOICE": "Duplicate invoice",
    "SUPPLIER_MISMATCH": "Supplier mismatch",
    "SUPPLIER_INELIGIBLE": "Supplier ineligible",
    "PO_NOT_OPEN": "PO not open",
    "CURRENCY_MISMATCH": "Currency mismatch",
    "UNMATCHED_LINE": "Unmatched line",
    "ARITHMETIC_INCONSISTENCY": "Arithmetic inconsistency",
    "HIGH_VALUE": "High-value invoice",
}
NO_EXCEPTION_LABEL = "Exact match"

EVENT_LABELS = {
    "CASE_CREATED": "Case created",
    "INVOICE_VALIDATED": "Invoice validated",
    "ERP_CONTEXT_LOADED": "ERP context loaded",
    "MATCH_COMPLETED": "Three-way match completed",
    "EXCEPTION_IDENTIFIED": "Exception identified",
    "INVESTIGATION_COMPLETED": "Investigation completed",
    "RESOLUTION_PROPOSED": "Resolution proposed",
    "CRITIQUE_FAILED": "Critique failed",
    "RESOLUTION_REVISED": "Resolution revised",
    "CRITIQUE_PASSED": "Critique passed",
    "POLICY_CHECK_COMPLETED": "Policy check completed",
    "HUMAN_REVIEW_REQUESTED": "Human review requested",
    "HUMAN_DECISION_RECEIVED": "Human decision received",
    "CASE_COMPLETED": "Case completed",
}

CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}
CENT = Decimal("0.01")


def _humanize(value: str) -> str:
    return value.replace("_", " ").capitalize()


def label_action(action: str) -> str:
    return ACTION_LABELS.get(action, _humanize(action))


def label_exception(exception_type: str | None) -> str:
    if not exception_type:
        return NO_EXCEPTION_LABEL
    return EXCEPTION_LABELS.get(exception_type, _humanize(exception_type))


def label_event(event_type: str) -> str:
    return EVENT_LABELS.get(event_type, _humanize(event_type))


@dataclass(frozen=True)
class StatusView:
    key: str
    label: str


def display_status(case: dict) -> StatusView:
    """Accepts a get_case() view or a query_cases() row."""
    status = case.get("status")
    if case.get("last_error") and status != "COMPLETED":
        return StatusView("recovery", "Needs recovery")
    if case.get("busy"):
        return StatusView("processing", "Processing")
    if status == "PENDING_REVIEW":
        return StatusView("review", "Awaiting review")
    if status == "COMPLETED":
        outcome = (case.get("state") or {}).get("outcome") or {
            "action": case.get("outcome_action"),
            "source": case.get("outcome_source"),
        }
        action = outcome.get("action")
        if action in OUTCOME_LABELS:
            source = "Autonomous" if outcome.get("source") == "AUTONOMOUS" else "Human"
            return StatusView(ACTION_STATUS_KEYS[action], f"{OUTCOME_LABELS[action]} · {source}")
    return StatusView("recovery", "Needs recovery")


def format_money(amount: object, currency: str) -> str:
    value = Decimal(str(amount)).quantize(CENT, rounding=ROUND_HALF_EVEN)
    code = currency.upper()
    prefix = CURRENCY_SYMBOLS.get(code, f"{code} ")
    sign = "-" if value < 0 else ""
    return f"{sign}{prefix}{abs(value):,.2f}"


def format_duration(seconds: float) -> str:
    total = int(seconds)
    if total < 60:
        return "just now"
    minutes = total // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def _field_path(loc: tuple) -> str:
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else str(part)
    return path or "invoice"


def validation_rows(exc: ValidationError) -> list[tuple[str, str]]:
    rows = []
    for error in exc.errors():
        name = "JSON" if error["type"] == "json_invalid" else _field_path(tuple(error["loc"]))
        rows.append((name, error["msg"].removeprefix("Value error, ")))
    return rows


# --- Time -----------------------------------------------------------------------------------


def format_delta(seconds: float) -> str:
    if seconds < 60:
        return f"+{seconds:.2f} s"
    return "+" + format_duration(seconds)


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO timestamp; a naive value is UTC."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def local_zone(name: str | None) -> tuple[tzinfo, str]:
    """The browser's zone, or UTC plus a visible " UTC" suffix when it is missing or unknown."""
    if name:
        try:
            return ZoneInfo(name), ""
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return UTC, " UTC"


def _relative(now: datetime, moment: datetime) -> str:
    text = format_duration((now - moment).total_seconds())
    return text if text == "just now" else f"{text} ago"


# --- Shared ---------------------------------------------------------------------------------


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _number(value: object) -> str:
    text = format(_decimal(value).normalize(), "f")
    return text


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _types(state: dict) -> set[str]:
    return {item["type"] for item in (state.get("match_result") or {}).get("discrepancies", [])}


def _events(case: dict, *event_types: str) -> list[dict]:
    return [event for event in case.get("audit", []) if event["event_type"] in event_types]


@dataclass(frozen=True)
class Chip:
    key: str
    text: str


def severity_chip(severity: str | None) -> Chip:
    return Chip((severity or "none").lower(), severity or "—")


# --- Header ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class MetaItem:
    label: str
    value: str
    chip: Chip | None = None
    extra: str = ""


@dataclass(frozen=True)
class HeaderView:
    case_id: str
    title: str
    status: StatusView
    meta: list[MetaItem]


def case_header(
    case: dict, now: datetime, reviewer: str, zone: tuple[tzinfo, str] = (UTC, " UTC")
) -> HeaderView:
    invoice, state = case["invoice"], case.get("state") or {}
    exception, severity, others = primary_exception(state)
    meta = [
        MetaItem("Amount", format_money(invoice["total"], invoice["currency"])),
        MetaItem("Purchase order", invoice.get("po_number") or "No PO"),
        MetaItem(
            "Exception",
            label_exception(exception) if state.get("match_result") else "Not checked",
            severity_chip(severity) if severity else None,
            f"+{others} more" if others else "",
        ),
    ]
    if case.get("status") == "PENDING_REVIEW":
        requested = _events(case, "HUMAN_REVIEW_REQUESTED")
        since = parse_timestamp(requested[-1]["timestamp"] if requested else case["updated_at"])
        meta += [
            MetaItem("Open for", format_duration((now - since).total_seconds())),
            MetaItem("Assigned to", reviewer),
        ]
    else:
        audit = case.get("audit") or []
        created = parse_timestamp(audit[0]["timestamp"] if audit else case["updated_at"])
        tz, suffix = zone
        review = state.get("human_review") or {}
        decided_by = (
            review.get("reviewer") or "Policy engine" if case.get("status") == "COMPLETED" else "—"
        )
        meta += [
            MetaItem("Created", created.astimezone(tz).strftime("%d %b %Y %H:%M") + suffix),
            MetaItem("Decided by", decided_by),
        ]
    return HeaderView(
        case_id=case["case_id"],
        title=f"{invoice['supplier_name']} · {invoice['invoice_id']}",
        status=display_status(case),
        meta=meta,
    )


# --- Three-way match ------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchTile:
    label: str
    state: str  # "ok", "fail" or "na"
    text: str


@dataclass(frozen=True)
class MatchRow:
    field: str
    invoice: str
    po: str
    receipt: str
    variance: str
    result: str  # "Within tolerance", "Exceeds tolerance" or "Cannot compare"
    tolerance: str


@dataclass(frozen=True)
class LineGrid:
    title: str
    rows: list[MatchRow]
    note: str = ""


@dataclass(frozen=True)
class DiscrepancyView:
    severity: Chip
    label: str
    explanation: str
    reference: str


@dataclass(frozen=True)
class MatchView:
    tiles: list[MatchTile]
    lines: list[LineGrid]
    discrepancies: list[DiscrepancyView]
    summary: str
    ran: bool = True


WITHIN, EXCEEDS, CANNOT = "Within tolerance", "Exceeds tolerance", "Cannot compare"


def _explained(discrepancies: list[dict], *kinds: str) -> str:
    return "; ".join(item["explanation"] for item in discrepancies if item["type"] in kinds)


def _tile(
    label: str, discrepancies: list[dict], kinds: tuple[str, ...], na: bool, ok: str, na_text: str
) -> MatchTile:
    failure = _explained(discrepancies, *kinds)
    if failure:
        return MatchTile(label, "fail", failure)
    if na:
        return MatchTile(label, "na", na_text)
    return MatchTile(label, "ok", ok)


def _variance_text(item: dict | None, currency: str, money: bool) -> str:
    if item is None or item.get("variance_absolute") is None:
        return "None recorded"
    absolute = _decimal(item["variance_absolute"])
    if money:
        amount = f"{absolute:+,.2f} {currency}"
    else:
        amount = f"{'+' if absolute > 0 else ''}{_number(absolute)} units"
    percent = item.get("variance_percent")
    if percent is None:
        return amount
    return f"{amount} ({_decimal(percent):+.1f}%)"


def _result(items: list[dict], cannot: bool) -> str:
    if any(item["severity"] in {"MATERIAL", "CRITICAL"} for item in items):
        return EXCEEDS
    return CANNOT if cannot else WITHIN


def match_view(state: dict, rules: BusinessRules) -> MatchView:
    invoice = state["invoice"] if isinstance(state.get("invoice"), dict) else {}
    erp = state.get("erp_context") or {}
    match = state.get("match_result") or {}
    if not match:
        # The run stopped before the matcher; nothing has been compared yet.
        return MatchView(tiles=[], lines=[], discrepancies=[], summary="", ran=False)
    discrepancies = match.get("discrepancies") or []
    types = {item["type"] for item in discrepancies}
    po = erp.get("purchase_order")
    no_po = "MISSING_PO" in types or po is None
    receipts = [
        receipt
        for receipt in erp.get("goods_receipts") or []
        if po and receipt["po_number"] == po["po_number"]
    ]
    currency = invoice.get("currency", "")

    tiles = [
        _tile(
            "Supplier",
            discrepancies,
            ("SUPPLIER_MISMATCH", "SUPPLIER_INELIGIBLE"),
            no_po,
            f"{invoice.get('supplier_id')} · matches {po['po_number'] if po else ''}".strip(),
            f"{invoice.get('supplier_id')} · no PO to compare",
        ),
        _tile(
            "Currency",
            discrepancies,
            ("CURRENCY_MISMATCH",),
            no_po,
            currency,
            f"{currency} · no PO to compare",
        ),
        _tile(
            "Purchase order",
            discrepancies,
            ("MISSING_PO", "PO_NOT_OPEN"),
            False,
            f"{po['po_number']} · {po['status']}" if po else "—",
            "",
        ),
        _tile(
            "Goods receipt",
            discrepancies,
            ("MISSING_GRN",),
            no_po,
            ", ".join(receipt["receipt_id"] for receipt in receipts) or "—",
            "n/a",
        ),
        _tile(
            "Duplicate check", discrepancies, ("DUPLICATE_INVOICE",), False, "No prior invoice", ""
        ),
    ]

    price = rules.price_variance
    price_tolerance = f"≤ {_number(price.auto_approve_percent)}% and ≤ {price.auto_approve_absolute:,.2f} per line"
    quantity_tolerance = (
        f"≤ {_number(rules.quantity_variance.tolerance_percent)}% vs received · ≤ ordered"
    )
    po_lines = {line["line_id"]: line for line in (po or {}).get("line_items", [])}
    lines = []
    for line in invoice.get("line_items", []):
        line_id = line["line_id"]
        on_line = [item for item in discrepancies if item.get("line_id") == line_id]
        unmatched = any(item["type"] == "UNMATCHED_LINE" for item in on_line)
        po_line = po_lines.get(line_id)
        received = sum(
            (
                _decimal(item["received_quantity"])
                for receipt in receipts
                for item in receipt["line_items"]
                if item["po_line_id"] == line_id
            ),
            Decimal("0"),
        )
        quantity_items = [
            item
            for item in on_line
            if item["type"]
            in {"QUANTITY_VARIANCE", "QUANTITY_EXCEEDS_RECEIPT", "QUANTITY_EXCEEDS_ORDER"}
        ]
        price_items = [item for item in on_line if item["type"] == "PRICE_VARIANCE"]
        price_result = _result(price_items, no_po or unmatched)
        rows = [
            MatchRow(
                "Quantity",
                _number(line["quantity"]),
                _number(po_line["ordered_quantity"]) if po_line else "—",
                _number(received) if receipts else ("—" if no_po else "Not received"),
                "; ".join(
                    _variance_text(item, currency, money=False)
                    for item in quantity_items
                    if item.get("variance_absolute") is not None
                )
                or "None recorded",
                _result(quantity_items, no_po or unmatched or "MISSING_GRN" in types),
                quantity_tolerance,
            ),
            MatchRow(
                "Unit price",
                format_money(line["unit_price"], currency),
                format_money(po_line["unit_price"], currency) if po_line else "—",
                "—",
                _variance_text(price_items[0] if price_items else None, currency, money=True),
                price_result,
                price_tolerance,
            ),
            MatchRow(
                "Line total",
                format_money(line["line_total"], currency),
                format_money(_decimal(po_line["unit_price"]) * _decimal(line["quantity"]), currency)
                if po_line
                else "—",
                "—",
                "—",
                price_result,
                f"Checked against {price.auto_approve_absolute:,.2f} per line",
            ),
        ]
        reserved = _decimal((erp.get("reserved_quantities") or {}).get(line_id, 0))
        note = (
            f"{_number(reserved)} units are already reserved by other open cases; "
            "the matcher compares against what remains."
            if reserved > 0
            else ""
        )
        lines.append(LineGrid(f"Line {line_id} · {line['description']}", rows, note))

    return MatchView(
        tiles=tiles,
        lines=lines,
        discrepancies=[
            DiscrepancyView(
                severity_chip(item["severity"]),
                label_exception(item["type"]),
                item["explanation"],
                f"{item['discrepancy_id']} · three_way_matcher",
            )
            for item in discrepancies
        ],
        summary=match.get("summary", ""),
    )


# --- AI reasoning ---------------------------------------------------------------------------


@dataclass
class Attempt:
    number: int
    action: str
    confidence: float
    rationale: str
    information_request: str | None
    verdict: str | None = None  # "passed", "failed" or None when no critique was recorded
    issues: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    adjustment: float | None = None
    adjusted: float | None = None


@dataclass(frozen=True)
class Investigation:
    root_cause: str
    missing: str
    action: str
    confidence: float


@dataclass(frozen=True)
class AiStoryView:
    invoked: bool
    not_invoked_text: str = ""
    title: str = ""
    investigation: Investigation | None = None
    attempts: list[Attempt] = field(default_factory=list)
    diff: list[tuple[str, str, str]] = field(default_factory=list)
    model_error: str | None = None

    @property
    def rejected_attempt(self) -> bool:
        return any(attempt.verdict == "failed" for attempt in self.attempts)


def _attempts(case: dict) -> list[Attempt]:
    attempts: list[Attempt] = []
    for event in case.get("audit") or []:
        data = event["data"]
        if event["event_type"] in {"RESOLUTION_PROPOSED", "RESOLUTION_REVISED"}:
            attempts.append(
                Attempt(
                    number=int(data.get("attempt", len(attempts))) + 1,
                    action=data["action"],
                    confidence=float(data["confidence"]),
                    rationale=data.get("rationale", ""),
                    information_request=data.get("information_request"),
                )
            )
        elif event["event_type"] in {"CRITIQUE_PASSED", "CRITIQUE_FAILED"} and attempts:
            current = attempts[-1]
            current.verdict = "passed" if event["event_type"] == "CRITIQUE_PASSED" else "failed"
            current.issues = list(data.get("issues") or [])
            current.changes = list(data.get("recommended_changes") or [])
            current.adjustment = data.get("confidence_adjustment")
            current.adjusted = data.get("adjusted_confidence")
    return attempts


def _confidence_text(attempt: Attempt) -> str:
    if attempt.adjusted is not None and attempt.adjusted != attempt.confidence:
        return f"{attempt.confidence:.2f} ({attempt.adjusted:.2f} after Critic)"
    return f"{attempt.confidence:.2f}"


def ai_story(case: dict, rules: BusinessRules) -> AiStoryView:
    state = case.get("state") or {}
    if not state.get("investigation"):
        types = _types(state)
        triggered = (state.get("policy_result") or {}).get("triggered_rules") or []
        if "DUPLICATE_INVOICE" in types:
            text = "Duplicate invoices are rejected deterministically. No interpretation is needed."
        elif not types and HIGH_VALUE_RULE in triggered:
            threshold = f"{rules.risk.human_review_invoice_value:,.2f}"
            text = (
                "The invoice matched exactly. Policy paused it because the value is at or above "
                f"{threshold}."
            )
        else:
            text = "Nothing ambiguous to interpret, so the workflow went straight to policy."
        return AiStoryView(invoked=False, not_invoked_text=text)

    investigation = state["investigation"]
    attempts = _attempts(case)
    resolution = state.get("resolution")
    if not attempts and resolution:
        critique = state.get("critique") or {}
        attempts = [
            Attempt(
                number=1,
                action=resolution["action"],
                confidence=float(resolution["confidence"]),
                rationale=resolution.get("rationale", ""),
                information_request=resolution.get("information_request"),
                verdict=None if not critique else ("passed" if critique.get("valid") else "failed"),
                issues=list(critique.get("issues") or []),
                changes=list(critique.get("recommended_changes") or []),
                adjustment=critique.get("confidence_adjustment"),
            )
        ]
    revised = any(
        attempt.verdict == "failed" and index < len(attempts) - 1
        for index, attempt in enumerate(attempts)
    )
    if state.get("reflection_exhausted"):
        title = "Reflection limit reached without a valid recommendation"
    elif revised:
        title = "The Critic rejected the first proposal and forced a revision"
    else:
        title = "Proposal accepted by the Critic on the first attempt"

    diff: list[tuple[str, str, str]] = []
    if len(attempts) >= 2:
        first, last = attempts[0], attempts[-1]
        for name, before, after in [
            ("Action", label_action(first.action), label_action(last.action)),
            ("Confidence", _confidence_text(first), _confidence_text(last)),
            (
                "Information request",
                first.information_request or "None",
                last.information_request or "None",
            ),
            ("Rationale", first.rationale, last.rationale),
        ]:
            if before != after:
                diff.append((name, before, after))

    return AiStoryView(
        invoked=True,
        title=title,
        investigation=Investigation(
            root_cause=investigation["root_cause"],
            missing="; ".join(investigation.get("missing_information") or []) or "None",
            action=label_action(investigation["suggested_action"]),
            confidence=float(investigation["confidence"]),
        ),
        attempts=attempts,
        diff=diff,
        model_error=state.get("model_error"),
    )


def default_tab(story: AiStoryView) -> str:
    return "ai" if story.rejected_attempt else "match"


# --- Policy ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyView:
    verdict_key: str  # "approved", "rejected" or "review"
    verdict: str
    checks: list[dict]
    reasons: list[str]
    caption: str = ""

    @property
    def failed(self) -> int:
        return sum(1 for check in self.checks if check["passed"] is False)


def policy_view(state: dict) -> PolicyView | None:
    policy = state.get("policy_result")
    if not policy:
        return None
    checks = policy.get("checks") or []
    failed = sum(1 for check in checks if check["passed"] is False)
    if policy.get("enforced_action") == "REJECT" and policy.get("permitted"):
        key, verdict = "rejected", "Duplicate control enforced rejection without AI."
    elif policy.get("requires_human_review"):
        key = "review"
        verdict = (
            f"{failed} of {len(checks)} rules failed. Autonomous action not permitted; "
            "routed to human review."
            if checks
            else "Autonomous action not permitted; routed to human review."
        )
    else:
        key, verdict = "approved", "All rules passed. Autonomous approval permitted."
    caption = "" if checks else "Checklist not recorded for cases created before this version."
    return PolicyView(key, verdict, checks, list(policy.get("reasons") or []), caption)


# --- Audit timeline -------------------------------------------------------------------------


@dataclass(frozen=True)
class TimelineEvent:
    label: str
    actor: str
    time: str
    relative: str
    delta: str
    dot: str
    details: list[tuple[str, str]]


EVENT_DOTS = {
    "CRITIQUE_FAILED": "#d23a50",
    "HUMAN_REVIEW_REQUESTED": "#c98512",
    "EXCEPTION_IDENTIFIED": "#c98512",
    "HUMAN_DECISION_RECEIVED": "#2f6fed",
}
HASH_KEYS = {"rules_sha256", "evidence_sha256"}


def _detail(key: str, value: object) -> str:
    if value is None:
        return "—"
    if key in HASH_KEYS and isinstance(value, str):
        return value[:12] + "…"
    if isinstance(value, list) and all(not isinstance(item, (dict, list)) for item in value):
        return ", ".join(str(item) for item in value) or "—"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, default=str)
        return text if len(text) <= 300 else text[:300] + "…"
    return str(value)


def timeline(case: dict, zone: tuple[tzinfo, str], now: datetime) -> list[TimelineEvent]:
    tz, suffix = zone
    events = []
    previous: datetime | None = None
    for event in case.get("audit") or []:
        moment = parse_timestamp(event["timestamp"])
        events.append(
            TimelineEvent(
                label=label_event(event["event_type"]),
                actor=event["actor"],
                time=moment.astimezone(tz).strftime("%H:%M:%S") + suffix,
                relative=_relative(now, moment),
                delta="start"
                if previous is None
                else format_delta((moment - previous).total_seconds()),
                dot=EVENT_DOTS.get(event["event_type"], "#1f9a5e"),
                details=[
                    (key, _detail(key, value)) for key, value in sorted(event["data"].items())
                ],
            )
        )
        previous = moment
    return events


# --- Outcome --------------------------------------------------------------------------------

SOURCE_LABELS = {
    "AUTONOMOUS": "Autonomous",
    "HUMAN_APPROVED": "Human · accepted AI recommendation",
    "HUMAN_OVERRIDDEN": "Human · changed AI recommendation",
}


def waiting_labels(waiting: list[dict]) -> list[str]:
    """Chart labels: the invoice id, plus the case id when two waiting cases share an invoice id."""
    counts: dict[str, int] = {}
    for item in waiting:
        counts[item["invoice_id"]] = counts.get(item["invoice_id"], 0) + 1
    return [
        item["invoice_id"]
        if counts[item["invoice_id"]] == 1
        else f"{item['invoice_id']} · {item['case_id']}"
        for item in waiting
    ]


def outcome_view(case: dict) -> list[tuple[str, str]]:
    state = case.get("state") or {}
    outcome = state.get("outcome")
    if not outcome:
        return []
    resolution = state.get("resolution")
    if resolution:
        recommendation = label_action(resolution["action"])
        if outcome["source"] == "HUMAN_OVERRIDDEN":
            recommendation += " · overridden"
        source = SOURCE_LABELS.get(outcome["source"], _humanize(outcome["source"]))
    else:
        recommendation = "AI not invoked"
        source = "Autonomous" if outcome["source"] == "AUTONOMOUS" else "Human"
    review = state.get("human_review") or {}
    note = review.get("comment") or ("—" if review else outcome.get("rationale") or "—")
    return [
        ("Outcome", OUTCOME_LABELS.get(outcome["action"], _humanize(outcome["action"]))),
        ("Decision source", source),
        ("AI recommendation", recommendation),
        ("Note", note),
    ]


def tab_labels(
    match: MatchView, story: AiStoryView, policy: PolicyView | None, events: int
) -> dict[str, str]:
    exceptions = len(match.discrepancies)
    if policy is None or not policy.checks:
        policy_label = "Policy"
    else:
        policy_label = (
            f"Policy · {policy.failed} failed" if policy.failed else "Policy · All passed"
        )
    if not match.ran:
        match_label = "Three-way match · Not run"
    elif exceptions:
        match_label = f"Three-way match · {_plural(exceptions, 'exception')}"
    else:
        match_label = "Three-way match · Matched"
    return {
        "match": match_label,
        "ai": f"AI reasoning · {_plural(len(story.attempts), 'attempt')}"
        if story.invoked
        else "AI reasoning · Not used",
        "policy": policy_label,
        "timeline": f"Audit timeline · {events}",
    }
