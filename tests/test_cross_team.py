"""Attempts to reach another team's records through every door a user or the model has.

Each attack has a counterexample: the same call made by the owning team works, so a refusal here
means the team boundary held, and not that the call was simply broken.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from payoai.agent import chips, llm, main_loop, read_tools
from payoai.db import fetch_one, new_id, tx
from payoai.fixtures import EXAMPLO_IBAN, invoice_fields, render_invoice, seed_business, with_quotes
from payoai.money import approval, execute, reconcile
from payoai.routines import accounting, contacts, engine, inputs as routine_inputs, purchases, recurring
from payoai.routines.nodes.documents import store_document
from payoai.trace import TraceContext


def new_team(name):
    team_id = new_id("team")
    users = {role: new_id("user") for role in ("VIEWER", "OPERATOR", "APPROVER")}
    with tx() as conn:
        conn.execute("INSERT INTO teams (id, name) VALUES (%s, %s)", (team_id, name))
        conn.execute("INSERT INTO team_controls (team_id) VALUES (%s)", (team_id,))
        for role, user_id in users.items():
            conn.execute("INSERT INTO users (id, name, email) VALUES (%s, %s, %s)", (user_id, role.title(), f"{user_id}@example.test"))
            conn.execute("INSERT INTO memberships (team_id, user_id, role) VALUES (%s, %s, %s)", (team_id, user_id, role))
    return {"id": team_id, "users": users}


@dataclass
class World:
    """Team A owns everything. Team B is the attacker, with an Approver of its own."""

    a: dict
    a_biz: dict
    b: dict
    b_biz: dict
    document: str
    bill: dict
    paid: dict
    expense: str
    mandate: str
    waiting_run: str


@pytest.fixture(scope="module")
def world():
    state = {"fields": None}

    def fake(step_name, parts, schema):
        if step_name == "extract_fields":
            return state["fields"]
        if step_name == "investigate":
            return {"findings": []}
        if step_name == "draft_request":
            return {"message": "Please send it."}
        if step_name == "suggest_category":
            return {"category": "Other"}
        return {"explanation": "Plain explanation."}

    llm.set_fake(fake)
    a, b = new_team("Team A"), new_team("Team B")
    a_biz, b_biz = seed_business(a["id"], a["users"]["APPROVER"]), seed_business(b["id"], b["users"]["APPROVER"])
    op = a["users"]["OPERATOR"]

    def upload(fields):
        state["fields"] = with_quotes(fields)
        return store_document(a["id"], "secret-invoice.txt", "text/plain", render_invoice(fields).encode(), op)

    document = upload(invoice_fields("SECRET-1", notes="Team A confidential text"))
    bill = engine.start("pay_bill", a["id"], {"document_id": document}, "CHAT", actor=op)
    assert bill["approval_id"], bill
    someone = engine.start("pay_someone", a["id"], routine_inputs.validate("pay_someone", {"counterparty_name": "Alex Chen", "amount": "300", "asset": "USDC", "purpose": "March"}), "CONFIRMATION", actor=op)
    with tx() as conn:
        card = fetch_one(conn, "SELECT * FROM approvals WHERE id = %s", (someone["approval_id"],))
    assert approval.decide(a["id"], a["users"]["APPROVER"], card["id"], card["payload_hash"], "APPROVED", reviewed=True).won
    outcome = execute.execute(a["id"], someone["intent_id"], card["id"])
    reconcile.reconcile(outcome["attempt_id"])
    with tx() as conn:
        expense = fetch_one(conn, "SELECT expense_id FROM expense_versions WHERE intent_id = %s", (someone["intent_id"],))["expense_id"]
    mandate = engine.start("set_up_recurring", a["id"], routine_inputs.validate("set_up_recurring", {"counterparty_name": "Alex Chen", "max_amount": "300", "asset": "USDC", "frequency": "MONTHLY", "day_of_period": 1, "valid_until": (date.today() + timedelta(days=100)).isoformat()}), "CONFIRMATION", actor=op)["mandate_id"]
    recurring.set_mandate_status(a["id"], a["users"]["APPROVER"], mandate, "ACTIVE")
    waiting = engine.start("pay_someone", a["id"], routine_inputs.validate("pay_someone", {"counterparty_name": "Alex Chen", "amount": "200", "asset": "USDC"}), "CONFIRMATION", actor=op)
    assert waiting["status"] == "WAITING_ANSWER"
    yield World(a, a_biz, b, b_biz, document, bill, someone, expense, mandate, waiting["run_id"])
    llm.set_fake(None)


@pytest.fixture(autouse=True)
def model_stays_fake(world):
    state_fields = with_quotes(invoice_fields("ATTACK-1"))

    def fake(step_name, parts, schema):
        if step_name == "extract_fields":
            return state_fields
        if step_name == "investigate":
            return {"findings": []}
        return {"explanation": "Plain explanation.", "message": "m", "category": "Other"}

    llm.set_fake(fake)
    yield
    llm.set_fake(None)
    main_loop.set_fake_model(None)


# Read Tools. Each row: tool, its arguments, and how to tell it found the record.


def by_id_tools(w):
    return [
        ("get_bill", {"bill_id": w.bill["obligation_id"]}),
        ("get_payment", {"payment_id": w.bill["intent_id"]}),
        ("get_run", {"run_id": w.bill["run_id"]}),
        ("read_document", {"document_id": w.document}),
    ]


def test_read_tools_by_id_find_nothing_in_another_team(world):
    for name, args in by_id_tools(world):
        fn = read_tools.READ_TOOLS[name][0]
        assert fn(world.a["id"], **args), f"{name} should work for the owner"
        assert fn(world.b["id"], **args) is None, f"{name} leaked across teams"


@pytest.mark.parametrize("name,marker", [("list_bills", "SECRET-1"), ("list_payments", "SECRET-1"), ("list_expenses", "March")])
def test_read_tools_listing_show_only_this_team(world, name, marker):
    fn = read_tools.READ_TOOLS[name][0]
    assert marker in str(fn(world.a["id"]))
    assert marker not in str(fn(world.b["id"]))


def test_search_and_balances_return_only_this_teams_ids(world):
    a_ids = {r["id"] for r in read_tools.search_counterparties(world.a["id"], "Alex")}
    b_ids = {r["id"] for r in read_tools.search_counterparties(world.b["id"], "Alex")}
    assert a_ids and b_ids and not a_ids & b_ids
    assert {r["id"] for r in read_tools.get_balances(world.a["id"])}.isdisjoint(r["id"] for r in read_tools.get_balances(world.b["id"]))
    assert read_tools.list_alerts(world.b["id"]) == []


def test_every_read_tool_is_covered_here():
    """A new Read Tool must be added to this file."""
    covered = {"get_bill", "get_payment", "get_run", "read_document", "list_bills", "list_payments", "list_expenses", "search_counterparties", "get_balances", "list_alerts", "get_compliance_rules"}
    assert set(read_tools.READ_TOOLS) == covered


def test_the_model_cannot_choose_a_team(world):
    """A team_id passed by the model never replaces the session's team."""
    session = main_loop.Session(world.b["id"], world.b["users"]["OPERATOR"], "chat_x", "turn_x", TraceContext(world.b["id"], run_id=world.bill["run_id"]))
    result = main_loop._dispatch(session, "get_bill", {"bill_id": world.bill["obligation_id"], "team_id": world.a["id"]})
    assert "SECRET-1" not in str(result)
    # Counterexample: without the forged argument the session's own team still sees nothing.
    assert main_loop._dispatch(session, "get_bill", {"bill_id": world.bill["obligation_id"]}) == {"error": "not found in this team"}


