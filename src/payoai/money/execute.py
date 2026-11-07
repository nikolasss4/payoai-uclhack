"""Execute Payment. Plain code with no LLM, run only by money-worker. See ADR 0001.

An attempt is saved before anything is sent. A resend happens only if the request provably never
left. Everything else becomes UNKNOWN and goes to Reconcile Payment.
"""

from datetime import datetime, timedelta, timezone

from psycopg.errors import UniqueViolation

from payoai.db import Jsonb, fetch_one, new_id, tx
from payoai.domain.money import payload_hash
from payoai.money.locks import lock_obligation
from payoai.providers import get_provider
from payoai.providers.base import SubmitRequest, SubmitResult
from payoai.queue import enqueue
from payoai.routines.nodes.checks import account_position, refresh_balance
from payoai.routines.nodes.decision import notify_approvers
from payoai.trace import TraceContext, audit, step

ROUTINE = "execute_payment"


class StopPayment(Exception):
    """A re-check failed before anything was sent. The payment needs a fresh approval."""


class NotApprovedAnyMore(Exception):
    """Something changed after the re-checks, such as Replace Bill. Nothing was recorded or sent."""


def execute(team_id: str, intent_id: str, approval_id: str) -> dict:
    run_id = new_id("run")
    with tx() as conn:
        intent = fetch_one(conn, "SELECT * FROM payment_intents WHERE id = %s AND team_id = %s", (intent_id, team_id))
        if intent is None:
            return {"status": "STOPPED", "reason": "That payment isn't in this team."}
        conn.execute(
            "INSERT INTO routine_runs (id, team_id, routine, trigger_type, inputs, status) VALUES (%s, %s, %s, 'TAP', %s, 'RUNNING')",
            (run_id, team_id, ROUTINE, Jsonb({"intent_id": intent_id, "approval_id": approval_id})),
        )
        existing = fetch_one(conn, "SELECT * FROM payment_attempts WHERE intent_id = %s ORDER BY created_at DESC LIMIT 1", (intent_id,))
    ctx = TraceContext(team_id, run_id=run_id)
    if existing is not None:
        return _finish_run(run_id, _resume_existing(ctx, existing, intent))
    try:
        with step(ctx, "NODE", "Verify Approval", {"approval_id": approval_id}):
            _verify_approval(team_id, intent, approval_id)
        with step(ctx, "NODE", "Re-check Authority", {"approval_id": approval_id}):
            _recheck_authority(team_id, approval_id)
        with step(ctx, "NODE", "Re-check Payment", {"intent_id": intent_id}) as s:
            s.output = _recheck_payment(intent)
    except StopPayment as stop:
        _stop(team_id, intent, approval_id, str(stop))
        return _finish_run(run_id, {"status": "STOPPED", "reason": str(stop)})
    try:
        attempt = _record_attempt(ctx, intent, approval_id)
    except NotApprovedAnyMore:
        with tx() as conn:
            exists = fetch_one(conn, "SELECT id FROM payment_attempts WHERE intent_id = %s LIMIT 1", (intent_id,))
        return _finish_run(run_id, {"status": "ALREADY_ATTEMPTED"} if exists else {"status": "STOPPED", "reason": "This payment changed after its checks, so nothing was sent."})
    if attempt is None:
        return _finish_run(run_id, {"status": "ALREADY_ATTEMPTED"})
    with tx() as conn:
        destination = fetch_one(conn, "SELECT details FROM payment_destinations WHERE id = %s", (intent["destination_id"],))
        account = fetch_one(conn, "SELECT external_id FROM financial_accounts WHERE id = %s", (intent["source_account_id"],))
    request = SubmitRequest(
        idempotency_key=intent["idempotency_key"], account_external_id=account["external_id"], amount_minor=int(intent["amount_minor"]),
        asset=intent["asset"], destination_details=destination["details"], reference=intent["payment_reference"],
        max_fee_minor=int(intent["max_fee_minor"]), chain_id=intent["chain_id"], token_contract=intent["token_contract"],
    )
    result = _submit(ctx, intent, request)
    return _finish_run(run_id, _record_result(ctx, intent, attempt, result))


def _verify_approval(team_id, intent, approval_id) -> None:
    with tx() as conn:
        approval = fetch_one(conn, "SELECT * FROM approvals WHERE id = %s AND team_id = %s AND intent_id = %s", (approval_id, team_id, intent["id"]))
    if approval is None or approval["status"] != "APPROVED":
        raise StopPayment("There's no approved record for this payment.")
    if approval["expires_at"] <= datetime.now(timezone.utc):
        raise StopPayment("The approval has expired.")
    # Recomputed from the stored fields, so a changed payment can't reach the provider.
    recomputed = payload_hash(intent["canonical"])
    if not (recomputed == intent["payload_hash"] == approval["payload_hash"]):
        raise StopPayment("The payment no longer matches what was approved.")


