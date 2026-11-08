"""Reconcile Payment. Webhooks only prompt a check, and the status comes from asking the provider.

Only this module moves a payment out of UNKNOWN. "Not found" is never proof of failure, and time
passing never turns a payment into FAILED. No LLM takes part.
"""

import hashlib
from datetime import datetime, timedelta, timezone

from payoai.db import Jsonb, fetch_all, fetch_one, new_id, tx
from payoai.money.execute import schedule_reconcile
from payoai.money.expense import record_expense
from payoai.money.locks import lock_obligation
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
    # A provider may send no event ID. The body stands in, so one can't swallow another payment's event.
    event_id = event.get("event_id") or hashlib.sha256(raw_body).hexdigest()
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
            (new_id("ext"), attempt["team_id"], attempt["id"], provider_name, event_id, event.get("status"), Jsonb(event)),
        ).fetchone()
        if inserted is None:
            return {"accepted": True, "duplicate": True}
        enqueue(conn, "reconcile_payment", attempt["team_id"], {"attempt_id": attempt["id"], "trigger": "WEBHOOK"}, dedupe_key=f"webhook:{provider_name}:{event_id}")
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
    # Timers stop at a final state, but a provider's own webhook may still prove a late outcome.
    if attempt["state"] == "CONFIRMED" or (attempt["state"] in FINAL and trigger != "WEBHOOK"):
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
    lock_obligation(conn, intent["obligation_id"])
    # Re-read under the lock, so a slower check holding an older answer can't overwrite a newer one.
    attempt = fetch_one(conn, "SELECT * FROM payment_attempts WHERE id = %s FOR UPDATE", (attempt["id"],))
    intent = fetch_one(conn, "SELECT * FROM payment_intents WHERE id = %s FOR UPDATE", (intent["id"],))
    previous = attempt["state"]
    _save_observation(conn, attempt, intent, observed, mismatch)
    settled = previous == "CONFIRMED" or (previous in FINAL and observed.status != "COMPLETED" and not (previous == "NEEDS_MANUAL_REVIEW" and observed.status == "FAILED"))
    if settled:
        return previous
    # Money was recorded as not sent, and now it has. Any replacement may be a duplicate.
    late = previous in ("FAILED", "REJECTED") or (previous == "MANUALLY_RESOLVED" and intent["status"] == "FAILED")
    obligation_status = None
    if observed.status == "COMPLETED" and mismatch:
        state, intent_status, obligation_status = "NEEDS_MANUAL_REVIEW", "NEEDS_MANUAL_REVIEW", "OUTCOME_UNKNOWN"
        notify_approvers(conn, intent["team_id"], "PAYMENT_MISMATCH", {"intent_id": intent["id"], "mismatch": mismatch}, f"mismatch:{attempt['id']}")
    elif observed.status == "COMPLETED":
        state, intent_status, obligation_status = "CONFIRMED", "CONFIRMED", "PAID"
        notify_approvers(conn, intent["team_id"], "PAYMENT_CONFIRMED", {"intent_id": intent["id"]}, f"confirmed:{attempt['id']}")
    elif observed.status == "FAILED":
        state, intent_status, obligation_status = "FAILED", "FAILED", "FAILED"
        notify_approvers(conn, intent["team_id"], "PAYMENT_FAILED", {"intent_id": intent["id"]}, f"failed:{attempt['id']}")
    elif observed.status == "PENDING":
        state, intent_status, obligation_status = "PENDING", "PENDING", "PAYMENT_IN_PROGRESS"
    else:
        # NOT_FOUND and UNREACHABLE prove nothing, so what's on record stays.
        state = "UNKNOWN" if previous in ("SUBMITTED", "RECORDED") else previous
        intent_status = "UNKNOWN" if state == "UNKNOWN" else intent["status"]
        obligation_status = "OUTCOME_UNKNOWN" if state == "UNKNOWN" else None

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
    if obligation_status:
        # A Superseded Bill stays superseded even if its old payment lands, which keeps its replacement valid.
        conn.execute("UPDATE obligations SET status = %s, updated_at = now() WHERE id = %s AND status <> 'SUPERSEDED'", (obligation_status, intent["obligation_id"]))
    if state != previous:
        audit(conn, intent["team_id"], "PROVIDER", attempt["provider"], "ExternalStatusObserved", "payment_attempt", attempt["id"], {"from": previous, "to": state, "evidence_label": attempt["evidence_label"]}, step_id)
    if late and observed.status == "COMPLETED":
        _raise_late_confirmation(conn, attempt, intent, previous)
    if state == "CONFIRMED":
        record_expense(conn, intent, attempt)
    if state in ("CONFIRMED", "FAILED"):
        enqueue(conn, "cash_analysis", intent["team_id"], {}, dedupe_key=f"cash:final:{attempt['id']}:{state}")
    if still_open:
        delay = POLL_DELAYS_SECONDS[poll - 1] if poll <= len(POLL_DELAYS_SECONDS) else HOURLY
        schedule_reconcile(conn, intent["team_id"], attempt["id"], poll, delay)
    return state


