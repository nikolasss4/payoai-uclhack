"""The API. Every route checks team membership and role on the server. It holds no Gemini or provider key."""

import asyncio
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from payoai.agent import chips, read_tools
from payoai.api import avatars, views
from payoai.db import Jsonb, fetch_all, fetch_one, new_id, tx
from payoai.domain.money import DECIMALS, format_minor, to_minor
from payoai.money import approval as approvals
from payoai.money.reconcile import handle_webhook, resolve_manually
from payoai.queue import enqueue
from payoai.routines import accounting, contacts, purchases, recurring
from payoai.providers.simulator import set_balance
from payoai.routines.nodes.checks import refresh_balance
from payoai.routines.nodes.documents import media_type_of, store_document
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


def _profile(conn, user_id: str) -> dict | None:
    """The person, their teams and whether they're a developer, with each profile picture's URL."""
    user = fetch_one(conn, "SELECT u.id, u.name, u.email, a.updated_at AS avatar_at FROM users u LEFT JOIN avatars a ON a.owner_kind = 'user' AND a.owner_id = u.id WHERE u.id = %s", (user_id,))
    if user is None:
        return None
    teams = fetch_all(
        conn,
        """
        SELECT t.id, t.name, m.role, (SELECT count(*) FROM memberships x WHERE x.team_id = t.id) AS members, a.updated_at AS avatar_at
        FROM memberships m JOIN teams t ON t.id = m.team_id LEFT JOIN avatars a ON a.owner_kind = 'team' AND a.owner_id = t.id
        WHERE m.user_id = %s AND NOT t.is_eval ORDER BY t.name
        """,
        (user_id,),
    )
    is_developer = fetch_one(conn, "SELECT 1 AS ok FROM platform_roles WHERE user_id = %s", (user_id,)) is not None
    return {
        "user": {"id": user["id"], "name": user["name"], "email": user["email"], "avatar_url": avatars.url("user", user["id"], user["avatar_at"])},
        "teams": [{"id": t["id"], "name": t["name"], "role": t["role"], "members": t["members"], "avatar_url": avatars.url("team", t["id"], t["avatar_at"])} for t in teams],
        "developer": is_developer,
    }


@app.post("/api/session")
def sign_in(body: SignIn):
    """Demo sign-in with seeded users only. There are no real accounts."""
    with tx() as conn:
        user = fetch_one(conn, "SELECT id FROM users WHERE email = %s", (body.email,))
        if user is None:
            raise HTTPException(404, "No demo user with that email.")
        profile = _profile(conn, user["id"])
    return {"token": _sign(user["id"]), **profile}


@app.get("/api/me")
def me_profile(user_id: str = Depends(current_user)):
    """The signed-in person's profile, re-read so names, teams and pictures stay current."""
    with tx() as conn:
        profile = _profile(conn, user_id)
    if profile is None:
        raise HTTPException(401, "Sign in first.")
    return profile


@app.get("/api/demo-users")
def demo_users():
    with tx() as conn:
        rows = fetch_all(conn, "SELECT u.id, u.name, u.email, m.role, t.name AS team_name, a.updated_at AS avatar_at FROM users u JOIN memberships m ON m.user_id = u.id JOIN teams t ON t.id = m.team_id LEFT JOIN avatars a ON a.owner_kind = 'user' AND a.owner_id = u.id WHERE t.id = 'team_demo' ORDER BY m.role, u.name")
    return [{"name": r["name"], "email": r["email"], "role": r["role"], "team_name": r["team_name"], "avatar_url": avatars.url("user", r["id"], r["avatar_at"])} for r in rows]


# Profile pictures


