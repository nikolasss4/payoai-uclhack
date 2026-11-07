"""Revise Expense, Chase Missing Document, category suggestions and manual corrections."""

from datetime import timedelta

from payoai.agent import llm
from payoai.db import Jsonb, fetch_one, new_id, tx
from payoai.domain.decide import same_details
from payoai.domain.money import format_minor, to_minor
from payoai.memory import preferences_for
from payoai.routines.nodes.common import RunState, node
from payoai.routines.nodes.decision import notify_approvers
from payoai.routines.nodes.documents import EXTRACTION_PROMPT, EXTRACTION_SCHEMA, verify_quotes
from payoai.trace import TraceContext, audit

CATEGORIES = ["Software", "Marketing", "Contractors", "Office", "Travel", "Professional fees", "Equipment", "Other"]
CATEGORY_SCHEMA = {"type": "object", "properties": {"category": {"type": "string", "enum": CATEGORIES}}, "required": ["category"]}
DRAFT_SCHEMA = {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}
DATE_WINDOW = timedelta(days=45)


def latest(conn, team_id: str, expense_id: str) -> dict | None:
    return fetch_one(conn, "SELECT * FROM expense_versions WHERE team_id = %s AND expense_id = %s ORDER BY version DESC LIMIT 1", (team_id, expense_id))


def add_version(conn, current: dict, actor: str, reason: str, **changes) -> dict:
    """History is append-only, so every change is a new version with the old values kept."""
    fields = ("obligation_id", "intent_id", "counterparty_id", "business_purpose", "category", "category_is_suggestion", "accounting_period", "asset", "gross_minor", "net_minor", "tax_minor", "gbp_value_minor", "rate_source", "status")
    values = {k: changes.get(k, current[k]) for k in fields}
    version = current["version"] + 1
    conn.execute(
        f"""
        INSERT INTO expense_versions (id, team_id, expense_id, version, {", ".join(fields)}, reason, actor)
        VALUES (%s, %s, %s, %s, {", ".join(["%s"] * len(fields))}, %s, %s)
        """,
        (new_id("exv"), current["team_id"], current["expense_id"], version, *[values[k] for k in fields], reason, actor),
    )
    audit(conn, current["team_id"], "USER" if actor != "AGENT" and actor != "SYSTEM" else actor, actor, "ExpenseUpdated", "expense", current["expense_id"], {"from_version": current["version"], "to_version": version, "changes": {k: str(v) for k, v in changes.items()}, "reason": reason})
    return {**values, "version": version}


@node("Revise Expense")
def revise_expense(state: RunState, ctx) -> dict:
    """Corrects category or purpose only. The amount, recipient and invoice details stay locked."""
    team, inputs, actor = state["team_id"], state["inputs"], state.get("actor") or "SYSTEM"
    changes = {}
    if inputs.get("category"):
        changes.update(category=inputs["category"], category_is_suggestion=False)
    if inputs.get("business_purpose"):
        changes["business_purpose"] = inputs["business_purpose"]
    if not changes:
        return {"stop": "Only the category or business purpose can be corrected here."}
    with tx() as conn:
        current = latest(conn, team, inputs["expense_id"])
        if current is None:
            return {"stop": "That expense wasn't found in this team."}
        role = fetch_one(conn, "SELECT role FROM memberships WHERE team_id = %s AND user_id = %s", (team, actor))
        if current["status"] == "ACCOUNTED":
            return {"result": _adjustment(conn, current, changes, inputs["reason"], actor)}
        if current["status"] == "REVIEWED" and (not role or role["role"] != "APPROVER"):
            changes["status"] = "SUPPORTED"
            notify_approvers(conn, team, "EXPENSE_NEEDS_REVIEW", {"expense_id": current["expense_id"]}, f"expreview:{current['expense_id']}:{current['version'] + 1}")
        added = add_version(conn, current, actor, inputs["reason"], **changes)
    return {"result": {"expense_id": inputs["expense_id"], "version": added["version"], "status": added["status"]}}


