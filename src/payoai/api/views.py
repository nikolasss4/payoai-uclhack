"""Read models for the app's screens. Each query is scoped to the caller's team and changes nothing.

Money is always formatted here, so the browser never calculates or formats an amount.
Full account numbers never leave through these views. Only the audited reveal routes return them.
"""

import re
from collections import defaultdict

from payoai.db import fetch_all, fetch_one
from payoai.domain.money import format_minor
from payoai.trace import redact


def money(minor, asset) -> str | None:
    return None if minor is None or not asset else format_minor(int(minor), asset)


def iso(value) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else (str(value) if value is not None else None)


def words(code: str | None) -> str:
    text = str(code or "").replace("_", " ").lower()
    return text[:1].upper() + text[1:]


RAIL = {"FIAT_BANK": "Bank", "CRYPTO": "Crypto"}
CHAINS = {84532: "Base Sepolia (testnet)", 11155111: "Sepolia (testnet)", 8453: "Base", 1: "Ethereum"}


def chain_name(chain_id) -> str | None:
    return None if chain_id is None else CHAINS.get(int(chain_id), f"Chain {chain_id}")


def short(value: str | None) -> str | None:
    if not value or len(value) <= 14:
        return value
    return f"{value[:6]}…{value[-4:]}"


# Records that a screen can open. Each reference becomes {kind, id, label}, or nothing if it isn't in the team.

PREFIX_TYPES = {"obl": "obligation", "pay": "payment_intent", "dest": "payment_destination", "cp": "counterparty", "exp": "expense", "apr": "approval", "att": "payment_attempt"}


def type_of_id(record_id: str) -> str | None:
    return PREFIX_TYPES.get(str(record_id).split("_", 1)[0])


def resolve_records(conn, team_id: str, refs) -> dict:
    """Batch lookup, one query per record type, so a long list doesn't make a query per row."""
    wanted: dict[str, set] = defaultdict(set)
    for entity_type, entity_id in refs:
        if entity_type and entity_id:
            wanted[entity_type].add(entity_id)
    out: dict = {}
    attempt_intent = {}
    if wanted["payment_attempt"]:
        for row in fetch_all(conn, "SELECT id, intent_id FROM payment_attempts WHERE team_id = %s AND id = ANY(%s)", (team_id, list(wanted["payment_attempt"]))):
            attempt_intent[row["id"]] = row["intent_id"]
            wanted["payment_intent"].add(row["intent_id"])
    approval_intent = {}
    if wanted["approval"]:
        for row in fetch_all(conn, "SELECT id, intent_id FROM approvals WHERE team_id = %s AND id = ANY(%s)", (team_id, list(wanted["approval"]))):
            approval_intent[row["id"]] = row["intent_id"]
            wanted["payment_intent"].add(row["intent_id"])
    if wanted["obligation"]:
        rows = fetch_all(conn, "SELECT o.id, o.invoice_reference, o.amount_minor, o.asset, c.display_name FROM obligations o LEFT JOIN counterparties c ON c.id = o.counterparty_id WHERE o.team_id = %s AND o.id = ANY(%s)", (team_id, list(wanted["obligation"])))
        for r in rows:
            out[("obligation", r["id"])] = {"kind": "bill", "id": r["id"], "label": " · ".join(x for x in (r["display_name"] or "Unknown supplier", r["invoice_reference"] or money(r["amount_minor"], r["asset"])) if x)}
    if wanted["payment_intent"]:
        rows = fetch_all(conn, "SELECT p.id, p.amount_minor, p.asset, c.display_name FROM payment_intents p JOIN counterparties c ON c.id = p.counterparty_id WHERE p.team_id = %s AND p.id = ANY(%s)", (team_id, list(wanted["payment_intent"])))
        for r in rows:
            out[("payment_intent", r["id"])] = {"kind": "payment", "id": r["id"], "label": f"{r['display_name']} · {money(r['amount_minor'], r['asset'])}"}
    if wanted["payment_destination"]:
        rows = fetch_all(conn, "SELECT d.id, d.masked_display, c.display_name FROM payment_destinations d JOIN counterparties c ON c.id = d.counterparty_id WHERE d.team_id = %s AND d.id = ANY(%s)", (team_id, list(wanted["payment_destination"])))
        for r in rows:
            out[("payment_destination", r["id"])] = {"kind": "dest", "id": r["id"], "label": f"{r['display_name']} · {r['masked_display']}"}
    if wanted["expense"]:
        rows = fetch_all(
            conn,
            "SELECT DISTINCT ON (e.expense_id) e.expense_id, e.gross_minor, e.asset, c.display_name FROM expense_versions e LEFT JOIN counterparties c ON c.id = e.counterparty_id WHERE e.team_id = %s AND e.expense_id = ANY(%s) ORDER BY e.expense_id, e.version DESC",
            (team_id, list(wanted["expense"])),
        )
        for r in rows:
            out[("expense", r["expense_id"])] = {"kind": "expense", "id": r["expense_id"], "label": " · ".join(x for x in (r["display_name"] or "Expense", money(r["gross_minor"], r["asset"])) if x)}
    if wanted["counterparty"]:
        for r in fetch_all(conn, "SELECT id, display_name FROM counterparties WHERE team_id = %s AND id = ANY(%s)", (team_id, list(wanted["counterparty"]))):
            out[("counterparty", r["id"])] = {"kind": "contact", "id": r["id"], "label": r["display_name"]}
    for attempt_id, intent_id in attempt_intent.items():
        if ("payment_intent", intent_id) in out:
            out[("payment_attempt", attempt_id)] = out[("payment_intent", intent_id)]
    for approval_id, intent_id in approval_intent.items():
        if ("payment_intent", intent_id) in out:
            out[("approval", approval_id)] = {**out[("payment_intent", intent_id)], "kind": "approval", "id": approval_id}
    return out


def cite(conn, team_id: str, record_ids) -> list[dict]:
    refs = [(type_of_id(i), i) for i in record_ids or []]
    found = resolve_records(conn, team_id, refs)
    return [found[ref] for ref in refs if ref in found]