async def _read_picture(file: UploadFile) -> bytes:
    data = await file.read(avatars.MAX_BYTES + 1)
    try:
        avatars.check(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return data


@app.put("/api/me/avatar")
async def set_my_avatar(file: UploadFile = File(...), user_id: str = Depends(current_user)):
    """Anyone signed in can change their own picture. It shows in every team they belong to."""
    data = await _read_picture(file)
    with tx() as conn:
        return {"avatar_url": avatars.save(conn, "user", user_id, data, user_id)}


@app.delete("/api/me/avatar")
def remove_my_avatar(user_id: str = Depends(current_user)):
    with tx() as conn:
        avatars.remove(conn, "user", user_id)
    return {"avatar_url": None}


@app.put("/api/team/avatar")
async def set_team_avatar(file: UploadFile = File(...), me: Member = Depends(member)):
    """The organisation's picture. Only an Approver can change it, and each change is in the Audit."""
    me.require("APPROVER")
    data = await _read_picture(file)
    with tx() as conn:
        avatar_url = avatars.save(conn, "team", me.team_id, data, me.user_id)
        audit(conn, me.team_id, "USER", me.user_id, "OrganisationPictureChanged", "team", me.team_id)
    return {"avatar_url": avatar_url}


@app.delete("/api/team/avatar")
def remove_team_avatar(me: Member = Depends(member)):
    me.require("APPROVER")
    with tx() as conn:
        if avatars.remove(conn, "team", me.team_id):
            audit(conn, me.team_id, "USER", me.user_id, "OrganisationPictureRemoved", "team", me.team_id)
    return {"avatar_url": None}


@app.get("/api/avatars/{kind}/{owner_id}")
def get_avatar(kind: str, owner_id: str, v: str = "", s: str = ""):
    """A picture from a signed URL. The version is in the URL, so the image never changes at that address."""
    if not avatars.valid(kind, owner_id, v, s):
        raise HTTPException(404)
    with tx() as conn:
        row = fetch_one(conn, "SELECT media_type, data FROM avatars WHERE owner_kind = %s AND owner_id = %s", (kind, owner_id))
    if row is None:
        raise HTTPException(404)
    headers = {"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"}
    return Response(content=bytes(row["data"]), media_type=row["media_type"], headers=headers)


# Chat


MAX_TURN_DOCUMENTS = 10


class NewTurn(BaseModel):
    text: str
    document_id: str | None = None
    document_ids: list[str] = []


@app.post("/api/chats")
def create_chat(me: Member = Depends(member)):
    chat_id = new_id("chat")
    with tx() as conn:
        conn.execute("INSERT INTO chats (id, team_id, user_id) VALUES (%s, %s, %s)", (chat_id, me.team_id, me.user_id))
    return {"id": chat_id}


# Each chat's latest Turn comes from the (chat_id, seq) index, and the latest reply is only looked up
# for the 30 chats shown, through a partial index on REPLY events. Previews are cut short in SQL.
CHAT_LIST_SQL = """
SELECT c.id, c.title, c.created_at, c.last_at, c.last_input,
       CASE WHEN r.created_at >= c.last_at THEN r.text END AS last_reply
FROM (
  SELECT c.id, c.title, c.created_at, COALESCE(t.started_at, c.created_at) AS last_at, left(t.user_input, 400) AS last_input
  FROM chats c
  LEFT JOIN LATERAL (SELECT started_at, user_input FROM turns WHERE chat_id = c.id ORDER BY seq DESC LIMIT 1) t ON true
  WHERE c.team_id = %s AND c.user_id = %s
  ORDER BY last_at DESC
  LIMIT 30
) c
LEFT JOIN LATERAL (
  SELECT created_at, left(payload->>'text', 400) AS text FROM chat_events
  WHERE chat_id = c.id AND kind = 'REPLY' ORDER BY id DESC LIMIT 1
) r ON true
ORDER BY c.last_at DESC
"""
ATTACHED_NOTE = re.compile(r"\n?\[The user attached document_id [^\]]*\]")
MARKDOWN = re.compile(r"[*_`#>]+")


def _preview(text: str | None) -> str | None:
    """One plain line: Chip markers become their record kind, and markdown and newlines go."""
    if not text:
        return None
    text = ATTACHED_NOTE.sub("", text)
    text = chips.CITATION.sub(lambda m: m.group(1), text)
    text = " ".join(MARKDOWN.sub("", text).split())
    return text[:140] or None


@app.get("/api/chats")
def list_chats(me: Member = Depends(member)):
    with tx() as conn:
        rows = fetch_all(conn, CHAT_LIST_SQL, (me.team_id, me.user_id))
    out = []
    for r in rows:
        reply = _preview(r.pop("last_reply"))
        user = _preview(r.pop("last_input"))
        r["last_message"] = reply or user
        r["last_from"] = "agent" if reply else ("user" if user else None)
        out.append(r)
    return out


def _own_chat(conn, me: Member, chat_id: str) -> dict:
    chat = fetch_one(conn, "SELECT * FROM chats WHERE id = %s AND team_id = %s AND user_id = %s", (chat_id, me.team_id, me.user_id))
    if chat is None:
        raise HTTPException(404, "Chat not found.")
    return chat


@app.post("/api/chats/{chat_id}/turns")
def send_turn(chat_id: str, body: NewTurn, me: Member = Depends(member)):
    # One marker line per document, in the order they were attached. Readers split the user's own words off at the first one.
    document_ids = list(dict.fromkeys(([body.document_id] if body.document_id else []) + body.document_ids))
    if len(document_ids) > MAX_TURN_DOCUMENTS:
        raise HTTPException(422, f"Attach at most {MAX_TURN_DOCUMENTS} documents to one message.")
    text = body.text + "".join(f"\n[The user attached document_id {d}]" for d in document_ids)
    turn_id = new_id("turn")
    with tx() as conn:
        chat = _own_chat(conn, me, chat_id)
        if document_ids:
            found = fetch_all(conn, "SELECT id FROM documents WHERE id = ANY(%s) AND team_id = %s", (document_ids, me.team_id))
            if len(found) != len(document_ids):
                raise HTTPException(404, "Document not found.")
        seq = fetch_one(conn, "SELECT COALESCE(max(seq), -1) + 1 AS n FROM turns WHERE chat_id = %s", (chat_id,))["n"]
        conn.execute("INSERT INTO turns (id, chat_id, team_id, seq, user_input) VALUES (%s, %s, %s, %s, %s)", (turn_id, chat_id, me.team_id, seq, text))
        if chat["title"] is None:
            conn.execute("UPDATE chats SET title = %s WHERE id = %s", (body.text[:60], chat_id))
        enqueue(conn, "run_turn", me.team_id, {"chat_id": chat_id, "turn_id": turn_id, "user_id": me.user_id})
    return {"id": turn_id}


def _render_event(team_id: str, event: dict) -> dict:
    out = {"id": event["id"], "kind": event["kind"], "turn_id": event["turn_id"], "run_id": event["run_id"], "payload": event["payload"], "at": views.iso(event["created_at"])}
    if event["kind"] == "REPLY":
        out["parts"] = chips.render(team_id, event["payload"].get("text", ""))
    elif event["kind"] in ("START_CARD", "QUESTION", "STATUS"):
        with tx() as conn:
            out.update(views.event_extra(conn, team_id, event))
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
        last, quiet_since = after, time.monotonic()
        while not await request.is_disconnected():
            rows = await asyncio.to_thread(_events_after, chat_id, last)
            for row in rows:
                last = row["id"]
                yield f"data: {json.dumps(_render_event(me.team_id, row), default=str)}\n\n"
            if rows:
                quiet_since = time.monotonic()
            # Poll quickly while a Turn is streaming, and slowly once the chat goes quiet.
            await asyncio.sleep(0.25 if time.monotonic() - quiet_since < 30 else 1)

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
            enqueue(conn, "run_routine", me.team_id, {"routine": row["routine"], "inputs": row["inputs"], "actor": me.user_id, "chat_id": row["chat_id"], "turn_id": row["turn_id"], "confirmation_id": confirmation_id}, dedupe_key=f"confirm:{confirmation_id}")
    return {"status": row["status"]}


# Documents and bills


@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...), me: Member = Depends(member)):
    me.require("OPERATOR", "APPROVER")
    data = await file.read()
    filename = file.filename or "document"
    media_type = media_type_of(filename, file.content_type)
    if media_type is None:
        raise HTTPException(415, "Upload a PDF, Word document (.docx), text file or image.")
    try:
        return {"id": await asyncio.to_thread(store_document, me.team_id, filename, media_type, data, me.user_id)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/bills")
def bills(status: str | None = None, me: Member = Depends(member)):
    with tx() as conn:
        return views.bill_rows(conn, me.team_id, status)


@app.get("/api/bills/{bill_id}")
def bill(bill_id: str, me: Member = Depends(member)):
    found = read_tools.get_bill(me.team_id, bill_id)
    if found is None:
        raise HTTPException(404, "Bill not found.")
    with tx() as conn:
        return {**found, **views.bill_extra(conn, me.team_id, bill_id)}


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
        "suggestions": views.suggestions(conn, me.team_id, investigation["findings"] if investigation else []), "decided_reason": row["decided_reason"],
        "added_and_verified_by_same_person": bool(same_person and same_person["same_person_added"]), "expired": row["is_expired"], "can_decide": me.role == "APPROVER" and row["approval_status"] == "PENDING" and not row["is_expired"],
        **views.approval_extra(conn, me.team_id, row),
    }


