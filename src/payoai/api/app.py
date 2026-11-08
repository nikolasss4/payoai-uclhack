"""The API. Every route checks team membership and role on the server. It holds no Gemini or provider key."""

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from payoai.agent import chips, plans, read_tools
from payoai.db import Jsonb, fetch_all, fetch_one, new_id, tx
from payoai.domain.money import format_minor
from payoai.money import approval as approvals
from payoai.money.reconcile import handle_webhook, resolve_manually
from payoai.queue import enqueue
from payoai.routines import accounting, contacts, purchases, recurring
from payoai.routines.nodes.documents import store_document
from payoai.routines.registry import get_routine
from payoai.settings import ROOT, load_env
from payoai.trace import audit

load_env(".env.shared", ".env.api")
app = FastAPI(title="PayoAI", description="Synthetic data only. No real money.")


def _fmt(minor, asset) -> str | None:
    """Money for display, always formatted by the server."""
    return None if minor is None or not asset else format_minor(int(minor), asset)


def _secret() -> bytes:
    return os.environ.get("SESSION_SECRET", "local-demo-session-secret").encode()


def _sign(user_id: str) -> str:
    return f"{user_id}.{hmac.new(_secret(), user_id.encode(), hashlib.sha256).hexdigest()}"


def current_user(authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    user_id = token.rsplit(".", 1)[0] if "." in token else ""
    if not user_id or not hmac.compare_digest(_sign(user_id), token):
        raise HTTPException(401, "Sign in first.")
    return user_id


class Member:
    def __init__(self, user_id: str, team_id: str, role: str):
        self.user_id, self.team_id, self.role = user_id, team_id, role

    def require(self, *roles: str) -> None:
        if self.role not in roles:
            raise HTTPException(403, f"Your role ({self.role.title()}) can't do this.")


def member(user_id: str = Depends(current_user), x_team_id: str = Header(default="")) -> Member:
    """The team comes from a checked membership, never from trusting the request alone."""
    with tx() as conn:
        row = fetch_one(conn, "SELECT role FROM memberships WHERE team_id = %s AND user_id = %s", (x_team_id, user_id))
    if row is None:
        raise HTTPException(403, "You aren't a member of that team.")
    return Member(user_id, x_team_id, row["role"])


def developer(user_id: str = Depends(current_user)) -> str:
    with tx() as conn:
        row = fetch_one(conn, "SELECT 1 AS ok FROM platform_roles WHERE user_id = %s AND role = 'DEVELOPER'", (user_id,))
    if row is None:
        raise HTTPException(403, "Developer access only.")
    return user_id


# Session


class SignIn(BaseModel):
    email: str


@app.post("/api/session")
def sign_in(body: SignIn):
    """Demo sign-in with seeded users only. There are no real accounts."""
    with tx() as conn:
        user = fetch_one(conn, "SELECT id, name, email FROM users WHERE email = %s", (body.email,))
        if user is None:
            raise HTTPException(404, "No demo user with that email.")
        teams = fetch_all(conn, "SELECT t.id, t.name, m.role FROM memberships m JOIN teams t ON t.id = m.team_id WHERE m.user_id = %s AND NOT t.is_eval", (user["id"],))
        is_developer = fetch_one(conn, "SELECT 1 AS ok FROM platform_roles WHERE user_id = %s", (user["id"],)) is not None
    return {"token": _sign(user["id"]), "user": user, "teams": teams, "developer": is_developer}


@app.get("/api/demo-users")
def demo_users():
    with tx() as conn:
        return fetch_all(conn, "SELECT u.name, u.email, m.role FROM users u JOIN memberships m ON m.user_id = u.id JOIN teams t ON t.id = m.team_id WHERE t.id = 'team_demo' ORDER BY m.role")


# Chat


class NewTurn(BaseModel):
    text: str
    document_id: str | None = None


@app.post("/api/chats")
def create_chat(me: Member = Depends(member)):
    chat_id = new_id("chat")
    with tx() as conn:
        conn.execute("INSERT INTO chats (id, team_id, user_id) VALUES (%s, %s, %s)", (chat_id, me.team_id, me.user_id))
    return {"id": chat_id}


@app.get("/api/chats")
def list_chats(me: Member = Depends(member)):
    with tx() as conn:
        return fetch_all(conn, "SELECT id, title, created_at FROM chats WHERE team_id = %s AND user_id = %s ORDER BY created_at DESC LIMIT 30", (me.team_id, me.user_id))


def _own_chat(conn, me: Member, chat_id: str) -> dict:
    chat = fetch_one(conn, "SELECT * FROM chats WHERE id = %s AND team_id = %s AND user_id = %s", (chat_id, me.team_id, me.user_id))
    if chat is None:
        raise HTTPException(404, "Chat not found.")
    return chat


@app.post("/api/chats/{chat_id}/turns")
def send_turn(chat_id: str, body: NewTurn, me: Member = Depends(member)):
    text = body.text
    if body.document_id:
        text += f"\n[The user attached document_id {body.document_id}]"
    turn_id = new_id("turn")
    with tx() as conn:
        chat = _own_chat(conn, me, chat_id)
        if body.document_id and not fetch_one(conn, "SELECT id FROM documents WHERE id = %s AND team_id = %s", (body.document_id, me.team_id)):
            raise HTTPException(404, "Document not found.")
        seq = fetch_one(conn, "SELECT COALESCE(max(seq), -1) + 1 AS n FROM turns WHERE chat_id = %s", (chat_id,))["n"]
        conn.execute("INSERT INTO turns (id, chat_id, team_id, seq, user_input) VALUES (%s, %s, %s, %s, %s)", (turn_id, chat_id, me.team_id, seq, text))
        if chat["title"] is None:
            conn.execute("UPDATE chats SET title = %s WHERE id = %s", (body.text[:60], chat_id))
        enqueue(conn, "run_turn", me.team_id, {"chat_id": chat_id, "turn_id": turn_id, "user_id": me.user_id})
    return {"id": turn_id}


def _render_event(team_id: str, event: dict) -> dict:
    out = {"id": event["id"], "kind": event["kind"], "turn_id": event["turn_id"], "run_id": event["run_id"], "payload": event["payload"], "at": str(event["created_at"])}
    if event["kind"] == "REPLY":
        out["parts"] = chips.render(team_id, event["payload"].get("text", ""))
    return out


@app.get("/api/chats/{chat_id}/events")
def chat_events(chat_id: str, after: int = 0, me: Member = Depends(member)):
    with tx() as conn:
        _own_chat(conn, me, chat_id)
        rows = fetch_all(conn, "SELECT * FROM chat_events WHERE chat_id = %s AND id > %s ORDER BY id", (chat_id, after))
    return [_render_event(me.team_id, r) for r in rows]


@app.get("/api/chats/{chat_id}/stream")
async def stream_events(chat_id: str, request: Request, after: int = 0, me: Member = Depends(member)):
    """Server-sent events. Safety never depends on this, because every tap is checked by the database."""
    with tx() as conn:
        _own_chat(conn, me, chat_id)

    async def generate():
        last = after
        while not await request.is_disconnected():
            rows = await asyncio.to_thread(_events_after, chat_id, last)
            for row in rows:
                last = row["id"]
                yield f"data: {json.dumps(_render_event(me.team_id, row), default=str)}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(generate(), media_type="text/event-stream")


def _events_after(chat_id: str, last: int) -> list[dict]:
    with tx() as conn:
        return fetch_all(conn, "SELECT * FROM chat_events WHERE chat_id = %s AND id > %s ORDER BY id", (chat_id, last))


# Start Confirmations


@app.post("/api/confirmations/{confirmation_id}/{decision}")
def decide_confirmation(confirmation_id: str, decision: str, me: Member = Depends(member)):
    if decision not in ("confirm", "decline"):
        raise HTTPException(404)
    with tx() as conn:
        row = conn.execute(
            "UPDATE start_confirmations SET status = %s WHERE id = %s AND team_id = %s AND requested_by = %s AND status = 'PENDING' RETURNING *",
            ("CONFIRMED" if decision == "confirm" else "DECLINED", confirmation_id, me.team_id, me.user_id),
        ).fetchone()
        if row is None:
            raise HTTPException(409, "This card was already decided or isn't yours.")
        if decision == "confirm":
            if me.role not in get_routine(row["routine"]).roles:
                raise HTTPException(403, "Your role can't start this Routine.")
            enqueue(conn, "run_routine", me.team_id, plans.routine_job(row, me.user_id), dedupe_key=f"confirm:{confirmation_id}")
    if row["plan_item_id"]:
        plans.on_confirmation_decided(confirmation_id)
    return {"status": row["status"]}


# Plans


@app.get("/api/plans/{plan_id}")
def plan(plan_id: str, me: Member = Depends(member)):
    with tx() as conn:
        row = fetch_one(conn, "SELECT chat_id FROM plans WHERE id = %s AND team_id = %s", (plan_id, me.team_id))
        if row is None:
            raise HTTPException(404, "Plan not found.")
        _own_chat(conn, me, row["chat_id"])
    return plans.snapshot(plan_id, me.team_id)


@app.post("/api/plans/{plan_id}/stop")
def stop_plan(plan_id: str, me: Member = Depends(member)):
    result = plans.stop(plan_id, me.team_id, me.user_id)
    if result["status"] == "NOT_FOUND":
        raise HTTPException(404, "Plan not found.")
    return plans.snapshot(plan_id, me.team_id)


@app.post("/api/plans/{plan_id}/confirm-all")
def confirm_plan_cards(plan_id: str, me: Member = Depends(member)):
    """One tap for every start card in this plan that is waiting on this person."""
    with tx() as conn:
        row = fetch_one(conn, "SELECT chat_id FROM plans WHERE id = %s AND team_id = %s", (plan_id, me.team_id))
        if row is None:
            raise HTTPException(404, "Plan not found.")
        _own_chat(conn, me, row["chat_id"])
    started = plans.confirm_all(plan_id, me.team_id, me.user_id, me.role)
    return {"started": len(started), "plan": plans.snapshot(plan_id, me.team_id)}


# Documents and bills


@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...), me: Member = Depends(member)):
    me.require("OPERATOR", "APPROVER")
    data = await file.read()
    if file.content_type not in ("application/pdf", "text/plain", "image/png", "image/jpeg"):
        raise HTTPException(415, "Upload a PDF, text file or image.")
    return {"id": await asyncio.to_thread(store_document, me.team_id, file.filename or "document", file.content_type, data, me.user_id)}


