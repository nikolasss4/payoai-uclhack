"""Nodes for Pay Someone, Replace Bill, Check Bill Compliance, Prepare Batch and Cash Analysis."""

from datetime import date, datetime, timedelta

from payoai.agent import llm
from payoai.compliance.rules import uk_vat
from payoai.db import Jsonb, fetch_all, fetch_one, new_id, tx
from payoai.domain.money import format_minor, to_minor
from payoai.routines.nodes import checks, decision
from payoai.routines.nodes.checks import account_position, fee_cap, refresh_balance
from payoai.routines.nodes.common import RunState, node, question, record_check
from payoai.routines.nodes.decision import notify_approvers
from payoai.trace import TraceContext, audit, step

MONEY_FIELDS = {"total", "subtotal", "vat_total", "currency", "invoice_number"}
FORBIDDEN_CORRECTIONS = {"payment_details", "iban", "sort_code", "account_number", "wallet_address", "chain_id", "token_contract"}
REPLACEABLE_INTENT = ("READY_FOR_APPROVAL", "APPROVED", "REJECTED", "FAILED", "CANCELLED")
BATCH_INVESTIGATION_CAP = 3


@node("Require Purpose")
def require_purpose(state: RunState, ctx) -> dict:
    if state["inputs"].get("purpose") or (state.get("answers") or {}).get("purpose"):
        return {}
    return question("purpose", "What is this payment for? It's needed for the expense record.", [])


@node("Prepare Replacement")
def prepare_replacement(state: RunState, ctx) -> dict:
    """Marks the old bill superseded if no money can have moved. Whichever of this and approval comes first wins."""
    team, old_id = state["team_id"], state["inputs"]["obligation_id"]
    corrections = state["inputs"].get("corrections") or {}
    bad = FORBIDDEN_CORRECTIONS & set(corrections)
    if bad:
        return {"stop": "Payment details can't be corrected here. Add them with Add Counterparty and verify them."}
    with tx() as conn:
        old = fetch_one(conn, "SELECT * FROM obligations WHERE id = %s AND team_id = %s FOR UPDATE", (old_id, team))
        if old is None:
            return {"stop": "That bill wasn't found in this team."}
        if old["status"] == "PAID":
            return {"stop": "This bill has been paid, so it can't be edited in chat. Change it manually on the bill's screen."}
        intents = fetch_all(conn, "SELECT id, status FROM payment_intents WHERE obligation_id = %s", (old_id,))
        for intent in intents:
            if intent["status"] == "CONFIRMED":
                return {"stop": "This bill has been paid, so it can't be edited in chat. Change it manually on the bill's screen."}
            if intent["status"] not in REPLACEABLE_INTENT:
                return {"stop": f"A payment for this bill is {intent['status']}, so money may be moving. Wait for the outcome."}
            active = fetch_one(conn, "SELECT id FROM payment_attempts WHERE intent_id = %s AND state NOT IN ('REJECTED', 'FAILED')", (intent["id"],))
            if active:
                return {"stop": "A payment attempt exists for this bill, so wait for its outcome."}
        for intent in intents:
            if intent["status"] in ("READY_FOR_APPROVAL", "APPROVED"):
                conn.execute("UPDATE approvals SET status = 'CANCELLED', reason = 'Bill replaced' WHERE intent_id = %s AND status IN ('PENDING', 'APPROVED')", (intent["id"],))
                conn.execute("UPDATE payment_intents SET status = 'CANCELLED', updated_at = now() WHERE id = %s", (intent["id"],))
        conn.execute("UPDATE obligations SET status = 'SUPERSEDED', updated_at = now() WHERE id = %s", (old_id,))
        audit(conn, team, "USER", state.get("actor"), "BillSuperseded", "obligation", old_id, {"reason": state["inputs"].get("reason")})
    return {"document_id": state["inputs"].get("document_id") or old["document_id"], "result": {"superseded_bill": old_id}}


@node("Apply Corrections")
def apply_corrections(state: RunState, ctx) -> dict:
    """A correction goes on top of the new extraction, so re-extracting can't undo it."""
    corrections = state["inputs"].get("corrections") or {}
    return {"fields": {**(state.get("fields") or {}), **corrections}} if corrections else {}


@node("Record Corrections")
def record_corrections(state: RunState, ctx) -> dict:
    team, corrections = state["team_id"], state["inputs"].get("corrections") or {}
    old_id = state["inputs"]["obligation_id"]
    with tx() as conn:
        conn.execute("UPDATE obligations SET superseded_by = %s WHERE id = %s", (state["obligation_id"], old_id))
        for field, value in corrections.items():
            conn.execute(
                "INSERT INTO field_corrections (id, team_id, obligation_id, field, new_value, is_money_field, corrected_by, reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (new_id("cor"), team, state["obligation_id"], field, Jsonb(value), field in MONEY_FIELDS, state.get("actor"), state["inputs"].get("reason")),
            )
    return {}