CARD_SQL = """
    SELECT p.*, a.id AS approval_id, a.status AS approval_status, a.expires_at, a.expires_at <= now() AS is_expired, a.decided_at, a.reason AS decided_reason, a.created_at AS approval_created_at, u.name AS decided_name, c.display_name AS counterparty,
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
    """One row per payment, newest first, with the latest attempt's evidence label."""
    with tx() as conn:
        return views.payment_rows(conn, me.team_id, status)


@app.get("/api/payments/{payment_id}/detail")
def payment_detail(payment_id: str, me: Member = Depends(member)):
    """Everything the payment trace shows, from source document to expense, read from the records."""
    with tx() as conn:
        found = views.payment_detail(conn, me.team_id, payment_id)
    if found is None:
        raise HTTPException(404, "Payment not found.")
    return found


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
    """What the user typed, without the markers the app adds for the agent."""
    with tx() as conn:
        _own_chat(conn, me, chat_id)
        return views.turn_rows(conn, me.team_id, chat_id)


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
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/balances")
def balances(me: Member = Depends(member)):
    return {"balances": read_tools.get_balances(me.team_id), "alerts": read_tools.list_alerts(me.team_id)}


# Accounts. The banks are fictional and every account runs on the simulator, so no real bank is ever contacted.

BANKS = [
    {"id": "hnbc", "name": "HNBC Bank", "available": True, "assets": ["GBP", "EUR", "USD"]},
    {"id": "lords", "name": "Lords Bank", "available": True, "assets": ["GBP"]},
    {"id": "barcls", "name": "Barcls Bank", "available": True, "assets": ["GBP", "EUR", "USD"]},
    {"id": "natwist", "name": "NatWist", "available": False, "assets": []},
    {"id": "santandor", "name": "Santandor", "available": False, "assets": []},
    {"id": "monzu", "name": "Monzu", "available": False, "assets": []},
]
IN_FLIGHT = ["APPROVED", "SUBMITTING", "SUBMITTED", "PENDING", "UNKNOWN", "NEEDS_MANUAL_REVIEW"]


