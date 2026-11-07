"""Server-side role and team checks, tested through the real API."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from payoai.agent import llm
from payoai.api.app import _sign, app
from payoai.db import fetch_one, new_id, tx
from payoai.fixtures import invoice_fields, render_invoice, seed_business, with_quotes
from payoai.queue import claim, finish
from payoai.routines import engine
from payoai.routines.nodes.documents import store_document

client = TestClient(app)


@pytest.fixture()
def biz(team):
    return {"team": team, **seed_business(team["id"], team["users"]["APPROVER"])}


def headers(biz, role):
    return {"Authorization": f"Bearer {_sign(biz['team']['users'][role])}", "X-Team-Id": biz["team"]["id"]}


@pytest.fixture()
def prepared(biz):
    fields = invoice_fields("API-1")
    llm.set_fake(lambda step, parts, schema: with_quotes(fields) if step == "extract_fields" else {"explanation": "ok", "findings": []})
    document_id = store_document(biz["team"]["id"], "i.txt", "text/plain", render_invoice(fields).encode(), None)
    yield engine.start("pay_bill", biz["team"]["id"], {"document_id": document_id}, "CHAT", actor=biz["team"]["users"]["OPERATOR"])
    llm.set_fake(None)


def test_forged_tokens_are_rejected(biz):
    assert client.get("/api/bills", headers={"Authorization": "Bearer user_x.forged", "X-Team-Id": biz["team"]["id"]}).status_code == 401
    assert client.get("/api/bills").status_code == 401


def test_cannot_use_a_team_you_are_not_in(biz, team):
    other = new_id("team")
    with tx() as conn:
        conn.execute("INSERT INTO teams (id, name) VALUES (%s, 'Other')", (other,))
    token = {"Authorization": f"Bearer {_sign(biz['team']['users']['APPROVER'])}"}
    assert client.get("/api/bills", headers={**token, "X-Team-Id": other}).status_code == 403


def test_records_from_another_team_are_not_found(biz, prepared, team):
    other = new_id("team")
    outsider = new_id("user")
    with tx() as conn:
        conn.execute("INSERT INTO teams (id, name) VALUES (%s, 'Other')", (other,))
        conn.execute("INSERT INTO team_controls (team_id) VALUES (%s)", (other,))
        conn.execute("INSERT INTO users (id, name, email) VALUES (%s, 'Out', %s)", (outsider, f"{outsider}@x.test"))
        conn.execute("INSERT INTO memberships VALUES (%s, %s, 'APPROVER')", (other, outsider))
    h = {"Authorization": f"Bearer {_sign(outsider)}", "X-Team-Id": other}
    assert client.get(f"/api/bills/{prepared['obligation_id']}", headers=h).status_code == 404
    assert client.get(f"/api/approvals/{prepared['approval_id']}", headers=h).status_code == 404
    assert client.get(f"/api/approvals/{prepared['approval_id']}/reveal", headers=h).status_code == 404
    assert client.get(f"/api/payments/{prepared['intent_id']}/trace", headers=h).status_code == 404
    assert client.get("/api/payments", headers=h).json() == []
    assert client.get("/api/counterparties", headers=h).json() == []
    decided = client.post(f"/api/approvals/{prepared['approval_id']}/decide", headers=h, json={"hash": "x", "decision": "APPROVED"}).json()
    assert decided["won"] is False and decided["status"] == "NOT_FOUND"


def test_role_matrix(biz, prepared):
    card = client.get(f"/api/approvals/{prepared['approval_id']}", headers=headers(biz, "VIEWER")).json()
    assert card["can_decide"] is False and "iban" not in json.dumps(card).lower()
    for role in ("VIEWER", "OPERATOR"):
        result = client.post(f"/api/approvals/{prepared['approval_id']}/decide", headers=headers(biz, role), json={"hash": card["hash"], "decision": "APPROVED"}).json()
        assert result["won"] is False
        assert client.post("/api/controls/pause", headers=headers(biz, role), json={"paused": True}).status_code == 403
        assert client.get(f"/api/approvals/{prepared['approval_id']}/reveal", headers=headers(biz, role)).status_code == 403
    assert client.post("/api/documents", headers=headers(biz, "VIEWER"), files={"file": ("a.txt", b"x", "text/plain")}).status_code == 403


def test_approver_can_approve_and_full_details_are_audited(biz, prepared):
    card = client.get(f"/api/approvals/{prepared['approval_id']}", headers=headers(biz, "APPROVER")).json()
    assert card["can_decide"] is True
    assert client.get(f"/api/approvals/{prepared['approval_id']}/reveal", headers=headers(biz, "APPROVER")).json()["details"]["iban"]
    result = client.post(f"/api/approvals/{prepared['approval_id']}/decide", headers=headers(biz, "APPROVER"), json={"hash": card["hash"], "decision": "APPROVED"}).json()
    assert result["won"] is True
    with tx() as conn:
        assert fetch_one(conn, "SELECT count(*) AS n FROM audit_events WHERE team_id = %s AND event_type = 'DestinationRevealed'", (biz["team"]["id"],))["n"] == 1
    job = claim("t", ["execute_payment"])
    assert job["payload"]["intent_id"] == prepared["intent_id"]
    finish(job["id"])


def test_pause_blocks_approval_through_the_api(biz, prepared):
    assert client.post("/api/controls/pause", headers=headers(biz, "APPROVER"), json={"paused": True}).json()["payments_paused"] is True
    card = client.get(f"/api/approvals/{prepared['approval_id']}", headers=headers(biz, "APPROVER")).json()
    result = client.post(f"/api/approvals/{prepared['approval_id']}/decide", headers=headers(biz, "APPROVER"), json={"hash": card["hash"], "decision": "APPROVED"}).json()
    assert result["won"] is False and "paused" in result["message"]


def test_someone_elses_chat_is_not_found(biz):
    chat = client.post("/api/chats", headers=headers(biz, "OPERATOR")).json()["id"]
    assert client.get(f"/api/chats/{chat}/events", headers=headers(biz, "APPROVER")).status_code == 404
    assert client.post(f"/api/chats/{chat}/turns", headers=headers(biz, "APPROVER"), json={"text": "hi"}).status_code == 404


def test_webhook_needs_a_valid_signature():
    assert client.post("/api/webhooks/simulator", content=b"{}", headers={"x-sim-signature": "forged"}).status_code == 400
    body = b'{"event_id": "e1", "external_id": "nope"}'
    signature = hmac.new(b"simulator-webhook-secret", body, hashlib.sha256).hexdigest()
    assert client.post("/api/webhooks/simulator", content=body, headers={"x-sim-signature": signature}).status_code == 400


def test_developer_console_is_separate_and_synthetic_only(biz):
    assert client.get("/api/dev/runs", headers=headers(biz, "APPROVER")).status_code == 403
    dev = new_id("user")
    with tx() as conn:
        conn.execute("INSERT INTO users (id, name, email) VALUES (%s, 'Dev', %s)", (dev, f"{dev}@x.test"))
        conn.execute("INSERT INTO platform_roles VALUES (%s, 'DEVELOPER')", (dev,))
    h = {"Authorization": f"Bearer {_sign(dev)}"}
    assert client.get(f"/api/dev/traces/{biz['team']['id']}", headers=h).status_code == 403
    assert client.post("/api/dev/runs", headers=h, json={"suite": "routines", "split": "heldout"}).status_code == 403


def test_money_is_formatted_and_expired_cards_cannot_be_decided(biz, prepared):
    with tx() as conn:
        conn.execute("UPDATE approvals SET expires_at = now() - interval '1 hour' WHERE id = %s", (prepared["approval_id"],))
    card = client.get(f"/api/approvals/{prepared['approval_id']}", headers=headers(biz, "APPROVER")).json()
    assert card["expired"] is True and card["can_decide"] is False
    assert client.get(f"/api/payments/{prepared['intent_id']}", headers=headers(biz, "VIEWER")).json()["amount"] == "£480.00"
    assert client.get("/api/payments/nope", headers=headers(biz, "VIEWER")).status_code == 404


def test_chat_goes_ahead_of_background_jobs(team):
    from payoai.queue import enqueue

    with tx() as conn:
        enqueue(conn, "suggest_category", team["id"], {"n": 1})
        enqueue(conn, "run_turn", team["id"], {"n": 2})
    job = claim("t", ["suggest_category", "run_turn"])
    assert job["kind"] == "run_turn"
    finish(job["id"])
    finish(claim("t", ["suggest_category"])["id"])
