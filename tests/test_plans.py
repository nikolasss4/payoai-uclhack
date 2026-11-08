"""Plans: made once by the Main Loop, then run by code one item per job, one chat job at a time."""

from datetime import datetime, timezone

import pytest

from payoai.agent import llm, main_loop, plans
from payoai.db import Jsonb, fetch_all, fetch_one, new_id, tx
from payoai.fixtures import FRAUD_IBAN, invoice_fields, render_invoice, seed_business, with_quotes
from payoai.queue import claim, enqueue, finish
from payoai.routines.nodes.documents import store_document


@pytest.fixture()
def biz(team):
    return {"team": team, **seed_business(team["id"], team["users"]["APPROVER"])}


@pytest.fixture(autouse=True)
def reset():
    yield
    main_loop.set_fake_model(None)
    llm.set_fake(None)


def new_turn(biz, text="Handle these bills", role="OPERATOR"):
    team = biz["team"]
    user = team["users"][role]
    chat_id, turn_id = new_id("chat"), new_id("turn")
    with tx() as conn:
        conn.execute("INSERT INTO chats (id, team_id, user_id) VALUES (%s, %s, %s)", (chat_id, team["id"], user))
        conn.execute("INSERT INTO turns (id, chat_id, team_id, seq, user_input) VALUES (%s, %s, %s, 0, %s)", (turn_id, chat_id, team["id"], text))
    return team["id"], user, chat_id, turn_id


def invoices(biz, *fields_list):
    """Stores each invoice, and fakes the extractor so each document reads as its own fields."""
    docs = {}
    for fields in fields_list:
        doc = store_document(biz["team"]["id"], f"{fields['invoice_number']}.txt", "text/plain", render_invoice(fields).encode(), None)
        docs[fields["invoice_number"]] = (doc, fields)

    def fake(step, parts, schema):
        if step == "extract_fields":
            text = str(parts)
            return with_quotes(next(f for _, f in docs.values() if f"INVOICE {f['invoice_number']}" in text))
        return {"explanation": "Checked.", "findings": []}

    llm.set_fake(fake)
    return [doc for doc, _ in docs.values()]


def session(biz, role="OPERATOR"):
    team_id, user, chat_id, turn_id = new_turn(biz, role=role)
    from payoai.trace import TraceContext

    return main_loop.Session(team_id, user, chat_id, turn_id, TraceContext(team_id, turn_id=turn_id))


def queued_steps(plan_id):
    with tx() as conn:
        return fetch_one(conn, "SELECT count(*) AS n FROM jobs WHERE kind = 'advance_plan' AND status = 'QUEUED' AND payload->>'plan_id' = %s", (plan_id,))["n"]


def drain(plan_id, limit=20):
    """Runs the plan's queued steps the way the worker would, one job at a time."""
    for _ in range(limit):
        with tx() as conn:
            job = fetch_one(conn, "SELECT id FROM jobs WHERE kind = 'advance_plan' AND status = 'QUEUED' AND payload->>'plan_id' = %s ORDER BY run_at LIMIT 1", (plan_id,))
            if job is None:
                return
            conn.execute("UPDATE jobs SET status = 'DONE' WHERE id = %s", (job["id"],))
        plans.advance(plan_id)
    raise AssertionError("the plan kept queueing steps")


def test_a_refused_item_saves_nothing(biz):
    s = session(biz)
    result = main_loop.make_plan(s, "Two things", [
        {"description": "Check cash", "routine": "cash_analysis", "inputs": {"days": 7}},
        {"description": "Something odd", "routine": "prepare_occurrence", "inputs": {}},
    ])
    assert result["status"] == "REFUSED" and result["problems"][0].startswith("Step 2")
    with tx() as conn:
        assert fetch_one(conn, "SELECT count(*) AS n FROM plans WHERE chat_id = %s", (s.chat_id,))["n"] == 0


def test_a_viewer_cant_plan_what_they_cant_start(biz):
    s = session(biz, role="VIEWER")
    result = main_loop.make_plan(s, "Bills", [{"description": "Register", "routine": "register_bill", "inputs": {"obligation_id": "obl_x"}}])
    assert result["status"] == "REFUSED" and "role" in result["problems"][0]