def _recheck_authority(team_id, approval_id) -> None:
    with tx() as conn:
        row = fetch_one(
            conn,
            """
            SELECT m.role, c.payments_paused FROM approvals a
            LEFT JOIN memberships m ON m.team_id = a.team_id AND m.user_id = a.decided_by
            JOIN team_controls c ON c.team_id = a.team_id
            WHERE a.id = %s AND a.team_id = %s
            """,
            (approval_id, team_id),
        )
    if row["role"] != "APPROVER":
        raise StopPayment("The person who approved this no longer has that role.")
    if row["payments_paused"]:
        raise StopPayment("New payments are paused for this team.")


def _recheck_payment(intent) -> dict:
    with tx() as conn:
        destination = fetch_one(conn, "SELECT * FROM payment_destinations WHERE id = %s", (intent["destination_id"],))
        account = fetch_one(conn, "SELECT * FROM financial_accounts WHERE id = %s", (intent["source_account_id"],))
    if destination["status"] != "VERIFIED":
        raise StopPayment("The destination is no longer verified.")
    if destination["details"] != intent["canonical"]["destination_details"]:
        raise StopPayment("The destination's details changed after approval.")
    snapshot = refresh_balance(account)
    if snapshot is None:
        raise StopPayment("The balance can't be read.")
    position = account_position(account, snapshot)
    if position["stale"]:
        raise StopPayment("A fresh balance can't be read.")
    # This payment is itself counted as approved, so add it back before comparing.
    spendable = position["spendable_minor"] + int(intent["amount_minor"])
    # An Approver who cleared a reserve warning accepted that. Real money must still cover it.
    if _reserve_shortfall_was_reviewed(intent):
        spendable += position["reserve_minor"]
    if int(intent["amount_minor"]) + int(intent["max_fee_minor"]) > spendable:
        raise StopPayment("The account can no longer cover this and keep its reserve.")
    return position


def _reserve_shortfall_was_reviewed(intent) -> bool:
    if not intent["requires_review"]:
        return False
    with tx() as conn:
        row = fetch_one(conn, "SELECT id FROM check_results WHERE team_id = %s AND run_id = %s AND obligation_id = %s AND rule_id = ANY(%s) LIMIT 1", (intent["team_id"], intent["run_id"], intent["obligation_id"], ["CASH_BELOW_RESERVE", "BATCH_SHORTFALL"]))
    return row is not None


def _record_attempt(ctx, intent, approval_id) -> dict | None:
    """The attempt and its fixed key are saved before anything is sent, in one transaction with its record."""
    attempt_id = new_id("att")
    try:
        with tx() as conn:
            # The last check before money can move, in one transaction with saving the attempt. Replace
            # Bill and Pause All Payments can't slip in between this and the send. It runs before the
            # Trace Step opens, because a step that fails on a shared connection can't record itself.
            lock_obligation(conn, intent["obligation_id"])
            still_approved = fetch_one(
                conn,
                """
                UPDATE payment_intents p SET status = 'SUBMITTING', updated_at = now()
                WHERE p.id = %s AND p.status = 'APPROVED'
                  AND EXISTS (SELECT 1 FROM approvals a WHERE a.id = %s AND a.intent_id = p.id AND a.status = 'APPROVED')
                  AND NOT EXISTS (SELECT 1 FROM team_controls c WHERE c.team_id = p.team_id AND c.payments_paused)
                RETURNING p.id
                """,
                (intent["id"], approval_id),
            )
            if still_approved is None:
                raise NotApprovedAnyMore()
            with step(ctx, "NODE", "Record Attempt", {"attempt_id": attempt_id}, conn=conn):
                conn.execute(
                    """
                    INSERT INTO payment_attempts (id, team_id, intent_id, approval_id, provider, idempotency_key, state, evidence_label)
                    VALUES (%s, %s, %s, %s, %s, %s, 'RECORDED', %s)
                    """,
                    (attempt_id, intent["team_id"], intent["id"], approval_id, intent["provider"], intent["idempotency_key"], get_provider(intent["provider"]).evidence_label),
                )
                conn.execute("UPDATE obligations SET status = 'PAYMENT_IN_PROGRESS', updated_at = now() WHERE id = %s", (intent["obligation_id"],))
                audit(conn, intent["team_id"], "SYSTEM", None, "ProviderSubmitAttempted", "payment_attempt", attempt_id, {"intent_id": intent["id"]})
    except UniqueViolation:
        return None
    return {"id": attempt_id}


def _submit(ctx, intent, request: SubmitRequest) -> SubmitResult:
    provider = get_provider(intent["provider"])
    with step(ctx, "PROVIDER_CALL", "Submit", {"reference": request.reference, "amount_minor": request.amount_minor, "asset": request.asset}) as s:
        result = _call_submit(provider, request)
        if result.outcome == "NOT_SENT":
            # Provably never left, so one resend with the same key is safe.
            result = _call_submit(provider, request)
        s.output = {"outcome": result.outcome, "external_id": result.external_id, "reason": result.reason}
    return result


def _call_submit(provider, request) -> SubmitResult:
    try:
        return provider.submit(request)
    except Exception as exc:
        # Any unexpected failure might have happened after the provider acted.
        return SubmitResult("UNKNOWN", reason=f"{type(exc).__name__}: {exc}"[:300])