# Chips


@pytest.mark.parametrize("kind,attr", [("bill", "obligation_id"), ("payment", "intent_id"), ("run", "run_id")])
def test_chips_cannot_render_another_teams_record(world, kind, attr):
    record = world.bill[attr]
    assert not chips.render(world.a["id"], f"[[{kind}:{record}]]")[0].get("missing")
    chip = chips.render(world.b["id"], f"[[{kind}:{record}]]")[0]
    assert chip["missing"] and chip.get("amount") is None and chip.get("status") is None


def test_expense_and_account_chips_cannot_cross(world):
    assert not chips.render(world.a["id"], f"[[expense:{world.expense}]]")[0].get("missing")
    assert chips.render(world.b["id"], f"[[expense:{world.expense}]]")[0]["missing"]
    assert not chips.render(world.a["id"], f"[[account:{world.a_biz['gbp']}]]")[0].get("missing")
    assert chips.render(world.b["id"], f"[[account:{world.a_biz['gbp']}]]")[0]["missing"]


def test_the_reply_check_treats_a_foreign_payment_as_unconfirmed(world):
    text = f"That was paid: [[payment:{world.paid['intent_id']}]]."
    assert chips.check_reply(world.a["id"], text, [{"x": 1}]) == []
    assert chips.check_reply(world.b["id"], text, [{"x": 1}]) != []


# Routine inputs


def attack(world, routine, inputs, trigger="CONFIRMATION"):
    return engine.start(routine, world.b["id"], inputs, trigger, actor=world.b["users"]["OPERATOR"])


def test_pay_bill_with_another_teams_document_stops(world):
    outcome = attack(world, "pay_bill", {"document_id": world.document})
    assert "wasn't found in this team" in outcome["stop"] and outcome["obligation_id"] is None