def _adjustment(conn, current, changes, reason, actor) -> dict:
    """A closed record never changes. The accountant gets a reversal and adjustment proposal instead."""
    gross = int(current["gross_minor"] or 0)
    lines = [
        {"account": f"Expenses: {current['category'] or 'uncategorised'}", "debit_minor": 0, "credit_minor": gross, "asset": current["asset"], "note": "Reverse the original"},
        {"account": f"Expenses: {changes.get('category', current['category'])}", "debit_minor": gross, "credit_minor": 0, "asset": current["asset"], "note": reason},
    ]
    proposal_id = new_id("jnl")
    conn.execute("INSERT INTO journal_proposals (id, team_id, expense_id, kind, lines) VALUES (%s, %s, %s, 'ADJUSTMENT', %s)", (proposal_id, current["team_id"], current["expense_id"], Jsonb(lines)))
    audit(conn, current["team_id"], "USER", actor, "AdjustmentProposed", "expense", current["expense_id"], {"proposal": proposal_id, "reason": reason})
    return {"expense_id": current["expense_id"], "status": "ACCOUNTED", "adjustment_proposal": proposal_id, "note": "This month is closed, so the record is unchanged and a proposal was created for the accountant."}


def suggest_category(team_id: str, expense_id: str) -> dict:
    """An LLM suggestion, labelled as one. A saved hint for this counterparty comes first."""
    with tx() as conn:
        current = latest(conn, team_id, expense_id)
        if current is None or current["category"]:
            return {"skipped": True}
        cp = fetch_one(conn, "SELECT display_name, classifications FROM counterparties WHERE id = %s", (current["counterparty_id"],))
    hints = preferences_for(team_id, None, ("category_hint",), current["counterparty_id"])
    if hints:
        category = hints[0]["value"].get("category")
    else:
        run_id = new_id("run")
        with tx() as conn:
            conn.execute("INSERT INTO routine_runs (id, team_id, routine, trigger_type, inputs, status) VALUES (%s, %s, 'suggest_category', 'SYSTEM', %s, 'COMPLETED')", (run_id, team_id, Jsonb({"expense_id": expense_id})))
        try:
            category = llm.call(
                "suggest_category",
                [llm.text_part(f"Suggest one expense category. Counterparty: {cp['display_name']}, type: {cp['classifications']}. Purpose: {current['business_purpose']}.")],
                TraceContext(team_id, run_id=run_id),
                schema=CATEGORY_SCHEMA,
            ).data["category"]
        except llm.LLMFailure:
            return {"skipped": True}
    if category not in CATEGORIES:
        return {"skipped": True}
    with tx() as conn:
        add_version(conn, current, "AGENT", "Suggested category", category=category, category_is_suggestion=True)
    return {"category": category}


@node("Open Exception")
def open_exception(state: RunState, ctx) -> dict:
    team, expense_id = state["team_id"], state["inputs"]["expense_id"]
    with tx() as conn:
        current = latest(conn, team, expense_id)
        if current is None:
            return {"stop": "That expense wasn't found in this team."}
        conn.execute(
            "INSERT INTO exceptions (id, team_id, expense_id, kind, details) VALUES (%s, %s, %s, 'MISSING_DOCUMENT', %s) ON CONFLICT DO NOTHING",
            (new_id("exc"), team, expense_id, Jsonb({"reason": "Requested from chat"})),
        )
        notify_approvers(conn, team, "MISSING_DOCUMENT", {"expense_id": expense_id}, f"missing:{expense_id}")
        cp = fetch_one(conn, "SELECT display_name FROM counterparties WHERE id = %s", (current["counterparty_id"],))
    message = None
    try:
        message = llm.call(
            "draft_request",
            [llm.text_part(f"Draft a short, polite message asking {cp['display_name']} for the invoice or receipt for a payment of {format_minor(int(current['gross_minor']), current['asset'])}. The user will send it themselves.")],
            ctx,
            schema=DRAFT_SCHEMA,
        ).data["message"]
    except llm.LLMFailure:
        pass
    return {"result": {"expense_id": expense_id, "exception": "OPEN", "draft_message": message, "note": "PayoAI doesn't send this. Upload the document when it arrives."}}


