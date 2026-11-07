"""Reconcile Payment. Webhooks only prompt a check, and the status comes from asking the provider.

Only this module moves a payment out of UNKNOWN. "Not found" is never proof of failure, and time
passing never turns a payment into FAILED. No LLM takes part.
"""

from datetime import datetime, timedelta, timezone

from payoai.db import Jsonb, fetch_one, new_id, tx
from payoai.money.execute import schedule_reconcile
from payoai.money.expense import record_expense
from payoai.providers import get_provider
from payoai.providers.simulator import destination_ref
from payoai.queue import enqueue
from payoai.routines.nodes.decision import notify_approvers
from payoai.trace import TraceContext, audit, step

POLL_DELAYS_SECONDS = [60, 300, 900, 3600]
HOURLY = 3600
GIVE_UP_AFTER = timedelta(hours=24)
FINAL = ("CONFIRMED", "REJECTED", "NEEDS_MANUAL_REVIEW", "MANUALLY_RESOLVED")


def handle_webhook(provider_name: str, raw_body: bytes, headers: dict[str, str]) -> dict:
    """Verify Trigger. An invalid signature is logged and goes no further. Duplicates are ignored."""
    provider = get_provider(provider_name)
    try:
        event = provider.verify_webhook(raw_body, headers)
    except ValueError as exc:
        with tx() as conn:
            conn.execute(
                "INSERT INTO external_events (id, provider, source, signature_valid, payload) VALUES (%s, %s, 'WEBHOOK', false, %s)",
                (new_id("ext"), provider_name, Jsonb({"error": str(exc)})),
            )
        return {"accepted": False, "reason": "invalid signature"}
    with tx() as conn:
        attempt = fetch_one(
            conn,
            "SELECT id, team_id FROM payment_attempts WHERE provider = %s AND (external_id = %s OR idempotency_key = %s)",
            (provider_name, event.get("external_id"), event.get("idempotency_key")),
        )
        if attempt is None:
            return {"accepted": False, "reason": "unknown payment"}
        inserted = conn.execute(
            """
            INSERT INTO external_events (id, team_id, attempt_id, provider, provider_event_id, source, signature_valid, observed_status, payload)
            VALUES (%s, %s, %s, %s, %s, 'WEBHOOK', true, %s, %s) ON CONFLICT (provider, provider_event_id) DO NOTHING RETURNING id
            """,
            (new_id("ext"), attempt["team_id"], attempt["id"], provider_name, event.get("event_id"), event.get("status"), Jsonb(event)),
        ).fetchone()
        if inserted is None:
            return {"accepted": True, "duplicate": True}
        enqueue(conn, "reconcile_payment", attempt["team_id"], {"attempt_id": attempt["id"], "trigger": "WEBHOOK"}, dedupe_key=f"webhook:{provider_name}:{event.get('event_id')}")
    return {"accepted": True, "duplicate": False}


def reconcile(attempt_id: str, trigger: str = "TIMER") -> dict:
    with tx() as conn:
        attempt = fetch_one(conn, "SELECT * FROM payment_attempts WHERE id = %s", (attempt_id,))
        intent = fetch_one(conn, "SELECT * FROM payment_intents WHERE id = %s", (attempt["intent_id"],))
        run_id = new_id("run")
        conn.execute(
            "INSERT INTO routine_runs (id, team_id, routine, trigger_type, inputs, status) VALUES (%s, %s, 'reconcile_payment', %s, %s, 'RUNNING')",
            (run_id, attempt["team_id"], "WEBHOOK" if trigger == "WEBHOOK" else "TIMER", Jsonb({"attempt_id": attempt_id, "trigger": trigger})),
        )
    ctx = TraceContext(attempt["team_id"], run_id=run_id)
    if attempt["state"] in FINAL:
        return _finish_run(run_id, {"state": attempt["state"], "changed": False})
    provider = get_provider(attempt["provider"])
    with step(ctx, "PROVIDER_CALL", "Query Source", {"external_id": attempt["external_id"], "reference": intent["payment_reference"]}) as s:
        observed = provider.get_status(attempt["external_id"], intent["payment_reference"], attempt["idempotency_key"])
        s.output = {"status": observed.status, "external_id": observed.external_id, "amount_minor": observed.amount_minor}
    with step(ctx, "NODE", "Compare Outcome", {"status": observed.status}) as s:
        mismatch = _compare(intent, observed) if observed.status == "COMPLETED" else []
        s.output = {"mismatch": mismatch}
    with tx() as conn:
        with step(ctx, "NODE", "Update State", {"observed": observed.status, "mismatch": mismatch}, conn=conn) as s:
            new_state = _apply(conn, attempt, intent, observed, mismatch, s.id)
            s.output = {"state": new_state}
    return _finish_run(run_id, {"state": new_state, "changed": new_state != attempt["state"], "mismatch": mismatch})


def _compare(intent, observed) -> list[dict]:
    out = []
    expected_destination = destination_ref(intent["canonical"]["destination_details"])
    for field, expected, actual in (
        ("amount_minor", int(intent["amount_minor"]), observed.amount_minor),
        ("asset", intent["asset"], observed.asset),
        ("destination", expected_destination, observed.destination_ref),
        ("reference", intent["payment_reference"], observed.reference),
    ):
        if expected != actual:
            out.append({"field": field, "approved": str(expected), "observed": str(actual)})
    return out