# Audit

EVENT_TEXT = {
    "ApprovalGranted": "Approval granted",
    "ApprovalRejected": "Approval rejected",
    "BillCorrectedManually": "Bill corrected by hand",
    "BillSuperseded": "Bill superseded",
    "CounterpartySaved": "Contact saved",
    "DestinationRevealed": "Full details viewed",
    "DestinationRevoked": "Destination revoked",
    "DestinationVerified": "Destination verified",
    "ExpenseCreated": "Expense recorded",
    "ExpenseUpdated": "Expense updated",
    "ExternalStatusObserved": "Status read from the source",
    "MandateDrafted": "Mandate drafted",
    "MemoryObservation": "Preference learned",
    "OrderPlaced": "Order placed",
    "PaymentIntentPrepared": "Payment prepared",
    "PaymentManuallyResolved": "Payment resolved by hand",
    "PaymentStoppedBeforeSend": "Payment stopped before sending",
    "PaymentsPaused": "Payments paused",
    "PaymentsResumed": "Payments resumed",
    "PolicyDecisionRecorded": "Decision recorded",
    "ProviderSubmitAttempted": "Payment submitted",
    "ProviderSubmitResult": "Provider answered the submit",
    "PurchaseApproved": "Purchase approved",
}


def event_text(event_type: str, data: dict) -> str:
    if event_type == "AccountConnected":
        return f"Account added: {(data or {}).get('name', 'an account')}"
    if event_type == "AccountDisconnected":
        return f"Account removed: {(data or {}).get('name', 'an account')}"
    if event_type == "AutoModeChanged":
        return "Auto Mode turned on" if (data or {}).get("on") else "Auto Mode turned off"
    if event_type == "PaymentManuallyResolved" and (data or {}).get("outcome"):
        return f"Payment resolved by hand: {words(data['outcome'])}"
    if event_type == "PolicyDecisionRecorded" and (data or {}).get("decision"):
        return f"Decision recorded: {words(data['decision'])}"
    return EVENT_TEXT.get(event_type) or words(re.sub(r"(?<!^)(?=[A-Z])", "_", event_type))


def actor_name(actor_type: str, actor_id: str | None, user_name: str | None) -> str:
    if actor_type == "USER":
        return user_name or "Unknown person"
    if actor_type == "AGENT":
        return "PayoAI agent"
    if actor_type == "PROVIDER":
        return words(actor_id) if actor_id else "Provider"
    return "System"


def audit_feed(conn, team_id: str, actor: str | None, limit: int) -> dict:
    rows = fetch_all(
        conn,
        """
        SELECT e.id, e.actor_type, e.actor_id, e.event_type, e.entity_type, e.entity_id, e.data, e.created_at, u.name AS user_name
        FROM audit_events e LEFT JOIN users u ON u.id = e.actor_id
        WHERE e.team_id = %s AND (%s::text IS NULL OR e.actor_type = %s) ORDER BY e.id DESC LIMIT %s
        """,
        (team_id, actor, actor, limit),
    )
    total = fetch_one(conn, "SELECT count(*) AS n FROM audit_events WHERE team_id = %s AND (%s::text IS NULL OR actor_type = %s)", (team_id, actor, actor))["n"]
    records = resolve_records(conn, team_id, [(r["entity_type"], r["entity_id"]) for r in rows])
    return {
        "total": total,
        "entries": [
            {
                "id": r["id"],
                "at": iso(r["created_at"]),
                "actor_type": r["actor_type"],
                "who": actor_name(r["actor_type"], r["actor_id"], r["user_name"]),
                "what": event_text(r["event_type"], r["data"]),
                "event_type": r["event_type"],
                "record": records.get((r["entity_type"], r["entity_id"])),
            }
            for r in rows
        ],
    }


# Turns and Runs in the audit. A turn holds its own steps and the steps of every run it started.
# A run nobody started from chat (a timer, a webhook, a tap on a card) is its own entry.

RUN_ACTOR = {"TAP": "USER", "TIMER": "SYSTEM", "WEBHOOK": "SYSTEM", "SYSTEM": "SYSTEM", "CHAT": "AGENT", "CONFIRMATION": "AGENT", "EVAL": "SYSTEM"}


def _duration_ms(start, end) -> int | None:
    return None if start is None or end is None else int((end - start).total_seconds() * 1000)


def _turn_entries(conn, team_id: str, actor: str | None, limit: int) -> tuple[list[dict], int]:
    """Turns are asked by a person and answered by the agent, so they show under both."""
    if actor not in (None, "USER", "AGENT"):
        return [], 0
    rows = fetch_all(
        conn,
        """
        SELECT t.id, t.seq, t.chat_id, t.user_input, t.final_reply, t.model, t.status, t.cost_micros, t.started_at, t.ended_at,
               c.title AS chat_title, u.name AS user_name, s.n AS steps, s.failed, s.tokens, s.cost, s.runs
        FROM turns t JOIN chats c ON c.id = t.chat_id LEFT JOIN users u ON u.id = c.user_id
        LEFT JOIN LATERAL (
            SELECT count(*) AS n, count(*) FILTER (WHERE st.status = 'FAILED') AS failed,
                   COALESCE(sum(COALESCE(st.input_tokens, 0) + COALESCE(st.output_tokens, 0)), 0) AS tokens,
                   COALESCE(sum(st.cost_micros), 0) AS cost, count(DISTINCT st.run_id) AS runs
            FROM trace_steps st
            WHERE st.turn_id = t.id OR st.run_id IN (SELECT r.id FROM routine_runs r WHERE r.started_turn_id = t.id)
        ) s ON true
        WHERE t.team_id = %s ORDER BY t.started_at DESC LIMIT %s
        """,
        (team_id, limit),
    )
    total = fetch_one(conn, "SELECT count(*) AS n FROM turns WHERE team_id = %s", (team_id,))["n"]
    return [
        {
            "id": f"turn:{r['id']}",
            "kind": "turn",
            "trace_id": r["id"],
            "at": iso(r["started_at"]),
            "actor_type": "AGENT",
            "who": r["user_name"] or "Unknown person",
            "what": r["user_input"],
            "reply": r["final_reply"],
            "chat": r["chat_title"],
            "status": r["status"],
            "model": r["model"],
            "duration_ms": _duration_ms(r["started_at"], r["ended_at"]),
            "steps": r["steps"] or 0,
            "failed": r["failed"] or 0,
            "runs": r["runs"] or 0,
            "tokens": int(r["tokens"] or 0),
            "cost_micros": int(r["cost"] or r["cost_micros"] or 0),
            "record": None,
        }
        for r in rows
    ], total