def test_dependencies_must_point_back(biz):
    s = session(biz)
    result = main_loop.make_plan(s, "Odd", [{"description": "A", "routine": "cash_analysis", "inputs": {}, "depends_on": [2]}, {"description": "B", "routine": "cash_analysis", "inputs": {}}])
    assert result["status"] == "REFUSED" and "earlier" in result["reason"]


def test_items_run_one_per_job_in_order_and_the_plan_finishes(biz):
    docs = invoices(biz, invoice_fields("INV-P1"), invoice_fields("INV-P2", total="120.00", subtotal="100.00", vat="20.00"))
    s = session(biz)
    result = main_loop.make_plan(s, "Register 2 bills", [{"description": f"Register bill {i + 1}", "routine": "register_bill", "inputs": {"document_id": d}} for i, d in enumerate(docs)])
    plan_id = result["plan_id"]
    assert result["status"] == "PLANNED" and queued_steps(plan_id) == 1

    plans.advance(plan_id)
    snap = plans.snapshot(plan_id)
    assert [i["state"] for i in snap["items"]][1] == "PENDING" and snap["items"][0]["run_id"]
    assert queued_steps(plan_id) == 1  # The run's own wake-up and the step's merge into one job.

    drain(plan_id)
    snap = plans.snapshot(plan_id)
    assert snap["status"] == "FINISHED" and all(i["run_id"] for i in snap["items"])
    with tx() as conn:
        runs = fetch_all(conn, "SELECT plan_item_id, started_turn_id, triggered_by FROM routine_runs r JOIN plan_items i ON i.id = r.plan_item_id WHERE i.plan_id = %s ORDER BY i.position", (plan_id,))
        events = fetch_all(conn, "SELECT kind, payload FROM chat_events WHERE chat_id = %s ORDER BY id", (s.chat_id,))
    assert len(runs) == 2 and {r["started_turn_id"] for r in runs} == {s.turn_id} and {r["triggered_by"] for r in runs} == {s.user_id}
    assert any(e["kind"] == "PLAN" for e in events)
    assert events[-1]["kind"] == "SUGGESTION" and "has been started" in events[-1]["payload"]["text"]


def test_a_retried_step_never_starts_a_second_run(biz):
    docs = invoices(biz, invoice_fields("INV-P3"))
    s = session(biz)
    plan_id = main_loop.make_plan(s, "One bill", [{"description": "Register", "routine": "register_bill", "inputs": {"document_id": docs[0]}}])["plan_id"]
    plans.advance(plan_id)
    with tx() as conn:
        # As if the worker crashed after the run was made, before the item was marked started.
        conn.execute("UPDATE plan_items SET status = 'STARTING' WHERE plan_id = %s", (plan_id,))
        conn.execute("UPDATE plans SET status = 'RUNNING' WHERE id = %s", (plan_id,))
    plans.advance(plan_id)
    with tx() as conn:
        assert fetch_one(conn, "SELECT count(*) AS n FROM routine_runs r JOIN plan_items i ON i.id = r.plan_item_id WHERE i.plan_id = %s", (plan_id,))["n"] == 1


def test_a_step_after_a_blocked_one_is_not_started(biz):
    docs = invoices(biz, invoice_fields("INV-P4", payment={"iban": FRAUD_IBAN}), invoice_fields("INV-P5"))
    s = session(biz)
    plan_id = main_loop.make_plan(s, "Two bills", [
        {"description": "Register the first", "routine": "register_bill", "inputs": {"document_id": docs[0]}},
        {"description": "Register the second", "routine": "register_bill", "inputs": {"document_id": docs[1]}, "depends_on": [1]},
    ])["plan_id"]
    drain(plan_id)
    first, second = plans.snapshot(plan_id)["items"]
    assert first["decision"] == "BLOCK"
    assert second["state"] == "BLOCKED" and second["run_id"] is None and "Step 1" in second["note"]