@app.get("/api/banks")
def list_banks(me: Member = Depends(member)):
    return {"banks": BANKS}


class NewAccount(BaseModel):
    bank: str
    asset: str = "GBP"
    name: str | None = None
    reserve: str = "0"


@app.post("/api/accounts")
def connect_account(body: NewAccount, me: Member = Depends(member)):
    """Connects a simulated account at a fictional bank. It starts with a synthetic balance."""
    me.require("APPROVER")
    bank = next((b for b in BANKS if b["id"] == body.bank), None)
    if bank is None:
        raise HTTPException(404, "That bank isn't in the list.")
    if not bank["available"]:
        raise HTTPException(400, f"{bank['name']} is coming soon.")
    if body.asset not in bank["assets"]:
        raise HTTPException(400, f"{bank['name']} doesn't offer {body.asset} accounts.")
    try:
        reserve_minor = to_minor(body.reserve.strip() or "0", body.asset)
    except ValueError as exc:
        raise HTTPException(400, "Enter the reserve as an amount, such as 500 or 500.00.") from exc
    if reserve_minor < 0:
        raise HTTPException(400, "The reserve can't be negative.")
    name = (body.name or "").strip()[:80] or f"{bank['name']} {body.asset} account"
    account_id, external_id = new_id("acct"), new_id("sim")
    set_balance(external_id, (5000 + secrets.randbelow(20001)) * 10 ** DECIMALS[body.asset])
    with tx() as conn:
        conn.execute(
            "INSERT INTO financial_accounts (id, team_id, name, provider, rail, asset, external_id, reserve_minor) VALUES (%s, %s, %s, 'SIMULATOR', 'FIAT_BANK', %s, %s, %s)",
            (account_id, me.team_id, name, body.asset, external_id, reserve_minor),
        )
        audit(conn, me.team_id, "USER", me.user_id, "AccountConnected", "account", account_id, {"name": name, "bank": bank["name"]})
        account = fetch_one(conn, "SELECT * FROM financial_accounts WHERE id = %s", (account_id,))
    refresh_balance(account)
    return {"id": account_id, "name": name}