def _run_entries(conn, team_id: str, actor: str | None, limit: int) -> tuple[list[dict], int]:
    triggers = None if actor is None else [k for k, v in RUN_ACTOR.items() if v == actor]
    if triggers == []:
        return [], 0
    where = "r.team_id = %s AND r.started_turn_id IS NULL AND (%s::text[] IS NULL OR r.trigger_type = ANY(%s::text[]))"
    rows = fetch_all(
        conn,
        f"""
        SELECT r.id, r.routine, r.trigger_type, r.triggered_by, r.status, r.error, r.created_at, r.updated_at, u.name AS user_name,
               s.n AS steps, s.failed, s.tokens, s.cost, s.last_end
        FROM routine_runs r LEFT JOIN users u ON u.id = r.triggered_by
        LEFT JOIN LATERAL (
            SELECT count(*) AS n, count(*) FILTER (WHERE st.status = 'FAILED') AS failed,
                   COALESCE(sum(COALESCE(st.input_tokens, 0) + COALESCE(st.output_tokens, 0)), 0) AS tokens,
                   COALESCE(sum(st.cost_micros), 0) AS cost, max(st.ended_at) AS last_end
            FROM trace_steps st WHERE st.run_id = r.id
        ) s ON true
        WHERE {where} ORDER BY r.created_at DESC LIMIT %s
        """,
        (team_id, triggers, triggers, limit),
    )
    total = fetch_one(conn, f"SELECT count(*) AS n FROM routine_runs r WHERE {where}", (team_id, triggers, triggers))["n"]
    entries = []
    for r in rows:
        actor_type = RUN_ACTOR.get(r["trigger_type"], "SYSTEM")
        entries.append({
            "id": f"run:{r['id']}",
            "kind": "run",
            "trace_id": r["id"],
            "at": iso(r["created_at"]),
            "actor_type": actor_type,
            "who": r["user_name"] if actor_type == "USER" and r["user_name"] else actor_name(actor_type, None, None),
            "what": f"{words(r['routine'])} routine · {words(r['trigger_type'])}",
            "status": r["status"],
            "error": r["error"],
            "duration_ms": _duration_ms(r["created_at"], r["last_end"]),
            "steps": r["steps"] or 0,
            "failed": r["failed"] or 0,
            "runs": 1,
            "tokens": int(r["tokens"] or 0),
            "cost_micros": int(r["cost"] or 0),
            "record": None,
        })
    return entries, total


def activity_feed(conn, team_id: str, actor: str | None, limit: int) -> dict:
    """Audit events, turns and runs on one timeline, newest first."""
    events = audit_feed(conn, team_id, actor, limit)
    for e in events["entries"]:
        e["kind"] = "event"
    turns, turn_total = _turn_entries(conn, team_id, actor, limit)
    runs, run_total = _run_entries(conn, team_id, actor, limit)
    merged = sorted(events["entries"] + turns + runs, key=lambda e: e["at"] or "", reverse=True)[:limit]
    return {"total": events["total"] + turn_total + run_total, "entries": merged}


STEP_FIELDS = "id, turn_id, run_id, parent_id, step_type, name, status, input, output, trust_label, evidence_ids, error, model, thinking_level, input_tokens, output_tokens, cost_micros, started_at, ended_at"


def _step_view(s: dict, events: list[dict]) -> dict:
    return {
        "id": s["id"],
        "run_id": s["run_id"],
        "parent_id": s["parent_id"],
        "type": s["step_type"],
        "name": s["name"],
        "status": s["status"],
        "trust": s["trust_label"],
        "input": s["input"],
        "output": s["output"],
        "error": s["error"],
        "evidence_ids": s["evidence_ids"] or [],
        "model": s["model"],
        "thinking": s["thinking_level"],
        "input_tokens": s["input_tokens"],
        "output_tokens": s["output_tokens"],
        "cost_micros": s["cost_micros"],
        "at": iso(s["started_at"]),
        "duration_ms": _duration_ms(s["started_at"], s["ended_at"]),
        "events": events,
    }


