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


def test_screen_views_read_live_records_and_stay_in_the_team(biz, prepared):
    h = headers(biz, "APPROVER")
    card = client.get(f"/api/approvals/{prepared['approval_id']}", headers=h).json()
    assert card["obligation_id"] and "suggestions" in card
    detail = client.get(f"/api/payments/{prepared['intent_id']}/detail", headers=h).json()
    assert detail["amount"] == "£480.00" and detail["status"] == "READY_FOR_APPROVAL" and detail["checks"]
    bill = client.get(f"/api/bills/{card['obligation_id']}", headers=h).json()
    assert bill["payment_rows"][0]["id"] == prepared["intent_id"]
    contact = client.get(f"/api/counterparties/{bill['counterparty_id']}", headers=h).json()
    destination = client.get(f"/api/destinations/{contact['destinations'][0]['id']}", headers=h).json()
    assert "details" not in destination and destination["masked"]
    feed = client.get("/api/audit", headers=h).json()
    assert feed["total"] >= 1 and all(e["actor_type"] in ("USER", "AGENT", "SYSTEM", "PROVIDER") for e in feed["entries"])
    assert client.get("/api/audit?actor=ROBOT", headers=h).status_code == 400
    other_team, other_user = new_id("team"), new_id("user")
    with tx() as conn:
        conn.execute("INSERT INTO teams (id, name) VALUES (%s, 'Other Ltd')", (other_team,))
        conn.execute("INSERT INTO users (id, name, email) VALUES (%s, 'Other', %s)", (other_user, f"{other_user}@example.test"))
        conn.execute("INSERT INTO memberships (team_id, user_id, role) VALUES (%s, %s, 'APPROVER')", (other_team, other_user))
    other = {"Authorization": f"Bearer {_sign(other_user)}", "X-Team-Id": other_team}
    assert client.get(f"/api/payments/{prepared['intent_id']}/detail", headers=other).status_code == 404
    assert client.get(f"/api/counterparties/{bill['counterparty_id']}", headers=other).status_code == 404
    assert client.get("/api/audit", headers=other).json()["total"] == 0


def test_notifications_are_read_per_person(biz, prepared):
    approver, operator = headers(biz, "APPROVER"), headers(biz, "OPERATOR")
    before = client.get("/api/notifications", headers=operator).json()["unread"]
    assert before >= 1
    assert client.post("/api/notifications/read", headers=approver, json={}).status_code == 200
    assert client.get("/api/notifications", headers=approver).json()["unread"] == 0
    assert client.get("/api/notifications", headers=operator).json()["unread"] == before


@pytest.mark.parametrize("filename, content_type, status", [
    ("bill.png", "image/png", 200),
    ("bill.heic", "application/octet-stream", 200),
    ("bill.webp", "", 200),
    ("bill.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", 422),
    ("bill.gif", "image/gif", 422),
    ("tool.exe", "application/octet-stream", 415),
])
def test_upload_accepts_images_pdfs_and_word_documents(biz, filename, content_type, status):
    response = client.post("/api/documents", headers=headers(biz, "OPERATOR"), files={"file": (filename, b"not really that file", content_type)})
    assert response.status_code == status, response.text


def test_accounts_can_be_added_and_removed_by_an_approver(biz):
    banks = client.get("/api/banks", headers=headers(biz, "VIEWER")).json()["banks"]
    assert {"HNBC Bank", "Lords Bank", "Barcls Bank"} <= {b["name"] for b in banks if b["available"]}
    assert any(not b["available"] for b in banks)
    body = {"bank": "hnbc", "asset": "GBP", "name": "HNBC ops", "reserve": "250.00"}
    assert client.post("/api/accounts", json=body, headers=headers(biz, "OPERATOR")).status_code == 403
    assert client.post("/api/accounts", json={**body, "bank": "monzu"}, headers=headers(biz, "APPROVER")).status_code == 400
    assert client.post("/api/accounts", json={**body, "asset": "EUR", "bank": "lords"}, headers=headers(biz, "APPROVER")).status_code == 400
    created = client.post("/api/accounts", json=body, headers=headers(biz, "APPROVER"))
    assert created.status_code == 200
    account_id = created.json()["id"]
    listed = {b["id"]: b for b in client.get("/api/balances", headers=headers(biz, "VIEWER")).json()["balances"]}
    assert listed[account_id]["name"] == "HNBC ops" and listed[account_id]["reserve"] == "£250.00" and listed[account_id]["balance"]
    assert client.post(f"/api/accounts/{account_id}/disconnect", headers=headers(biz, "OPERATOR")).status_code == 403
    assert client.post(f"/api/accounts/{account_id}/disconnect", headers=headers(biz, "APPROVER")).status_code == 200
    assert account_id not in {b["id"] for b in client.get("/api/balances", headers=headers(biz, "VIEWER")).json()["balances"]}
    assert client.post(f"/api/accounts/{account_id}/disconnect", headers=headers(biz, "APPROVER")).status_code == 404