@app.post("/api/accounts/{account_id}/disconnect")
def disconnect_account(account_id: str, me: Member = Depends(member)):
    """Takes an account out of the list. Its history stays, so past payments still show where they came from."""
    me.require("APPROVER")
    with tx() as conn:
        account = fetch_one(conn, "SELECT id, name FROM financial_accounts WHERE id = %s AND team_id = %s AND status = 'ACTIVE'", (account_id, me.team_id))
        if account is None:
            raise HTTPException(404, "Account not found.")
        busy = fetch_one(conn, "SELECT 1 AS busy FROM payment_intents WHERE source_account_id = %s AND status = ANY(%s) LIMIT 1", (account_id, IN_FLIGHT))
        if busy:
            raise HTTPException(409, "A payment from this account is still in progress. Remove it once that payment settles.")
        conn.execute("UPDATE financial_accounts SET status = 'DISCONNECTED' WHERE id = %s", (account_id,))
        audit(conn, me.team_id, "USER", me.user_id, "AccountDisconnected", "account", account_id, {"name": account["name"]})
    return {"id": account_id, "status": "DISCONNECTED"}


@app.get("/api/cash/latest")
def cash_latest(me: Member = Depends(member)):
    with tx() as conn:
        row = fetch_one(conn, "SELECT id, result, created_at FROM cash_analyses WHERE team_id = %s ORDER BY created_at DESC LIMIT 1", (me.team_id,))
        if row is None:
            return {}
        # Bills behind a shortfall, labelled from the live records.
        for entry in row["result"].get("by_asset", []):
            entry["driving"] = views.bill_rows_by_id(conn, me.team_id, entry.get("driving_bills") or [])
    return {**row, "created_at": views.iso(row["created_at"])}


@app.get("/api/controls")
def get_controls(me: Member = Depends(member)):
    with tx() as conn:
        return fetch_one(conn, "SELECT c.payments_paused, c.paused_at, u.name AS paused_by_name, c.auto_mode, c.approval_ttl_hours FROM team_controls c LEFT JOIN users u ON u.id = c.paused_by WHERE c.team_id = %s", (me.team_id,))


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


@app.get("/api/counterparties/{counterparty_id}")
def counterparty(counterparty_id: str, me: Member = Depends(member)):
    """A contact with masked destinations, channels on file and recent records. Full details need the audited route."""
    with tx() as conn:
        found = views.contact_detail(conn, me.team_id, counterparty_id)
    if found is None:
        raise HTTPException(404, "Contact not found.")
    return found