def _save_observation(conn, attempt, intent, observed, mismatch) -> None:
    """Every observation is evidence, including one that changes nothing."""
    conn.execute(
        "INSERT INTO external_events (id, team_id, attempt_id, provider, source, observed_status, payload) VALUES (%s, %s, %s, %s, 'QUERY', %s, %s)",
        (new_id("ext"), intent["team_id"], attempt["id"], attempt["provider"], observed.status, Jsonb({**observed.raw, "mismatch": mismatch, "evidence_label": attempt["evidence_label"]})),
    )


def _raise_late_confirmation(conn, attempt, intent, previous) -> None:
    """Holds what hasn't moved yet and names what has. Nothing already sent can be undone from here."""
    related = [intent["obligation_id"]]
    current = fetch_one(conn, "SELECT superseded_by FROM obligations WHERE id = %s", (intent["obligation_id"],))
    while current and current["superseded_by"] and current["superseded_by"] not in related:
        related.append(current["superseded_by"])
        current = fetch_one(conn, "SELECT superseded_by FROM obligations WHERE id = %s", (current["superseded_by"],))
    held, moved = [], []
    for obligation_id in related:
        lock_obligation(conn, obligation_id)
        others = fetch_all(conn, "SELECT id, status FROM payment_intents WHERE obligation_id = %s AND id <> %s AND status <> ALL(%s)", (obligation_id, intent["id"], ["FAILED", "REJECTED", "CANCELLED"]))
        for other in others:
            if other["status"] in ("READY_FOR_APPROVAL", "APPROVED"):
                conn.execute("UPDATE approvals SET status = 'CANCELLED', reason = 'The earlier payment was confirmed late' WHERE intent_id = %s AND status IN ('PENDING', 'APPROVED')", (other["id"],))
                conn.execute("UPDATE payment_intents SET status = 'CANCELLED', updated_at = now() WHERE id = %s", (other["id"],))
                if obligation_id != intent["obligation_id"]:
                    conn.execute("UPDATE obligations SET status = 'NEEDS_REVIEW', updated_at = now() WHERE id = %s", (obligation_id,))
                held.append(other["id"])
            else:
                moved.append({"intent_id": other["id"], "status": other["status"]})
    details = {"intent_id": intent["id"], "recorded_as": previous, "held_replacements": held, "possible_duplicates": moved, "replacement_bills": related[1:]}
    notify_approvers(conn, intent["team_id"], "CRITICAL_LATE_CONFIRMATION", details, f"late:{attempt['id']}")
    audit(conn, intent["team_id"], "PROVIDER", attempt["provider"], "LateConfirmationAfterFailure", "payment_attempt", attempt["id"], details)


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
        attempt = fetch_one(conn, "SELECT * FROM payment_attempts WHERE id = %s AND team_id = %s AND state = 'NEEDS_MANUAL_REVIEW' FOR UPDATE", (attempt_id, team_id))
        if attempt is None:
            raise ValueError("This payment isn't waiting for manual review.")
        intent = fetch_one(conn, "SELECT * FROM payment_intents WHERE id = %s", (attempt["intent_id"],))
        lock_obligation(conn, intent["obligation_id"])
        conn.execute("UPDATE payment_attempts SET state = 'MANUALLY_RESOLVED', updated_at = now() WHERE id = %s", (attempt_id,))
        # NOT_PAID frees the bill for a new payment and a new approval. PAID keeps it from being paid again.
        conn.execute("UPDATE payment_intents SET status = %s, updated_at = now() WHERE id = %s", ("MANUALLY_RESOLVED" if outcome == "PAID" else "FAILED", intent["id"]))
        conn.execute("UPDATE obligations SET status = %s, updated_at = now() WHERE id = %s AND status <> 'SUPERSEDED'", ("PAID" if outcome == "PAID" else "FAILED", intent["obligation_id"]))
        audit(conn, team_id, "USER", user_id, "PaymentManuallyResolved", "payment_attempt", attempt_id, {"outcome": outcome, "evidence": evidence})
        if outcome == "PAID":
            record_expense(conn, intent, attempt)
    return {"state": "MANUALLY_RESOLVED", "outcome": outcome}


def _finish_run(run_id: str, result: dict) -> dict:
    with tx() as conn:
        conn.execute("UPDATE routine_runs SET status = 'COMPLETED', result = %s, updated_at = now() WHERE id = %s", (Jsonb(result), run_id))
    return result
