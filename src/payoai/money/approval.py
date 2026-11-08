"""Approval decisions. The first decision to reach the database wins, in one conditional update."""

from dataclasses import dataclass

from payoai.db import fetch_one, tx
from payoai.money.locks import lock_obligation
from payoai.queue import enqueue
from payoai.routines.nodes.decision import notify_approvers
from payoai.trace import TraceContext, audit, step


@dataclass
class DecisionResult:
    won: bool
    status: str
    message: str
    decided_by: str | None = None


def decide(team_id: str, user_id: str, approval_id: str, shown_hash: str, decision: str, reviewed: bool = False, reason: str | None = None) -> DecisionResult:
    """Handles one tap. Check and update are one statement, so there's no gap between them."""
    assert decision in ("APPROVED", "REJECTED")
    with tx() as conn:
        row = fetch_one(conn, "SELECT a.*, p.run_id, p.requires_review FROM approvals a JOIN payment_intents p ON p.id = a.intent_id WHERE a.id = %s AND a.team_id = %s", (approval_id, team_id))
    if row is None:
        return DecisionResult(False, "NOT_FOUND", "That approval isn't in this team.")
    ctx = TraceContext(team_id, run_id=row["run_id"])
    with step(ctx, "TAP", f"Approval tap: {decision}", input={"approval_id": approval_id, "user_id": user_id, "shown_hash": shown_hash}) as s:
        with tx() as conn:
            role = fetch_one(conn, "SELECT role FROM memberships WHERE team_id = %s AND user_id = %s", (team_id, user_id))
        if not role or role["role"] != "APPROVER":
            result = DecisionResult(False, row["status"], "Only an Approver can decide on a payment.")
        elif decision == "APPROVED" and row["status"] == "PENDING" and row["requires_review"] and not reviewed:
            result = DecisionResult(False, row["status"], "This payment needs review. Open it and clear the warnings first.")
        else:
            result = _apply(team_id, user_id, row, approval_id, shown_hash, decision, reason, reviewed, s.id)
        s.output = {"won": result.won, "status": result.status, "message": result.message}
    return result


def _apply(team_id, user_id, row, approval_id, shown_hash, decision, reason, reviewed, step_id) -> DecisionResult:
    with tx() as conn:
        lock_obligation(conn, fetch_one(conn, "SELECT obligation_id FROM payment_intents WHERE id = %s", (row["intent_id"],))["obligation_id"])
        won = fetch_one(
            conn,
            """
            UPDATE approvals a SET status = %(decision)s, decided_by = %(user)s, decided_at = now(), reason = %(reason)s
            WHERE a.id = %(id)s AND a.team_id = %(team)s AND a.status = 'PENDING' AND a.payload_hash = %(hash)s
              AND (a.expires_at > now() OR %(decision)s = 'REJECTED')
              AND EXISTS (SELECT 1 FROM memberships m WHERE m.team_id = a.team_id AND m.user_id = %(user)s AND m.role = 'APPROVER')
              AND (%(decision)s = 'REJECTED' OR NOT EXISTS (SELECT 1 FROM team_controls c WHERE c.team_id = a.team_id AND c.payments_paused))
              AND (%(decision)s = 'REJECTED' OR %(reviewed)s OR NOT EXISTS (SELECT 1 FROM payment_intents p WHERE p.id = a.intent_id AND p.requires_review))
            RETURNING a.id, a.intent_id
            """,
            {"decision": decision, "user": user_id, "reason": reason, "id": approval_id, "team": team_id, "hash": shown_hash, "reviewed": reviewed},
        )
        if won:
            intent_id = won["intent_id"]
            if decision == "APPROVED":
                conn.execute("UPDATE payment_intents SET status = 'APPROVED', updated_at = now() WHERE id = %s", (intent_id,))
                enqueue(conn, "execute_payment", team_id, {"intent_id": intent_id, "approval_id": approval_id}, dedupe_key=f"execute:{approval_id}")
            else:
                conn.execute("UPDATE payment_intents SET status = 'CANCELLED', updated_at = now() WHERE id = %s", (intent_id,))
                conn.execute("UPDATE obligations SET status = 'NEEDS_REVIEW', updated_at = now() WHERE id = (SELECT obligation_id FROM payment_intents WHERE id = %s)", (intent_id,))
            conn.execute(
                "UPDATE routine_runs SET status = 'COMPLETED', result = result || %s::jsonb, updated_at = now() WHERE id = %s AND status = 'AWAITING_APPROVAL'",
                (f'{{"approval_decision": "{decision}"}}', row["run_id"]),
            )
            audit(conn, team_id, "USER", user_id, "ApprovalGranted" if decision == "APPROVED" else "ApprovalRejected", "payment_intent", intent_id, {"hash": shown_hash, "reason": reason}, step_id)
            notify_approvers(conn, team_id, f"PAYMENT_{decision}", {"intent_id": intent_id, "by": user_id}, f"decided:{approval_id}")
            return DecisionResult(True, decision, "Approved. Sending now." if decision == "APPROVED" else "Rejected.", user_id)
        return _why_not(conn, team_id, user_id, approval_id, shown_hash, decision)


def _why_not(conn, team_id, user_id, approval_id, shown_hash, decision) -> DecisionResult:
    now = fetch_one(conn, "SELECT a.*, a.expires_at <= now() AS expired, u.name FROM approvals a LEFT JOIN users u ON u.id = a.decided_by WHERE a.id = %s", (approval_id,))
    member = fetch_one(conn, "SELECT role FROM memberships WHERE team_id = %s AND user_id = %s", (team_id, user_id))
    if not member or member["role"] != "APPROVER":
        return DecisionResult(False, now["status"], "Only an Approver can decide on a payment.")
    if now["status"] == decision and now["decided_by"] == user_id:
        return DecisionResult(True, decision, "Already recorded.", user_id)
    if now["status"] in ("APPROVED", "REJECTED"):
        return DecisionResult(False, now["status"], f"Already {now['status'].lower()} by {now['name']} at {now['decided_at']:%H:%M}.", now["decided_by"])
    if now["status"] == "CANCELLED":
        return DecisionResult(False, "CANCELLED", "This payment was replaced or cancelled. Nothing was sent.")
    if now["payload_hash"] != shown_hash:
        return DecisionResult(False, now["status"], "The payment changed since this screen loaded. Review it again.")
    if now["expired"]:
        return DecisionResult(False, "EXPIRED", "This approval has expired. Prepare the payment again.")
    paused = fetch_one(conn, "SELECT payments_paused FROM team_controls WHERE team_id = %s", (team_id,))
    if paused and paused["payments_paused"]:
        return DecisionResult(False, now["status"], "New payments are paused for this team.")
    return DecisionResult(False, now["status"], "This payment needs review. Open it and clear the warnings first.")


def cancel_pending(conn, intent_id: str) -> bool:
    """Used by Replace Bill. Same rule as a tap: only if still pending, so whichever comes first wins."""
    row = conn.execute("UPDATE approvals SET status = 'CANCELLED' WHERE intent_id = %s AND status = 'PENDING' RETURNING id", (intent_id,)).fetchone()
    return row is not None