@app.get("/api/destinations/{destination_id}")
def destination(destination_id: str, me: Member = Depends(member)):
    """Masked details, where they came from and how they were verified."""
    with tx() as conn:
        found = views.destination_detail(conn, me.team_id, destination_id)
    if found is None:
        raise HTTPException(404, "Destination not found.")
    return found


@app.get("/api/counterparties/{counterparty_id}/channels")
def channels(counterparty_id: str, me: Member = Depends(member)):
    with tx() as conn:
        return fetch_all(conn, "SELECT id, kind, value, added_at FROM contact_channels WHERE team_id = %s AND counterparty_id = %s ORDER BY added_at", (me.team_id, counterparty_id))


class NewChannel(BaseModel):
    kind: str
    value: str


@app.post("/api/counterparties/{counterparty_id}/channels")
def add_channel(counterparty_id: str, body: NewChannel, me: Member = Depends(member)):
    try:
        channel = contacts.add_channel(me.team_id, me.user_id, counterparty_id, body.kind, body.value)
    except contacts.VerificationRefused as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**channel, "added_at": channel["added_at"].isoformat()}


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
    return read_tools.all_expenses(me.team_id, status)


EXPORT_COLUMNS = ["Expense ID", "Date", "Counterparty", "Category", "Category is a suggestion", "Business purpose", "Accounting period", "Currency", "Gross", "Net", "Tax", "GBP value", "Status", "Invoice reference", "Version"]


def _plain(minor, asset) -> str:
    """An amount a spreadsheet can add up: no symbol, no thousands separators."""
    if minor is None or not asset:
        return ""
    places = DECIMALS.get(asset, 2)
    return f"{Decimal(int(minor)) / (Decimal(10) ** places):.{places}f}"


def _cell(value) -> str:
    """Text from bills is untrusted, so a spreadsheet must not run it as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


# Declared before /api/expenses/{expense_id} so "export" isn't read as an ID.
@app.get("/api/expenses/export")
def export_expenses(start: date = Query(...), end: date = Query(...), status: str | None = None, me: Member = Depends(member)):
    """A CSV of every expense recorded between start and end, both inclusive (UTC days), in its latest version."""
    if end < start:
        raise HTTPException(422, "The end date is before the start date.")
    if (end - start).days > 366 * 5:
        raise HTTPException(422, "Export at most five years at a time.")
    with tx() as conn:
        rows = [r for r in read_tools.expense_rows(conn, me.team_id, start, end) if not status or r["status"] == status]
        audit(conn, me.team_id, "USER", me.user_id, "ExpensesExported", "team", me.team_id, {"start": start.isoformat(), "end": end.isoformat(), "status": status, "count": len(rows)})
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(EXPORT_COLUMNS)
    for r in rows:
        writer.writerow([
            r["expense_id"], r["recorded_at"].date().isoformat(), _cell(r["counterparty"]), _cell(r["category"]),
            "yes" if r["category_is_suggestion"] else "no", _cell(r["business_purpose"]), _cell(r["accounting_period"]), r["asset"],
            _plain(r["gross_minor"], r["asset"]), _plain(r["net_minor"], r["asset"]), _plain(r["tax_minor"], r["asset"]),
            _plain(r["gbp_value_minor"], "GBP"), r["status"], _cell(r["invoice_reference"]), r["version"],
        ])
    filename = f"expenses_{start.isoformat()}_to_{end.isoformat()}.csv"
    # A BOM so Excel reads £ and accented names as UTF-8.
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/expenses/{expense_id}")
def expense(expense_id: str, me: Member = Depends(member)):
    with tx() as conn:
        versions = fetch_all(conn, "SELECT * FROM expense_versions WHERE team_id = %s AND expense_id = %s ORDER BY version", (me.team_id, expense_id))
        if not versions:
            raise HTTPException(404)
        journal = fetch_all(conn, "SELECT kind, lines, reviewed FROM journal_proposals WHERE expense_id = %s", (expense_id,))
        for entry in journal:
            entry["lines"] = [{**line, "debit": _fmt(line.get("debit_minor"), line.get("asset")), "credit": _fmt(line.get("credit_minor"), line.get("asset"))} for line in entry["lines"]]
        counterparty_id = next((v["counterparty_id"] for v in reversed(versions) if v["counterparty_id"]), None)
        cp = counterparty_id and fetch_one(conn, "SELECT display_name FROM counterparties WHERE id = %s", (counterparty_id,))
        return {
            "id": expense_id,
            "counterparty": cp["display_name"] if cp else None,
            "versions": [{**v, "gross": _fmt(v["gross_minor"], v["asset"]), "net": _fmt(v["net_minor"], v["asset"]), "tax": _fmt(v["tax_minor"], v["asset"]), "created_at": views.iso(v["created_at"])} for v in versions],
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
    """Newest first. Read state is per person, and each item links to the record it's about."""
    with tx() as conn:
        return views.notifications(conn, me.team_id, me.user_id)