def trace_detail(conn, team_id: str, kind: str, trace_id: str) -> dict | None:
    """Every step of one turn (with the runs it started) or of one run, with the audit events each step wrote."""
    turn = None
    if kind == "turn":
        turn = fetch_one(conn, "SELECT id, user_input, final_reply, model, prompt_version, status, started_at, ended_at FROM turns WHERE id = %s AND team_id = %s", (trace_id, team_id))
        if turn is None:
            return None
        runs = fetch_all(conn, "SELECT id, routine, routine_version, trigger_type, status, inputs, result, error, created_at, updated_at FROM routine_runs WHERE team_id = %s AND started_turn_id = %s ORDER BY created_at", (team_id, trace_id))
    else:
        runs = fetch_all(conn, "SELECT id, routine, routine_version, trigger_type, status, inputs, result, error, created_at, updated_at FROM routine_runs WHERE team_id = %s AND id = %s", (team_id, trace_id))
        if not runs:
            return None
    run_ids = [r["id"] for r in runs]
    steps = fetch_all(
        conn,
        f"SELECT {STEP_FIELDS} FROM trace_steps WHERE team_id = %s AND (turn_id = %s OR run_id = ANY(%s::text[])) ORDER BY started_at",
        (team_id, turn["id"] if turn else None, run_ids),
    )
    event_rows = fetch_all(
        conn,
        """
        SELECT e.id, e.trace_step_id, e.actor_type, e.actor_id, e.event_type, e.entity_type, e.entity_id, e.data, e.created_at, u.name AS user_name
        FROM audit_events e LEFT JOIN users u ON u.id = e.actor_id
        WHERE e.team_id = %s AND e.trace_step_id = ANY(%s::text[]) ORDER BY e.id
        """,
        (team_id, [s["id"] for s in steps]),
    )
    records = resolve_records(conn, team_id, [(e["entity_type"], e["entity_id"]) for e in event_rows])
    by_step: dict[str, list] = defaultdict(list)
    for e in event_rows:
        by_step[e["trace_step_id"]].append({
            "id": e["id"],
            "at": iso(e["created_at"]),
            "actor_type": e["actor_type"],
            "who": actor_name(e["actor_type"], e["actor_id"], e["user_name"]),
            "what": event_text(e["event_type"], e["data"]),
            "event_type": e["event_type"],
            "data": e["data"],
            "record": records.get((e["entity_type"], e["entity_id"])),
        })
    return {
        "kind": kind,
        "turn": turn and {
            "id": turn["id"],
            "input": turn["user_input"],
            "reply": turn["final_reply"],
            "model": turn["model"],
            "prompt_version": turn["prompt_version"],
            "status": turn["status"],
            "at": iso(turn["started_at"]),
            "duration_ms": _duration_ms(turn["started_at"], turn["ended_at"]),
        },
        "runs": [
            {
                "id": r["id"],
                "routine": r["routine"],
                "label": words(r["routine"]),
                "version": r["routine_version"],
                "trigger": r["trigger_type"],
                "status": r["status"],
                "inputs": redact(r["inputs"]),
                "result": redact(r["result"]),
                "error": r["error"],
                "at": iso(r["created_at"]),
            }
            for r in runs
        ],
        "steps": [_step_view(s, by_step.get(s["id"], [])) for s in steps],
    }


# Notifications

NOTICE = {
    "PAYMENT_READY": ("Payment ready", "{s} is ready for approval"),
    "PAYMENT_APPROVED": ("Approved", "{by} approved {s}"),
    "PAYMENT_REJECTED": ("Rejected", "{by} rejected {s}"),
    "PAYMENT_FAILED": ("Failed", "{s} failed"),
    "PAYMENT_STOPPED": ("Stopped before sending", "{s} was stopped before it was sent"),
    "PAYMENT_MISMATCH": ("Mismatch", "{s}: the outcome doesn't match what was approved"),
    "PAYMENT_OUTCOME_UNKNOWN": ("Outcome unknown", "{s}: checking with the provider. Don't pay it again"),
    "PAYMENT_NEEDS_MANUAL_REVIEW": ("Needs manual review", "{s} needs settling by hand"),
    "PAYMENT_REJECTED_BY_PROVIDER": ("Rejected by provider", "The provider rejected {s}"),
    "CRITICAL_LATE_CONFIRMATION": ("Critical late confirmation", "{s} was confirmed after it was thought to have failed"),
    "CASH_SHORTFALL": ("Cash shortfall", "{s}"),
    "DESTINATION_REVOKED": ("Destination revoked", "{s} was revoked"),
    "EXPENSE_NEEDS_REVIEW": ("Expense needs review", "{s} needs review"),
    "MISSING_DOCUMENT": ("Missing document", "{s}: no receipt or invoice yet"),
    "MISSING_DOCUMENT_REMINDER": ("Missing document", "Reminder: {s} still has no receipt or invoice"),
    "PURCHASE_READY": ("Purchase ready", "{s} is ready for approval"),
}


def _notice_ref(kind: str, payload: dict):
    if payload.get("approval_id") and kind == "PAYMENT_READY":
        return ("approval", payload["approval_id"])
    if payload.get("intent_id"):
        return ("payment_intent", payload["intent_id"])
    if payload.get("expense_id"):
        return ("expense", payload["expense_id"])
    if payload.get("destination_id"):
        return ("payment_destination", payload["destination_id"])
    return None


def notifications(conn, team_id: str, user_id: str, limit: int = 50) -> dict:
    rows = fetch_all(
        conn,
        """
        SELECT n.id, n.kind, n.payload, n.created_at, r.read_at
        FROM notifications n LEFT JOIN notification_reads r ON r.notification_id = n.id AND r.user_id = %s
        WHERE n.team_id = %s AND (n.user_id IS NULL OR n.user_id = %s) ORDER BY n.created_at DESC LIMIT %s
        """,
        (user_id, team_id, user_id, limit),
    )
    unread = fetch_one(
        conn,
        "SELECT count(*) AS n FROM notifications n WHERE n.team_id = %s AND (n.user_id IS NULL OR n.user_id = %s) AND NOT EXISTS (SELECT 1 FROM notification_reads r WHERE r.notification_id = n.id AND r.user_id = %s)",
        (team_id, user_id, user_id),
    )["n"]
    refs = [_notice_ref(r["kind"], r["payload"] or {}) for r in rows]
    records = resolve_records(conn, team_id, [ref for ref in refs if ref])
    by_ids = {r["payload"].get("by") for r in rows if (r["payload"] or {}).get("by")}
    names = {u["id"]: u["name"] for u in fetch_all(conn, "SELECT id, name FROM users WHERE id = ANY(%s)", (list(by_ids),))} if by_ids else {}
    accounts = {a["id"]: a for a in fetch_all(conn, "SELECT id, name, asset FROM financial_accounts WHERE team_id = %s", (team_id,))}
    out = []
    for row, ref in zip(rows, refs):
        payload = row["payload"] or {}
        record = records.get(ref) if ref else None
        label, template = NOTICE.get(row["kind"], (words(row["kind"]), "{s}"))
        subject = record["label"] if record else None
        if row["kind"] == "CASH_SHORTFALL":
            account = accounts.get(payload.get("account")) or {}
            amount = money(payload.get("shortfall_minor"), account.get("asset"))
            subject = f"{account.get('name') or 'An account'}: shortfall of {amount} on {payload.get('shortfall_date')}" if amount else f"Shortfall forecast on {payload.get('shortfall_date')}"
            record = {"kind": "cash", "id": payload.get("account"), "label": account.get("name")}
        if row["kind"] == "PURCHASE_READY":
            subject = "A purchase"
        first = (names.get(payload.get("by")) or "An Approver").split(" ")[0]
        title = template.format(s=subject or "A record that's no longer available", by=first)
        if payload.get("reason") and row["kind"] in ("PAYMENT_STOPPED", "PAYMENT_REJECTED_BY_PROVIDER"):
            title += f". {payload['reason']}"
        out.append({"id": row["id"], "kind": row["kind"], "label": label, "title": title, "at": iso(row["created_at"]), "unread": row["read_at"] is None, "record": record})
    return {"unread": unread, "items": out}