def _apply(conn, attempt, intent, observed, mismatch, step_id) -> str:
    was_failed = attempt["state"] == "FAILED"
    if observed.status == "COMPLETED" and mismatch:
        state, intent_status, obligation_status = "NEEDS_MANUAL_REVIEW", "NEEDS_MANUAL_REVIEW", "OUTCOME_UNKNOWN"
        notify_approvers(conn, intent["team_id"], "PAYMENT_MISMATCH", {"intent_id": intent["id"], "mismatch": mismatch}, f"mismatch:{attempt['id']}")
    elif observed.status == "COMPLETED":
        state, intent_status, obligation_status = "CONFIRMED", "CONFIRMED", "PAID"
        if was_failed:
            # Any replacement payment may now be a duplicate.
            notify_approvers(conn, intent["team_id"], "CRITICAL_LATE_CONFIRMATION", {"intent_id": intent["id"]}, f"late:{attempt['id']}")
    elif observed.status == "FAILED":
        state, intent_status, obligation_status = "FAILED", "FAILED", "FAILED"
        notify_approvers(conn, intent["team_id"], "PAYMENT_FAILED", {"intent_id": intent["id"]}, f"failed:{attempt['id']}")
    elif observed.status == "PENDING":
        state, intent_status, obligation_status = "PENDING", "PENDING", "PAYMENT_IN_PROGRESS"
    else:
        # NOT_FOUND and UNREACHABLE prove nothing.
        state = attempt["state"] if attempt["state"] != "SUBMITTED" else "UNKNOWN"
        intent_status, obligation_status = ("UNKNOWN", "OUTCOME_UNKNOWN") if state == "UNKNOWN" else (intent["status"], "PAYMENT_IN_PROGRESS")

    still_open = state in ("UNKNOWN", "PENDING", "SUBMITTED")
    if still_open and datetime.now(timezone.utc) - attempt["created_at"] > GIVE_UP_AFTER:
        state, intent_status, obligation_status = "NEEDS_MANUAL_REVIEW", "NEEDS_MANUAL_REVIEW", "OUTCOME_UNKNOWN"
        notify_approvers(conn, intent["team_id"], "PAYMENT_NEEDS_MANUAL_REVIEW", {"intent_id": intent["id"]}, f"manual:{attempt['id']}")
        still_open = False

    poll = attempt["poll_count"] + 1
    conn.execute(
        "UPDATE payment_attempts SET state = %s, external_id = COALESCE(external_id, %s), poll_count = %s, updated_at = now() WHERE id = %s",
        (state, observed.external_id, poll, attempt["id"]),
    )
    conn.execute("UPDATE payment_intents SET status = %s, updated_at = now() WHERE id = %s", (intent_status, intent["id"]))
    conn.execute("UPDATE obligations SET status = %s, updated_at = now() WHERE id = %s", (obligation_status, intent["obligation_id"]))
    conn.execute(
        "INSERT INTO external_events (id, team_id, attempt_id, provider, source, observed_status, payload) VALUES (%s, %s, %s, %s, 'QUERY', %s, %s)",
        (new_id("ext"), intent["team_id"], attempt["id"], attempt["provider"], observed.status, Jsonb({**observed.raw, "mismatch": mismatch, "evidence_label": attempt["evidence_label"]})),
    )
    if state != attempt["state"]:
        audit(conn, intent["team_id"], "PROVIDER", attempt["provider"], "ExternalStatusObserved", "payment_attempt", attempt["id"], {"from": attempt["state"], "to": state, "evidence_label": attempt["evidence_label"]}, step_id)
    if state == "CONFIRMED":
        record_expense(conn, intent, attempt)
    if still_open:
        delay = POLL_DELAYS_SECONDS[poll - 1] if poll <= len(POLL_DELAYS_SECONDS) else HOURLY
        schedule_reconcile(conn, intent["team_id"], attempt["id"], poll, delay)
    return state


def resolve_manually(team_id: str, user_id: str, attempt_id: str, outcome: str, evidence: str) -> dict:
    """An Approver settles a review case with evidence. It stays labelled as manual, not confirmed."""
    if outcome not in ("PAID", "NOT_PAID"):
        raise ValueError("outcome must be PAID or NOT_PAID")
    if not evidence.strip():
        raise ValueError("evidence is required")
    with tx() as conn:
        role = fetch_one(conn, "SELECT role FROM memberships WHERE team_id = %s AND user_id = %s", (team_id, user_id))
        if not role or role["role"] != "APPROVER":
            raise PermissionError("Only an Approver can resolve this.")
        attempt = fetch_one(conn, "SELECT * FROM payment_attempts WHERE id = %s AND team_id = %s AND state = 'NEEDS_MANUAL_REVIEW'", (attempt_id, team_id))
        if attempt is None:
            raise ValueError("This payment isn't waiting for manual review.")
        conn.execute("UPDATE payment_attempts SET state = 'MANUALLY_RESOLVED', updated_at = now() WHERE id = %s", (attempt_id,))
        conn.execute("UPDATE obligations SET status = %s, updated_at = now() WHERE id = (SELECT obligation_id FROM payment_intents WHERE id = %s)", ("PAID" if outcome == "PAID" else "FAILED", attempt["intent_id"]))
        audit(conn, team_id, "USER", user_id, "PaymentManuallyResolved", "payment_attempt", attempt_id, {"outcome": outcome, "evidence": evidence})
    return {"state": "MANUALLY_RESOLVED", "outcome": outcome}


def _finish_run(run_id: str, result: dict) -> dict:
    with tx() as conn:
        conn.execute("UPDATE routine_runs SET status = 'COMPLETED', result = %s, updated_at = now() WHERE id = %s", (Jsonb(result), run_id))
    return result
