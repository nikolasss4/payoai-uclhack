"""Decide, Investigate, Explain and Prepare Payment. LLM Nodes never change a result or a state."""

from datetime import datetime, timedelta, timezone

from payoai.agent import llm
from payoai.db import Jsonb, fetch_all, fetch_one, new_id, tx
from payoai.domain.decide import POLICY_VERSION, combine
from payoai.domain.money import canonical_payload, format_minor, payload_hash
from payoai.routines.nodes.checks import fee_cap
from payoai.routines.nodes.common import RunState, node
from payoai.trace import audit

OBLIGATION_STATUS = {
    "ELIGIBLE_FOR_APPROVAL": "VERIFIED",
    "NEEDS_INFORMATION": "NEEDS_INFORMATION",
    "NEEDS_REVIEW": "NEEDS_REVIEW",
    "BLOCK": "BLOCKED",
}
INVESTIGATION_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"finding": {"type": "string"}, "record_ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["finding", "record_ids"],
            },
        }
    },
    "required": ["findings"],
}
EXPLAIN_SCHEMA = {"type": "object", "properties": {"explanation": {"type": "string"}}, "required": ["explanation"]}


@node("Decide", step_type="DECISION")
def decide(state: RunState, ctx) -> dict:
    decision, reasons = combine(state.get("checks") or [])
    decision_id = new_id("dec")
    with tx() as conn:
        conn.execute(
            "INSERT INTO decisions (id, team_id, run_id, obligation_id, decision, reasons, policy_version) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (decision_id, state["team_id"], state["run_id"], state.get("obligation_id"), decision, Jsonb(reasons), POLICY_VERSION),
        )
        conn.execute("UPDATE obligations SET status = %s, updated_at = now() WHERE id = %s", (OBLIGATION_STATUS[decision], state.get("obligation_id")))
        audit(conn, state["team_id"], "SYSTEM", None, "PolicyDecisionRecorded", "obligation", state["obligation_id"], {"decision": decision, "reasons": reasons})
    return {"decision": decision, "decision_id": decision_id, "reasons": reasons}


@node("Investigate", trust_label="UNTRUSTED")
def investigate(state: RunState, ctx) -> dict:
    """Adds cited suggestions for a human. A failure here never stops the Routine."""
    try:
        records = _related_records(state)
        known = {r["id"] for group in records.values() for r in group}
        data = llm.call(
            "investigate",
            [
                llm.text_part(
                    "A payment check needs human review. Using only these records, find anything that may explain the issue. "
                    "Each finding must cite record IDs. Say nothing you can't cite. You can't change the decision."
                ),
                llm.text_part(f"Issues: {_jsonable(state.get('reasons'))}"),
                llm.untrusted("team records", str(_jsonable(records))),
            ],
            ctx,
            schema=INVESTIGATION_SCHEMA,
            trust_label="UNTRUSTED",
        ).data
    except llm.LLMFailure:
        return {"findings": []}
    # A finding with no real citation is dropped rather than shown.
    findings = [f for f in data["findings"] if f["record_ids"] and set(f["record_ids"]) <= known]
    with tx() as conn:
        conn.execute(
            "INSERT INTO investigations (id, team_id, run_id, decision_id, findings) VALUES (%s, %s, %s, %s, %s)",
            (new_id("inv"), state["team_id"], state["run_id"], state["decision_id"], Jsonb(findings)),
        )
    return {"findings": findings}


def _related_records(state: RunState) -> dict:
    team, counterparty = state["team_id"], state["counterparty_id"]
    with tx() as conn:
        bills = fetch_all(conn, "SELECT id, invoice_reference, amount_minor, asset, status, period, due_date, description FROM obligations WHERE team_id = %s AND counterparty_id = %s AND id <> %s ORDER BY created_at DESC LIMIT 12", (team, counterparty, state["obligation_id"]))
        contracts = fetch_all(conn, "SELECT id, kind, value, added_at FROM contact_channels WHERE team_id = %s AND counterparty_id = %s AND kind = 'contract'", (team, counterparty))
    return {"earlier_bills": bills, "contracts": contracts}


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def plain_list(state: RunState) -> str:
    """Used when Explain can't run, and by the rules-only baseline."""
    decision = state.get("decision") or "STOPPED"
    reasons = state.get("reasons") or []
    if not reasons:
        return f"{decision}: all checks passed."
    return f"{decision}: " + " ".join(r["reason"] for r in reasons)


@node("Explain")
def explain(state: RunState, ctx) -> dict:
    fallback = plain_list(state)
    try:
        data = llm.call(
            "explain",
            [
                llm.text_part(
                    "Explain this payment check result to a busy small business owner in two or three plain sentences. "
                    "Use only these facts. Don't add numbers, promises or advice beyond them, and don't soften a block. "
                    f"Decision: {state.get('decision')}. Reasons: {_jsonable(state.get('reasons'))}. "
                    f"Suggestions from the agent, not facts: {_jsonable(state.get('findings'))}."
                )
            ],
            ctx,
            schema=EXPLAIN_SCHEMA,
        ).data
        explanation = data["explanation"]
    except llm.LLMFailure:
        explanation = fallback
    with tx() as conn:
        conn.execute("UPDATE decisions SET explanation = %s WHERE id = %s", (explanation, state.get("decision_id")))
    return {"explanation": explanation}