# Payments

def payment_rows(conn, team_id: str, status: str | None, counterparty_id: str | None = None, obligation_id: str | None = None, limit: int = 100) -> list[dict]:
    rows = fetch_all(
        conn,
        """
        SELECT p.id, p.amount_minor, p.asset, p.status, p.payment_reference, p.rail, p.created_at, p.obligation_id, p.counterparty_id,
               c.display_name AS counterparty, o.invoice_reference,
               (SELECT a.evidence_label FROM payment_attempts a WHERE a.intent_id = p.id ORDER BY a.created_at DESC LIMIT 1) AS evidence,
               (SELECT a.state FROM payment_attempts a WHERE a.intent_id = p.id ORDER BY a.created_at DESC LIMIT 1) AS attempt_state
        FROM payment_intents p JOIN counterparties c ON c.id = p.counterparty_id LEFT JOIN obligations o ON o.id = p.obligation_id
        WHERE p.team_id = %s AND (%s::text IS NULL OR p.status = %s) AND (%s::text IS NULL OR p.counterparty_id = %s) AND (%s::text IS NULL OR p.obligation_id = %s)
        ORDER BY p.created_at DESC LIMIT %s
        """,
        (team_id, status, status, counterparty_id, counterparty_id, obligation_id, obligation_id, limit),
    )
    return [
        {
            "id": r["id"], "amount": money(r["amount_minor"], r["asset"]), "status": r["status"], "reference": r["payment_reference"], "invoice": r["invoice_reference"],
            "counterparty": r["counterparty"], "counterparty_id": r["counterparty_id"], "obligation_id": r["obligation_id"], "evidence": r["evidence"],
            "attempt_state": r["attempt_state"], "rail": RAIL.get(r["rail"], words(r["rail"])), "asset": r["asset"], "created_at": iso(r["created_at"]),
        }
        for r in rows
    ]


def payment_detail(conn, team_id: str, payment_id: str) -> dict | None:
    p = fetch_one(
        conn,
        """
        SELECT p.*, c.display_name AS counterparty, c.classifications, d.masked_display, d.status AS destination_status, f.name AS account_name
        FROM payment_intents p JOIN counterparties c ON c.id = p.counterparty_id JOIN payment_destinations d ON d.id = p.destination_id
        JOIN financial_accounts f ON f.id = p.source_account_id WHERE p.id = %s AND p.team_id = %s
        """,
        (payment_id, team_id),
    )
    if p is None:
        return None
    bill = fetch_one(
        conn,
        """
        SELECT o.id, o.invoice_reference, o.due_date, o.status, o.created_at, o.source_type, o.extraction_id, doc.filename, doc.created_at AS document_at, u.name AS uploaded_by
        FROM obligations o LEFT JOIN documents doc ON doc.id = o.document_id LEFT JOIN users u ON u.id = doc.uploaded_by WHERE o.id = %s
        """,
        (p["obligation_id"],),
    )
    extraction = fetch_one(conn, "SELECT created_at, jsonb_array_length(failed_quotes) AS failed FROM extractions WHERE id = %s", (bill["extraction_id"],)) if bill and bill["extraction_id"] else None
    checks = fetch_all(conn, "SELECT check_name, outcome, reason FROM check_results WHERE obligation_id = %s AND run_id IS NOT DISTINCT FROM %s ORDER BY created_at", (p["obligation_id"], p["run_id"]))
    if not checks:
        checks = fetch_all(conn, "SELECT check_name, outcome, reason FROM check_results WHERE obligation_id = %s ORDER BY created_at", (p["obligation_id"],))
    approvals = fetch_all(conn, "SELECT a.id, a.status, a.created_at, a.expires_at, a.decided_at, a.reason, u.name AS decided_by FROM approvals a LEFT JOIN users u ON u.id = a.decided_by WHERE a.intent_id = %s ORDER BY a.created_at", (payment_id,))
    taps = fetch_all(
        conn,
        "SELECT t.name, t.output, t.started_at, u.name AS who FROM trace_steps t LEFT JOIN users u ON u.id = t.input->>'user_id' WHERE t.run_id = %s AND t.step_type = 'TAP' AND t.name LIKE 'Approval tap%%' ORDER BY t.started_at",
        (p["run_id"],),
    ) if p["run_id"] else []
    attempt = fetch_one(conn, "SELECT id, state, evidence_label, external_id, created_at, updated_at FROM payment_attempts WHERE intent_id = %s ORDER BY created_at DESC LIMIT 1", (payment_id,))
    outcomes = fetch_all(conn, "SELECT e.source, e.observed_status, e.created_at FROM external_events e JOIN payment_attempts a ON a.id = e.attempt_id WHERE a.intent_id = %s ORDER BY e.created_at", (payment_id,))
    resolution = None
    if attempt and attempt["state"] == "MANUALLY_RESOLVED":
        row = fetch_one(conn, "SELECT e.data, e.created_at, u.name FROM audit_events e LEFT JOIN users u ON u.id = e.actor_id WHERE e.team_id = %s AND e.event_type = 'PaymentManuallyResolved' AND e.entity_id = %s ORDER BY e.id DESC LIMIT 1", (team_id, attempt["id"]))
        if row:
            resolution = {"outcome": row["data"].get("outcome"), "evidence": row["data"].get("evidence"), "by": row["name"], "at": iso(row["created_at"])}
    expense = fetch_one(conn, "SELECT expense_id, status, version FROM expense_versions WHERE intent_id = %s AND team_id = %s ORDER BY version DESC LIMIT 1", (payment_id, team_id))
    stopped = fetch_one(conn, "SELECT data, created_at FROM audit_events WHERE team_id = %s AND event_type = 'PaymentStoppedBeforeSend' AND entity_id = %s ORDER BY id DESC LIMIT 1", (team_id, payment_id))
    return {
        "id": p["id"], "status": p["status"], "counterparty": p["counterparty"], "counterparty_id": p["counterparty_id"], "counterparty_type": ", ".join(words(c) for c in (p["classifications"] or [])),
        "amount": money(p["amount_minor"], p["asset"]), "max_fee": money(p["max_fee_minor"], p["asset"]), "reference": p["payment_reference"],
        "rail": RAIL.get(p["rail"], words(p["rail"])), "asset": p["asset"], "chain": chain_name(p["chain_id"]), "source_account": p["account_name"],
        "destination": p["masked_display"], "destination_status": p["destination_status"], "destination_id": p["destination_id"],
        "created_at": iso(p["created_at"]), "updated_at": iso(p["updated_at"]), "warnings": p["warnings"],
        "bill": bill and {
            "id": bill["id"], "invoice": bill["invoice_reference"], "due_date": iso(bill["due_date"]), "status": bill["status"], "created_at": iso(bill["created_at"]),
            "source_type": bill["source_type"], "document": bill["filename"], "document_at": iso(bill["document_at"]), "uploaded_by": bill["uploaded_by"],
        },
        "extraction": extraction and {"at": iso(extraction["created_at"]), "failed_quotes": extraction["failed"]},
        "checks": checks,
        "approvals": [{**a, "created_at": iso(a["created_at"]), "expires_at": iso(a["expires_at"]), "decided_at": iso(a["decided_at"])} for a in approvals],
        "taps": [{"who": t["who"], "at": iso(t["started_at"]), "counted": bool((t["output"] or {}).get("won")), "message": (t["output"] or {}).get("message") or t["name"]} for t in taps],
        "attempt": attempt and {**attempt, "created_at": iso(attempt["created_at"]), "updated_at": iso(attempt["updated_at"])},
        "outcomes": [{"source": o["source"], "status": o["observed_status"], "at": iso(o["created_at"])} for o in outcomes],
        "resolution": resolution,
        "stopped": stopped and {"reason": (stopped["data"] or {}).get("reason"), "at": iso(stopped["created_at"])},
        "expense": expense and {"id": expense["expense_id"], "status": expense["status"], "version": expense["version"]},
    }