def test_pay_bill_with_another_teams_bill_stops(world):
    outcome = attack(world, "pay_bill", {"obligation_id": world.bill["obligation_id"]})
    assert "wasn't found in this team" in outcome["stop"] and outcome["intent_id"] is None


def test_register_and_compliance_cannot_use_another_teams_document(world):
    for routine in ("register_bill", "check_bill_compliance"):
        assert "wasn't found in this team" in attack(world, routine, {"document_id": world.document})["stop"]


def test_replace_bill_cannot_touch_another_teams_bill(world):
    outcome = attack(world, "replace_bill", {"obligation_id": world.bill["obligation_id"], "reason": "Mine now"})
    assert "wasn't found in this team" in outcome["stop"]
    with tx() as conn:
        assert fetch_one(conn, "SELECT status FROM obligations WHERE id = %s", (world.bill["obligation_id"],))["status"] == "PAYMENT_PREPARED"
        assert fetch_one(conn, "SELECT status FROM approvals WHERE id = %s", (world.bill["approval_id"],))["status"] == "PENDING"


def test_replace_bill_cannot_borrow_another_teams_document(world):
    own = attack(world, "pay_someone", {"counterparty_name": "Alex Chen", "amount_minor": "1000000", "asset": "USDC", "purpose": "x"})
    outcome = attack(world, "replace_bill", {"obligation_id": own["obligation_id"], "document_id": world.document, "reason": "x"})
    assert "no document in this team" in outcome["stop"]
    with tx() as conn:
        assert fetch_one(conn, "SELECT status FROM obligations WHERE id = %s", (own["obligation_id"],))["status"] != "SUPERSEDED"


def test_revise_and_chase_cannot_reach_another_teams_expense(world):
    assert "wasn't found" in attack(world, "revise_expense", {"expense_id": world.expense, "category": "Travel", "reason": "x"})["stop"]
    assert "wasn't found" in attack(world, "chase_missing_document", {"expense_id": world.expense})["stop"]
    with tx() as conn:
        assert accounting.latest(conn, world.a["id"], world.expense)["category"] != "Travel"


def test_evidence_cannot_cross_either_way(world):
    own_doc = store_document(world.b["id"], "b.txt", "text/plain", b"B's receipt", None)
    with pytest.raises(ValueError):
        accounting.attach_evidence(world.b["id"], world.b["users"]["OPERATOR"], world.expense, own_doc)
    with pytest.raises(ValueError):
        accounting.attach_evidence(world.a["id"], world.a["users"]["OPERATOR"], world.expense, own_doc)


def test_an_answer_cannot_choose_another_teams_counterparty_or_account(world):
    team = world.b
    with tx() as conn:
        conn.execute("INSERT INTO financial_accounts (id, team_id, name, provider, rail, asset, external_id) VALUES (%s, %s, 'Second', 'SIMULATOR', 'FIAT_BANK', 'GBP', %s)", (new_id("acct"), team["id"], team["id"] + "_2"))
    forged = attack(world, "pay_someone", {"counterparty_name": "Examplo", "amount_minor": "48000", "asset": "GBP", "purpose": "x", "source_account_id": world.a_biz["gbp"]})
    assert "doesn't hold this asset in this team" in forged["stop"] and forged["intent_id"] is None
    named = attack(world, "pay_someone", {"counterparty_id": world.a_biz["alex"], "amount_minor": "48000", "asset": "USDC", "purpose": "x"})
    assert "isn't in this team" in named["stop"]


def test_a_waiting_run_cannot_be_answered_by_another_team(world):
    with pytest.raises(engine.InvalidAnswer):
        engine.answer(world.waiting_run, world.b["id"], "Stolen purpose")
    with tx() as conn:
        assert fetch_one(conn, "SELECT status FROM routine_runs WHERE id = %s", (world.waiting_run,))["status"] == "WAITING_ANSWER"


def test_prepare_occurrence_cannot_claim_for_another_teams_mandate(world):
    outcome = engine.start("prepare_occurrence", world.b["id"], {"mandate_id": world.mandate, "period": "2099-11"}, "TIMER")
    assert "isn't active" in outcome["stop"]
    with tx() as conn:
        assert fetch_one(conn, "SELECT count(*) AS n FROM occurrences WHERE mandate_id = %s", (world.mandate,))["n"] == 0
    # Counterexample: the owning team can claim that same period.
    owned = engine.start("prepare_occurrence", world.a["id"], {"mandate_id": world.mandate, "period": "2099-11"}, "TIMER")
    assert owned["status"] == "AWAITING_APPROVAL"


