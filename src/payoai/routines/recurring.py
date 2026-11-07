"""Set Up Recurring and Prepare Occurrence. Each Occurrence still needs its own approval.

A period has one claim. The timer and a matching invoice race for it in the database: the first
claims it and the other attaches to it, so there's only one payment.
"""

import calendar
import re
from datetime import date, timedelta

from payoai.db import Jsonb, fetch_all, fetch_one, new_id, tx
from payoai.domain.money import format_minor, to_minor
from payoai.money.locks import lock_obligation
from payoai.queue import enqueue
from payoai.routines import accounting
from payoai.routines.nodes.checks import _date, _matches, _normal_name
from payoai.routines.nodes.common import RunState, node, record_check
from payoai.routines.nodes.decision import notify_approvers
from payoai.trace import audit

NO_INVOICE_WARNING = "No invoice attached."
UNPAID_WITHOUT_PAYMENT = ("REGISTERED", "NEEDS_INFORMATION", "NEEDS_REVIEW", "VERIFIED", "BLOCKED")
DEAD_INTENT = ["FAILED", "REJECTED", "CANCELLED"]
OCCURRENCE_DOCUMENT = "OCCURRENCE_DOCUMENT"


def business_day(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _nominal_in_month(year: int, month: int, day_of_period: int) -> date:
    return date(year, month, min(day_of_period, calendar.monthrange(year, month)[1]))


def next_due(frequency: str, day_of_period: int, after: date, use_business_days: bool = True) -> date:
    """The next scheduled date strictly after `after`, using the nominal date to keep the schedule stable."""
    if frequency == "WEEKLY":
        delta = (day_of_period - after.weekday() - 1) % 7 + 1
        nominal = after + timedelta(days=delta)
    else:
        year, month = after.year, after.month
        nominal = _nominal_in_month(year, month, day_of_period)
        if nominal <= after:
            month = month % 12 + 1
            year = year + (1 if month == 1 else 0)
            nominal = _nominal_in_month(year, month, day_of_period)
    return business_day(nominal) if use_business_days else nominal


def period_of(frequency: str, day: date) -> str:
    return day.strftime("%Y-%m") if frequency == "MONTHLY" else f"{day.isocalendar().year}-W{day.isocalendar().week:02d}"


def period_of_due(frequency: str, day_of_period: int, due: date) -> str:
    """The period a scheduled date belongs to.

    A date moved past a weekend still belongs to its nominal period. Otherwise 31 January moved to
    2 February would take February's period, and February's own payment would never be prepared.
    """
    for back in (0, 1, 2):
        day = due - timedelta(days=back)
        nominal = day.weekday() == day_of_period if frequency == "WEEKLY" else day == _nominal_in_month(day.year, day.month, day_of_period)
        if nominal:
            return period_of(frequency, day)
    return period_of(frequency, due)


def invoice_period(fields: dict, frequency: str) -> str | None:
    """An exact period on the invoice, or the period its due date falls in. Never a guess beyond that."""
    stated = str(fields.get("period") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}" if frequency == "MONTHLY" else r"\d{4}-W\d{2}", stated):
        return stated
    for key in ("due_date", "issue_date"):
        day = _date(fields.get(key))
        if day:
            return period_of(frequency, day)
    return None


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
        destinations = fetch_all(conn, "SELECT id FROM payment_destinations WHERE team_id = %s AND counterparty_id = %s AND status = 'VERIFIED' AND asset = %s", (team, matches[0]["id"], asset))
        if not destinations:
            return {"stop": f"There's no Verified Destination that takes {asset} for this counterparty."}
        mandate_id, rule_id = new_id("man"), new_id("rule")
        frequency = inputs["frequency"].upper()
        regular = to_minor(inputs["amount"], asset) if inputs.get("amount") else None
        conn.execute(
            "INSERT INTO mandates (id, team_id, counterparty_id, allowed_destination_ids, max_amount_minor, regular_amount_minor, asset, required_evidence, valid_until, drafted_by, purpose) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (mandate_id, team, matches[0]["id"], Jsonb([d["id"] for d in destinations]), to_minor(inputs["max_amount"], asset), regular, asset, Jsonb(inputs.get("required_evidence", [])), inputs["valid_until"], state.get("actor"), inputs.get("purpose")),
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
        # A revoked Mandate stays revoked. Changing one means a new version that's activated again.
        row = conn.execute(
            "UPDATE mandates SET status = %s, activated_by = CASE WHEN %s = 'ACTIVE' THEN %s ELSE activated_by END WHERE id = %s AND team_id = %s AND status NOT IN ('REVOKED', 'SUPERSEDED') RETURNING id",
            (status, status, user_id, mandate_id, team_id),
        ).fetchone()
        if row is None:
            raise ValueError("That Mandate isn't in this team, or has already ended.")
        cancelled = 0
        if status == "ACTIVE":
            _skip_periods_before_activation(conn, mandate_id)
        else:
            cancelled = _cancel_unapproved(conn, team_id, mandate_id)
        audit(conn, team_id, "USER", user_id, f"Mandate{status.title()}", "mandate", mandate_id, {"cancelled": cancelled})
    return {"status": status, "cancelled": cancelled}


def _skip_periods_before_activation(conn, mandate_id: str) -> None:
    """Periods from before activation or during a pause were never authorised, so they don't back-fill."""
    today = date.today()
    for rule in fetch_all(conn, "SELECT * FROM automation_rules WHERE mandate_id = %s FOR UPDATE", (mandate_id,)):
        due = rule["next_due"]
        while due is not None and due < today:
            due = next_due(rule["frequency"], rule["day_of_period"], due)
        conn.execute("UPDATE automation_rules SET next_due = %s WHERE id = %s", (due, rule["id"]))


def _cancel_unapproved(conn, team_id: str, mandate_id: str) -> int:
    """A real invoice is still owed, so it returns for review. A timer's own record is simply cancelled."""
    cancelled = 0
    rows = fetch_all(conn, "SELECT id, source_type FROM obligations WHERE team_id = %s AND mandate_id = %s AND status = ANY(%s) ORDER BY id", (team_id, mandate_id, ["PAYMENT_PREPARED", *UNPAID_WITHOUT_PAYMENT]))
    for obligation in rows:
        lock_obligation(conn, obligation["id"])
        intents = fetch_all(conn, "SELECT id FROM payment_intents WHERE obligation_id = %s AND status = 'READY_FOR_APPROVAL'", (obligation["id"],))
        for intent in intents:
            conn.execute("UPDATE approvals SET status = 'CANCELLED', reason = 'Mandate changed' WHERE intent_id = %s AND status = 'PENDING'", (intent["id"],))
            conn.execute("UPDATE payment_intents SET status = 'CANCELLED', updated_at = now() WHERE id = %s", (intent["id"],))
            cancelled += 1
        conn.execute(
            "UPDATE obligations SET status = %s, updated_at = now() WHERE id = %s AND status = ANY(%s)",
            ("CANCELLED" if obligation["source_type"] == "OCCURRENCE" else "NEEDS_REVIEW", obligation["id"], ["PAYMENT_PREPARED", *UNPAID_WITHOUT_PAYMENT]),
        )
    return cancelled


def _claim(conn, team_id: str, mandate_id: str, period: str, claimed_by: str, run_id: str, late: bool = False) -> str | None:
    """Claims a period for an active Mandate in this team. Returns None if it can't be claimed."""
    row = conn.execute(
        """
        INSERT INTO occurrences (id, team_id, mandate_id, period, claimed_by, run_id, is_late)
        SELECT %s, m.team_id, m.id, %s, %s, %s, %s FROM mandates m WHERE m.id = %s AND m.team_id = %s AND m.status = 'ACTIVE'
        ON CONFLICT (mandate_id, period) DO NOTHING RETURNING id
        """,
        (new_id("occ"), period, claimed_by, run_id, late, mandate_id, team_id),
    ).fetchone()
    if row:
        return row["id"]
    # A claim whose run failed before recording anything would otherwise lose the period for good.
    row = conn.execute(
        """
        UPDATE occurrences o SET claimed_by = %s, run_id = %s, is_late = %s
        FROM routine_runs r, mandates m
        WHERE o.mandate_id = %s AND o.period = %s AND o.team_id = %s AND o.obligation_id IS NULL AND o.document_id IS NULL
          AND r.id = o.run_id AND r.status IN ('FAILED', 'CANCELLED') AND m.id = o.mandate_id AND m.status = 'ACTIVE'
        RETURNING o.id
        """,
        (claimed_by, run_id, late, mandate_id, period, team_id),
    ).fetchone()
    return row["id"] if row else None


@node("Claim Occurrence")
def claim_occurrence(state: RunState, ctx) -> dict:
    """The database allows one claim per Mandate and period, so a second one stops here."""
    team, inputs = state["team_id"], state["inputs"]
    with tx() as conn:
        occurrence_id = _claim(conn, team, inputs["mandate_id"], inputs["period"], inputs.get("claimed_by", "TIMER"), state["run_id"], bool(inputs.get("late")))
        if occurrence_id is None:
            mandate = fetch_one(conn, "SELECT status FROM mandates WHERE id = %s AND team_id = %s", (inputs["mandate_id"], team))
    if occurrence_id is None:
        if mandate is None or mandate["status"] != "ACTIVE":
            return {"stop": "This Mandate isn't active, so nothing was prepared."}
        return {"stop": f"The {inputs['period']} Occurrence was already claimed, so there's still only one payment."}
    return {"occurrence_id": occurrence_id, "result": {"occurrence_id": occurrence_id, "late": bool(inputs.get("late"))}}


@node("Load Mandate")
def load_mandate(state: RunState, ctx) -> dict:
    with tx() as conn:
        mandate = fetch_one(conn, "SELECT * FROM mandates WHERE id = %s AND team_id = %s", (state["inputs"]["mandate_id"], state["team_id"]))
        if mandate is None or mandate["status"] != "ACTIVE":
            # It stopped between the claim and here, so the period stays open if it's resumed.
            conn.execute("DELETE FROM occurrences WHERE id = %s AND obligation_id IS NULL AND document_id IS NULL", (state.get("occurrence_id"),))
            return {"stop": "This Mandate isn't active, so nothing was prepared."}
    amount = state["inputs"].get("amount_minor") or mandate["regular_amount_minor"]
    note = None
    if amount is None:
        # A maximum is a limit, not what's owed, so a person must confirm this figure.
        amount = mandate["max_amount_minor"]
        note = {"rule_id": "AMOUNT_ASSUMED_MAXIMUM", "reason": f"This Mandate has no regular amount, so {format_minor(int(amount), mandate['asset'])}, its maximum, was assumed. Check what's actually owed."}
    return {
        "mandate_note": note,
        "counterparty_id": mandate["counterparty_id"],
        "asset": mandate["asset"],
        "amount_minor": str(int(amount)),
        "mandate_id": mandate["id"],
        "period": state["inputs"]["period"],
        "inputs": {**state["inputs"], "asset": mandate["asset"], "amount_minor": str(int(amount)), "purpose": mandate["purpose"] or "Recurring payment", "source_type": "OCCURRENCE"},
    }


@node("Match Mandate")
def match_mandate(state: RunState, ctx) -> dict:
    """Links an invoice to an active Mandate only on exact facts: counterparty, asset and destination.

    A near match never takes the period. It carries on as an ordinary bill, with a note for review,
    and Check Destination still blocks a changed destination.
    """
    if state.get("mandate_id"):
        return {}
    fields = state.get("fields") or {}
    team, counterparty = state["team_id"], state.get("counterparty_id")
    asset = (fields.get("currency") or "").upper()
    with tx() as conn:
        mandates = fetch_all(
            conn,
            """
            SELECT m.*, r.frequency, c.display_name FROM mandates m JOIN automation_rules r ON r.mandate_id = m.id
            JOIN counterparties c ON c.id = m.counterparty_id
            WHERE m.team_id = %s AND m.counterparty_id = %s AND m.status = 'ACTIVE'
            """,
            (team, counterparty),
        )
    if not mandates:
        return {}
    same_asset = [m for m in mandates if m["asset"] == asset]
    if not same_asset:
        return _note("MANDATE_CURRENCY_DIFFERS", f"{mandates[0]['display_name']} has an active Mandate in {mandates[0]['asset']}, but this invoice is in {asset or 'an unknown currency'}. It wasn't matched to the Mandate and nothing was converted.")
    if len(same_asset) > 1:
        return _note("MANDATE_AMBIGUOUS", f"{same_asset[0]['display_name']} has several active Mandates in {asset}, so this invoice wasn't matched to one.")
    mandate = same_asset[0]
    if date.today() > mandate["valid_until"]:
        return _note("MANDATE_ENDED", "This counterparty's Mandate has ended, so the invoice wasn't matched to it.")
    if _already_recorded(state, fields):
        return {}  # An exact repeat takes no claim. Check Duplicates blocks it as an ordinary bill.
    if not _destination_fits(team, mandate, fields):
        return _note("MANDATE_DESTINATION_DIFFERS", "The payment details on this invoice aren't a destination this Mandate allows, so it wasn't matched to the Mandate.")
    period = invoice_period(fields, mandate["frequency"])
    if period is None:
        return _note("MANDATE_PERIOD_UNKNOWN", "This invoice shows no period or date, so it couldn't be matched to a Mandate period. A person should check it isn't also paid by the Mandate.")
    with tx() as conn:
        occurrence_id = _claim(conn, team, mandate["id"], period, "INVOICE", state["run_id"])
    if occurrence_id:
        return {"mandate_id": mandate["id"], "period": period, "occurrence_id": occurrence_id, "mandate_note": None}
    return _attach(state, mandate, period)


def _note(rule_id: str, reason: str) -> dict:
    return {"mandate_note": {"rule_id": rule_id, "reason": reason}}


def _already_recorded(state: RunState, fields: dict) -> bool:
    reference = fields.get("invoice_number")
    with tx() as conn:
        row = fetch_one(
            conn,
            """
            SELECT o.id FROM obligations o LEFT JOIN documents d ON d.id = o.document_id
            WHERE o.team_id = %(team)s AND o.id IS DISTINCT FROM %(me)s AND o.status <> ALL(%(dead)s)
              AND ((o.counterparty_id = %(cp)s AND o.invoice_reference = %(ref)s)
                OR d.sha256 = (SELECT sha256 FROM documents WHERE id = %(doc)s AND team_id = %(team)s))
            LIMIT 1
            """,
            {"team": state["team_id"], "me": state.get("obligation_id"), "dead": ["SUPERSEDED", "CANCELLED", "BLOCKED"], "cp": state.get("counterparty_id"), "ref": reference, "doc": state.get("document_id")},
        )
    return row is not None


def _destination_fits(team_id: str, mandate: dict, fields: dict) -> bool:
    """Full equality with a verified, allowed destination. An invoice that states none relies on the Mandate's."""
    stated = {k: v for k, v in (fields.get("payment_details") or {}).items() if v not in (None, "")}
    with tx() as conn:
        allowed = fetch_all(
            conn,
            "SELECT * FROM payment_destinations WHERE team_id = %s AND id = ANY(%s) AND status = 'VERIFIED' AND asset = %s",
            (team_id, list(mandate["allowed_destination_ids"]), mandate["asset"]),
        )
    return any(_fits(row, stated) for row in allowed)


def _fits(destination: dict, stated: dict) -> bool:
    if (stated.get("iban") or stated.get("account_number") or stated.get("wallet_address")) and not _matches(destination, stated):
        return False
    if stated.get("chain_id") and str(stated["chain_id"]) != str(destination["chain_id"]):
        return False
    if stated.get("token_contract") and str(stated["token_contract"]).lower() != str(destination["token_contract"]).lower():
        return False
    return True


def _attach(state: RunState, mandate: dict, period: str) -> dict:
    """The period is already claimed, so this invoice can only attach to that claim. It never adds a payment."""
    team, fields = state["team_id"], state.get("fields") or {}
    document_id, extraction_id = state.get("document_id"), state.get("extraction_id")
    total = _invoice_total(fields, mandate["asset"])
    with tx() as conn:
        occurrence = fetch_one(conn, "SELECT * FROM occurrences WHERE mandate_id = %s AND period = %s AND team_id = %s FOR UPDATE", (mandate["id"], period, team))
        if occurrence["claimed_by"] == "INVOICE":
            return _refuse_second_invoice(conn, state, occurrence, period)
        if occurrence["obligation_id"] is None:
            # The timer has claimed but not yet recorded what's owed. Check Mandate picks this up.
            if occurrence["document_id"]:
                return _refuse_second_invoice(conn, state, occurrence, period)
            conn.execute("UPDATE occurrences SET document_id = %s, extraction_id = %s WHERE id = %s", (document_id, extraction_id, occurrence["id"]))
            audit(conn, team, "SYSTEM", None, "InvoiceAttachedToOccurrence", "occurrence", occurrence["id"], {"document_id": document_id})
            return _attached(state, occurrence, None, None, "Attached to this period's Occurrence, which is being prepared. There's still only one payment.")
        lock_obligation(conn, occurrence["obligation_id"])
        obligation = fetch_one(conn, "SELECT * FROM obligations WHERE id = %s", (occurrence["obligation_id"],))
        if obligation["document_id"] is not None:
            return _refuse_second_invoice(conn, state, occurrence, period)
        # A bill registered earlier is now this Occurrence. Retiring it first keeps its invoice number free.
        if state.get("obligation_id"):
            conn.execute("UPDATE obligations SET status = 'SUPERSEDED', superseded_by = %s, updated_at = now() WHERE id = %s", (obligation["id"], state["obligation_id"]))
        intent = fetch_one(conn, "SELECT * FROM payment_intents WHERE obligation_id = %s AND status <> ALL(%s) ORDER BY created_at DESC LIMIT 1", (obligation["id"], DEAD_INTENT))
        same_amount = total is not None and obligation["amount_minor"] is not None and total == int(obligation["amount_minor"])
        if intent is None and obligation["status"] in UNPAID_WITHOUT_PAYMENT:
            return _adopt_invoice(conn, state, occurrence, obligation, mandate, total, period)
        conn.execute("UPDATE occurrences SET document_id = %s, extraction_id = %s WHERE id = %s", (document_id, extraction_id, occurrence["id"]))
        audit(conn, team, "SYSTEM", None, "InvoiceAttachedToOccurrence", "occurrence", occurrence["id"], {"document_id": document_id, "same_amount": same_amount})
        if intent is None:
            return _attached(state, occurrence, obligation, None, f"This period's payment is {obligation['status']}, so the invoice was kept on the Occurrence and no payment was prepared.")
        figures = f"The invoice says {_money(total, mandate['asset'])}, and this period's payment is {format_minor(int(intent['amount_minor']), intent['asset'])}."
        if not same_amount:
            return _attach_with_different_amount(conn, state, occurrence, obligation, intent, figures)
        _link_document(conn, obligation["id"], document_id, extraction_id, fields.get("invoice_number"))
        if intent["status"] == "READY_FOR_APPROVAL":
            conn.execute("UPDATE payment_intents SET warnings = warnings - %s, updated_at = now() WHERE id = %s", (NO_INVOICE_WARNING, intent["id"]))
        _resolve_occurrence_exception(conn, obligation["id"])
        _settle_expense_evidence(conn, team, intent, document_id, fields)
    return _attached(state, occurrence, obligation, intent, "Attached to this period's existing payment, so no second payment was prepared.")


def _adopt_invoice(conn, state: RunState, occurrence: dict, obligation: dict, mandate: dict, total: int | None, period: str) -> dict:
    """No payment exists yet, such as a run waiting for its invoice. The shared checks now run with it."""
    fields = state.get("fields") or {}
    conn.execute(
        "UPDATE obligations SET document_id = %s, extraction_id = %s, invoice_reference = %s, amount_minor = %s, due_date = COALESCE(%s, due_date), updated_at = now() WHERE id = %s",
        (state.get("document_id"), state.get("extraction_id"), fields.get("invoice_number"), total, _date(fields.get("due_date")), obligation["id"]),
    )
    conn.execute("UPDATE occurrences SET document_id = %s, extraction_id = %s WHERE id = %s", (state.get("document_id"), state.get("extraction_id"), occurrence["id"]))
    _resolve_occurrence_exception(conn, obligation["id"])
    audit(conn, state["team_id"], "SYSTEM", None, "InvoiceAttachedToOccurrence", "occurrence", occurrence["id"], {"document_id": state.get("document_id"), "prepares_payment": True})
    return {
        "obligation_id": obligation["id"], "mandate_id": mandate["id"], "period": period, "occurrence_id": occurrence["id"], "mandate_note": None,
        "amount_minor": str(total) if total is not None else None, "asset": mandate["asset"],
    }


def _attach_with_different_amount(conn, state: RunState, occurrence: dict, obligation: dict, intent: dict, figures: str) -> dict:
    """The approved figure can't change, so a person sees both. The invoice isn't counted as evidence yet."""
    team = state["team_id"]
    warning = f"{figures} Check before approving."
    if intent["status"] == "READY_FOR_APPROVAL":
        conn.execute("UPDATE payment_intents SET warnings = warnings || %s, requires_review = true, updated_at = now() WHERE id = %s", (Jsonb([warning]), intent["id"]))
    notify_approvers(conn, team, "OCCURRENCE_INVOICE_DIFFERS", {"occurrence_id": occurrence["id"], "intent_id": intent["id"], "document_id": state.get("document_id"), "message": figures}, f"occdiff:{occurrence['id']}")
    return _attached(state, occurrence, obligation, intent, f"Attached to this period's existing payment, so no second payment was prepared. {figures} A person needs to check this.", amount_differs=True)


def _refuse_second_invoice(conn, state: RunState, occurrence: dict, period: str) -> dict:
    """A period can't be claimed twice, however close the match. Nothing is recorded as owed."""
    team = state["team_id"]
    audit(conn, team, "SYSTEM", None, "SecondInvoiceRefused", "occurrence", occurrence["id"], {"document_id": state.get("document_id")})
    notify_approvers(conn, team, "OCCURRENCE_ALREADY_CLAIMED", {"occurrence_id": occurrence["id"], "document_id": state.get("document_id")}, f"occsecond:{occurrence['id']}:{state.get('document_id')}")
    bill = occurrence["obligation_id"] or "another run"
    return {
        "stop": f"The {period} period of this Mandate is already claimed by {bill}, so no payment was prepared. If this invoice corrects that bill, use Replace Bill.",
        "result": {"occurrence_id": occurrence["id"], "attached": False, "already_claimed_by": occurrence["obligation_id"]},
    }


def _attached(state: RunState, occurrence: dict, obligation: dict | None, intent: dict | None, message: str, amount_differs: bool = False) -> dict:
    return {
        "stop": message,
        "result": {
            "occurrence_id": occurrence["id"], "attached": True, "amount_differs": amount_differs,
            "attached_to_obligation": obligation["id"] if obligation else None, "attached_to_payment": intent["id"] if intent else None,
        },
    }


def _link_document(conn, obligation_id: str, document_id: str | None, extraction_id: str | None, reference: str | None) -> None:
    conn.execute(
        "UPDATE obligations SET document_id = %s, extraction_id = %s, invoice_reference = %s, updated_at = now() WHERE id = %s AND document_id IS NULL",
        (document_id, extraction_id, reference, obligation_id),
    )


def _resolve_occurrence_exception(conn, obligation_id: str) -> None:
    conn.execute("UPDATE exceptions SET status = 'RESOLVED' WHERE expense_id = %s AND kind = %s AND status = 'OPEN'", (obligation_id, OCCURRENCE_DOCUMENT))


def _settle_expense_evidence(conn, team_id: str, intent: dict, document_id: str | None, fields: dict) -> None:
    """The timer's payment already went through and is missing evidence. This invoice may be that evidence."""
    row = fetch_one(conn, "SELECT expense_id FROM expense_versions WHERE team_id = %s AND intent_id = %s LIMIT 1", (team_id, intent["id"]))
    if row:
        accounting.apply_evidence(conn, team_id, "SYSTEM", row["expense_id"], document_id, fields)


def _invoice_total(fields: dict, asset: str) -> int | None:
    try:
        return to_minor(fields["total"], asset) if fields.get("total") else None
    except (ValueError, TypeError):
        return None


def _money(amount: int | None, asset: str) -> str:
    return format_minor(amount, asset) if amount is not None else "an unreadable amount"


@node("Check Mandate")
def check_mandate(state: RunState, ctx) -> dict:
    """Confirms the amount, currency, date and evidence fit, and limits the destinations to the Mandate's."""
    note = state.get("mandate_note")
    if not state.get("mandate_id"):
        return {"checks": [record_check(state, "Check Mandate", note["rule_id"], "NEEDS_REVIEW", note["reason"])] if note else []}
    updates = _link_occurrence(state)
    current = {**state, **updates}
    with tx() as conn:
        mandate = fetch_one(conn, "SELECT * FROM mandates WHERE id = %s AND team_id = %s", (current["mandate_id"], current["team_id"]))
        has_contract = fetch_one(conn, "SELECT id FROM contact_channels WHERE team_id = %s AND counterparty_id = %s AND kind = 'contract' LIMIT 1", (current["team_id"], mandate["counterparty_id"]))
    results = [record_check(current, "Check Mandate", note["rule_id"], "NEEDS_REVIEW", note["reason"])] if note else []
    if current.get("asset") != mandate["asset"]:
        results.append(record_check(current, "Check Mandate", "MANDATE_CURRENCY_DIFFERS", "BLOCK", f"This is in {current.get('asset')}, but the Mandate only allows {mandate['asset']}. Nothing is converted."))
    elif current.get("amount_minor") and int(current["amount_minor"]) > int(mandate["max_amount_minor"]):
        amount, maximum = format_minor(int(current["amount_minor"]), mandate["asset"]), format_minor(int(mandate["max_amount_minor"]), mandate["asset"])
        results.append(record_check(current, "Check Mandate", "ABOVE_MANDATE_MAX", "NEEDS_REVIEW", f"The amount {amount} is above the Mandate's maximum of {maximum}.", {"amount": current["amount_minor"], "max": str(mandate["max_amount_minor"])}))
    if date.today() > mandate["valid_until"]:
        results.append(record_check(current, "Check Mandate", "MANDATE_EXPIRED", "BLOCK", "This Mandate has ended."))
    results += _check_evidence(current, mandate, bool(has_contract))
    if not results:
        results.append(record_check(current, "Check Mandate", "WITHIN_MANDATE", "PASS", "The amount, date and evidence fit this Mandate."))
    return {**updates, "checks": results, "allowed_destination_ids": list(mandate["allowed_destination_ids"])}


def _link_occurrence(state: RunState) -> dict:
    """Ties the claim to what's owed, and picks up an invoice that attached while this run was starting."""
    team, obligation_id = state["team_id"], state["obligation_id"]
    occurrence_id = state.get("occurrence_id")
    with tx() as conn:
        conn.execute("UPDATE obligations SET mandate_id = %s, period = COALESCE(period, %s), updated_at = now() WHERE id = %s AND team_id = %s AND mandate_id IS NULL", (state["mandate_id"], state.get("period"), obligation_id, team))
        if not occurrence_id:
            return {}
        row = fetch_one(conn, "UPDATE occurrences SET obligation_id = %s WHERE id = %s AND team_id = %s AND obligation_id IS NULL RETURNING document_id, extraction_id", (obligation_id, occurrence_id, team))
        if row is None or row["document_id"] is None or state.get("document_id"):
            return {}
        extraction = fetch_one(conn, "SELECT fields FROM extractions WHERE id = %s", (row["extraction_id"],))
        fields = (extraction or {}).get("fields") or {}
        total = _invoice_total(fields, state.get("asset") or "")
        conn.execute(
            "UPDATE obligations SET document_id = %s, extraction_id = %s, invoice_reference = %s, amount_minor = %s, due_date = COALESCE(%s, due_date), updated_at = now() WHERE id = %s",
            (row["document_id"], row["extraction_id"], fields.get("invoice_number"), total, _date(fields.get("due_date")), obligation_id),
        )
    return {"document_id": row["document_id"], "extraction_id": row["extraction_id"], "fields": fields, "amount_minor": str(total) if total is not None else None}


def _check_evidence(state: RunState, mandate: dict, has_contract: bool) -> list[dict]:
    """Any one listed kind of evidence is enough, and a contract on file counts where it's listed."""
    required = mandate["required_evidence"] or []
    if not required or (state.get("document_id") and "invoice" in required) or (has_contract and "contract" in required):
        return []
    if "invoice" not in required:
        return [record_check(state, "Check Mandate", "EVIDENCE_MISSING", "NEEDS_INFORMATION", "This Mandate requires a contract on file, and there isn't one.")]
    with tx() as conn:
        conn.execute(
            "INSERT INTO exceptions (id, team_id, expense_id, kind, details) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (new_id("exc"), state["team_id"], state["obligation_id"], OCCURRENCE_DOCUMENT, Jsonb({"reason": "This Mandate requires an invoice, and none has arrived for this period.", "mandate_id": mandate["id"], "period": state.get("period"), "obligation_id": state["obligation_id"]})),
        )
        notify_approvers(conn, state["team_id"], "MISSING_DOCUMENT", {"obligation_id": state["obligation_id"], "mandate_id": mandate["id"], "period": state.get("period")}, f"occmissing:{state['obligation_id']}")
    return [record_check(state, "Check Mandate", "EVIDENCE_MISSING", "NEEDS_INFORMATION", "This Mandate requires an invoice, and none has arrived for this period.")]


def upcoming_due(conn, team_id: str, horizon: date) -> list[dict]:
    """Occurrences not yet claimed, for Cash Analysis. Claimed ones are counted through their bill."""
    rows = fetch_all(
        conn,
        """
        SELECT m.id AS mandate_id, m.asset, m.max_amount_minor, r.next_due, r.frequency, r.day_of_period
        FROM automation_rules r JOIN mandates m ON m.id = r.mandate_id
        WHERE m.team_id = %s AND m.status = 'ACTIVE' AND r.next_due <= %s AND r.next_due <= m.valid_until
        """,
        (team_id, horizon),
    )
    out = []
    for row in rows:
        period = period_of_due(row["frequency"], row["day_of_period"], row["next_due"])
        claimed = fetch_one(conn, "SELECT id FROM occurrences WHERE mandate_id = %s AND period = %s", (row["mandate_id"], period))
        if not claimed:
            out.append({"id": f"{row['mandate_id']}:{period}", "amount_minor": row["max_amount_minor"], "asset": row["asset"], "due_date": row["next_due"], "status": "UPCOMING_OCCURRENCE", "invoice_reference": None})
    return out


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
                period = period_of_due(rule["frequency"], rule["day_of_period"], due)
                enqueue(conn, "fire_occurrence", rule["team_id"], {"mandate_id": rule["mandate_id"], "period": period, "late": due < today}, dedupe_key=f"occ:{rule['mandate_id']}:{period}")
                created += 1
                due = next_due(rule["frequency"], rule["day_of_period"], due)
            conn.execute("UPDATE automation_rules SET next_due = %s WHERE id = %s", (due, rule["id"]))
    return created