# Bills

def bill_rows(conn, team_id: str, status: str | None, counterparty_id: str | None = None, limit: int = 100) -> list[dict]:
    rows = fetch_all(
        conn,
        """
        SELECT o.id, o.invoice_reference, o.amount_minor, o.asset, o.due_date, o.status, o.counterparty_id, o.created_at, c.display_name AS counterparty
        FROM obligations o LEFT JOIN counterparties c ON c.id = o.counterparty_id
        WHERE o.team_id = %s AND (%s::text IS NULL OR o.status = %s) AND (%s::text IS NULL OR o.counterparty_id = %s) ORDER BY o.created_at DESC LIMIT %s
        """,
        (team_id, status, status, counterparty_id, counterparty_id, limit),
    )
    return [{"id": r["id"], "invoice": r["invoice_reference"], "amount": money(r["amount_minor"], r["asset"]), "due_date": iso(r["due_date"]), "status": r["status"], "counterparty": r["counterparty"], "counterparty_id": r["counterparty_id"], "created_at": iso(r["created_at"])} for r in rows]


def bill_extra(conn, team_id: str, bill_id: str) -> dict:
    row = fetch_one(
        conn,
        """
        SELECT o.counterparty_id, o.created_at, o.source_type, o.description, o.superseded_by, doc.filename, doc.media_type, doc.created_at AS document_at,
               du.name AS uploaded_by, cu.name AS created_by
        FROM obligations o LEFT JOIN documents doc ON doc.id = o.document_id LEFT JOIN users du ON du.id = doc.uploaded_by LEFT JOIN users cu ON cu.id = o.created_by
        WHERE o.id = %s AND o.team_id = %s
        """,
        (bill_id, team_id),
    )
    corrections = fetch_all(conn, "SELECT f.field, f.old_value, f.new_value, f.reason, f.created_at, u.name FROM field_corrections f JOIN users u ON u.id = f.corrected_by WHERE f.obligation_id = %s AND f.team_id = %s ORDER BY f.created_at", (bill_id, team_id))
    return {
        "counterparty_id": row["counterparty_id"], "created_at": iso(row["created_at"]), "source_type": row["source_type"], "description": row["description"], "superseded_by": row["superseded_by"],
        "document": row["filename"], "document_type": row["media_type"], "document_at": iso(row["document_at"]), "uploaded_by": row["uploaded_by"] or row["created_by"],
        "corrections": [{"field": c["field"], "old_value": c["old_value"], "new_value": c["new_value"], "reason": c["reason"], "at": iso(c["created_at"]), "by": c["name"]} for c in corrections],
        "payment_rows": payment_rows(conn, team_id, None, obligation_id=bill_id, limit=20),
    }


# Contacts

def destination_rows(conn, team_id: str, counterparty_id: str) -> list[dict]:
    rows = fetch_all(
        conn,
        "SELECT d.id, d.rail, d.asset, d.masked_display, d.status, d.chain_id, d.token_contract, d.source, d.created_at, u.name AS added_by FROM payment_destinations d LEFT JOIN users u ON u.id = d.added_by WHERE d.team_id = %s AND d.counterparty_id = %s ORDER BY d.created_at",
        (team_id, counterparty_id),
    )
    return [{**r, "rail": RAIL.get(r["rail"], words(r["rail"])), "chain": chain_name(r["chain_id"]), "token_contract": short(r["token_contract"]), "created_at": iso(r["created_at"])} for r in rows]