def test_expenses_export_is_a_csv_for_the_chosen_period(biz):
    import csv
    from datetime import date, timedelta

    team_id = biz["team"]["id"]
    today = date.today()
    with tx() as conn:
        count = fetch_one(conn, "SELECT COUNT(DISTINCT expense_id) AS n FROM expense_versions WHERE team_id = %s", (team_id,))["n"]
    response = client.get("/api/expenses/export", params={"start": (today - timedelta(days=1)).isoformat(), "end": today.isoformat()}, headers=headers(biz, "VIEWER"))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert f'filename="expenses_{today - timedelta(days=1)}_to_{today}.csv"' in response.headers["content-disposition"]
    rows = list(csv.reader(response.text.lstrip("﻿").splitlines()))
    assert rows[0][:3] == ["Expense ID", "Date", "Counterparty"]
    assert len(rows) - 1 == count
    assert all("£" not in cell for row in rows[1:] for cell in row[8:12])

    past = client.get("/api/expenses/export", params={"start": "2000-01-01", "end": "2000-01-31"}, headers=headers(biz, "VIEWER"))
    assert len(list(csv.reader(past.text.lstrip("﻿").splitlines()))) == 1
    assert client.get("/api/expenses/export", params={"start": today.isoformat(), "end": "2000-01-01"}, headers=headers(biz, "VIEWER")).status_code == 422
    with tx() as conn:
        assert fetch_one(conn, "SELECT 1 AS ok FROM audit_events WHERE team_id = %s AND event_type = 'ExpensesExported'", (team_id,))


def test_audit_shows_runs_with_every_step_and_stays_in_the_team(biz, prepared):
    h = headers(biz, "APPROVER")
    feed = client.get("/api/audit?traces=1", headers=h).json()
    runs = [e for e in feed["entries"] if e["kind"] == "run"]
    run = next(e for e in runs if e["trace_id"] == prepared["run_id"])
    assert run["actor_type"] == "AGENT" and run["steps"] >= 1
    assert any(e["kind"] == "event" for e in feed["entries"])
    assert all(e["kind"] != "run" for e in client.get("/api/audit?traces=1&actor=PROVIDER", headers=h).json()["entries"])
    assert "kind" not in client.get("/api/audit", headers=h).json()["entries"][0]

    detail = client.get(f"/api/audit/run/{prepared['run_id']}", headers=h).json()
    assert detail["turn"] is None and detail["runs"][0]["id"] == prepared["run_id"]
    assert len(detail["steps"]) == run["steps"]
    assert all(s["run_id"] == prepared["run_id"] for s in detail["steps"])

    assert client.get("/api/audit/step/x", headers=h).status_code == 400
    assert client.get("/api/audit/turn/turn_missing", headers=h).status_code == 404
    other_team, other_user = new_id("team"), new_id("user")
    with tx() as conn:
        conn.execute("INSERT INTO teams (id, name) VALUES (%s, 'Other Ltd')", (other_team,))
        conn.execute("INSERT INTO users (id, name, email) VALUES (%s, 'Other', %s)", (other_user, f"{other_user}@example.test"))
        conn.execute("INSERT INTO memberships (team_id, user_id, role) VALUES (%s, %s, 'APPROVER')", (other_team, other_user))
    other = {"Authorization": f"Bearer {_sign(other_user)}", "X-Team-Id": other_team}
    assert client.get(f"/api/audit/run/{prepared['run_id']}", headers=other).status_code == 404
    assert client.get("/api/audit?traces=1", headers=other).json()["total"] == 0