@node("Explain")
def explain_without_decision_record(state: RunState, ctx) -> dict:
    """For a compliance report, which makes no payment decision to attach the explanation to."""
    fallback = plain_list(state)
    try:
        explanation = llm.call(
            "explain",
            [llm.text_part(
                "Explain this invoice check in two or three plain sentences. Use only these facts. Say what's missing and why it matters. "
                f"It's prepared for accountant review, not certified compliant. Facts: {_jsonable(state.get('reasons'))}"
            )],
            ctx,
            schema=EXPLAIN_SCHEMA,
        ).data["explanation"]
    except llm.LLMFailure:
        explanation = fallback
    return {"explanation": explanation, "result": {**(state.get("result") or {}), "explanation": explanation}}


@node("Prepare Payment")
def prepare_payment(state: RunState, ctx) -> dict:
    """Builds the exact payment and its hash. Blocked bills and unanswered questions get nothing."""
    team = state["team_id"]
    with tx() as conn:
        destination = fetch_one(conn, "SELECT * FROM payment_destinations WHERE id = %s AND team_id = %s AND status = 'VERIFIED'", (state["destination_id"], team))
        account = fetch_one(conn, "SELECT * FROM financial_accounts WHERE id = %s AND team_id = %s", (state["source_account_id"], team))
        version = fetch_one(conn, "SELECT count(*) AS n FROM payment_intents WHERE obligation_id = %s", (state["obligation_id"],))["n"] + 1
        ttl = fetch_one(conn, "SELECT approval_ttl_hours FROM team_controls WHERE team_id = %s", (team,))["approval_ttl_hours"]
        obligation = fetch_one(conn, "SELECT * FROM obligations WHERE id = %s", (state["obligation_id"],))
    if destination is None or account is None:
        return {"stop": "The destination or source account is no longer valid."}
    reference = obligation["invoice_reference"] or obligation["id"]
    max_fee = fee_cap(account["rail"])
    canonical = canonical_payload(
        team_id=team, source_account_id=account["id"], destination_id=destination["id"], destination_details=destination["details"],
        rail=destination["rail"], provider=account["provider"], amount_minor=int(state["amount_minor"]), asset=state["asset"],
        max_fee_minor=max_fee, obligation_id=obligation["id"], payment_reference=reference,
        chain_id=destination["chain_id"], token_contract=destination["token_contract"],
    )
    digest = payload_hash(canonical)
    intent_id, approval_id = new_id("pay"), new_id("apr")
    warnings = [r["reason"] for r in state.get("reasons") or []]
    if obligation["document_id"] is None:
        warnings.append("No invoice attached.")
    requires_review = state.get("decision") == "NEEDS_REVIEW"
    with tx() as conn:
        conn.execute(
            """
            INSERT INTO payment_intents (id, team_id, obligation_id, counterparty_id, destination_id, source_account_id, run_id, rail, provider, amount_minor, asset, payment_reference, max_fee_minor, chain_id, token_contract, canonical, payload_hash, idempotency_key, warnings, requires_review, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'READY_FOR_APPROVAL')
            """,
            (
                intent_id, team, obligation["id"], obligation["counterparty_id"], destination["id"], account["id"], state["run_id"],
                destination["rail"], account["provider"], int(state["amount_minor"]), state["asset"], reference, max_fee,
                destination["chain_id"], destination["token_contract"], Jsonb(canonical), digest, f"{team}-{obligation['id']}-v{version}",
                Jsonb(warnings), requires_review,
            ),
        )
        conn.execute(
            "INSERT INTO approvals (id, team_id, intent_id, payload_hash, expires_at) VALUES (%s, %s, %s, %s, %s)",
            (approval_id, team, intent_id, digest, datetime.now(timezone.utc) + timedelta(hours=ttl)),
        )
        conn.execute("UPDATE obligations SET status = 'PAYMENT_PREPARED', updated_at = now() WHERE id = %s", (obligation["id"],))
        audit(conn, team, "SYSTEM", None, "PaymentIntentPrepared", "payment_intent", intent_id, {"hash": digest, "amount": format_minor(int(state["amount_minor"]), state["asset"])})
        notify_approvers(conn, team, "PAYMENT_READY", {"intent_id": intent_id, "approval_id": approval_id}, f"ready:{approval_id}")
    return {"intent_id": intent_id, "approval_id": approval_id}


def notify_approvers(conn, team_id: str, kind: str, payload: dict, dedupe_key: str) -> None:
    conn.execute(
        "INSERT INTO notifications (id, team_id, kind, payload, dedupe_key) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (dedupe_key) DO NOTHING",
        (new_id("ntf"), team_id, kind, Jsonb(payload), dedupe_key),
    )