def test_mandates_and_destinations_cannot_be_changed_by_another_team(world):
    with pytest.raises(ValueError):
        recurring.set_mandate_status(world.b["id"], world.b["users"]["APPROVER"], world.mandate, "REVOKED")
    with pytest.raises(PermissionError):
        recurring.set_mandate_status(world.a["id"], world.b["users"]["APPROVER"], world.mandate, "REVOKED")
    with pytest.raises(contacts.VerificationRefused):
        contacts.revoke_destination(world.b["id"], world.b["users"]["APPROVER"], world.a_biz["examplo_dest"], "x")
    with pytest.raises(contacts.VerificationRefused):
        contacts.verify_destination(world.b["id"], world.b["users"]["APPROVER"], world.a_biz["examplo_dest"], "NEW_RELATIONSHIP", "x")
    with tx() as conn:
        assert fetch_one(conn, "SELECT status FROM payment_destinations WHERE id = %s", (world.a_biz["examplo_dest"],))["status"] == "VERIFIED"
        assert fetch_one(conn, "SELECT status FROM mandates WHERE id = %s", (world.mandate,))["status"] == "ACTIVE"


def test_add_counterparty_stays_in_the_callers_team(world):
    outcome = attack(world, "add_counterparty", {"display_name": "Alex Chen", "iban": EXAMPLO_IBAN})
    assert outcome["counterparty_id"] == world.b_biz["alex"]
    with tx() as conn:
        assert fetch_one(conn, "SELECT team_id FROM payment_destinations WHERE id = %s", (outcome["destination_id"],))["team_id"] == world.b["id"]


# Approvals and execution


def test_approval_cannot_be_decided_from_another_team(world):
    with tx() as conn:
        card = fetch_one(conn, "SELECT * FROM approvals WHERE id = %s", (world.bill["approval_id"],))
    # B's Approver, using B's team and A's approval, with the right hash from a leaked screen.
    refused = approval.decide(world.b["id"], world.b["users"]["APPROVER"], card["id"], card["payload_hash"], "APPROVED", reviewed=True)
    assert refused.status == "NOT_FOUND" and not refused.won
    # B's Approver claiming to be in team A has no role there.
    refused = approval.decide(world.a["id"], world.b["users"]["APPROVER"], card["id"], card["payload_hash"], "APPROVED", reviewed=True)
    assert not refused.won and "Only an Approver" in refused.message
    with tx() as conn:
        assert fetch_one(conn, "SELECT status FROM approvals WHERE id = %s", (card["id"],))["status"] == "PENDING"
        assert fetch_one(conn, "SELECT count(*) AS n FROM jobs WHERE dedupe_key = %s", (f"execute:{card['id']}",))["n"] == 0


def test_execute_refuses_another_teams_payment(world):
    assert execute.execute(world.b["id"], world.bill["intent_id"], world.bill["approval_id"])["status"] == "STOPPED"
    with tx() as conn:
        assert fetch_one(conn, "SELECT status FROM payment_intents WHERE id = %s", (world.bill["intent_id"],))["status"] == "READY_FOR_APPROVAL"


def test_manual_resolution_and_corrections_cannot_cross(world):
    with tx() as conn:
        attempt = fetch_one(conn, "SELECT id FROM payment_attempts WHERE intent_id = %s", (world.paid["intent_id"],))
    with pytest.raises(ValueError):
        reconcile.resolve_manually(world.b["id"], world.b["users"]["APPROVER"], attempt["id"], "NOT_PAID", "x")
    with pytest.raises(ValueError):
        accounting.manual_correction(world.b["id"], world.b["users"]["APPROVER"], world.bill["obligation_id"], "description", "x", "y")
    with pytest.raises(PermissionError):
        accounting.manual_correction(world.a["id"], world.b["users"]["APPROVER"], world.bill["obligation_id"], "description", "x", "y")


def test_purchases_cannot_be_approved_or_run_from_another_team(world):
    purchase_id = new_id("pur")
    with tx() as conn:
        conn.execute("INSERT INTO purchase_summaries (id, team_id, merchant, summary, payload_hash, order_status) VALUES (%s, %s, 'Shop', '{}', 'h', 'AWAITING_APPROVAL')", (purchase_id, world.a["id"]))
    assert not purchases.approve_purchase(world.b["id"], world.b["users"]["APPROVER"], purchase_id, "h")["won"]
    assert purchases.execute_checkout(world.b["id"], purchase_id)["status"] == "STOPPED"
    with tx() as conn:
        assert fetch_one(conn, "SELECT order_status FROM purchase_summaries WHERE id = %s", (purchase_id,))["order_status"] == "AWAITING_APPROVAL"


def test_memory_does_not_cross(world):
    from payoai import memory

    memory.save(world.a["id"], None, "reply_style", None, {"style": "short"}, "confirmed", [])
    assert memory.preferences_for(world.a["id"], None, ("reply_style",))
    assert memory.preferences_for(world.b["id"], None, ("reply_style",)) == []