@node("Finish Compliance Report")
def compliance_report(state: RunState, ctx) -> dict:
    fields = state.get("fields") or {}
    rows = uk_vat.evaluate(fields)
    checks_out = [record_check(state, "Check Compliance", r["rule_id"], r["outcome"], r["reason"], r["evidence"]) for r in rows]
    from payoai.domain.decide import combine

    worst, reasons = combine(checks_out)
    return {
        "decision": worst,
        "reasons": reasons,
        "checks": checks_out,
        "result": {"ruleset": uk_vat.RULESET_VERSION, "source": uk_vat.SOURCE, "invoice_type": uk_vat.classify(fields), "report": rows, "label": "Prepared for accountant review, not certified compliant."},
    }


def allocate_cash(bills: list[dict], spendable: int) -> tuple[list[dict], list[dict]]:
    """Fixed order, earliest due date then smaller amount, so the result is repeatable and testable."""
    fits, short = [], []
    ordered = sorted(bills, key=lambda b: (b["due_date"] or date.max, b["amount"], b["obligation_id"]))
    for bill in ordered:
        if bill["amount"] <= spendable:
            spendable -= bill["amount"]
            fits.append({**bill, "remaining": spendable})
        else:
            short.append({**bill, "shortfall": bill["amount"] - spendable})
    return fits, short


@node("Prepare Batch")
def prepare_batch(state: RunState, ctx) -> dict:
    """Never pauses for questions. Items that need information are listed together at the end."""
    team = state["team_id"]
    due_from, due_to = state["inputs"].get("due_from"), state["inputs"].get("due_to")
    with tx() as conn:
        bills = fetch_all(
            conn,
            """
            SELECT o.id FROM obligations o WHERE o.team_id = %s AND o.status = ANY(%s)
              AND (%s::date IS NULL OR o.due_date >= %s::date) AND (%s::date IS NULL OR o.due_date <= %s::date)
            ORDER BY o.due_date NULLS LAST
            """,
            # Blocked bills stay on the list with their reasons, and can't be selected for approval.
            (team, ["REGISTERED", "NEEDS_REVIEW", "NEEDS_INFORMATION", "VERIFIED", "BLOCKED"], due_from, due_from, due_to, due_to),
        )
    items = []
    for row in bills:
        item = _base_state(state, row["id"])
        item.update(checks.load_registered(item) or {})
        if item.get("stop"):
            continue
        item["checks"] = []
        for fn in (checks.check_duplicates, checks.check_compliance, checks.check_amount, checks.select_destination, checks.check_destination, checks.select_source_account):
            out = fn(item) or {}
            item["checks"] = item["checks"] + out.pop("checks", [])
            item.update(out)
        items.append(item)

    by_account: dict[str, list[dict]] = {}
    for item in items:
        if item.get("source_account_id") and item.get("amount_minor"):
            by_account.setdefault(item["source_account_id"], []).append(item)
        else:
            item["checks"] = item["checks"] + checks.check_cash({**item, "source_account_id": None})["checks"]
    for account_id, group in by_account.items():
        _allocate(state, account_id, group)

    investigated, summary = 0, []
    for item in items:
        item.update(decision.decide(item))
        if item["decision"] in ("NEEDS_REVIEW", "NEEDS_INFORMATION") and investigated < BATCH_INVESTIGATION_CAP:
            investigated += 1
            item.update(decision.investigate(item))
        item["explanation"] = decision.plain_list(item)
        prepared = {}
        if item["decision"] in ("ELIGIBLE_FOR_APPROVAL", "NEEDS_REVIEW") and item.get("destination_id") and item.get("source_account_id") and item.get("amount_minor"):
            prepared = decision.prepare_payment(item)
        summary.append({"obligation_id": item["obligation_id"], "decision": item["decision"], "reasons": item.get("reasons"), "intent_id": prepared.get("intent_id"), "approval_id": prepared.get("approval_id")})
    return {"result": {"items": summary, "counts": _counts(summary)}, "approval_id": None}


def _counts(summary) -> dict:
    out: dict[str, int] = {}
    for item in summary:
        out[item["decision"]] = out.get(item["decision"], 0) + 1
    return out


def _base_state(state: RunState, obligation_id: str) -> dict:
    return {
        "run_id": state["run_id"], "team_id": state["team_id"], "turn_id": state.get("turn_id"), "actor": state.get("actor"),
        "inputs": {"obligation_id": obligation_id, "batch": True}, "answers": {}, "checks": [],
    }