@app.get("/api/bills")
def bills(status: str | None = None, me: Member = Depends(member)):
    return read_tools.list_bills(me.team_id, status)


@app.get("/api/bills/{bill_id}")
def bill(bill_id: str, me: Member = Depends(member)):
    found = read_tools.get_bill(me.team_id, bill_id)
    if found is None:
        raise HTTPException(404, "Bill not found.")
    return found


class Correction(BaseModel):
    field: str
    value: str
    reason: str


@app.post("/api/bills/{bill_id}/manual-correction")
def correct_bill(bill_id: str, body: Correction, me: Member = Depends(member)):
    try:
        return accounting.manual_correction(me.team_id, me.user_id, bill_id, body.field, body.value, body.reason)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# Approvals


def _card(conn, row: dict, me: Member) -> dict:
    checks = fetch_all(conn, "SELECT check_name, rule_id, outcome, reason FROM check_results WHERE obligation_id = %s ORDER BY created_at", (row["obligation_id"],))
    decision = fetch_one(conn, "SELECT decision, explanation FROM decisions WHERE obligation_id = %s ORDER BY created_at DESC LIMIT 1", (row["obligation_id"],))
    investigation = fetch_one(conn, "SELECT findings FROM investigations WHERE run_id = %s ORDER BY created_at DESC LIMIT 1", (row["run_id"],))
    same_person = fetch_one(conn, "SELECT same_person_added FROM destination_verifications WHERE destination_id = %s ORDER BY verified_at DESC LIMIT 1", (row["destination_id"],))
    return {
        "approval_id": row["approval_id"], "status": row["approval_status"], "hash": row["payload_hash"], "expires_at": row["expires_at"], "decided_by": row["decided_name"], "decided_at": row["decided_at"],
        "intent_id": row["id"], "payment_status": row["status"], "counterparty": row["counterparty"], "destination": row["masked_display"], "destination_status": row["destination_status"],
        "rail": row["rail"], "asset": row["asset"], "chain_id": row["chain_id"], "token_contract": row["token_contract"], "source_account": row["account_name"],
        "amount": format_minor(int(row["amount_minor"]), row["asset"]), "max_fee": format_minor(int(row["max_fee_minor"]), row["asset"]), "reference": row["payment_reference"],
        "warnings": row["warnings"], "requires_review": row["requires_review"], "decision": decision, "checks": checks, "agent_suggestions": investigation["findings"] if investigation else [],
        "added_and_verified_by_same_person": bool(same_person and same_person["same_person_added"]), "expired": row["is_expired"], "can_decide": me.role == "APPROVER" and row["approval_status"] == "PENDING" and not row["is_expired"],
    }