def contact_detail(conn, team_id: str, counterparty_id: str) -> dict | None:
    c = fetch_one(conn, "SELECT id, display_name, legal_name, entity_type, classifications, vat_number FROM counterparties WHERE id = %s AND team_id = %s", (counterparty_id, team_id))
    if c is None:
        return None
    channels = fetch_all(conn, "SELECT id, kind, value, added_at FROM contact_channels WHERE team_id = %s AND counterparty_id = %s ORDER BY added_at", (team_id, counterparty_id))
    return {
        **c, "classifications": [words(x) for x in (c["classifications"] or [])], "entity_type": words(c["entity_type"]),
        "destinations": destination_rows(conn, team_id, counterparty_id),
        "channels": [{**ch, "added_at": iso(ch["added_at"])} for ch in channels],
        "bills": bill_rows(conn, team_id, None, counterparty_id=counterparty_id, limit=5),
        "payments": payment_rows(conn, team_id, None, counterparty_id=counterparty_id, limit=5),
    }


METHOD_TEXT = {"CALL_BACK": "Call back", "SIGNED_CONTRACT": "Signed contract", "CRYPTO_WALLET": "Crypto wallet confirmation", "NEW_RELATIONSHIP": "New relationship", "PROVIDER_CHECK": "Provider check"}


def destination_detail(conn, team_id: str, destination_id: str) -> dict | None:
    d = fetch_one(
        conn,
        """
        SELECT d.id, d.counterparty_id, d.rail, d.asset, d.masked_display, d.status, d.chain_id, d.token_contract, d.source, d.created_at, d.revoked_at,
               u.name AS added_by, c.display_name AS counterparty
        FROM payment_destinations d JOIN counterparties c ON c.id = d.counterparty_id LEFT JOIN users u ON u.id = d.added_by WHERE d.id = %s AND d.team_id = %s
        """,
        (destination_id, team_id),
    )
    if d is None:
        return None
    verification = fetch_one(
        conn,
        "SELECT v.method, v.verified_at, v.same_person_added, u.name AS verified_by, ch.kind AS channel_kind, ch.value AS channel_value FROM destination_verifications v JOIN users u ON u.id = v.verified_by LEFT JOIN contact_channels ch ON ch.id = v.channel_id WHERE v.destination_id = %s ORDER BY v.verified_at DESC LIMIT 1",
        (destination_id,),
    )
    revoked = fetch_one(conn, "SELECT data, created_at FROM audit_events WHERE team_id = %s AND event_type = 'DestinationRevoked' AND entity_id = %s ORDER BY id DESC LIMIT 1", (team_id, destination_id))
    channels = fetch_all(conn, "SELECT id, kind, value, added_at FROM contact_channels WHERE team_id = %s AND counterparty_id = %s ORDER BY added_at", (team_id, d["counterparty_id"]))
    return {
        "id": d["id"], "counterparty": d["counterparty"], "counterparty_id": d["counterparty_id"], "rail": RAIL.get(d["rail"], words(d["rail"])), "asset": d["asset"],
        "masked": d["masked_display"], "status": d["status"], "chain": chain_name(d["chain_id"]), "token_contract": short(d["token_contract"]), "source": words(d["source"]),
        "added_at": iso(d["created_at"]), "added_by": d["added_by"], "revoked_at": iso(d["revoked_at"]),
        "revoke_reason": ((revoked or {}).get("data") or {}).get("reason") or None,
        "verification": verification and {
            "method": METHOD_TEXT.get(verification["method"], words(verification["method"])), "at": iso(verification["verified_at"]), "by": verification["verified_by"],
            "same_person": verification["same_person_added"], "via": f"{words(verification['channel_kind'])} {verification['channel_value']}" if verification["channel_kind"] else None,
        },
        # A channel only counts if it was on file before these details came in. The server enforces it, and this lets the screen say so first.
        "channels": [{"id": ch["id"], "kind": ch["kind"], "label": f"{words(ch['kind'])} {ch['value']}", "added_at": iso(ch["added_at"]), "independent": ch["added_at"] < d["created_at"]} for ch in channels],
    }


# Approval cards

def approval_extra(conn, team_id: str, row: dict) -> dict:
    bill = fetch_one(conn, "SELECT id, invoice_reference, due_date, status FROM obligations WHERE id = %s", (row["obligation_id"],))
    destination = fetch_one(
        conn,
        "SELECT d.id, d.chain_id, u.name AS added_by, (SELECT vu.name FROM destination_verifications v JOIN users vu ON vu.id = v.verified_by WHERE v.destination_id = d.id ORDER BY v.verified_at DESC LIMIT 1) AS verified_by FROM payment_destinations d LEFT JOIN users u ON u.id = d.added_by WHERE d.id = %s",
        (row["destination_id"],),
    )
    return {
        "obligation_id": row["obligation_id"], "invoice": bill and bill["invoice_reference"], "due_date": bill and iso(bill["due_date"]), "bill_status": bill and bill["status"],
        "destination_id": row["destination_id"], "chain": chain_name(row.get("chain_id")), "token_contract_short": short(row.get("token_contract")),
        "destination_added_by": destination and destination["added_by"], "destination_verified_by": destination and destination["verified_by"],
        "created_at": iso(row.get("approval_created_at")),
    }


def suggestions(conn, team_id: str, findings) -> list[dict]:
    out = []
    for f in findings or []:
        if isinstance(f, str):
            out.append({"text": f, "cites": []})
        elif isinstance(f, dict) and f.get("finding"):
            out.append({"text": f["finding"], "cites": cite(conn, team_id, f.get("record_ids"))})
    return out


# Chat

