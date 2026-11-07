"""Set Up Recurring and Prepare Occurrence. Each Occurrence still needs its own approval."""

import calendar
from datetime import date, timedelta

from psycopg.errors import UniqueViolation

from payoai.db import Jsonb, fetch_all, fetch_one, new_id, tx
from payoai.domain.money import to_minor
from payoai.queue import enqueue
from payoai.routines.nodes.checks import _normal_name
from payoai.routines.nodes.common import RunState, node, record_check
from payoai.trace import audit


def business_day(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def next_due(frequency: str, day_of_period: int, after: date, use_business_days: bool = True) -> date:
    """The next scheduled date strictly after `after`, using the nominal date to keep the schedule stable."""
    if frequency == "WEEKLY":
        delta = (day_of_period - after.weekday() - 1) % 7 + 1
        nominal = after + timedelta(days=delta)
    else:
        year, month = after.year, after.month
        nominal = date(year, month, min(day_of_period, calendar.monthrange(year, month)[1]))
        if nominal <= after:
            month = month % 12 + 1
            year = year + (1 if month == 1 else 0)
            nominal = date(year, month, min(day_of_period, calendar.monthrange(year, month)[1]))
    return business_day(nominal) if use_business_days else nominal


def period_of(frequency: str, day: date) -> str:
    return day.strftime("%Y-%m") if frequency == "MONTHLY" else f"{day.isocalendar().year}-W{day.isocalendar().week:02d}"


@node("Draft Mandate")
def draft_mandate(state: RunState, ctx) -> dict:
    """Drafts only. Setting this up never pays anything, and only an Approver can activate it."""
    team, inputs = state["team_id"], state["inputs"]
    with tx() as conn:
        rows = fetch_all(conn, "SELECT id, display_name, legal_name FROM counterparties WHERE team_id = %s", (team,))
        target = _normal_name(inputs["counterparty_name"])
        matches = [r for r in rows if target in (_normal_name(r["display_name"]), _normal_name(r["legal_name"] or ""))]
        if len(matches) != 1:
            return {"stop": f"{inputs['counterparty_name']} needs to match exactly one known counterparty."}
        asset = inputs["asset"].upper()
        destinations = fetch_all(conn, "SELECT id FROM payment_destinations WHERE counterparty_id = %s AND status = 'VERIFIED' AND asset = %s", (matches[0]["id"], asset))
        if not destinations:
            return {"stop": f"There's no Verified Destination that takes {asset} for this counterparty."}
        mandate_id, rule_id = new_id("man"), new_id("rule")
        frequency = inputs["frequency"].upper()
        conn.execute(
            "INSERT INTO mandates (id, team_id, counterparty_id, allowed_destination_ids, max_amount_minor, asset, required_evidence, valid_until, drafted_by, purpose) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (mandate_id, team, matches[0]["id"], Jsonb([d["id"] for d in destinations]), to_minor(inputs["max_amount"], asset), asset, Jsonb(inputs.get("required_evidence", [])), inputs["valid_until"], state.get("actor"), inputs.get("purpose")),
        )
        conn.execute(
            "INSERT INTO automation_rules (id, team_id, mandate_id, frequency, day_of_period, next_due) VALUES (%s, %s, %s, %s, %s, %s)",
            (rule_id, team, mandate_id, frequency, inputs["day_of_period"], next_due(frequency, inputs["day_of_period"], date.today() - timedelta(days=1))),
        )
        audit(conn, team, "USER", state.get("actor"), "MandateDrafted", "mandate", mandate_id, {"max": inputs["max_amount"], "asset": asset})
    return {"result": {"mandate_id": mandate_id, "status": "DRAFT", "note": "An Approver must activate this on the Automations screen before anything is prepared."}}


def set_mandate_status(team_id: str, user_id: str, mandate_id: str, status: str) -> dict:
    """Activate, pause or revoke. Pausing or revoking cancels unapproved prepared payments from this Mandate."""
    assert status in ("ACTIVE", "PAUSED", "REVOKED")
    with tx() as conn:
        role = fetch_one(conn, "SELECT role FROM memberships WHERE team_id = %s AND user_id = %s", (team_id, user_id))
        if not role or role["role"] != "APPROVER":
            raise PermissionError("Only an Approver can change a Mandate.")
        row = conn.execute("UPDATE mandates SET status = %s, activated_by = CASE WHEN %s = 'ACTIVE' THEN %s ELSE activated_by END WHERE id = %s AND team_id = %s RETURNING id", (status, status, user_id, mandate_id, team_id)).fetchone()
        if row is None:
            raise ValueError("That Mandate isn't in this team.")
        cancelled = 0
        if status != "ACTIVE":
            intents = fetch_all(
                conn,
                "SELECT p.id, p.obligation_id FROM payment_intents p JOIN obligations o ON o.id = p.obligation_id WHERE o.mandate_id = %s AND p.status = 'READY_FOR_APPROVAL'",
                (mandate_id,),
            )
            for intent in intents:
                conn.execute("UPDATE approvals SET status = 'CANCELLED', reason = 'Mandate changed' WHERE intent_id = %s AND status = 'PENDING'", (intent["id"],))
                conn.execute("UPDATE payment_intents SET status = 'CANCELLED', updated_at = now() WHERE id = %s", (intent["id"],))
                conn.execute("UPDATE obligations SET status = 'CANCELLED', updated_at = now() WHERE id = %s", (intent["obligation_id"],))
                cancelled += 1
        audit(conn, team_id, "USER", user_id, f"Mandate{status.title()}", "mandate", mandate_id, {"cancelled": cancelled})
    return {"status": status, "cancelled": cancelled}


@node("Claim Occurrence")
def claim_occurrence(state: RunState, ctx) -> dict:
    """The database allows one claim per Mandate and period, so a second one stops here."""
    team, inputs = state["team_id"], state["inputs"]
    occurrence_id = new_id("occ")
    try:
        with tx() as conn:
            conn.execute(
                "INSERT INTO occurrences (id, team_id, mandate_id, period, claimed_by, run_id, is_late) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (occurrence_id, team, inputs["mandate_id"], inputs["period"], inputs.get("claimed_by", "TIMER"), state["run_id"], bool(inputs.get("late"))),
            )
    except UniqueViolation:
        return {"stop": f"The {inputs['period']} Occurrence was already claimed, so there's still only one payment."}
    return {"result": {"occurrence_id": occurrence_id, "late": bool(inputs.get("late"))}}


@node("Load Mandate")
def load_mandate(state: RunState, ctx) -> dict:
    with tx() as conn:
        mandate = fetch_one(conn, "SELECT * FROM mandates WHERE id = %s AND team_id = %s", (state["inputs"]["mandate_id"], state["team_id"]))
    if mandate is None or mandate["status"] != "ACTIVE":
        return {"stop": "This Mandate isn't active, so nothing was prepared."}
    amount = state["inputs"].get("amount_minor") or mandate["max_amount_minor"]
    return {
        "counterparty_id": mandate["counterparty_id"],
        "asset": mandate["asset"],
        "amount_minor": str(int(amount)),
        "inputs": {**state["inputs"], "asset": mandate["asset"], "amount_minor": str(int(amount)), "purpose": mandate["purpose"] or "Recurring payment", "source_type": "OCCURRENCE", "period": state["inputs"]["period"]},
    }


@node("Check Mandate")
def check_mandate(state: RunState, ctx) -> dict:
    with tx() as conn:
        mandate = fetch_one(conn, "SELECT * FROM mandates WHERE id = %s", (state["inputs"]["mandate_id"],))
        if state.get("result", {}).get("occurrence_id"):
            conn.execute("UPDATE occurrences SET obligation_id = %s WHERE id = %s", (state["obligation_id"], state["result"]["occurrence_id"]))
    results = []
    if int(state["amount_minor"]) > int(mandate["max_amount_minor"]):
        results.append(record_check(state, "Check Mandate", "ABOVE_MANDATE_MAX", "NEEDS_REVIEW", f"The amount {state['amount_minor']} is above the Mandate's maximum of {int(mandate['max_amount_minor'])}.", {"amount": state["amount_minor"], "max": str(mandate["max_amount_minor"])}))
    if date.today() > mandate["valid_until"]:
        results.append(record_check(state, "Check Mandate", "MANDATE_EXPIRED", "BLOCK", "This Mandate has ended."))
    if "invoice" in (mandate["required_evidence"] or []) and not state.get("document_id"):
        results.append(record_check(state, "Check Mandate", "EVIDENCE_MISSING", "NEEDS_INFORMATION", "This Mandate requires an invoice, and none has arrived for this period."))
    if not results:
        results.append(record_check(state, "Check Mandate", "WITHIN_MANDATE", "PASS", "The amount, date and evidence fit this Mandate."))
    return {"checks": results}


def fire_due_occurrences(today: date | None = None) -> int:
    """Called by the scheduler. Each missed period gets its own Occurrence and is labelled as late."""
    today = today or date.today()
    created = 0
    with tx() as conn:
        rules = fetch_all(
            conn,
            "SELECT r.*, m.valid_until FROM automation_rules r JOIN mandates m ON m.id = r.mandate_id WHERE m.status = 'ACTIVE' AND r.next_due <= %s FOR UPDATE OF r SKIP LOCKED",
            (today,),
        )
        for rule in rules:
            due = rule["next_due"]
            while due <= today and due <= rule["valid_until"]:
                period = period_of(rule["frequency"], due)
                enqueue(conn, "fire_occurrence", rule["team_id"], {"mandate_id": rule["mandate_id"], "period": period, "late": due < today}, dedupe_key=f"occ:{rule['mandate_id']}:{period}")
                created += 1
                due = next_due(rule["frequency"], rule["day_of_period"], due)
            conn.execute("UPDATE automation_rules SET next_due = %s WHERE id = %s", (due, rule["id"]))
    return created