CARD_SQL = """
    SELECT p.*, a.id AS approval_id, a.status AS approval_status, a.expires_at, a.expires_at <= now() AS is_expired, a.decided_at, u.name AS decided_name, c.display_name AS counterparty,
           d.masked_display, d.status AS destination_status, f.name AS account_name
    FROM approvals a JOIN payment_intents p ON p.id = a.intent_id JOIN counterparties c ON c.id = p.counterparty_id
    JOIN payment_destinations d ON d.id = p.destination_id JOIN financial_accounts f ON f.id = p.source_account_id LEFT JOIN users u ON u.id = a.decided_by
"""


@app.get("/api/approvals")
def approvals_inbox(status: str = "PENDING", me: Member = Depends(member)):
    with tx() as conn:
        rows = fetch_all(conn, CARD_SQL + " WHERE a.team_id = %s AND a.status = %s ORDER BY a.created_at DESC LIMIT 50", (me.team_id, status))
        return [_card(conn, r, me) for r in rows]


@app.get("/api/approvals/{approval_id}")
def approval_card(approval_id: str, me: Member = Depends(member)):
    """Rendered from the live record, so a card always shows the current state."""
    with tx() as conn:
        row = fetch_one(conn, CARD_SQL + " WHERE a.id = %s AND a.team_id = %s", (approval_id, me.team_id))
        if row is None:
            raise HTTPException(404, "Approval not found.")
        return _card(conn, row, me)