def attach_evidence(team_id: str, user_id: str, expense_id: str, document_id: str) -> dict:
    """Matches a document to the actual payment. It can never create a payment or an obligation."""
    run_id = new_id("run")
    with tx() as conn:
        conn.execute("INSERT INTO routine_runs (id, team_id, routine, trigger_type, triggered_by, inputs, status) VALUES (%s, %s, 'attach_evidence', 'TAP', %s, %s, 'RUNNING')", (run_id, team_id, user_id, Jsonb({"expense_id": expense_id, "document_id": document_id})))
        document = fetch_one(conn, "SELECT * FROM documents WHERE id = %s AND team_id = %s", (document_id, team_id))
        current = latest(conn, team_id, expense_id)
        if document is None or current is None:
            raise ValueError("That document or expense isn't in this team.")
        cp = fetch_one(conn, "SELECT display_name, legal_name FROM counterparties WHERE id = %s", (current["counterparty_id"],))
    ctx = TraceContext(team_id, run_id=run_id)
    fields = llm.call("extract_fields", [llm.text_part(EXTRACTION_PROMPT), llm.untrusted("receipt", document["text_content"] or "")], ctx, schema=EXTRACTION_SCHEMA, trust_label="UNTRUSTED").data
    differences = match_to_payment(fields, current, cp)
    failures = verify_quotes(fields, document["text_content"])
    differences += [{"field": f["field"], "reason": f["reason"]} for f in failures]
    with tx() as conn:
        if differences:
            conn.execute("UPDATE exceptions SET details = details || %s WHERE expense_id = %s AND status = 'OPEN'", (Jsonb({"differences": differences, "document_id": document_id}), expense_id))
            status = current["status"]
        else:
            add_version(conn, current, user_id, "Evidence attached", status="SUPPORTED")
            conn.execute("UPDATE exceptions SET status = 'RESOLVED' WHERE expense_id = %s AND status = 'OPEN'", (expense_id,))
            conn.execute("UPDATE obligations SET document_id = %s WHERE id = %s AND document_id IS NULL", (document_id, current["obligation_id"]))
            status = "SUPPORTED"
        conn.execute("UPDATE routine_runs SET status = 'COMPLETED', result = %s, updated_at = now() WHERE id = %s", (Jsonb({"status": status, "differences": differences}), run_id))
    return {"status": status, "differences": differences}


def match_to_payment(fields: dict, expense: dict, counterparty: dict) -> list[dict]:
    out = []
    asset = (fields.get("currency") or expense["asset"]).upper()
    try:
        total = to_minor(fields.get("total"), asset) if fields.get("total") else None
    except (ValueError, TypeError):
        total = None
    if total != int(expense["gross_minor"]) or asset != expense["asset"]:
        out.append({"field": "amount", "document": f"{fields.get('total')} {asset}", "payment": format_minor(int(expense["gross_minor"]), expense["asset"])})
    supplier = fields.get("supplier_name")
    if not (same_details(supplier, counterparty["display_name"]) or same_details(supplier, counterparty["legal_name"])):
        out.append({"field": "counterparty", "document": supplier, "payment": counterparty["display_name"]})
    return out


def manual_correction(team_id: str, user_id: str, obligation_id: str, field: str, value: str, reason: str) -> dict:
    """The manual screen for a paid bill: a new record of the correction, keeping the old value. It can't undo a payment."""
    with tx() as conn:
        role = fetch_one(conn, "SELECT role FROM memberships WHERE team_id = %s AND user_id = %s", (team_id, user_id))
        if not role or role["role"] not in ("OPERATOR", "APPROVER"):
            raise PermissionError("Your role can't correct records.")
        bill = fetch_one(conn, "SELECT * FROM obligations WHERE id = %s AND team_id = %s", (obligation_id, team_id))
        if bill is None:
            raise ValueError("That bill isn't in this team.")
        if field in ("payment_details", "iban", "account_number", "sort_code", "wallet_address"):
            raise ValueError("Payment details can't be corrected into a new destination.")
        conn.execute(
            "INSERT INTO field_corrections (id, team_id, obligation_id, field, old_value, new_value, is_money_field, corrected_by, reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (new_id("cor"), team_id, obligation_id, field, Jsonb(str(bill.get(field))), Jsonb(value), field in ("amount_minor", "asset", "invoice_reference"), user_id, reason),
        )
        audit(conn, team_id, "USER", user_id, "BillCorrectedManually", "obligation", obligation_id, {"field": field, "reason": reason})
    return {"recorded": True, "note": "The correction is recorded. A sent payment can't be undone from here, and a refund or credit is a separate record."}