def _allocate(state, account_id, group) -> None:
    with tx() as conn:
        account = fetch_one(conn, "SELECT * FROM financial_accounts WHERE id = %s", (account_id,))
    snapshot = refresh_balance(account)
    if snapshot is None:
        for item in group:
            item["checks"] = item["checks"] + [record_check(item, "Allocate Cash", "BALANCE_UNKNOWN", "NEEDS_REVIEW", "The balance can't be read, so affordability is unknown.")]
        return
    position = account_position(account, snapshot)
    bills = []
    for item in group:
        with tx() as conn:
            due = fetch_one(conn, "SELECT due_date FROM obligations WHERE id = %s", (item["obligation_id"],))["due_date"]
        bills.append({"obligation_id": item["obligation_id"], "amount": int(item["amount_minor"]) + fee_cap(account["rail"]), "due_date": due, "item": item})
    fits, short = allocate_cash(bills, position["spendable_minor"])
    for bill in fits:
        item = bill["item"]
        new = [record_check(item, "Allocate Cash", "CASH_OK", "PASS", "Fits within the batch's cash after the reserve.", {"remaining_minor": bill["remaining"]})]
        if position["stale"]:
            new.append(record_check(item, "Allocate Cash", "BALANCE_STALE", "NEEDS_REVIEW", f"The balance is from {position['as_of']}, so it may be out of date."))
        item["checks"] = item["checks"] + new
    ahead = [b["obligation_id"] for b in fits]
    for bill in short:
        item = bill["item"]
        reason = f"Doesn't fit: short by {format_minor(bill['shortfall'], account['asset'])} after the bills ahead of it and the reserve."
        item["checks"] = item["checks"] + [record_check(item, "Allocate Cash", "BATCH_SHORTFALL", "NEEDS_REVIEW", reason, {"ahead": ahead, "shortfall_minor": bill["shortfall"]})]


@node("Cash Analysis")
def cash_analysis(state: RunState, ctx) -> dict:
    """Per currency, each pound counted once, and no confident answer on stale data."""
    team = state["team_id"]
    days = int(state["inputs"].get("days", 7))
    today = date.today()
    horizon = today + timedelta(days=days)
    with tx() as conn:
        accounts = fetch_all(conn, "SELECT * FROM financial_accounts WHERE team_id = %s AND status = 'ACTIVE'", (team,))
        bills = fetch_all(
            conn,
            """
            SELECT id, amount_minor, asset, due_date, status, invoice_reference FROM obligations
            WHERE team_id = %s AND due_date <= %s AND amount_minor IS NOT NULL AND status = ANY(%s)
            """,
            (team, horizon, ["REGISTERED", "NEEDS_INFORMATION", "NEEDS_REVIEW", "VERIFIED", "PAYMENT_PREPARED"]),
        )
        approved_ids = {r["obligation_id"] for r in fetch_all(conn, "SELECT obligation_id FROM payment_intents WHERE team_id = %s AND status = ANY(%s)", (team, ["APPROVED", "SUBMITTING", "SUBMITTED", "PENDING", "UNKNOWN", "NEEDS_MANUAL_REVIEW"]))}
    assets: dict[str, dict] = {}
    for account in accounts:
        with step(ctx, "NODE", "Refresh Balances", {"account": account["id"]}) as s:
            snapshot = refresh_balance(account)
            s.output = {"as_of": str(snapshot["as_of"]) if snapshot else None}
        entry = assets.setdefault(account["asset"], {"asset": account["asset"], "accounts": [], "available_minor": 0, "in_flight_deducted_minor": 0, "in_flight_minor": 0, "approved_minor": 0, "reserve_minor": 0, "stale": False, "unknown": False})
        if snapshot is None:
            entry["unknown"] = True
            entry["accounts"].append({"id": account["id"], "name": account["name"], "balance": None})
            continue
        position = account_position(account, snapshot)
        for key in ("available_minor", "in_flight_deducted_minor", "in_flight_minor", "approved_minor", "reserve_minor"):
            entry[key] += position[key]
        entry["stale"] = entry["stale"] or position["stale"]
        entry["accounts"].append({"id": account["id"], "name": account["name"], "as_of": position["as_of"], "stale": position["stale"]})
    results = []
    for asset, entry in assets.items():
        due = [b for b in bills if b["asset"] == asset and b["id"] not in approved_ids]
        due_minor = sum(int(b["amount_minor"]) for b in due)
        projected = entry["available_minor"] - entry["in_flight_deducted_minor"] - entry["approved_minor"] - due_minor - entry["reserve_minor"]
        shortfall_date = None
        running = entry["available_minor"] - entry["in_flight_deducted_minor"] - entry["approved_minor"] - entry["reserve_minor"]
        driving = []
        for bill in sorted(due, key=lambda b: (b["due_date"], b["id"])):
            running -= int(bill["amount_minor"])
            if running < 0:
                shortfall_date = shortfall_date or bill["due_date"]
                driving.append(bill["id"])
        confident = not (entry["stale"] or entry["unknown"])
        results.append({
            **entry,
            "bills_due_minor": due_minor,
            "bills_due": [{"id": b["id"], "invoice": b["invoice_reference"], "amount": format_minor(int(b["amount_minor"]), asset), "due_date": str(b["due_date"]), "status": b["status"]} for b in due],
            "projected_minor": projected,
            "projected": format_minor(projected, asset),
            "shortfall_date": str(shortfall_date) if shortfall_date else None,
            "driving_bills": driving,
            "confident": confident,
            "note": None if confident else "A balance here is stale or unknown, so this can't be stated with confidence.",
            "expected_income": "Never included. Shown separately only if recorded.",
        })
        _raise_alert(team, accounts, asset, shortfall_date, -projected if projected < 0 else 0, driving)
    analysis_id = new_id("csh")
    payload = {"days": days, "as_of": datetime.now().isoformat(), "by_asset": results}
    with tx() as conn:
        conn.execute("INSERT INTO cash_analyses (id, team_id, run_id, result) VALUES (%s, %s, %s, %s)", (analysis_id, team, state["run_id"], Jsonb(payload)))
    return {"result": {"analysis_id": analysis_id, **payload}}