@app.get("/api/approvals/{approval_id}/reveal")
def reveal_destination(approval_id: str, me: Member = Depends(member)):
    """The full details, for an Approver, on a screen outside the model. It's recorded each time."""
    me.require("APPROVER")
    with tx() as conn:
        row = fetch_one(conn, "SELECT p.id, p.canonical FROM approvals a JOIN payment_intents p ON p.id = a.intent_id WHERE a.id = %s AND a.team_id = %s", (approval_id, me.team_id))
        if row is None:
            raise HTTPException(404, "Approval not found.")
        audit(conn, me.team_id, "USER", me.user_id, "DestinationRevealed", "payment_intent", row["id"])
    return {"details": row["canonical"]["destination_details"]}


class Decision(BaseModel):
    hash: str
    decision: str
    reviewed: bool = False
    reason: str | None = None


@app.post("/api/approvals/{approval_id}/decide")
def decide_approval(approval_id: str, body: Decision, me: Member = Depends(member)):
    if body.decision not in ("APPROVED", "REJECTED"):
        raise HTTPException(400, "decision must be APPROVED or REJECTED")
    result = approvals.decide(me.team_id, me.user_id, approval_id, body.hash, body.decision, body.reviewed, body.reason)
    return {"won": result.won, "status": result.status, "message": result.message}