def test_start_cards_dont_hold_the_plan_up_and_one_tap_starts_them(biz):
    docs = invoices(biz, invoice_fields("INV-P6"), invoice_fields("INV-P7", total="120.00", subtotal="100.00", vat="20.00"))
    s = session(biz)
    plan_id = main_loop.make_plan(s, "Pay 2 bills", [{"description": f"Pay bill {i + 1}", "routine": "pay_bill", "inputs": {"document_id": d}} for i, d in enumerate(docs)])["plan_id"]
    drain(plan_id)
    snap = plans.snapshot(plan_id)
    assert snap["status"] == "FINISHED" and [i["state"] for i in snap["items"]] == ["NEEDS_START", "NEEDS_START"]
    with tx() as conn:
        assert fetch_one(conn, "SELECT count(*) AS n FROM routine_runs r JOIN plan_items i ON i.id = r.plan_item_id WHERE i.plan_id = %s", (plan_id,))["n"] == 0

    started = plans.confirm_all(plan_id, s.team_id, s.user_id, "OPERATOR")
    assert len(started) == 2
    with tx() as conn:
        jobs = fetch_all(conn, "SELECT payload FROM jobs WHERE kind = 'run_routine' AND payload->>'confirmation_id' = ANY(%s)", (started,))
    assert {j["payload"]["plan_item_id"] for j in jobs} == {i["id"] for i in _items(plan_id)}
    assert [i["state"] for i in plans.snapshot(plan_id)["items"]] == ["RUNNING", "RUNNING"]
    assert plans.confirm_all(plan_id, s.team_id, s.user_id, "OPERATOR") == []  # Already decided.


def test_stop_cancels_what_hasnt_started(biz):
    s = session(biz)
    plan_id = main_loop.make_plan(s, "Checks", [{"description": f"Check {n}", "routine": "cash_analysis", "inputs": {"days": n}} for n in (7, 14, 30)])["plan_id"]
    result = main_loop.stop_plan(s, plan_id)
    assert result == {"status": "STOPPED", "cancelled": 3}
    plans.advance(plan_id)
    snap = plans.snapshot(plan_id)
    assert snap["status"] == "STOPPED" and {i["state"] for i in snap["items"]} == {"CANCELLED"}
    with tx() as conn:
        assert fetch_one(conn, "SELECT count(*) AS n FROM routine_runs r JOIN plan_items i ON i.id = r.plan_item_id WHERE i.plan_id = %s", (plan_id,))["n"] == 0


def test_only_one_running_plan_per_chat(biz):
    s = session(biz)
    assert main_loop.make_plan(s, "First", [{"description": "Check", "routine": "cash_analysis", "inputs": {}}])["status"] == "PLANNED"
    second = main_loop.make_plan(s, "Second", [{"description": "Check", "routine": "cash_analysis", "inputs": {}}])
    assert second["status"] == "REFUSED" and "still running" in second["reason"]


def test_another_users_plan_cant_be_stopped(biz):
    s = session(biz)
    plan_id = main_loop.make_plan(s, "Mine", [{"description": "Check", "routine": "cash_analysis", "inputs": {}}])["plan_id"]
    assert plans.stop(plan_id, s.team_id, biz["team"]["users"]["APPROVER"]) == {"status": "NOT_FOUND"}


def test_one_job_per_chat_at_a_time(biz):
    team_id = biz["team"]["id"]
    chat_a, chat_b = new_id("chat"), new_id("chat")
    old = datetime(2000, 1, 1, tzinfo=timezone.utc)
    with tx() as conn:
        a = new_id("job")
        conn.execute("INSERT INTO jobs (id, kind, team_id, payload, run_at) VALUES (%s, 'run_turn', %s, %s, %s)", (a, team_id, Jsonb({"chat_id": chat_a}), old))
        enqueue(conn, "advance_plan", team_id, {"plan_id": "plan_x", "chat_id": chat_a}, run_at=old.replace(day=2))
        enqueue(conn, "run_turn", team_id, {"chat_id": chat_b}, run_at=old.replace(day=3))
    kinds = ["run_turn", "advance_plan"]
    first = claim("test", kinds)
    assert first["id"] == a
    second = claim("test", kinds)
    assert second["payload"]["chat_id"] == chat_b  # Chat A's plan step waits for its turn.
    finish(first["id"])
    third = claim("test", ["advance_plan"])  # Other tests' turns would otherwise come first.
    assert third["kind"] == "advance_plan" and third["payload"]["chat_id"] == chat_a
    finish(second["id"])
    finish(third["id"])


def _items(plan_id):
    with tx() as conn:
        return fetch_all(conn, "SELECT id FROM plan_items WHERE plan_id = %s ORDER BY position", (plan_id,))