class MarkRead(BaseModel):
    ids: list[str] | None = None


@app.post("/api/notifications/read")
def mark_notifications_read(body: MarkRead, me: Member = Depends(member)):
    """Marks the given notifications read for this person, or all of them when no IDs are sent."""
    with tx() as conn:
        conn.execute(
            """
            INSERT INTO notification_reads (notification_id, user_id)
            SELECT n.id, %s FROM notifications n WHERE n.team_id = %s AND (n.user_id IS NULL OR n.user_id = %s) AND (%s::text[] IS NULL OR n.id = ANY(%s::text[]))
            ON CONFLICT DO NOTHING
            """,
            (me.user_id, me.team_id, me.user_id, body.ids, body.ids),
        )
    return {"ok": True}


# Audit


@app.get("/api/audit")
def audit_log(actor: str | None = None, limit: int = Query(default=200, ge=1, le=500), traces: bool = False, me: Member = Depends(member)):
    """The append-only audit record, newest first, with each entry linked to its record.
    With `traces`, agent turns and routine runs join the timeline, each openable to its steps."""
    if actor is not None and actor not in ("USER", "AGENT", "SYSTEM", "PROVIDER"):
        raise HTTPException(400, "actor must be USER, AGENT, SYSTEM or PROVIDER")
    with tx() as conn:
        if traces:
            return views.activity_feed(conn, me.team_id, actor, limit)
        return views.audit_feed(conn, me.team_id, actor, limit)


@app.get("/api/audit/{kind}/{trace_id}")
def audit_trace(kind: str, trace_id: str, me: Member = Depends(member)):
    """Every step of one turn or run, in order, with the audit events each step wrote."""
    if kind not in ("turn", "run"):
        raise HTTPException(400, "kind must be turn or run")
    with tx() as conn:
        detail = views.trace_detail(conn, me.team_id, kind, trace_id)
    if detail is None:
        raise HTTPException(404)
    return detail


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
APP = WEB / "dist"
# Everything the app loads is bundled and served from here. React sets inline style attributes, which style-src allows.
CSP = "default-src 'self'; img-src 'self' data:; font-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


@app.get("/")
def index():
    """The React app, built from frontend/ into web/dist. Its asset names change with every build."""
    built = APP / "index.html"
    if not built.exists():
        raise HTTPException(503, "The frontend isn't built. Run: cd frontend && npm ci && npm run build")
    return FileResponse(built, headers={"Cache-Control": "no-cache", "Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin"})


@app.get("/health")
def health():
    return {"ok": True, "data": "synthetic", "real_money": False}


class Frontend(StaticFiles):
    """There's no build step, so browsers check for a new copy of each file every time."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


class Assets(StaticFiles):
    """Built assets have a content hash in their name, so they can be cached for good."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


# Last, so that every route above takes priority. web/ holds only public files.
app.mount("/assets", Assets(directory=APP / "assets", check_dir=False), name="assets")
app.mount("/", Frontend(directory=WEB), name="web")


__all__ = ["Query", "Path"]