class BatchDecision(BaseModel):
    items: list[dict]


@app.post("/api/approvals/batch")
def decide_batch(body: BatchDecision, me: Member = Depends(member)):
    """One tap, a separate approval per payment, and the response says which were already decided."""
    out = []
    for item in body.items:
        result = approvals.decide(me.team_id, me.user_id, item["approval_id"], item["hash"], "APPROVED")
        out.append({"approval_id": item["approval_id"], "won": result.won, "message": result.message})
    return {"results": out}


# Payments, cash and traces


@app.get("/api/payments")
def payments(status: str | None = None, me: Member = Depends(member)):
    return read_tools.list_payments(me.team_id, status)


@app.get("/api/payments/{payment_id}")
def payment(payment_id: str, me: Member = Depends(member)):
    found = read_tools.get_payment(me.team_id, payment_id)
    if found is None:
        raise HTTPException(404, "Payment not found.")
    return found


@app.get("/api/confirmations/{confirmation_id}")
def confirmation(confirmation_id: str, me: Member = Depends(member)):
    with tx() as conn:
        row = fetch_one(conn, "SELECT id, routine, status, run_id FROM start_confirmations WHERE id = %s AND team_id = %s", (confirmation_id, me.team_id))
    if row is None:
        raise HTTPException(404, "Card not found.")
    return row


@app.get("/api/chats/{chat_id}/turns")
def chat_turns(chat_id: str, me: Member = Depends(member)):
    with tx() as conn:
        _own_chat(conn, me, chat_id)
        return fetch_all(conn, "SELECT id, seq, user_input, status, started_at FROM turns WHERE chat_id = %s ORDER BY seq", (chat_id,))


@app.post("/api/cash/analyse")
def start_cash_analysis(me: Member = Depends(member)):
    with tx() as conn:
        enqueue(conn, "cash_analysis", me.team_id, {})
    return {"status": "QUEUED"}


@app.get("/api/payments/{payment_id}/trace")
def payment_trace(payment_id: str, me: Member = Depends(member)):
    """Source to decision to approval to outcome, for one payment."""
    with tx() as conn:
        intent = fetch_one(conn, "SELECT id, run_id, obligation_id FROM payment_intents WHERE id = %s AND team_id = %s", (payment_id, me.team_id))
        if intent is None:
            raise HTTPException(404, "Payment not found.")
        runs = [intent["run_id"]] + [r["id"] for r in fetch_all(conn, "SELECT id FROM routine_runs WHERE team_id = %s AND (inputs->>'intent_id' = %s OR inputs->>'attempt_id' IN (SELECT id FROM payment_attempts WHERE intent_id = %s))", (me.team_id, payment_id, payment_id))]
        return {
            "steps": fetch_all(conn, "SELECT id, run_id, step_type, name, status, input, output, trust_label, model, thinking_level, input_tokens, output_tokens, cost_micros, error, started_at, ended_at FROM trace_steps WHERE run_id = ANY(%s) ORDER BY started_at", (runs,)),
            "audit": fetch_all(conn, "SELECT actor_type, actor_id, event_type, entity_type, entity_id, data, created_at FROM audit_events WHERE team_id = %s AND (entity_id = %s OR entity_id = %s) ORDER BY id", (me.team_id, payment_id, intent["obligation_id"])),
            "outcomes": fetch_all(conn, "SELECT e.source, e.observed_status, e.signature_valid, e.created_at, a.evidence_label FROM external_events e JOIN payment_attempts a ON a.id = e.attempt_id WHERE a.intent_id = %s ORDER BY e.created_at", (payment_id,)),
        }