ATTACHED = re.compile(r"\n?\[The user attached document_id ([A-Za-z0-9_]+)\]\s*$")
ANSWERED = re.compile(r"\s*\[answer value: [^\]]*\]\s*$")


def turn_rows(conn, team_id: str, chat_id: str) -> list[dict]:
    rows = fetch_all(conn, "SELECT id, seq, user_input, status, started_at, ended_at FROM turns WHERE chat_id = %s ORDER BY seq", (chat_id,))
    doc_ids = [m.group(1) for m in (ATTACHED.search(r["user_input"]) for r in rows) if m]
    docs = {d["id"]: d for d in fetch_all(conn, "SELECT id, filename, media_type FROM documents WHERE team_id = %s AND id = ANY(%s)", (team_id, doc_ids))} if doc_ids else {}
    out = []
    for r in rows:
        text = r["user_input"]
        found = ATTACHED.search(text)
        document = docs.get(found.group(1)) if found else None
        text = ANSWERED.sub("", ATTACHED.sub("", text)).strip()
        out.append({"id": r["id"], "seq": r["seq"], "text": text, "status": r["status"], "at": iso(r["started_at"]), "ended_at": iso(r["ended_at"]), "document": document})
    return out


INPUT_NAMES = {
    "counterparty_name": "Counterparty", "display_name": "Name", "legal_name": "Legal name", "max_amount": "Maximum", "valid_until": "Ends",
    "day_of_period": "Day of the period", "days": "Days ahead", "due_from": "Due from", "due_to": "Due to", "use_case": "What it's for",
    "min_specs": "Minimum specification", "preferred_merchants": "Preferred shops", "delivery_by": "Delivery by", "contact_phone": "Phone",
    "chain_id": "Chain", "token_contract": "Token contract", "required_evidence": "Evidence needed", "business_purpose": "Purpose",
}
SECRET_INPUTS = {"iban": "IBAN", "account_number": "Account number", "sort_code": "Sort code", "wallet_address": "Wallet"}


def start_inputs(conn, team_id: str, inputs: dict) -> list[list[str]]:
    """A start card's inputs as label and value. Amounts are formatted here, and bank or wallet details are masked."""
    rows = []
    asset = inputs.get("asset")
    for key, value in inputs.items():
        if value in (None, "", [], {}) or (key == "asset" and "amount_minor" in inputs):
            continue
        if key == "document_id":
            doc = fetch_one(conn, "SELECT filename FROM documents WHERE id = %s AND team_id = %s", (value, team_id))
            rows.append(["Document", doc["filename"] if doc else "Attached document"])
        elif key in ("obligation_id", "expense_id"):
            found = resolve_records(conn, team_id, [("obligation" if key == "obligation_id" else "expense", value)])
            rows.append(["Bill" if key == "obligation_id" else "Expense", next(iter(found.values()))["label"] if found else "Record not found"])
        elif key == "amount_minor":
            rows.append(["Amount", money(value, asset) or str(value)])
        elif key in SECRET_INPUTS:
            rows.append([SECRET_INPUTS[key], f"…{str(value)[-4:]} (not verified)"])
        elif key == "chain_id":
            rows.append(["Chain", chain_name(value) or str(value)])
        elif isinstance(value, list):
            rows.append([INPUT_NAMES.get(key, words(key)), ", ".join(str(v) for v in value)])
        elif isinstance(value, dict):
            rows.append([INPUT_NAMES.get(key, words(key)), ", ".join(f"{words(k)}: {v}" for k, v in value.items())])
        else:
            rows.append([INPUT_NAMES.get(key, words(key)), words(value) if key in ("frequency", "entity_type", "scope", "type") else str(value)])
    return rows


def event_extra(conn, team_id: str, event: dict) -> dict:
    """Cards in the chat show their live state, so a decided card can't be tapped again after a reload."""
    payload = event["payload"] or {}
    if event["kind"] == "START_CARD" and payload.get("confirmation_id"):
        row = fetch_one(conn, "SELECT s.status, u.name FROM start_confirmations s JOIN users u ON u.id = s.requested_by WHERE s.id = %s AND s.team_id = %s", (payload["confirmation_id"], team_id))
        return {"confirmation_status": row and row["status"], "requested_by": row and row["name"], "inputs_display": start_inputs(conn, team_id, payload.get("inputs") or {})}
    if event["kind"] == "QUESTION" and payload.get("question_id"):
        row = fetch_one(conn, "SELECT status, answer FROM run_questions WHERE id = %s AND team_id = %s", (payload["question_id"], team_id))
        if row is None:
            return {}
        options = {str(o.get("value")): o.get("label") for o in payload.get("options") or []}
        answer = row["answer"]
        return {"question_status": row["status"], "answer_label": options.get(str(answer), None if answer is None else str(answer))}
    if event["kind"] == "STATUS" and payload.get("finished") and event.get("run_id"):
        flagged = fetch_all(conn, "SELECT check_name, outcome, reason FROM check_results WHERE run_id = %s AND team_id = %s AND outcome NOT IN ('PASS', 'NOT_APPLICABLE', 'ELIGIBLE_FOR_APPROVAL') ORDER BY created_at", (event["run_id"], team_id))
        return {"flagged": flagged}
    return {}


# Cash

def bill_rows_by_id(conn, team_id: str, ids) -> list[dict]:
    if not ids:
        return []
    rows = fetch_all(conn, "SELECT o.id, o.invoice_reference, o.amount_minor, o.asset, o.due_date, o.status, c.display_name FROM obligations o LEFT JOIN counterparties c ON c.id = o.counterparty_id WHERE o.team_id = %s AND o.id = ANY(%s)", (team_id, list(ids)))
    order = {i: n for n, i in enumerate(ids)}
    rows.sort(key=lambda r: order.get(r["id"], 0))
    return [{"id": r["id"], "label": " · ".join(x for x in (r["display_name"] or "Unknown supplier", r["invoice_reference"], money(r["amount_minor"], r["asset"])) if x), "status": r["status"], "due_date": iso(r["due_date"])} for r in rows]