def _record_result(ctx, intent, attempt, result: SubmitResult) -> dict:
    mapping = {
        "ACCEPTED": ("SUBMITTED", "SUBMITTED", "PAYMENT_IN_PROGRESS"),
        "UNKNOWN": ("UNKNOWN", "UNKNOWN", "OUTCOME_UNKNOWN"),
        "REJECTED": ("REJECTED", "REJECTED", "FAILED"),
        # Sent nothing twice in a row: no money moved, so it ends like a rejection.
        "NOT_SENT": ("REJECTED", "REJECTED", "FAILED"),
    }
    state, intent_status, obligation_status = mapping[result.outcome]
    with tx() as conn:
        with step(ctx, "NODE", "Record Result", {"outcome": result.outcome}, conn=conn) as s:
            conn.execute("UPDATE payment_attempts SET state = %s, external_id = %s, updated_at = now() WHERE id = %s", (state, result.external_id, attempt["id"]))
            conn.execute("UPDATE payment_intents SET status = %s, updated_at = now() WHERE id = %s", (intent_status, intent["id"]))
            conn.execute("UPDATE obligations SET status = %s, updated_at = now() WHERE id = %s", (obligation_status, intent["obligation_id"]))
            conn.execute(
                "INSERT INTO external_events (id, team_id, attempt_id, provider, source, observed_status, payload) VALUES (%s, %s, %s, %s, 'SUBMIT', %s, %s)",
                (new_id("ext"), intent["team_id"], attempt["id"], intent["provider"], result.outcome, Jsonb({"reason": result.reason, **result.raw})),
            )
            audit(conn, intent["team_id"], "PROVIDER", intent["provider"], "ProviderSubmitResult", "payment_attempt", attempt["id"], {"outcome": result.outcome, "reason": result.reason}, s.id)
            if state in ("SUBMITTED", "UNKNOWN"):
                schedule_reconcile(conn, intent["team_id"], attempt["id"], 0, delay_seconds=0 if state == "SUBMITTED" else 60)
            if state == "REJECTED":
                notify_approvers(conn, intent["team_id"], "PAYMENT_REJECTED_BY_PROVIDER", {"intent_id": intent["id"], "reason": result.reason}, f"rejected:{attempt['id']}")
            if state == "UNKNOWN":
                notify_approvers(conn, intent["team_id"], "PAYMENT_OUTCOME_UNKNOWN", {"intent_id": intent["id"]}, f"unknown:{attempt['id']}")
            s.output = {"state": state}
    return {"status": state, "attempt_id": attempt["id"], "reason": result.reason}


def schedule_reconcile(conn, team_id: str, attempt_id: str, poll: int, delay_seconds: int) -> None:
    enqueue(
        conn, "reconcile_payment", team_id, {"attempt_id": attempt_id, "trigger": "TIMER"},
        dedupe_key=f"reconcile:{attempt_id}:{poll}", run_at=datetime.now(timezone.utc) + timedelta(seconds=delay_seconds),
    )


def _resume_existing(ctx, attempt, intent) -> dict:
    """A repeat job or restart. A saved attempt with no result may have sent money, so never resend."""
    with step(ctx, "NODE", "Existing Attempt Found", {"attempt_id": attempt["id"], "state": attempt["state"]}) as s:
        if attempt["state"] != "RECORDED":
            s.output = {"action": "none"}
            return {"status": attempt["state"], "attempt_id": attempt["id"]}
        s.output = {"action": "marked UNKNOWN"}
    return _record_result(ctx, intent, attempt, SubmitResult("UNKNOWN", reason="restarted with no recorded result"))


def _stop(team_id, intent, approval_id, reason) -> None:
    with tx() as conn:
        lock_obligation(conn, intent["obligation_id"])
        conn.execute("UPDATE approvals SET status = 'CANCELLED', reason = %s WHERE id = %s AND status = 'APPROVED'", (reason, approval_id))
        # Only an approved payment stops here. A bill already replaced or cancelled must not come back to life.
        stopped = fetch_one(conn, "UPDATE payment_intents SET status = 'CANCELLED', updated_at = now() WHERE id = %s AND status = 'APPROVED' RETURNING id", (intent["id"],))
        if stopped:
            conn.execute("UPDATE obligations SET status = 'NEEDS_REVIEW', updated_at = now() WHERE id = %s AND status NOT IN ('SUPERSEDED', 'CANCELLED', 'PAID')", (intent["obligation_id"],))
        audit(conn, team_id, "SYSTEM", None, "PaymentStoppedBeforeSend", "payment_intent", intent["id"], {"reason": reason})
        notify_approvers(conn, team_id, "PAYMENT_STOPPED", {"intent_id": intent["id"], "reason": reason}, f"stopped:{approval_id}")


def _finish_run(run_id: str, result: dict) -> dict:
    with tx() as conn:
        conn.execute("UPDATE routine_runs SET status = 'COMPLETED', result = %s, updated_at = now() WHERE id = %s", (Jsonb(result), run_id))
    return result