@app.post("/api/attempts/{attempt_id}/resolve")
def resolve_attempt(attempt_id: str, body: dict, me: Member = Depends(member)):
    me.require("APPROVER")
    try:
        return resolve_manually(me.team_id, me.user_id, attempt_id, body.get("outcome", ""), body.get("evidence", ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/balances")
def balances(me: Member = Depends(member)):
    return {"balances": read_tools.get_balances(me.team_id), "alerts": read_tools.list_alerts(me.team_id)}


@app.get("/api/cash/latest")
def cash_latest(me: Member = Depends(member)):
    with tx() as conn:
        row = fetch_one(conn, "SELECT result, created_at FROM cash_analyses WHERE team_id = %s ORDER BY created_at DESC LIMIT 1", (me.team_id,))
    return row or {}


@app.get("/api/controls")
def get_controls(me: Member = Depends(member)):
    with tx() as conn:
        return fetch_one(conn, "SELECT payments_paused, paused_at, auto_mode, approval_ttl_hours FROM team_controls WHERE team_id = %s", (me.team_id,))


@app.post("/api/controls/pause")
def pause_payments(body: dict, me: Member = Depends(member)):
    """Pause All Payments is a plain control, so an emergency stop never depends on a model."""
    me.require("APPROVER")
    paused = bool(body.get("paused", True))
    with tx() as conn:
        conn.execute("UPDATE team_controls SET payments_paused = %s, paused_by = %s, paused_at = now() WHERE team_id = %s", (paused, me.user_id, me.team_id))
        audit(conn, me.team_id, "USER", me.user_id, "PaymentsPaused" if paused else "PaymentsResumed", "team", me.team_id)
    return {"payments_paused": paused}


@app.post("/api/controls/auto-mode")
def set_auto_mode(body: dict, me: Member = Depends(member)):
    me.require("APPROVER")
    with tx() as conn:
        conn.execute("UPDATE team_controls SET auto_mode = %s WHERE team_id = %s", (bool(body.get("on")), me.team_id))
        audit(conn, me.team_id, "USER", me.user_id, "AutoModeChanged", "team", me.team_id, {"on": bool(body.get("on"))})
    return {"auto_mode": bool(body.get("on"))}


# Contacts


@app.get("/api/counterparties")
def counterparties(name: str = "", me: Member = Depends(member)):
    return read_tools.search_counterparties(me.team_id, name)


@app.get("/api/counterparties/{counterparty_id}/channels")
def channels(counterparty_id: str, me: Member = Depends(member)):
    with tx() as conn:
        return fetch_all(conn, "SELECT id, kind, value, added_at FROM contact_channels WHERE team_id = %s AND counterparty_id = %s ORDER BY added_at", (me.team_id, counterparty_id))


class Verify(BaseModel):
    method: str
    evidence: str
    channel_id: str | None = None


@app.get("/api/destinations/{destination_id}/full")
def full_destination(destination_id: str, me: Member = Depends(member)):
    """Shown on the verification screen, so the Approver confirms the whole number."""
    me.require("APPROVER")
    with tx() as conn:
        row = fetch_one(conn, "SELECT details, status, source, chain_id, token_contract FROM payment_destinations WHERE id = %s AND team_id = %s", (destination_id, me.team_id))
        if row is None:
            raise HTTPException(404)
        audit(conn, me.team_id, "USER", me.user_id, "DestinationRevealed", "payment_destination", destination_id)
    return row


@app.post("/api/destinations/{destination_id}/verify")
def verify(destination_id: str, body: Verify, me: Member = Depends(member)):
    try:
        return contacts.verify_destination(me.team_id, me.user_id, destination_id, body.method, body.evidence, body.channel_id)
    except contacts.VerificationRefused as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/destinations/{destination_id}/revoke")
def revoke(destination_id: str, body: dict, me: Member = Depends(member)):
    try:
        return contacts.revoke_destination(me.team_id, me.user_id, destination_id, body.get("reason", ""))
    except contacts.VerificationRefused as exc:
        raise HTTPException(400, str(exc)) from exc


# Recurring


@app.get("/api/mandates")
def mandates(me: Member = Depends(member)):
    with tx() as conn:
        rows = fetch_all(conn, "SELECT m.id, m.status, m.max_amount_minor, m.asset, m.valid_until, m.purpose, c.display_name AS counterparty, r.frequency, r.next_due FROM mandates m JOIN counterparties c ON c.id = m.counterparty_id LEFT JOIN automation_rules r ON r.mandate_id = m.id WHERE m.team_id = %s ORDER BY m.created_at DESC", (me.team_id,))
    return [{**r, "max_amount": _fmt(r["max_amount_minor"], r["asset"])} for r in rows]


@app.post("/api/mandates/{mandate_id}/{action}")
def change_mandate(mandate_id: str, action: str, me: Member = Depends(member)):
    statuses = {"activate": "ACTIVE", "pause": "PAUSED", "revoke": "REVOKED"}
    if action not in statuses:
        raise HTTPException(404)
    try:
        return recurring.set_mandate_status(me.team_id, me.user_id, mandate_id, statuses[action])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


# Accounting


@app.get("/api/expenses")
def expenses(status: str | None = None, me: Member = Depends(member)):
    return read_tools.list_expenses(me.team_id, status)


@app.get("/api/expenses/{expense_id}")
def expense(expense_id: str, me: Member = Depends(member)):
    with tx() as conn:
        versions = fetch_all(conn, "SELECT * FROM expense_versions WHERE team_id = %s AND expense_id = %s ORDER BY version", (me.team_id, expense_id))
        if not versions:
            raise HTTPException(404)
        journal = fetch_all(conn, "SELECT kind, lines, reviewed FROM journal_proposals WHERE expense_id = %s", (expense_id,))
        for entry in journal:
            entry["lines"] = [{**line, "debit": _fmt(line.get("debit_minor"), line.get("asset")), "credit": _fmt(line.get("credit_minor"), line.get("asset"))} for line in entry["lines"]]
        return {
            "versions": [{**v, "gross": _fmt(v["gross_minor"], v["asset"]), "net": _fmt(v["net_minor"], v["asset"]), "tax": _fmt(v["tax_minor"], v["asset"])} for v in versions],
            "journal": journal,
            "exceptions": fetch_all(conn, "SELECT kind, status, details FROM exceptions WHERE expense_id = %s", (expense_id,)),
            "label": "Prepared for accountant review, not certified compliant.",
        }


@app.post("/api/expenses/{expense_id}/evidence")
def add_evidence(expense_id: str, body: dict, me: Member = Depends(member)):
    me.require("OPERATOR", "APPROVER")
    with tx() as conn:
        enqueue(conn, "attach_evidence", me.team_id, {"expense_id": expense_id, "document_id": body["document_id"], "user_id": me.user_id})
    return {"status": "QUEUED"}


# Memory


@app.get("/api/memories")
def memories(me: Member = Depends(member)):
    with tx() as conn:
        return fetch_all(conn, "SELECT id, user_id, type, subject, value, origin, source_ids, evidence_count, version, created_at FROM memories WHERE team_id = %s AND status = 'ACTIVE' AND (user_id IS NULL OR user_id = %s) ORDER BY created_at DESC", (me.team_id, me.user_id))


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: str, me: Member = Depends(member)):
    me.require("OPERATOR", "APPROVER")
    with tx() as conn:
        row = conn.execute("UPDATE memories SET status = 'DELETED' WHERE id = %s AND team_id = %s AND (user_id IS NULL OR user_id = %s) RETURNING id", (memory_id, me.team_id, me.user_id)).fetchone()
    if row is None:
        raise HTTPException(404)
    return {"deleted": True}


# Purchases


@app.get("/api/purchases")
def list_purchases(me: Member = Depends(member)):
    with tx() as conn:
        return fetch_all(conn, "SELECT id, merchant, summary, payload_hash, order_number, order_status, payment_status, delivery_status, created_at FROM purchase_summaries WHERE team_id = %s ORDER BY created_at DESC", (me.team_id,))


@app.post("/api/purchases/{purchase_id}/approve")
def approve_purchase(purchase_id: str, body: dict, me: Member = Depends(member)):
    return purchases.approve_purchase(me.team_id, me.user_id, purchase_id, body.get("hash", ""))


# Notifications


@app.get("/api/notifications")
def notifications(me: Member = Depends(member)):
    with tx() as conn:
        return fetch_all(conn, "SELECT id, kind, payload, created_at, read_at FROM notifications WHERE team_id = %s ORDER BY created_at DESC LIMIT 50", (me.team_id,))


# Webhooks


@app.post("/api/webhooks/{provider}")
async def webhook(provider: str, request: Request):
    """Signature-checked, and only prompts a status query. It never sets a payment's status."""
    result = await asyncio.to_thread(handle_webhook, provider.upper(), await request.body(), {k.lower(): v for k, v in request.headers.items()})
    if not result["accepted"]:
        raise HTTPException(400, result["reason"])
    return result


# Developer Console. A separate role, synthetic eval teams only.


@app.get("/api/dev/runs")
def dev_runs(_: str = Depends(developer)):
    with tx() as conn:
        return fetch_all(conn, "SELECT id, suite, system, split, summary, started_by, created_at FROM eval_runs ORDER BY created_at DESC LIMIT 50")


@app.get("/api/dev/runs/{eval_run_id}")
def dev_run(eval_run_id: str, _: str = Depends(developer)):
    with tx() as conn:
        run = fetch_one(conn, "SELECT * FROM eval_runs WHERE id = %s", (eval_run_id,))
        if run is None:
            raise HTTPException(404)
        run["results"] = fetch_all(conn, "SELECT case_id, team_id, passed, details, cost_micros, latency_ms FROM eval_results WHERE eval_run_id = %s ORDER BY case_id", (eval_run_id,))
    return run


@app.get("/api/dev/traces/{team_id}")
def dev_trace(team_id: str, _: str = Depends(developer)):
    """Replay: shows stored steps and calls nothing. Only synthetic eval teams are readable here."""
    with tx() as conn:
        team = fetch_one(conn, "SELECT is_eval FROM teams WHERE id = %s", (team_id,))
        if team is None or not team["is_eval"]:
            raise HTTPException(403, "Only synthetic eval teams can be viewed here.")
        return fetch_all(conn, "SELECT id, turn_id, run_id, step_type, name, status, input, output, trust_label, model, thinking_level, input_tokens, output_tokens, cost_micros, error, started_at, ended_at FROM trace_steps WHERE team_id = %s ORDER BY started_at", (team_id,))


class DevRun(BaseModel):
    suite: str
    split: str = "dev"
    system: str = "payoai"
    case_id: str | None = None
    unlock_heldout: str | None = None


@app.post("/api/dev/runs")
def dev_start(body: DevRun, user_id: str = Depends(developer)):
    """Re-run calls the model again and makes a new record. Held-out needs an explicit unlock, and every run is logged."""
    if body.split == "heldout" and body.unlock_heldout != "I am running the locked held-out set":
        raise HTTPException(403, "The held-out set is locked. Send the exact unlock phrase.")
    eval_run_id = new_id("evr")
    with tx() as conn:
        conn.execute("INSERT INTO eval_runs (id, suite, system, split, config, started_by) VALUES (%s, %s, %s, %s, %s, %s)", (eval_run_id, body.suite, body.system, body.split, Jsonb({"case_id": body.case_id}), user_id))
        team = fetch_one(conn, "SELECT id FROM teams WHERE is_eval LIMIT 1")
        if team is None:
            raise HTTPException(409, "Run the eval seed first.")
        enqueue(conn, "run_eval", team["id"], {"eval_run_id": eval_run_id})
    return {"id": eval_run_id, "provider": "simulator"}


# Frontend


from fastapi.staticfiles import StaticFiles  # noqa: E402

WEB = ROOT / "web"


@app.get("/")
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/health")
def health():
    return {"ok": True, "data": "synthetic", "real_money": False}


class Frontend(StaticFiles):
    """There's no build step, so browsers check for a new copy of each file every time."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Last, so that every route above takes priority. web/ holds only public files.
app.mount("/", Frontend(directory=WEB), name="web")


__all__ = ["Query", "Path"]