def _raise_alert(team, accounts, asset, shortfall_date, shortfall_minor, driving) -> None:
    account = next((a for a in accounts if a["asset"] == asset), None)
    if account is None:
        return
    with tx() as conn:
        if shortfall_date is None:
            conn.execute("UPDATE alerts SET status = 'RESOLVED', updated_at = now() WHERE team_id = %s AND account_id = %s AND status = 'OPEN'", (team, account["id"]))
            return
        existing = fetch_one(conn, "SELECT * FROM alerts WHERE team_id = %s AND account_id = %s AND shortfall_date = %s", (team, account["id"], shortfall_date))
        worse = existing is None or shortfall_minor > int(existing["shortfall_minor"]) * 1.1 or set(driving) - set(existing["driving_obligation_ids"])
        conn.execute(
            """
            INSERT INTO alerts (id, team_id, account_id, shortfall_date, shortfall_minor, driving_obligation_ids) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (team_id, account_id, shortfall_date) DO UPDATE SET shortfall_minor = EXCLUDED.shortfall_minor, driving_obligation_ids = EXCLUDED.driving_obligation_ids, status = 'OPEN', updated_at = now()
            """,
            (new_id("alr"), team, account["id"], shortfall_date, shortfall_minor, Jsonb(driving)),
        )
        if worse:
            notify_approvers(conn, team, "CASH_SHORTFALL", {"account": account["id"], "shortfall_date": str(shortfall_date), "shortfall_minor": str(shortfall_minor), "driving": driving}, f"shortfall:{account['id']}:{shortfall_date}:{shortfall_minor}")


EXPLAIN_SCHEMA = {"type": "object", "properties": {"explanation": {"type": "string"}}, "required": ["explanation"]}


@node("Explain Cash")
def explain_cash(state: RunState, ctx) -> dict:
    result = state["result"]
    plain = []
    for entry in result["by_asset"]:
        line = f"{entry['asset']}: projected {entry['projected']} after bills due and the reserve."
        if entry["shortfall_date"]:
            line += f" First shortfall on {entry['shortfall_date']}."
        if entry["note"]:
            line = f"{entry['note']} {line}"
        plain.append(line)
    fallback = " ".join(plain)
    try:
        data = llm.call(
            "explain",
            [llm.text_part(f"Summarise this cash position in two or three plain sentences for a small business owner. Use only these figures. If confident is false, say the answer is uncertain and why. Never say they're fine on stale data. Facts: {plain}")],
            ctx,
            schema=EXPLAIN_SCHEMA,
        ).data
        explanation = data["explanation"]
    except llm.LLMFailure:
        explanation = fallback
    # Freshness is a typed field, so the warning is always present even if the summary omits it.
    notes = [e["note"] for e in result["by_asset"] if e["note"]]
    if notes and notes[0] not in explanation:
        explanation = f"{notes[0]} {explanation}"
    return {"explanation": explanation, "result": {**result, "explanation": explanation}}


__all__ = ["TraceContext", "to_minor"]
